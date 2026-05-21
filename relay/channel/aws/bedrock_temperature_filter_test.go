package aws

import "testing"

func TestIsTemperatureDeprecatedForBedrock(t *testing.T) {
	tests := []struct {
		model    string
		expected bool
	}{
		{"claude-opus-4-7", true},
		{"claude-opus-4-6", true},
		{"us.anthropic.claude-opus-4-7-v1:0", true},
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

func TestStripBedrockDeprecatedTemperature(t *testing.T) {
	temp := 0.7
	req := &AwsClaudeRequest{Temperature: &temp}
	stripBedrockDeprecatedTemperature("claude-opus-4-7", req)
	if req.Temperature != nil {
		t.Error("expected Temperature to be nil for claude-opus-4-7")
	}

	temp2 := 0.5
	req2 := &AwsClaudeRequest{Temperature: &temp2}
	stripBedrockDeprecatedTemperature("claude-sonnet-4-6", req2)
	if req2.Temperature == nil || *req2.Temperature != 0.5 {
		t.Error("expected Temperature to be preserved for claude-sonnet-4-6")
	}
}

func TestStripBedrockDeprecatedTemperatureRaw(t *testing.T) {
	data := map[string]interface{}{"temperature": 0.7, "max_tokens": 1024}
	stripBedrockDeprecatedTemperatureRaw("claude-opus-4-7", data)
	if _, ok := data["temperature"]; ok {
		t.Error("expected temperature to be removed for claude-opus-4-7")
	}
	if _, ok := data["max_tokens"]; !ok {
		t.Error("expected max_tokens to be preserved")
	}

	data2 := map[string]interface{}{"temperature": 0.7}
	stripBedrockDeprecatedTemperatureRaw("claude-sonnet-4-6", data2)
	if _, ok := data2["temperature"]; !ok {
		t.Error("expected temperature to be preserved for claude-sonnet-4-6")
	}
}
