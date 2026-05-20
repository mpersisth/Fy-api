// Fy-api overlay: Prometheus /metrics endpoint and relay metrics middleware.
// This file is a TraceNex customization and does not exist upstream.
package router

import (
	"os"

	"github.com/QuantumNous/new-api/middleware"
	"github.com/gin-gonic/gin"
)

// SetPrometheusRouter registers the Prometheus relay metrics middleware and
// the /metrics scrape endpoint. Enabled by PROMETHEUS_METRICS=1.
// The /metrics endpoint is unauthenticated (Prometheus needs direct access).
// Protect it via network policy or reverse proxy ACL in production.
func SetPrometheusRouter(router *gin.Engine) {
	if os.Getenv("PROMETHEUS_METRICS") != "1" {
		return
	}
	router.Use(middleware.PrometheusRelay())
	router.GET("/metrics", middleware.PrometheusMetricsHandler())
}
