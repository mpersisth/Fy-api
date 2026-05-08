package main

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"time"
)

// Exporter writes aggregated results to JSON and/or CSV.
type Exporter struct {
	cfg *BenchmarkConfig
	ts  string
}

func NewExporter(cfg *BenchmarkConfig) *Exporter {
	return &Exporter{cfg: cfg, ts: time.Now().Format("2006-01-02_15-04-05")}
}

// Export writes results to each configured format; returns the list of files written.
func (e *Exporter) Export(aggs []Aggregate) ([]string, error) {
	if err := os.MkdirAll(e.cfg.Export.OutputDir, 0o755); err != nil {
		return nil, fmt.Errorf("mkdir output dir: %w", err)
	}
	sort.Slice(aggs, func(i, j int) bool {
		if aggs[i].ChannelID != aggs[j].ChannelID {
			return aggs[i].ChannelID < aggs[j].ChannelID
		}
		if aggs[i].Model != aggs[j].Model {
			return aggs[i].Model < aggs[j].Model
		}
		return !aggs[i].Streamed && aggs[j].Streamed // non-streamed first
	})

	var files []string
	for _, format := range e.cfg.Export.Formats {
		switch format {
		case "json":
			f, err := e.writeJSON(aggs)
			if err != nil {
				return files, err
			}
			files = append(files, f)
		case "csv":
			f, err := e.writeCSV(aggs)
			if err != nil {
				return files, err
			}
			files = append(files, f)
		default:
			return files, fmt.Errorf("unknown export format: %s", format)
		}
	}
	return files, nil
}

func (e *Exporter) writeJSON(aggs []Aggregate) (string, error) {
	path := filepath.Join(e.cfg.Export.OutputDir, "benchmark_"+e.ts+".json")
	f, err := os.Create(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	doc := map[string]any{
		"generated_at": time.Now().UTC().Format(time.RFC3339),
		"gateway":      e.cfg.Gateway.BaseURL,
		"test": map[string]any{
			"concurrency":     e.cfg.Test.Concurrency,
			"reps_per_case":   e.cfg.Test.RepsPerCase,
			"timeout_seconds": e.cfg.Test.TimeoutSec,
			"max_tokens":      e.cfg.Test.MaxTokens,
			"prompt":          e.cfg.Test.Prompt,
		},
		"results": aggs,
	}
	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	if err := enc.Encode(doc); err != nil {
		return "", err
	}
	return path, nil
}

var csvHeader = []string{
	"channel_id", "channel_name", "model", "streamed",
	"total", "ok", "failed", "success_rate_pct",
	"e2e_p50_ms", "e2e_p95_ms", "e2e_p99_ms", "e2e_avg_ms", "e2e_max_ms",
	"ttft_p50_ms", "ttft_p95_ms", "ttft_p99_ms",
	"itl_p50_ms", "itl_p95_ms",
	"tokens_per_sec_avg", "tokens_per_sec_p50",
	"avg_prompt_tokens", "avg_completion_tokens", "avg_cached_tokens",
	"top_error",
}

func (e *Exporter) writeCSV(aggs []Aggregate) (string, error) {
	path := filepath.Join(e.cfg.Export.OutputDir, "benchmark_"+e.ts+".csv")
	f, err := os.Create(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	w := csv.NewWriter(f)
	defer w.Flush()

	if err := w.Write(csvHeader); err != nil {
		return "", err
	}
	for _, a := range aggs {
		row := []string{
			strconv.Itoa(a.ChannelID),
			a.ChannelName,
			a.Model,
			strconv.FormatBool(a.Streamed),
			strconv.Itoa(a.Total),
			strconv.Itoa(a.OK),
			strconv.Itoa(a.Failed),
			fmtFloat(a.SuccessRatePct, 1),
			fmtFloat(a.E2E.P50Ms, 1),
			fmtFloat(a.E2E.P95Ms, 1),
			fmtFloat(a.E2E.P99Ms, 1),
			fmtFloat(a.E2E.AvgMs, 1),
			fmtFloat(a.E2E.MaxMs, 1),
			fmtFloat(a.TTFT.P50Ms, 1),
			fmtFloat(a.TTFT.P95Ms, 1),
			fmtFloat(a.TTFT.P99Ms, 1),
			fmtFloat(a.ITL.P50Ms, 1),
			fmtFloat(a.ITL.P95Ms, 1),
			fmtFloat(a.TokensPerSec.Avg, 2),
			fmtFloat(a.TokensPerSec.P50, 2),
			fmtFloat(a.AvgPromptTokens, 1),
			fmtFloat(a.AvgCompletion, 1),
			fmtFloat(a.AvgCachedTokens, 1),
			topError(a.ErrorBreakdown),
		}
		if err := w.Write(row); err != nil {
			return "", err
		}
	}
	return path, nil
}

func fmtFloat(v float64, dp int) string {
	if v == 0 {
		return ""
	}
	return strconv.FormatFloat(v, 'f', dp, 64)
}

// topError returns the most common error signature, or "" if no errors.
func topError(m map[string]int) string {
	var best string
	var bestN int
	for k, n := range m {
		if n > bestN {
			best, bestN = k, n
		}
	}
	if best == "" {
		return ""
	}
	return fmt.Sprintf("%s (x%d)", truncate(best, 80), bestN)
}
