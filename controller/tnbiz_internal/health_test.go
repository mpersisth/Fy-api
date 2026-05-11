// Copyright 2026 TraceNex Partner OVERLAY
package tnbiz_internal

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/setting/overlay_flag"

	"github.com/gin-gonic/gin"
)

func TestHealthEndpointReturnsFlagsState(t *testing.T) {
	gin.SetMode(gin.TestMode)
	overlay_flag.SetForTest(overlay_flag.FlagInternalAPI, "true")
	overlay_flag.SetForTest(overlay_flag.FlagHMACKeystore, "true")
	overlay_flag.SetForTest(overlay_flag.FlagOutbox, overlay_flag.OutboxShadow)
	defer func() {
		overlay_flag.SetForTest(overlay_flag.FlagInternalAPI, "false")
		overlay_flag.SetForTest(overlay_flag.FlagHMACKeystore, "false")
		overlay_flag.SetForTest(overlay_flag.FlagOutbox, overlay_flag.OutboxOff)
	}()

	r := gin.New()
	r.GET("/health", Health)

	req, _ := http.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d want 200", w.Code)
	}
	var got map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if got["status"] != "ok" {
		t.Fatalf("status field = %v want ok", got["status"])
	}
	if got["overlay_internal_api"] != true {
		t.Fatalf("overlay_internal_api flag not surfaced")
	}
	if got["overlay_outbox"] != overlay_flag.OutboxShadow {
		t.Fatalf("overlay_outbox = %v want %q", got["overlay_outbox"], overlay_flag.OutboxShadow)
	}
}

func TestRespondErrorEnvelope(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/x", func(c *gin.Context) {
		respondError(c, http.StatusBadRequest, "oops", "bad")
	})
	req, _ := http.NewRequest(http.MethodGet, "/x", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("status %d", w.Code)
	}
	var got map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &got)
	if got["success"] != false {
		t.Fatalf("envelope success not false")
	}
	errObj, ok := got["error"].(map[string]any)
	if !ok || errObj["code"] != "oops" || errObj["message"] != "bad" {
		t.Fatalf("error envelope malformed: %v", got)
	}
}
