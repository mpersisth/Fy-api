package dto

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestMessageParseContentPreservesImageURLMimeType(t *testing.T) {
	message := Message{
		Content: []any{
			map[string]any{
				"type": ContentTypeImageURL,
				"image_url": map[string]any{
					"url":       "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+P+/HgAFeAKB+IvqGwAAAABJRU5ErkJggg==",
					"mime_type": "image/png",
				},
			},
		},
	}

	content := message.ParseContent()

	require.Len(t, content, 1)
	image := content[0].GetImageMedia()
	require.NotNil(t, image)
	require.Equal(t, "image/png", image.MimeType)
}

func TestMessageParseContentPreservesImageURLCamelCaseMimeType(t *testing.T) {
	message := Message{
		Content: []any{
			map[string]any{
				"type": ContentTypeImageURL,
				"image_url": map[string]any{
					"url":      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+P+/HgAFeAKB+IvqGwAAAABJRU5ErkJggg==",
					"mimeType": "image/png",
				},
			},
		},
	}

	content := message.ParseContent()

	require.Len(t, content, 1)
	image := content[0].GetImageMedia()
	require.NotNil(t, image)
	require.Equal(t, "image/png", image.MimeType)
}
