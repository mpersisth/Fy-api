// channel-benchmark is a CLI that smokes an Fy-api deployment's configured
// channels by sending real /v1/chat/completions requests and reporting
// latency, throughput, and token-accounting metrics per channel × model × mode.
//
// It is NOT a load tester (see py/ for llmperf-based load work) and NOT a
// quality evaluator. It answers: "is this channel responding, how fast, and
// with sane usage numbers?"
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"
)

func main() {
	var (
		configPath  = flag.String("config", "channel-benchmark.yaml", "Path to benchmark YAML config")
		baseURL     = flag.String("base-url", "", "Override gateway.base_url")
		outDir      = flag.String("output", "", "Override export.output_dir")
		concurrency = flag.Int("concurrency", 0, "Override test.concurrency")
		reps        = flag.Int("reps", 0, "Override test.reps_per_case")
		formats     = flag.String("formats", "", "Override export.formats (comma-separated, e.g. json,csv)")
		dryRun      = flag.Bool("dry-run", false, "Print resolved config and exit")

		// Daemon mode: when --prom-listen is set the binary stays up,
		// re-running the benchmark on --prom-interval and exposing
		// /metrics for Prometheus to scrape.
		promListen   = flag.String("prom-listen", "", "If set (e.g. ':9090'), run as a daemon and serve Prometheus metrics on that addr")
		promInterval = flag.Duration("prom-interval", 5*time.Minute, "How often to re-run the benchmark when --prom-listen is set")
		noExport     = flag.Bool("no-export", false, "Skip writing JSON/CSV to disk (useful in daemon mode)")

		// Long-thinking preset: overlays a long-reasoning prompt + 30-min
		// timeout + single rep onto whatever the YAML config says. Used to
		// regression-test the timeout chain (incident 2026-05-11). See
		// incidents/2026-05-11-long-reasoning-timeout.md.
		longThinking = flag.Bool("long-thinking", false, "Run the long-reasoning regression preset (overrides timeout/prompt/reps)")
	)
	flag.Parse()

	cfg, err := LoadConfig(*configPath)
	if err != nil {
		log.Fatalf("config: %v", err)
	}

	// Apply CLI overrides before validation so --base-url etc. can rescue an
	// otherwise-invalid file in ad-hoc runs.
	if *baseURL != "" {
		cfg.Gateway.BaseURL = *baseURL
	}
	if *outDir != "" {
		cfg.Export.OutputDir = *outDir
	}
	if *concurrency > 0 {
		cfg.Test.Concurrency = *concurrency
	}
	if *reps > 0 {
		cfg.Test.RepsPerCase = *reps
	}
	if *formats != "" {
		cfg.Export.Formats = splitCSV(*formats)
	}

	if *longThinking {
		applyLongThinkingPreset(cfg)
	}

	if err := cfg.Validate(); err != nil {
		log.Fatalf("config invalid: %v", err)
	}

	fmt.Printf("Gateway:       %s\n", cfg.Gateway.BaseURL)
	fmt.Printf("Channels:      %d configured\n", len(cfg.Channels))
	fmt.Printf("Concurrency:   %d\n", cfg.Test.Concurrency)
	fmt.Printf("Reps/case:     %d\n", cfg.Test.RepsPerCase)
	fmt.Printf("Stream:        %v (+ non-stream=%v)\n", cfg.Test.Stream, cfg.Test.NonStream)
	fmt.Printf("Max tokens:    %d\n", cfg.Test.MaxTokens)
	fmt.Printf("Output dir:    %s\n", cfg.Export.OutputDir)
	fmt.Printf("Formats:       %v\n", cfg.Export.Formats)
	if *promListen != "" {
		fmt.Printf("Mode:          daemon (prom listen=%s, interval=%s)\n", *promListen, *promInterval)
	} else {
		fmt.Printf("Mode:          one-shot\n")
	}
	if *longThinking {
		fmt.Printf("Preset:        long-thinking (timeout=%ds, max_tokens=%d, reps=%d)\n",
			cfg.Test.TimeoutSec, cfg.Test.MaxTokens, cfg.Test.RepsPerCase)
	}
	if *dryRun {
		fmt.Println("\n(dry-run: config valid, no requests sent)")
		return
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		sig := <-sigs
		fmt.Fprintf(os.Stderr, "\nreceived %s, shutting down...\n", sig)
		cancel()
	}()

	if *promListen != "" {
		runDaemon(ctx, cfg, *promListen, *promInterval, *noExport)
		return
	}

	runner := NewRunner(cfg)
	aggs, err := runner.Run(ctx)
	if err != nil {
		log.Fatalf("run: %v", err)
	}

	exp := NewExporter(cfg)
	files, err := exp.Export(aggs)
	if err != nil {
		log.Fatalf("export: %v", err)
	}
	for _, f := range files {
		fmt.Printf("wrote %s\n", f)
	}
	printSummary(aggs)
}

// runDaemon loops the benchmark on a fixed cadence and serves results as
// Prometheus metrics. Returns only on context cancellation. The Replace()
// of the registry is the authoritative atomic switch — partial results from
// a still-running benchmark never leak to scrapers.
func runDaemon(ctx context.Context, cfg *BenchmarkConfig, listenAddr string, interval time.Duration, noExport bool) {
	registry := NewMetricsRegistry()
	srv := &http.Server{
		Addr:              listenAddr,
		Handler:           registry.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
	}

	go func() {
		fmt.Fprintf(os.Stderr, "prom: serving /metrics on %s\n", listenAddr)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("prom listen: %v", err)
		}
	}()

	// Run once immediately so first scrape after startup has data.
	for {
		select {
		case <-ctx.Done():
			shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = srv.Shutdown(shutdownCtx)
			return
		default:
		}

		runStart := time.Now()
		fmt.Fprintf(os.Stderr, "prom: starting cycle at %s\n", runStart.Format(time.RFC3339))

		aggs, err := NewRunner(cfg).Run(ctx)
		registry.Replace(aggs, err)
		if err != nil {
			fmt.Fprintf(os.Stderr, "prom: cycle failed: %v\n", err)
		} else {
			fmt.Fprintf(os.Stderr, "prom: cycle ok in %s\n", time.Since(runStart).Round(time.Millisecond))
			if !noExport {
				exp := NewExporter(cfg)
				if files, expErr := exp.Export(aggs); expErr != nil {
					fmt.Fprintf(os.Stderr, "prom: export failed: %v\n", expErr)
				} else {
					for _, f := range files {
						fmt.Fprintf(os.Stderr, "prom: wrote %s\n", f)
					}
				}
			}
		}

		// Sleep to next cycle, accounting for run time so the cadence is stable.
		next := interval - time.Since(runStart)
		if next < time.Second {
			next = time.Second
		}
		select {
		case <-ctx.Done():
			shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = srv.Shutdown(shutdownCtx)
			return
		case <-time.After(next):
		}
	}
}

func splitCSV(s string) []string {
	parts := strings.Split(s, ",")
	out := parts[:0]
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

// printSummary writes a brief per-case table to stdout so the operator sees
// the worst offenders without opening the CSV.
func printSummary(aggs []Aggregate) {
	fmt.Println()
	fmt.Printf("%-5s %-20s %-28s %-7s %5s %5s %7s %8s %8s %8s\n",
		"chID", "channel", "model", "stream", "ok", "fail", "succ%", "e2e_p95", "ttft_p95", "tok/s")
	fmt.Println(strings.Repeat("-", 110))
	for _, a := range aggs {
		fmt.Printf("%-5d %-20s %-28s %-7v %5d %5d %6.1f%% %7.0f %8.0f %8.1f\n",
			a.ChannelID,
			truncate(a.ChannelName, 20),
			truncate(a.Model, 28),
			a.Streamed,
			a.OK, a.Failed, a.SuccessRatePct,
			a.E2E.P95Ms, a.TTFT.P95Ms, a.TokensPerSec.Avg,
		)
	}
}
