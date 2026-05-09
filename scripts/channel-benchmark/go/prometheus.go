// Tiny Prometheus exposition-format metrics, with no third-party deps.
//
// We deliberately don't pull in prometheus/client_golang here because:
//   1. This binary should stay drop-on-prod simple — go.mod has only yaml.v3.
//   2. We expose ~6 series, all flat. A dependency-free emitter is shorter
//      than the import block would be.
//
// What we emit per-scrape:
//
//   channel_benchmark_request_total{channel,model,streamed,outcome}     counter
//   channel_benchmark_success_rate{channel,model,streamed}              gauge   0-1
//   channel_benchmark_e2e_seconds{channel,model,streamed,quantile}      gauge   p50/p95/p99
//   channel_benchmark_ttft_seconds{channel,model,streamed,quantile}     gauge   stream only
//   channel_benchmark_tokens_per_sec{channel,model,streamed}            gauge   avg
//   channel_benchmark_run_age_seconds                                   gauge   how stale the data is
//   channel_benchmark_last_run_unix_seconds                             gauge
//
// We emit summary-style quantiles (single gauge per p) rather than full
// histogram buckets — Prometheus prefers histograms, but for this tool
// callers are looking at "p95 for ChannelX" not "rate over [50ms,100ms]";
// summary serves the 95% case in 1/10th the line count.

package main

import (
	"fmt"
	"io"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

// MetricsRegistry holds the most recent benchmark snapshot. It is safe for
// concurrent reads (HTTP scrape) and writes (benchmark completion).
//
// Replace-on-write semantics: every benchmark cycle calls Replace() with the
// fresh aggregate set. Counters carry across replaces (so request_total grows
// monotonically); gauges/quantiles are taken from the latest run only.
type MetricsRegistry struct {
	mu sync.RWMutex

	// Cumulative counters survive across runs.
	requestTotal map[seriesKey]uint64

	// Latest-run-only state.
	latest        []Aggregate
	lastRun       time.Time
	consecutiveOK int
	lastErr       string
}

// seriesKey identifies a single time-series for counter aggregation.
type seriesKey struct {
	channel  string
	model    string
	streamed bool
	outcome  string // "ok" | "fail"
}

// NewMetricsRegistry returns an empty registry ready for first Replace().
func NewMetricsRegistry() *MetricsRegistry {
	return &MetricsRegistry{
		requestTotal: make(map[seriesKey]uint64),
	}
}

// Replace swaps in a fresh aggregate set and bumps the cumulative counters
// by the OK / Failed counts of each case. lastErr is "" on success.
func (r *MetricsRegistry) Replace(aggs []Aggregate, runErr error) {
	r.mu.Lock()
	defer r.mu.Unlock()

	r.latest = aggs
	r.lastRun = time.Now()
	if runErr == nil {
		r.consecutiveOK++
		r.lastErr = ""
	} else {
		r.consecutiveOK = 0
		r.lastErr = runErr.Error()
	}

	for _, a := range aggs {
		if a.OK > 0 {
			k := seriesKey{a.ChannelName, a.Model, a.Streamed, "ok"}
			r.requestTotal[k] += uint64(a.OK)
		}
		if a.Failed > 0 {
			k := seriesKey{a.ChannelName, a.Model, a.Streamed, "fail"}
			r.requestTotal[k] += uint64(a.Failed)
		}
	}
}

// WriteExposition emits the Prometheus text exposition format to w.
func (r *MetricsRegistry) WriteExposition(w io.Writer) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	now := time.Now()
	staleSec := 0.0
	lastUnix := 0.0
	if !r.lastRun.IsZero() {
		staleSec = now.Sub(r.lastRun).Seconds()
		lastUnix = float64(r.lastRun.Unix())
	}

	// channel_benchmark_request_total
	emitHelp(w, "channel_benchmark_request_total", "Cumulative chat-completion requests issued, by outcome.")
	emitType(w, "channel_benchmark_request_total", "counter")
	keys := sortedSeriesKeys(r.requestTotal)
	for _, k := range keys {
		emitMetric(w, "channel_benchmark_request_total",
			labels{
				"channel":  k.channel,
				"model":    k.model,
				"streamed": boolStr(k.streamed),
				"outcome":  k.outcome,
			},
			float64(r.requestTotal[k]),
		)
	}

	// success_rate
	emitHelp(w, "channel_benchmark_success_rate", "Per-case success rate from the latest run, in [0,1].")
	emitType(w, "channel_benchmark_success_rate", "gauge")
	for _, a := range r.latest {
		if a.Total == 0 {
			continue
		}
		emitMetric(w, "channel_benchmark_success_rate",
			caseLabels(a),
			a.SuccessRatePct/100.0,
		)
	}

	// e2e quantiles
	emitHelp(w, "channel_benchmark_e2e_seconds", "End-to-end latency quantiles from the latest run.")
	emitType(w, "channel_benchmark_e2e_seconds", "gauge")
	for _, a := range r.latest {
		if a.E2E.Samples == 0 {
			continue
		}
		emitQuantile(w, "channel_benchmark_e2e_seconds", caseLabels(a), "0.5", a.E2E.P50Ms/1000)
		emitQuantile(w, "channel_benchmark_e2e_seconds", caseLabels(a), "0.95", a.E2E.P95Ms/1000)
		emitQuantile(w, "channel_benchmark_e2e_seconds", caseLabels(a), "0.99", a.E2E.P99Ms/1000)
	}

	// ttft quantiles
	emitHelp(w, "channel_benchmark_ttft_seconds", "Time-to-first-token quantiles from the latest run (streaming only).")
	emitType(w, "channel_benchmark_ttft_seconds", "gauge")
	for _, a := range r.latest {
		if !a.Streamed || a.TTFT.Samples == 0 {
			continue
		}
		emitQuantile(w, "channel_benchmark_ttft_seconds", caseLabels(a), "0.5", a.TTFT.P50Ms/1000)
		emitQuantile(w, "channel_benchmark_ttft_seconds", caseLabels(a), "0.95", a.TTFT.P95Ms/1000)
		emitQuantile(w, "channel_benchmark_ttft_seconds", caseLabels(a), "0.99", a.TTFT.P99Ms/1000)
	}

	// tokens/sec
	emitHelp(w, "channel_benchmark_tokens_per_sec", "Average decode throughput from the latest run.")
	emitType(w, "channel_benchmark_tokens_per_sec", "gauge")
	for _, a := range r.latest {
		if a.TokensPerSec.Samples == 0 {
			continue
		}
		emitMetric(w, "channel_benchmark_tokens_per_sec", caseLabels(a), a.TokensPerSec.Avg)
	}

	// run health
	emitHelp(w, "channel_benchmark_run_age_seconds", "Seconds since the most recent benchmark run completed.")
	emitType(w, "channel_benchmark_run_age_seconds", "gauge")
	emitMetric(w, "channel_benchmark_run_age_seconds", nil, staleSec)

	emitHelp(w, "channel_benchmark_last_run_unix_seconds", "Unix timestamp of the most recent benchmark run; 0 if none yet.")
	emitType(w, "channel_benchmark_last_run_unix_seconds", "gauge")
	emitMetric(w, "channel_benchmark_last_run_unix_seconds", nil, lastUnix)

	emitHelp(w, "channel_benchmark_consecutive_runs_ok", "Consecutive benchmark cycles that completed without a top-level error.")
	emitType(w, "channel_benchmark_consecutive_runs_ok", "gauge")
	emitMetric(w, "channel_benchmark_consecutive_runs_ok", nil, float64(r.consecutiveOK))
}

// caseLabels returns the standard 3-label set for per-case series.
func caseLabels(a Aggregate) labels {
	return labels{
		"channel":  a.ChannelName,
		"model":    a.Model,
		"streamed": boolStr(a.Streamed),
	}
}

// labels is a small map used in exposition formatting; key order is fixed by
// caller via sorted keys at render time so output is byte-stable.
type labels map[string]string

func (l labels) render() string {
	if len(l) == 0 {
		return ""
	}
	keys := make([]string, 0, len(l))
	for k := range l {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	b.WriteByte('{')
	for i, k := range keys {
		if i > 0 {
			b.WriteByte(',')
		}
		b.WriteString(k)
		b.WriteByte('=')
		b.WriteByte('"')
		b.WriteString(escapeLabelValue(l[k]))
		b.WriteByte('"')
	}
	b.WriteByte('}')
	return b.String()
}

// escapeLabelValue escapes \ " and newline per the Prometheus exposition spec.
func escapeLabelValue(v string) string {
	if !strings.ContainsAny(v, `\"`+"\n") {
		return v
	}
	var b strings.Builder
	b.Grow(len(v) + 4)
	for _, r := range v {
		switch r {
		case '\\':
			b.WriteString(`\\`)
		case '"':
			b.WriteString(`\"`)
		case '\n':
			b.WriteString(`\n`)
		default:
			b.WriteRune(r)
		}
	}
	return b.String()
}

func emitHelp(w io.Writer, name, help string) {
	fmt.Fprintf(w, "# HELP %s %s\n", name, help)
}

func emitType(w io.Writer, name, kind string) {
	fmt.Fprintf(w, "# TYPE %s %s\n", name, kind)
}

func emitMetric(w io.Writer, name string, lbl labels, v float64) {
	fmt.Fprintf(w, "%s%s %s\n", name, lbl.render(), formatFloat(v))
}

func emitQuantile(w io.Writer, name string, base labels, q string, v float64) {
	merged := make(labels, len(base)+1)
	for k, v := range base {
		merged[k] = v
	}
	merged["quantile"] = q
	emitMetric(w, name, merged, v)
}

// formatFloat keeps the output small and human-readable.
// We avoid scientific notation for typical ranges and emit at most 6
// fractional digits.
func formatFloat(v float64) string {
	// Special-case integral values for cleanliness.
	if v == float64(int64(v)) && v >= -1e15 && v <= 1e15 {
		return fmt.Sprintf("%d", int64(v))
	}
	return strings.TrimRight(strings.TrimRight(fmt.Sprintf("%.6f", v), "0"), ".")
}

func sortedSeriesKeys(m map[seriesKey]uint64) []seriesKey {
	keys := make([]seriesKey, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		a, b := keys[i], keys[j]
		if a.channel != b.channel {
			return a.channel < b.channel
		}
		if a.model != b.model {
			return a.model < b.model
		}
		if a.streamed != b.streamed {
			return !a.streamed && b.streamed
		}
		return a.outcome < b.outcome
	})
	return keys
}

func boolStr(b bool) string {
	if b {
		return "true"
	}
	return "false"
}

// Handler returns an http.Handler that serves /metrics in Prometheus
// text/plain exposition format. Other paths return 404.
func (r *MetricsRegistry) Handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.URL.Path != "/metrics" {
			http.NotFound(w, req)
			return
		}
		w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		r.WriteExposition(w)
	})
}
