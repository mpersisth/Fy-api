// Fy-api overlay: Prometheus metrics middleware for relay observability.
// This file is a TraceNex customization and does not exist upstream.
package middleware

import (
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/constant"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/gin-gonic/gin"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// --- Metric definitions ---

var (
	relayRequestsTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "fy",
			Subsystem: "relay",
			Name:      "requests_total",
			Help:      "Total relay requests by model, channel, status, endpoint type, and stream mode.",
		},
		[]string{"model", "channel_id", "status_code", "endpoint_type", "is_stream"},
	)

	relayErrorsTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "fy",
			Subsystem: "relay",
			Name:      "errors_total",
			Help:      "Total relay errors by model, channel, and error type.",
		},
		[]string{"model", "channel_id", "error_type"},
	)

	relayDurationSeconds = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Namespace: "fy",
			Subsystem: "relay",
			Name:      "duration_seconds",
			Help:      "End-to-end relay request duration in seconds.",
			Buckets:   []float64{0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600},
		},
		[]string{"model", "channel_id", "endpoint_type"},
	)

	relayTTFTSeconds = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Namespace: "fy",
			Subsystem: "relay",
			Name:      "ttft_seconds",
			Help:      "Time to first token in seconds (streaming requests only).",
			Buckets:   []float64{0.2, 0.5, 1, 2, 3, 5, 8, 10, 15, 20, 30},
		},
		[]string{"model", "channel_id"},
	)

	imageDurationSeconds = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Namespace: "fy",
			Subsystem: "image",
			Name:      "duration_seconds",
			Help:      "Image generation duration in seconds.",
			Buckets:   []float64{5, 10, 20, 30, 60, 90, 120, 180, 300, 600},
		},
		[]string{"model", "channel_id"},
	)

	relayRetriesTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Namespace: "fy",
			Subsystem: "relay",
			Name:      "retries_total",
			Help:      "Total relay retries (each retry = one upstream channel failure before fallback).",
		},
		[]string{"model", "endpoint_type"},
	)
)

func init() {
	prometheus.MustRegister(
		relayRequestsTotal,
		relayErrorsTotal,
		relayDurationSeconds,
		relayTTFTSeconds,
		imageDurationSeconds,
		relayRetriesTotal,
	)
}

// PrometheusMetricsHandler returns the promhttp handler for /metrics.
func PrometheusMetricsHandler() gin.HandlerFunc {
	h := promhttp.Handler()
	return func(c *gin.Context) {
		h.ServeHTTP(c.Writer, c.Request)
	}
}

// PrometheusRelay records Prometheus metrics after each relay request completes.
// TTFT is detected by wrapping the ResponseWriter to capture first-write time.
// Only records metrics for relay routes (paths starting with /v1/, /v1beta/, etc.).
func PrometheusRelay() gin.HandlerFunc {
	return func(c *gin.Context) {
		path := c.Request.URL.Path
		if !isRelayPath(path) {
			c.Next()
			return
		}

		start := time.Now()
		tw := &ttftWriter{ResponseWriter: c.Writer, start: start}
		c.Writer = tw

		c.Next()

		statusCode := tw.Status()
		channelId := getContextInt(c, constant.ContextKeyChannelId)
		model := normalizeModelLabel(getContextString(c, constant.ContextKeyOriginalModel))
		isStream := getContextBool(c, constant.ContextKeyIsStream)
		relayMode := c.GetInt("relay_mode")
		endpointType := relayModeToEndpointType(relayMode)
		channelStr := strconv.Itoa(channelId)
		statusStr := strconv.Itoa(statusCode)
		streamStr := strconv.FormatBool(isStream)

		duration := time.Since(start).Seconds()

		relayRequestsTotal.WithLabelValues(
			model, channelStr, statusStr, endpointType, streamStr,
		).Inc()

		if statusCode >= 400 {
			errorType := classifyErrorType(statusCode)
			relayErrorsTotal.WithLabelValues(model, channelStr, errorType).Inc()
		}

		if channelId == 0 {
			return
		}

		relayDurationSeconds.WithLabelValues(model, channelStr, endpointType).Observe(duration)

		if isStream && tw.ttft > 0 {
			relayTTFTSeconds.WithLabelValues(model, channelStr).Observe(tw.ttft.Seconds())
		}

		if endpointType == "image" {
			imageDurationSeconds.WithLabelValues(model, channelStr).Observe(duration)
		}

		useChannels := c.GetStringSlice("use_channel")
		if retries := len(useChannels) - 1; retries > 0 {
			relayRetriesTotal.WithLabelValues(model, endpointType).Add(float64(retries))
		}
	}
}

// ttftWriter wraps gin.ResponseWriter to detect time-to-first-byte.
type ttftWriter struct {
	gin.ResponseWriter
	start       time.Time
	ttft        time.Duration
	wroteFirst  bool
}

func (w *ttftWriter) Write(data []byte) (int, error) {
	if !w.wroteFirst {
		w.wroteFirst = true
		w.ttft = time.Since(w.start)
	}
	return w.ResponseWriter.Write(data)
}

// --- Helper functions ---

func getContextString(c *gin.Context, key constant.ContextKey) string {
	v, _ := c.Get(string(key))
	s, _ := v.(string)
	return s
}

func getContextInt(c *gin.Context, key constant.ContextKey) int {
	v, _ := c.Get(string(key))
	switch n := v.(type) {
	case int:
		return n
	case int64:
		return int(n)
	default:
		return 0
	}
}

func getContextBool(c *gin.Context, key constant.ContextKey) bool {
	v, _ := c.Get(string(key))
	b, _ := v.(bool)
	return b
}

func classifyErrorType(statusCode int) string {
	switch {
	case statusCode == 429:
		return "rate_limited"
	case statusCode == 401 || statusCode == 403:
		return "auth_error"
	case statusCode >= 400 && statusCode < 500:
		return "client_error"
	case statusCode == 502 || statusCode == 503 || statusCode == 504:
		return "upstream_unavailable"
	default:
		return "server_error"
	}
}

func relayModeToEndpointType(mode int) string {
	switch mode {
	case relayconstant.RelayModeChatCompletions,
		relayconstant.RelayModeCompletions:
		return "chat"
	case relayconstant.RelayModeImagesGenerations,
		relayconstant.RelayModeImagesEdits:
		return "image"
	case relayconstant.RelayModeAudioSpeech:
		return "audio"
	case relayconstant.RelayModeEmbeddings:
		return "embedding"
	default:
		if mode >= relayconstant.RelayModeMidjourneyImagine &&
			mode <= relayconstant.RelayModeMidjourneyEdits {
			return "image"
		}
		return "other"
	}
}

// normalizeModelLabel collapses dated model variants into their base name
// to prevent label cardinality explosion.
func normalizeModelLabel(model string) string {
	if model == "" {
		return "unknown"
	}
	return model
}

var relayPrefixes = []string{"/v1/", "/v1beta/"}

func isRelayPath(path string) bool {
	for _, prefix := range relayPrefixes {
		if strings.HasPrefix(path, prefix) {
			return true
		}
	}
	return false
}
