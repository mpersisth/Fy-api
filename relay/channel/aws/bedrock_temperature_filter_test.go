package aws

import "testing"

func TestIsTemperatureDeprecatedForBedrock(t *testing.T) {
	tests := []struct {
		model    string
		expected bool
	}{
		{"claude-opus-4-7", true},
		{"us.anthropic.claude-opus-4-7-v1:0", true},
		{"claude-opus-4-6", false},
		{"claude-sonnet-4-6", false},
		{"claude-sonnet-4-5-20241022", false},
		{"claude-haiku-4-5-20241022", false},
		{"gpt-4", false},
	}
	for _, tt := range tests {
		t.Run(tt.model, func(t *testing.T) {
			if got := isTemperatureDeprecatedForBedrock(tt.model); got != tt.expected {
				t.Errorf("isTemperatureDeprecatedForBedrock(%q) = %v, want %v", tt.model, got, tt.expected)
			}
		})
	}
}

func TestSanitizeBedrockSamplingParams(t *testing.T) {
	t.Run("opus-4-7 strips temperature", func(t *testing.T) {
		temp := 0.7
		req := &AwsClaudeRequest{Temperature: &temp, TopP: 0.9}
		sanitizeBedrockSamplingParams("claude-opus-4-7", req)
		if req.Temperature != nil {
			t.Error("expected Temperature to be nil for claude-opus-4-7")
		}
		if req.TopP != 0.9 {
			t.Error("expected TopP to be preserved for claude-opus-4-7")
		}
	})

	t.Run("opus-4-6 strips top_p when both present", func(t *testing.T) {
		temp := 0.7
		req := &AwsClaudeRequest{Temperature: &temp, TopP: 0.9}
		sanitizeBedrockSamplingParams("claude-opus-4-6", req)
		if req.Temperature == nil || *req.Temperature != 0.7 {
			t.Error("expected Temperature to be preserved for claude-opus-4-6")
		}
		if req.TopP != 0 {
			t.Error("expected TopP to be zeroed when both present")
		}
	})

	t.Run("opus-4-6 preserves temperature alone", func(t *testing.T) {
		temp := 0.5
		req := &AwsClaudeRequest{Temperature: &temp}
		sanitizeBedrockSamplingParams("claude-opus-4-6", req)
		if req.Temperature == nil || *req.Temperature != 0.5 {
			t.Error("expected Temperature to be preserved")
		}
	})

	t.Run("opus-4-6 preserves top_p alone", func(t *testing.T) {
		req := &AwsClaudeRequest{TopP: 0.9}
		sanitizeBedrockSamplingParams("claude-opus-4-6", req)
		if req.TopP != 0.9 {
			t.Error("expected TopP to be preserved when temperature absent")
		}
	})

	t.Run("sonnet preserves both", func(t *testing.T) {
		temp := 0.7
		req := &AwsClaudeRequest{Temperature: &temp, TopP: 0.9}
		sanitizeBedrockSamplingParams("claude-sonnet-4-6", req)
		if req.Temperature == nil || *req.Temperature != 0.7 {
			t.Error("expected Temperature preserved for sonnet")
		}
		if req.TopP != 0 {
			t.Error("expected TopP stripped for sonnet when both present")
		}
	})

	t.Run("clamps temperature above 1.0", func(t *testing.T) {
		temp := 1.5
		req := &AwsClaudeRequest{Temperature: &temp}
		sanitizeBedrockSamplingParams("claude-opus-4-6", req)
		if req.Temperature == nil || *req.Temperature != 1.0 {
			t.Errorf("expected Temperature clamped to 1.0, got %v", req.Temperature)
		}
	})

	t.Run("preserves temperature at 1.0", func(t *testing.T) {
		temp := 1.0
		req := &AwsClaudeRequest{Temperature: &temp}
		sanitizeBedrockSamplingParams("claude-opus-4-6", req)
		if req.Temperature == nil || *req.Temperature != 1.0 {
			t.Errorf("expected Temperature 1.0 preserved, got %v", req.Temperature)
		}
	})

	t.Run("preserves temperature below 1.0", func(t *testing.T) {
		temp := 0.3
		req := &AwsClaudeRequest{Temperature: &temp}
		sanitizeBedrockSamplingParams("claude-opus-4-6", req)
		if req.Temperature == nil || *req.Temperature != 0.3 {
			t.Errorf("expected Temperature 0.3 preserved, got %v", req.Temperature)
		}
	})

	t.Run("clamps temperature 2.0 from OpenAI-compat client", func(t *testing.T) {
		temp := 2.0
		req := &AwsClaudeRequest{Temperature: &temp, TopP: 0.9}
		sanitizeBedrockSamplingParams("claude-sonnet-4-5", req)
		if req.Temperature == nil || *req.Temperature != 1.0 {
			t.Errorf("expected Temperature clamped to 1.0, got %v", req.Temperature)
		}
		if req.TopP != 0 {
			t.Error("expected TopP stripped")
		}
	})
}

func TestSanitizeBedrockSamplingParamsRaw(t *testing.T) {
	t.Run("opus-4-7 strips temperature", func(t *testing.T) {
		data := map[string]interface{}{"temperature": 0.7, "top_p": 0.9}
		sanitizeBedrockSamplingParamsRaw("claude-opus-4-7", data)
		if _, ok := data["temperature"]; ok {
			t.Error("expected temperature removed for opus-4-7")
		}
		if _, ok := data["top_p"]; !ok {
			t.Error("expected top_p preserved for opus-4-7")
		}
	})

	t.Run("opus-4-6 strips top_p when both present", func(t *testing.T) {
		data := map[string]interface{}{"temperature": 0.7, "top_p": 0.9}
		sanitizeBedrockSamplingParamsRaw("claude-opus-4-6", data)
		if _, ok := data["temperature"]; !ok {
			t.Error("expected temperature preserved for opus-4-6")
		}
		if _, ok := data["top_p"]; ok {
			t.Error("expected top_p removed when both present")
		}
	})

	t.Run("preserves temperature alone", func(t *testing.T) {
		data := map[string]interface{}{"temperature": 0.7}
		sanitizeBedrockSamplingParamsRaw("claude-opus-4-6", data)
		if _, ok := data["temperature"]; !ok {
			t.Error("expected temperature preserved when alone")
		}
	})

	t.Run("clamps temperature above 1.0", func(t *testing.T) {
		data := map[string]any{"temperature": 1.8}
		sanitizeBedrockSamplingParamsRaw("claude-sonnet-4-5", data)
		if v, ok := data["temperature"]; !ok || v != 1.0 {
			t.Errorf("expected temperature clamped to 1.0, got %v", data["temperature"])
		}
	})

	t.Run("preserves temperature at 0.5", func(t *testing.T) {
		data := map[string]any{"temperature": 0.5}
		sanitizeBedrockSamplingParamsRaw("claude-sonnet-4-5", data)
		if v, ok := data["temperature"]; !ok || v != 0.5 {
			t.Errorf("expected temperature 0.5 preserved, got %v", data["temperature"])
		}
	})
}
