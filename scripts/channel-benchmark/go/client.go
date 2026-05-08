package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// ChatClient is a minimal OpenAI-compatible client that talks to Fy-api's
// /v1/chat/completions endpoint. It records every metric we care about for
// smoke testing — notably real TTFT (time to first usable token chunk) and
// real usage counts, not the placeholders the previous version invented.
type ChatClient struct {
	baseURL string
	token   string
	http    *http.Client
}

func NewChatClient(baseURL, token string, timeout time.Duration) *ChatClient {
	return &ChatClient{
		baseURL: baseURL,
		token:   token,
		http:    &http.Client{Timeout: timeout},
	}
}

// ChatRequest is the subset of the OpenAI schema we send.
type ChatRequest struct {
	Model     string        `json:"model"`
	Messages  []ChatMessage `json:"messages"`
	Stream    bool          `json:"stream"`
	MaxTokens int           `json:"max_tokens,omitempty"`

	// Fy-api honors this field on both streaming and non-streaming requests
	// and asks the upstream to return usage in the final stream chunk.
	// Without this, streaming responses usually omit usage entirely.
	StreamOptions *streamOptions `json:"stream_options,omitempty"`
}

type streamOptions struct {
	IncludeUsage bool `json:"include_usage"`
}

type ChatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// Usage is the OpenAI-compatible token accounting block.
type Usage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`

	PromptTokensDetails *struct {
		CachedTokens int `json:"cached_tokens"`
	} `json:"prompt_tokens_details,omitempty"`
}

// ChatResult captures everything measured about one request.
type ChatResult struct {
	// Identity
	Model    string
	Streamed bool

	// Outcome
	Success    bool
	HTTPStatus int
	ErrMessage string

	// Latency (all monotonic clock)
	StartedAt   time.Time
	E2E         time.Duration // from request sent to stream closed / body fully read
	TTFT        time.Duration // time to first non-empty content chunk (stream only)
	InterToken  []time.Duration // gaps between consecutive content chunks (stream only)

	// Content
	ContentBytes int    // total UTF-8 bytes of assistant content
	Chunks       int    // number of SSE content chunks (stream only)
	FinishReason string

	// Token accounting (from upstream usage; may be zero if upstream omits it)
	Usage Usage
}

// TPOT returns time-per-output-token if we can compute it.
// Definition (industry-standard, per NVIDIA GenAI-Perf):
//   TPOT = (E2E - TTFT) / (completion_tokens - 1)
// Returns 0 if we don't have enough data.
func (r *ChatResult) TPOT() time.Duration {
	if !r.Streamed || r.Usage.CompletionTokens < 2 || r.TTFT == 0 {
		return 0
	}
	decodeTime := r.E2E - r.TTFT
	if decodeTime <= 0 {
		return 0
	}
	return decodeTime / time.Duration(r.Usage.CompletionTokens-1)
}

// TokensPerSec returns decode throughput for this request.
func (r *ChatResult) TokensPerSec() float64 {
	if r.Usage.CompletionTokens == 0 {
		return 0
	}
	var decode time.Duration
	if r.Streamed && r.TTFT > 0 {
		decode = r.E2E - r.TTFT
	} else {
		decode = r.E2E
	}
	if decode <= 0 {
		return 0
	}
	return float64(r.Usage.CompletionTokens) / decode.Seconds()
}

// Do sends the chat request and returns a fully populated result.
// On any failure (network error, non-2xx, malformed stream) Success=false
// and ErrMessage is set; TTFT/chunks are still reported if we got far enough.
func (c *ChatClient) Do(ctx context.Context, req ChatRequest) *ChatResult {
	res := &ChatResult{
		Model:     req.Model,
		Streamed:  req.Stream,
		StartedAt: time.Now(),
	}

	// Ensure streaming responses include usage; some providers strip it otherwise.
	if req.Stream && req.StreamOptions == nil {
		req.StreamOptions = &streamOptions{IncludeUsage: true}
	}

	body, err := json.Marshal(req)
	if err != nil {
		res.ErrMessage = "marshal: " + err.Error()
		return res
	}

	httpReq, err := http.NewRequestWithContext(ctx,
		http.MethodPost,
		c.baseURL+"/v1/chat/completions",
		bytes.NewReader(body),
	)
	if err != nil {
		res.ErrMessage = "build request: " + err.Error()
		return res
	}
	// Per OpenAI client convention, user tokens go as Bearer.
	httpReq.Header.Set("Authorization", "Bearer "+c.token)
	httpReq.Header.Set("Content-Type", "application/json")
	if req.Stream {
		httpReq.Header.Set("Accept", "text/event-stream")
	} else {
		httpReq.Header.Set("Accept", "application/json")
	}

	start := time.Now()
	resp, err := c.http.Do(httpReq)
	if err != nil {
		res.E2E = time.Since(start)
		res.ErrMessage = "http do: " + err.Error()
		return res
	}
	defer resp.Body.Close()

	res.HTTPStatus = resp.StatusCode

	if resp.StatusCode >= 400 {
		peek, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		res.E2E = time.Since(start)
		res.ErrMessage = fmt.Sprintf("HTTP %d: %s", resp.StatusCode, truncate(string(peek), 300))
		return res
	}

	if req.Stream {
		c.consumeStream(resp.Body, res, start)
	} else {
		c.consumeJSON(resp.Body, res, start)
	}
	return res
}

// consumeJSON reads a non-streaming response. E2E covers the whole round-trip.
func (c *ChatClient) consumeJSON(body io.Reader, res *ChatResult, start time.Time) {
	var payload struct {
		Choices []struct {
			Message      ChatMessage `json:"message"`
			FinishReason string      `json:"finish_reason"`
		} `json:"choices"`
		Usage Usage `json:"usage"`
	}
	buf, err := io.ReadAll(body)
	res.E2E = time.Since(start)
	if err != nil {
		res.ErrMessage = "read body: " + err.Error()
		return
	}
	if err := json.Unmarshal(buf, &payload); err != nil {
		res.ErrMessage = "parse response: " + err.Error()
		return
	}
	if len(payload.Choices) > 0 {
		res.ContentBytes = len(payload.Choices[0].Message.Content)
		res.FinishReason = payload.Choices[0].FinishReason
	}
	res.Usage = payload.Usage
	res.Success = true
}

// consumeStream reads SSE and extracts TTFT, ITL samples, content, and usage.
//
// SSE framing per OpenAI: lines starting with "data: ", one JSON payload per
// line, final marker is literal "data: [DONE]". The chunk containing usage is
// typically the last non-[DONE] chunk when stream_options.include_usage=true.
//
// TTFT rule: we mark the first chunk that carries a non-empty content delta
// as the "first token". That excludes the preamble chunk many providers send
// with only a role=assistant header and no text, which would otherwise make
// TTFT artificially small.
func (c *ChatClient) consumeStream(body io.Reader, res *ChatResult, start time.Time) {
	reader := bufio.NewReaderSize(body, 16*1024)
	var lastContentAt time.Time

	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			if err != io.EOF {
				res.ErrMessage = "read stream: " + err.Error()
			}
			break
		}
		line = strings.TrimRight(line, "\r\n")
		if line == "" {
			continue
		}
		if !strings.HasPrefix(line, "data:") {
			continue
		}
		payload := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
		if payload == "[DONE]" {
			break
		}
		var chunk struct {
			Choices []struct {
				Delta struct {
					Content string `json:"content"`
				} `json:"delta"`
				FinishReason *string `json:"finish_reason"`
			} `json:"choices"`
			Usage *Usage `json:"usage"`
		}
		if err := json.Unmarshal([]byte(payload), &chunk); err != nil {
			// skip malformed chunks but don't abort — providers occasionally
			// interleave heartbeat or vendor-specific metadata chunks.
			continue
		}
		if chunk.Usage != nil {
			res.Usage = *chunk.Usage
		}
		for _, ch := range chunk.Choices {
			if ch.Delta.Content != "" {
				now := time.Now()
				if res.TTFT == 0 {
					res.TTFT = now.Sub(start)
				} else if !lastContentAt.IsZero() {
					res.InterToken = append(res.InterToken, now.Sub(lastContentAt))
				}
				lastContentAt = now
				res.Chunks++
				res.ContentBytes += len(ch.Delta.Content)
			}
			if ch.FinishReason != nil && *ch.FinishReason != "" && res.FinishReason == "" {
				res.FinishReason = *ch.FinishReason
			}
		}
	}

	res.E2E = time.Since(start)
	// Consider the request successful if we received any content OR upstream
	// reported a completion token count — some providers legitimately return
	// empty completions (e.g. safety filters) and still count as a live call.
	if res.ContentBytes > 0 || res.Usage.CompletionTokens > 0 {
		res.Success = true
	} else if res.ErrMessage == "" {
		res.ErrMessage = "stream closed with no content and no usage"
	}
}
