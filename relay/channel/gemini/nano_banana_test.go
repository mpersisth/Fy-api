package gemini

import "testing"

func TestIsNanoBananaModel(t *testing.T) {
	tests := []struct {
		model string
		want  bool
	}{
		{"gemini-3.1-flash-image", true},
		{"gemini-3-pro-image", true},
		{"gemini-2.5-flash-image", true},
		{"gemini-3.1-flash-image-preview", true},
		{"gemini-3-pro-image-preview", true},
		// non-matching
		{"imagen-4.0-generate-001", false},
		{"imagen-4.0-fast-generate-001", false},
		{"gemini-2.5-flash", false},
		{"gemini-3.5-flash", false},
		{"gpt-image-2", false},
	}

	for _, tt := range tests {
		t.Run(tt.model, func(t *testing.T) {
			got := isNanoBananaModel(tt.model)
			if got != tt.want {
				t.Errorf("isNanoBananaModel(%q) = %v, want %v", tt.model, got, tt.want)
			}
		})
	}
}
