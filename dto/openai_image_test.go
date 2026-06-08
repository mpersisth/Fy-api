package dto

import (
	"testing"
)

func TestImageRequest_GetTokenCountMeta_GPTImage2(t *testing.T) {
	tests := []struct {
		name    string
		model   string
		quality string
		want    float64
	}{
		{"gpt-image-2 low", "gpt-image-2", "low", 0.25},
		{"gpt-image-2 medium", "gpt-image-2", "medium", 1.0},
		{"gpt-image-2 high", "gpt-image-2", "high", 4.0},
		{"gpt-image-2 empty quality defaults to 1.0", "gpt-image-2", "", 1.0},
		{"gpt-image-1 low", "gpt-image-1", "low", 0.25},
		{"gpt-image-1 high", "gpt-image-1", "high", 4.0},
		// dall-e should still use old logic
		{"dall-e-3 standard 1024x1024", "dall-e-3", "standard", 1.0},
		{"dall-e-3 hd 1024x1024", "dall-e-3", "hd", 2.0},
		// non-matching model gets default
		{"unknown model", "some-model", "high", 1.0},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := &ImageRequest{
				Model:   tt.model,
				Quality: tt.quality,
				Size:    "1024x1024",
			}
			meta := req.GetTokenCountMeta()
			if meta.ImagePriceRatio != tt.want {
				t.Errorf("GetTokenCountMeta().ImagePriceRatio = %v, want %v", meta.ImagePriceRatio, tt.want)
			}
		})
	}
}
