// Copyright 2026 TraceNex Partner OVERLAY
package overlay_flag

import (
	"context"
	"testing"
)

func TestFlagDefaults(t *testing.T) {
	// 用 fake loader 模拟 option_map 完全空。
	SetLoader(func(key string) (string, bool) { return "", false })
	defer SetLoader(nil)

	Reload(context.Background())

	if IsInternalAPIEnabled() {
		t.Errorf("default InternalAPI must be off (prod-safe)")
	}
	if IsHMACKeystoreEnabled() {
		t.Errorf("default HMACKeystore must be off")
	}
	if IsGroupRatioOverrideEnabled() {
		t.Errorf("default GroupRatioOverride must be off (billing hot path)")
	}
	if IsOutboxTxEnabled() {
		t.Errorf("default OutboxTx must be off")
	}
	if got := OutboxMode(); got != OutboxOff {
		t.Errorf("default OutboxMode want %q got %q", OutboxOff, got)
	}
}

func TestFlagOverrides(t *testing.T) {
	cases := map[string]string{
		FlagInternalAPI:        "true",
		FlagHMACKeystore:       "true",
		FlagOutbox:             OutboxShadow,
		FlagGroupRatioOverride: "true",
		FlagOutboxTx:           "true",
	}
	SetLoader(func(key string) (string, bool) {
		v, ok := cases[key]
		return v, ok
	})
	defer SetLoader(nil)

	Reload(context.Background())

	if !IsInternalAPIEnabled() {
		t.Errorf("InternalAPI not toggled on")
	}
	if !IsHMACKeystoreEnabled() {
		t.Errorf("HMACKeystore not toggled on")
	}
	if !IsGroupRatioOverrideEnabled() {
		t.Errorf("GroupRatioOverride not toggled on")
	}
	if !IsOutboxTxEnabled() {
		t.Errorf("OutboxTx not toggled on")
	}
	if got := OutboxMode(); got != OutboxShadow {
		t.Errorf("OutboxMode want %q got %q", OutboxShadow, got)
	}
}

func TestParseBoolFallback(t *testing.T) {
	tests := []struct {
		in   string
		want bool
	}{
		{"true", true},
		{"false", false},
		{"1", true},
		{"0", false},
		{"", false},
		{"garbage", false}, // 防 panic
	}
	for _, tc := range tests {
		if got := parseBool(tc.in); got != tc.want {
			t.Errorf("parseBool(%q) = %v want %v", tc.in, got, tc.want)
		}
	}
}
