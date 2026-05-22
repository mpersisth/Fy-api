// Fy-api overlay: Prometheus metrics for prompt cache and channel affinity observability.
package prommetrics

import (
	"sync"

	"github.com/prometheus/client_golang/prometheus"
)

var (
	RelayPromptTokensTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "fy",
			Subsystem: "relay",
			Name:      "prompt_tokens_total",
			Help:      "Total prompt (input) tokens processed.",
		},
		[]string{"model", "channel_id"},
	)

	RelayCachedTokensTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "fy",
			Subsystem: "relay",
			Name:      "cached_tokens_total",
			Help:      "Total cached (cache-hit) tokens from upstream provider.",
		},
		[]string{"model", "channel_id"},
	)

	RelayCacheCreationTokensTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "fy",
			Subsystem: "relay",
			Name:      "cache_creation_tokens_total",
			Help:      "Total cache creation tokens charged by upstream provider.",
		},
		[]string{"model", "channel_id"},
	)

	AffinityLookupsTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "fy",
			Subsystem: "affinity",
			Name:      "lookups_total",
			Help:      "Channel affinity lookup attempts by outcome.",
		},
		[]string{"model", "rule_name", "outcome"},
	)

	affinityActiveEntriesDesc = prometheus.NewDesc(
		"fy_affinity_active_entries",
		"Current number of active channel affinity cache entries.",
		[]string{"rule_name"}, nil,
	)
)

var (
	affinityStatsProviderMu sync.RWMutex
	affinityStatsProvider   func() map[string]int
)

type affinityEntriesCollector struct{}

func (c affinityEntriesCollector) Describe(ch chan<- *prometheus.Desc) {
	ch <- affinityActiveEntriesDesc
}

func (c affinityEntriesCollector) Collect(ch chan<- prometheus.Metric) {
	affinityStatsProviderMu.RLock()
	provider := affinityStatsProvider
	affinityStatsProviderMu.RUnlock()
	if provider == nil {
		return
	}
	for ruleName, count := range provider() {
		ch <- prometheus.MustNewConstMetric(
			affinityActiveEntriesDesc, prometheus.GaugeValue,
			float64(count), ruleName,
		)
	}
}

func RegisterAffinityStatsProvider(fn func() map[string]int) {
	affinityStatsProviderMu.Lock()
	affinityStatsProvider = fn
	affinityStatsProviderMu.Unlock()
}

func init() {
	prometheus.MustRegister(
		RelayPromptTokensTotal,
		RelayCachedTokensTotal,
		RelayCacheCreationTokensTotal,
		AffinityLookupsTotal,
		affinityEntriesCollector{},
	)
}

func RecordCacheTokenMetrics(model, channelId string, promptTokens, cachedTokens, cacheCreationTokens int) {
	if promptTokens > 0 {
		RelayPromptTokensTotal.WithLabelValues(model, channelId).Add(float64(promptTokens))
	}
	if cachedTokens > 0 {
		RelayCachedTokensTotal.WithLabelValues(model, channelId).Add(float64(cachedTokens))
	}
	if cacheCreationTokens > 0 {
		RelayCacheCreationTokensTotal.WithLabelValues(model, channelId).Add(float64(cacheCreationTokens))
	}
}

func RecordAffinityLookup(model, ruleName, outcome string) {
	AffinityLookupsTotal.WithLabelValues(model, ruleName, outcome).Inc()
}
