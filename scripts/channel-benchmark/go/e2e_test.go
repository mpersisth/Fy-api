package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// TestEndToEndWithMockGateway spins up an httptest server that fakes the
// /api/channel/ list response and a streaming /v1/chat/completions response,
// then drives the full runner against it to validate:
//   - admin auth headers are sent correctly
//   - channel list is paginated and parsed
//   - SSE stream is parsed, TTFT is measured from first content chunk
//   - usage block from final pre-[DONE] chunk is captured
//   - aggregation produces the expected shape
func TestEndToEndWithMockGateway(t *testing.T) {
	var (
		gotAdminAuth   string
		gotAdminUserID string
		gotChatAuth    string
	)

	mux := http.NewServeMux()

	mux.HandleFunc("/api/channel/", func(w http.ResponseWriter, r *http.Request) {
		gotAdminAuth = r.Header.Get("Authorization")
		gotAdminUserID = r.Header.Get("New-Api-User")
		resp := map[string]any{
			"success": true,
			"message": "",
			"data": map[string]any{
				"items": []map[string]any{
					{"id": 42, "name": "mock-openai", "status": 1, "models": "gpt-4o-mini"},
				},
				"total":     1,
				"page":      1,
				"page_size": 200,
			},
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(resp)
	})

	mux.HandleFunc("/v1/chat/completions", func(w http.ResponseWriter, r *http.Request) {
		gotChatAuth = r.Header.Get("Authorization")
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		streamed, _ := body["stream"].(bool)

		if !streamed {
			resp := map[string]any{
				"choices": []map[string]any{{
					"message":       map[string]string{"role": "assistant", "content": "pong"},
					"finish_reason": "stop",
				}},
				"usage": map[string]int{"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10},
			}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(resp)
			return
		}

		w.Header().Set("Content-Type", "text/event-stream")
		flusher := w.(http.Flusher)

		write := func(payload map[string]any) {
			b, _ := json.Marshal(payload)
			fmt.Fprintf(w, "data: %s\n\n", b)
			flusher.Flush()
		}
		// Preamble (role only — should NOT count toward TTFT)
		write(map[string]any{
			"choices": []map[string]any{{"delta": map[string]string{"role": "assistant"}}},
		})
		time.Sleep(20 * time.Millisecond)
		// First content chunk → TTFT anchor
		write(map[string]any{
			"choices": []map[string]any{{"delta": map[string]string{"content": "po"}}},
		})
		time.Sleep(30 * time.Millisecond)
		// Second content chunk → ITL sample
		write(map[string]any{
			"choices": []map[string]any{{"delta": map[string]string{"content": "ng"}}},
		})
		// Finish + usage chunk
		fr := "stop"
		write(map[string]any{
			"choices": []map[string]any{{"delta": map[string]string{}, "finish_reason": &fr}},
			"usage":   map[string]int{"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11},
		})
		fmt.Fprint(w, "data: [DONE]\n\n")
		flusher.Flush()
	})

	srv := httptest.NewServer(mux)
	defer srv.Close()

	cfg := &BenchmarkConfig{
		Gateway: GatewayConfig{
			BaseURL:     srv.URL,
			AdminToken:  "admin-tok",
			AdminUserID: "7",
			UserToken:   "sk-user",
		},
		Test: TestConfig{
			Concurrency: 2, RepsPerCase: 2, TimeoutSec: 5, MaxTokens: 16,
			Stream: true, NonStream: true, Prompt: "ping",
		},
		Channels: []ChannelConfig{
			{ID: 42, Name: "mock-openai", TestModels: []string{"gpt-4o-mini"}},
		},
	}
	applyDefaults(cfg)

	runner := NewRunner(cfg)
	aggs, err := runner.Run(testContext(t))
	if err != nil {
		t.Fatalf("runner failed: %v", err)
	}

	// Headers sent correctly?
	if gotAdminAuth != "admin-tok" {
		t.Errorf("admin Authorization = %q, want raw token (no Bearer)", gotAdminAuth)
	}
	if gotAdminUserID != "7" {
		t.Errorf("New-Api-User = %q, want %q", gotAdminUserID, "7")
	}
	if !strings.HasPrefix(gotChatAuth, "Bearer ") {
		t.Errorf("chat Authorization = %q, want 'Bearer sk-user'", gotChatAuth)
	}

	// Two aggregates: stream=true and stream=false, each with 2 reps.
	if len(aggs) != 2 {
		t.Fatalf("got %d aggregates, want 2", len(aggs))
	}
	for _, a := range aggs {
		if a.ChannelID != 42 {
			t.Errorf("unexpected ChannelID=%d", a.ChannelID)
		}
		if a.OK != 2 {
			t.Errorf("[stream=%v] OK=%d, want 2 (errors: %v)", a.Streamed, a.OK, a.ErrorBreakdown)
		}
		if a.Streamed {
			// TTFT must be >0 and come from the first CONTENT chunk (so > 20ms
			// because of the preamble delay).
			if a.TTFT.P50Ms < 15 {
				t.Errorf("streamed TTFT p50=%.1fms is suspiciously low", a.TTFT.P50Ms)
			}
			if a.TTFT.P50Ms >= a.E2E.P50Ms {
				t.Errorf("TTFT p50=%.1f >= E2E p50=%.1f", a.TTFT.P50Ms, a.E2E.P50Ms)
			}
			if a.ITL.Samples != 2 { // 2 reps × 1 gap each
				t.Errorf("ITL samples=%d, want 2", a.ITL.Samples)
			}
			if a.AvgCompletion != 2 {
				t.Errorf("streamed AvgCompletion=%.1f, want 2 (usage from final chunk)", a.AvgCompletion)
			}
		} else {
			if a.TTFT.Samples != 0 {
				t.Errorf("non-stream TTFT should be empty, got %d samples", a.TTFT.Samples)
			}
			if a.AvgCompletion != 1 {
				t.Errorf("non-stream AvgCompletion=%.1f, want 1", a.AvgCompletion)
			}
		}
	}
}
