package main

import (
	"math"
	"sort"
	"time"
)

// CaseKey identifies an (channel, model, streaming) bucket that results
// get aggregated within. Keep the three dimensions independent so a channel
// that's fast at non-streaming but slow at streaming is visible in the report.
type CaseKey struct {
	ChannelID   int
	ChannelName string
	Model       string
	Streamed    bool
}

// Aggregate holds summary stats for all reps of one CaseKey.
type Aggregate struct {
	CaseKey
	Total           int
	OK              int
	Failed          int
	SuccessRatePct  float64
	E2E             LatencyStats     // always populated
	TTFT            LatencyStats     // streamed cases only
	ITL             LatencyStats     // streamed cases only; per-chunk gaps
	TokensPerSec    ThroughputStats  // successful cases only
	AvgPromptTokens float64
	AvgCompletion   float64
	AvgCachedTokens float64
	// ErrorBreakdown maps a short error signature to count (one line per distinct failure).
	ErrorBreakdown map[string]int
}

// LatencyStats are reported in milliseconds for human readability.
type LatencyStats struct {
	Samples  int
	MinMs    float64
	MaxMs    float64
	AvgMs    float64
	P50Ms    float64
	P95Ms    float64
	P99Ms    float64
	StdDevMs float64
}

// ThroughputStats in tokens/sec.
type ThroughputStats struct {
	Samples int
	Min     float64
	Max     float64
	Avg     float64
	P50     float64
}

// Aggregate groups a slice of ChatResults keyed by (channel, model, streamed).
func AggregateResults(caseKey CaseKey, results []*ChatResult) Aggregate {
	agg := Aggregate{
		CaseKey:        caseKey,
		Total:          len(results),
		ErrorBreakdown: map[string]int{},
	}
	if len(results) == 0 {
		return agg
	}

	var (
		e2eMs        []float64
		ttftMs       []float64
		itlMs        []float64
		tokPerSec    []float64
		promptSum    float64
		completeSum  float64
		cachedSum    float64
	)

	for _, r := range results {
		if !r.Success {
			agg.Failed++
			sig := r.ErrMessage
			if sig == "" {
				sig = "unknown error"
			}
			agg.ErrorBreakdown[sig]++
			continue
		}
		agg.OK++
		e2eMs = append(e2eMs, durMs(r.E2E))
		if r.Streamed && r.TTFT > 0 {
			ttftMs = append(ttftMs, durMs(r.TTFT))
		}
		for _, gap := range r.InterToken {
			itlMs = append(itlMs, durMs(gap))
		}
		if tps := r.TokensPerSec(); tps > 0 {
			tokPerSec = append(tokPerSec, tps)
		}
		promptSum += float64(r.Usage.PromptTokens)
		completeSum += float64(r.Usage.CompletionTokens)
		if r.Usage.PromptTokensDetails != nil {
			cachedSum += float64(r.Usage.PromptTokensDetails.CachedTokens)
		}
	}

	if agg.Total > 0 {
		agg.SuccessRatePct = 100.0 * float64(agg.OK) / float64(agg.Total)
	}
	agg.E2E = latencyStats(e2eMs)
	agg.TTFT = latencyStats(ttftMs)
	agg.ITL = latencyStats(itlMs)
	agg.TokensPerSec = throughputStats(tokPerSec)
	if agg.OK > 0 {
		agg.AvgPromptTokens = promptSum / float64(agg.OK)
		agg.AvgCompletion = completeSum / float64(agg.OK)
		agg.AvgCachedTokens = cachedSum / float64(agg.OK)
	}
	return agg
}

func durMs(d time.Duration) float64 {
	return float64(d.Nanoseconds()) / 1e6
}

func latencyStats(vals []float64) LatencyStats {
	if len(vals) == 0 {
		return LatencyStats{}
	}
	sorted := append([]float64(nil), vals...)
	sort.Float64s(sorted)
	avg := mean(sorted)
	return LatencyStats{
		Samples:  len(sorted),
		MinMs:    sorted[0],
		MaxMs:    sorted[len(sorted)-1],
		AvgMs:    avg,
		P50Ms:    percentile(sorted, 50),
		P95Ms:    percentile(sorted, 95),
		P99Ms:    percentile(sorted, 99),
		StdDevMs: stddev(sorted, avg),
	}
}

func throughputStats(vals []float64) ThroughputStats {
	if len(vals) == 0 {
		return ThroughputStats{}
	}
	sorted := append([]float64(nil), vals...)
	sort.Float64s(sorted)
	return ThroughputStats{
		Samples: len(sorted),
		Min:     sorted[0],
		Max:     sorted[len(sorted)-1],
		Avg:     mean(sorted),
		P50:     percentile(sorted, 50),
	}
}

// percentile uses linear interpolation between adjacent ranks — matches NumPy
// default and is what both llmperf and genai-perf report.
func percentile(sorted []float64, p float64) float64 {
	if len(sorted) == 0 {
		return 0
	}
	if p <= 0 {
		return sorted[0]
	}
	if p >= 100 {
		return sorted[len(sorted)-1]
	}
	idx := (p / 100.0) * float64(len(sorted)-1)
	lo := int(idx)
	hi := lo + 1
	if hi >= len(sorted) {
		return sorted[lo]
	}
	w := idx - float64(lo)
	return sorted[lo]*(1-w) + sorted[hi]*w
}

func mean(vals []float64) float64 {
	if len(vals) == 0 {
		return 0
	}
	var s float64
	for _, v := range vals {
		s += v
	}
	return s / float64(len(vals))
}

func stddev(vals []float64, avg float64) float64 {
	if len(vals) <= 1 {
		return 0
	}
	var sq float64
	for _, v := range vals {
		d := v - avg
		sq += d * d
	}
	return math.Sqrt(sq / float64(len(vals)-1))
}
