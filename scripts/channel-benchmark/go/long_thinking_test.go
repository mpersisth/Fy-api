package main

import (
	"strings"
	"testing"
)

// TestLongThinkingPresetOverlaysOnConfig verifies that -long-thinking flips
// every workload knob to the long-reasoning regression shape, while leaving
// gateway / channel / export config untouched.
//
// Why each value matters is documented in long_thinking.go and in
// incidents/2026-05-11-long-reasoning-timeout.md.
func TestLongThinkingPresetOverlaysOnConfig(t *testing.T) {
	cfg := &BenchmarkConfig{
		Gateway: GatewayConfig{
			BaseURL:     "http://untouched",
			AdminToken:  "atoken",
			AdminUserID: "1",
			UserToken:   "sk-untouched",
		},
		Test: TestConfig{
			// Pre-existing values that the preset MUST overwrite.
			Concurrency: 50,
			TimeoutSec:  60,
			RepsPerCase: 10,
			MaxTokens:   64,
			Prompt:      "ping",
			Stream:      false,
			NonStream:   true,
			PinChannel:  true, // must NOT be touched
		},
		Channels: []ChannelConfig{{ID: 7, Name: "x", TestModels: []string{"m"}}},
		Export: ExportConfig{
			Formats:   []string{"csv"},
			OutputDir: "untouched",
		},
	}

	applyLongThinkingPreset(cfg)

	// Workload shape — must be exactly the long-reasoning preset.
	if cfg.Test.TimeoutSec < 1800 {
		t.Errorf("TimeoutSec=%d, want >= 1800 (must be >= gateway RELAY_TIMEOUT to detect a gateway-side regression)", cfg.Test.TimeoutSec)
	}
	if cfg.Test.RepsPerCase != 1 {
		t.Errorf("RepsPerCase=%d, want 1 (each rep costs minutes; >1 is unaffordable)", cfg.Test.RepsPerCase)
	}
	if cfg.Test.Concurrency != 1 {
		t.Errorf("Concurrency=%d, want 1 (long-thinking probes the upper bound of ONE stream)", cfg.Test.Concurrency)
	}
	if cfg.Test.MaxTokens < 8000 {
		t.Errorf("MaxTokens=%d, want >= 8000 (thinking models emit thousands of tokens; low ceiling masks the bug)", cfg.Test.MaxTokens)
	}
	if !cfg.Test.Stream {
		t.Error("Stream must be true; STREAMING_TIMEOUT only applies to streaming upstreams")
	}
	if cfg.Test.NonStream {
		t.Error("NonStream must be false to keep the preset focused on the streaming timeout chain")
	}
	if !strings.Contains(cfg.Test.Prompt, "QED") {
		t.Errorf("prompt does not look like the proof-style fixture: %q", cfg.Test.Prompt)
	}
	if len(cfg.Test.Prompt) < 200 {
		t.Errorf("prompt is %d chars; the fixture is ~500 chars and a short prompt won't trigger long thinking", len(cfg.Test.Prompt))
	}

	// Things the preset MUST NOT touch — operator's own choices.
	if cfg.Gateway.BaseURL != "http://untouched" {
		t.Errorf("Gateway.BaseURL was modified: %q", cfg.Gateway.BaseURL)
	}
	if cfg.Gateway.UserToken != "sk-untouched" {
		t.Errorf("Gateway.UserToken was modified: %q", cfg.Gateway.UserToken)
	}
	if !cfg.Test.PinChannel {
		t.Error("PinChannel was reset; preset must not override the operator's pinning choice")
	}
	if len(cfg.Channels) != 1 || cfg.Channels[0].ID != 7 {
		t.Errorf("Channels were modified: %+v", cfg.Channels)
	}
	if cfg.Export.OutputDir != "untouched" {
		t.Errorf("Export.OutputDir was modified: %q", cfg.Export.OutputDir)
	}
}

// TestLongThinkingPresetIsIdempotent: applying twice produces the same config.
// Guards against accidental "if not already set" logic creeping in.
func TestLongThinkingPresetIsIdempotent(t *testing.T) {
	cfg := &BenchmarkConfig{
		Gateway:  GatewayConfig{BaseURL: "x", AdminToken: "a", AdminUserID: "1", UserToken: "sk"},
		Channels: []ChannelConfig{{ID: 1, Name: "x", TestModels: []string{"m"}}},
	}
	applyLongThinkingPreset(cfg)
	first := cfg.Test
	applyLongThinkingPreset(cfg)
	second := cfg.Test
	if first != second {
		t.Errorf("not idempotent:\n  first=%+v\n  second=%+v", first, second)
	}
}
