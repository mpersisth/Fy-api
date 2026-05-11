package service

import (
	"testing"

	"github.com/QuantumNous/new-api/types"
	"github.com/stretchr/testify/require"
)

func TestGetBase64DataInfersMimeTypeForRawBase64Image(t *testing.T) {
	source := types.NewFileSourceFromData("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+P+/HgAFeAKB+IvqGwAAAABJRU5ErkJggg==", "")

	base64Data, mimeType, err := GetBase64Data(nil, source)

	require.NoError(t, err)
	require.NotEmpty(t, base64Data)
	require.Equal(t, "image/png", mimeType)
}
