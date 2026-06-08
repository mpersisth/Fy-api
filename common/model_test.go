package common

import "testing"

func TestIsImageGenerationModel(t *testing.T) {
	tests := []struct {
		model string
		want  bool
	}{
		// existing models
		{"dall-e-3", true},
		{"dall-e-2", true},
		{"gpt-image-1", true},
		// F1: gpt-image-2 must be recognized
		{"gpt-image-2", true},
		// prefix match for imagen
		{"imagen-3.0-generate-001", true},
		{"imagen-4.0-fast-generate-001", true},
		{"imagen-4.0-generate-001", true},
		{"imagen-4.0-ultra-generate-001", true},
		// flux
		{"flux-pro-1.1", true},
		{"flux.1-pro", true},
		// non-image models
		{"gpt-4o", false},
		{"gemini-2.5-flash", false},
		{"claude-sonnet-4-20250514", false},
		// Nano Banana models should NOT match (they go through chat path, not image model list)
		{"gemini-3.1-flash-image", false},
	}

	for _, tt := range tests {
		t.Run(tt.model, func(t *testing.T) {
			got := IsImageGenerationModel(tt.model)
			if got != tt.want {
				t.Errorf("IsImageGenerationModel(%q) = %v, want %v", tt.model, got, tt.want)
			}
		})
	}
}
