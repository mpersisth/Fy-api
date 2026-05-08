package main

import (
	"context"
	"fmt"
	"sync"
	"time"
)

// Runner orchestrates (channel × model × mode × reps) test cases over a bounded
// worker pool and returns aggregated results.
type Runner struct {
	cfg         *BenchmarkConfig
	chatClient  *ChatClient
	adminClient *AdminClient

	mu      sync.Mutex
	results map[CaseKey][]*ChatResult
}

func NewRunner(cfg *BenchmarkConfig) *Runner {
	timeout := time.Duration(cfg.Test.TimeoutSec) * time.Second
	return &Runner{
		cfg:         cfg,
		chatClient:  NewChatClient(cfg.Gateway.BaseURL, cfg.Gateway.UserToken, timeout),
		adminClient: NewAdminClient(cfg.Gateway.BaseURL, cfg.Gateway.AdminToken, cfg.Gateway.AdminUserID, 30*time.Second),
		results:     make(map[CaseKey][]*ChatResult),
	}
}

// Run resolves channels via the admin API, fans out test cases to a worker
// pool, collects results, and returns aggregates per CaseKey.
func (r *Runner) Run(ctx context.Context) ([]Aggregate, error) {
	// 1. Resolve the channel catalog — we need real names for the report and
	//    we want to skip any channel the user listed that's no longer present.
	catalog, err := r.adminClient.ListChannels(ctx, false /* include disabled, we'll warn */)
	if err != nil {
		return nil, fmt.Errorf("list channels: %w", err)
	}
	byID := make(map[int]Channel, len(catalog))
	for _, ch := range catalog {
		byID[ch.ID] = ch
	}

	// 2. Build case list and print a plan up-front so it's obvious what will run.
	type job struct {
		channel  Channel
		model    string
		streamed bool
	}
	var jobs []job
	for _, cfgCh := range r.cfg.Channels {
		ch, ok := byID[cfgCh.ID]
		if !ok {
			fmt.Printf("  ! channel id=%d (%q) not found in gateway, skipping\n", cfgCh.ID, cfgCh.Name)
			continue
		}
		if ch.Status != 1 {
			fmt.Printf("  ! channel id=%d (%q) is disabled (status=%d), testing anyway\n", ch.ID, ch.Name, ch.Status)
		}
		for _, m := range cfgCh.TestModels {
			if r.cfg.Test.Stream {
				jobs = append(jobs, job{ch, m, true})
			}
			if r.cfg.Test.NonStream {
				jobs = append(jobs, job{ch, m, false})
			}
		}
	}

	reps := r.cfg.Test.RepsPerCase
	totalRequests := len(jobs) * reps
	fmt.Printf("Plan: %d cases × %d reps = %d requests at concurrency=%d\n",
		len(jobs), reps, totalRequests, r.cfg.Test.Concurrency)
	if len(jobs) == 0 {
		return nil, fmt.Errorf("no valid cases to run")
	}

	// 3. Run with a semaphore-bounded worker pool.
	sem := make(chan struct{}, r.cfg.Test.Concurrency)
	var wg sync.WaitGroup
	var completed int64
	var completedMu sync.Mutex

	start := time.Now()
	for _, j := range jobs {
		for i := 0; i < reps; i++ {
			select {
			case <-ctx.Done():
				wg.Wait()
				return nil, ctx.Err()
			case sem <- struct{}{}:
			}
			wg.Add(1)
			go func(j job, rep int) {
				defer wg.Done()
				defer func() { <-sem }()

				res := r.runOne(ctx, j.channel, j.model, j.streamed)
				r.record(j.channel, j.model, j.streamed, res)

				completedMu.Lock()
				completed++
				done := completed
				completedMu.Unlock()
				status := "ok"
				if !res.Success {
					status = "FAIL: " + truncate(res.ErrMessage, 120)
				}
				fmt.Printf("  [%d/%d] ch=%d %s stream=%v rep=%d E2E=%dms TTFT=%dms tok=%d → %s\n",
					done, totalRequests,
					j.channel.ID, j.model, j.streamed, rep+1,
					res.E2E.Milliseconds(), res.TTFT.Milliseconds(),
					res.Usage.CompletionTokens, status)
			}(j, i)
		}
	}
	wg.Wait()
	fmt.Printf("Completed %d requests in %s\n", totalRequests, time.Since(start).Round(time.Millisecond))

	// 4. Aggregate per CaseKey.
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]Aggregate, 0, len(r.results))
	for key, list := range r.results {
		out = append(out, AggregateResults(key, list))
	}
	return out, nil
}

// runOne builds the chat request for a single rep and dispatches it.
// Each rep gets its own per-request context bounded by the per-request timeout,
// so a hung upstream can't stall the whole suite.
func (r *Runner) runOne(parent context.Context, ch Channel, model string, streamed bool) *ChatResult {
	reqCtx, cancel := context.WithTimeout(parent, time.Duration(r.cfg.Test.TimeoutSec)*time.Second)
	defer cancel()

	req := ChatRequest{
		Model:     model,
		Stream:    streamed,
		MaxTokens: r.cfg.Test.MaxTokens,
		Messages: []ChatMessage{
			{Role: "user", Content: r.cfg.Test.Prompt},
		},
	}
	return r.chatClient.Do(reqCtx, req)
}

func (r *Runner) record(ch Channel, model string, streamed bool, res *ChatResult) {
	key := CaseKey{
		ChannelID:   ch.ID,
		ChannelName: ch.Name,
		Model:       model,
		Streamed:    streamed,
	}
	r.mu.Lock()
	r.results[key] = append(r.results[key], res)
	r.mu.Unlock()
}
