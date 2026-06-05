package openai

import (
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"

	"github.com/gin-gonic/gin"
)

func TestConvertImageRequestDropsAzureGPTImageResponseFormat(t *testing.T) {
	adaptor := &Adaptor{ChannelType: constant.ChannelTypeAzure}
	info := &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeImagesGenerations,
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelType:       constant.ChannelTypeAzure,
			UpstreamModelName: "gpt-image-2",
		},
	}
	request := dto.ImageRequest{
		Model:          "gpt-image-2",
		Prompt:         "draw a cat",
		ResponseFormat: "url",
	}

	got, err := adaptor.ConvertImageRequest(gin.CreateTestContextOnly(nil, gin.New()), info, request)
	if err != nil {
		t.Fatalf("ConvertImageRequest returned error: %v", err)
	}

	imageReq, ok := got.(dto.ImageRequest)
	if !ok {
		t.Fatalf("ConvertImageRequest returned %T, want dto.ImageRequest", got)
	}
	if imageReq.ResponseFormat != "" {
		t.Fatalf("ResponseFormat = %q, want empty", imageReq.ResponseFormat)
	}
}

func TestConvertImageRequestKeepsDallEResponseFormat(t *testing.T) {
	adaptor := &Adaptor{ChannelType: constant.ChannelTypeAzure}
	info := &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeImagesGenerations,
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelType:       constant.ChannelTypeAzure,
			UpstreamModelName: "dall-e-3",
		},
	}
	request := dto.ImageRequest{
		Model:          "dall-e-3",
		Prompt:         "draw a cat",
		ResponseFormat: "url",
	}

	got, err := adaptor.ConvertImageRequest(gin.CreateTestContextOnly(nil, gin.New()), info, request)
	if err != nil {
		t.Fatalf("ConvertImageRequest returned error: %v", err)
	}

	imageReq, ok := got.(dto.ImageRequest)
	if !ok {
		t.Fatalf("ConvertImageRequest returned %T, want dto.ImageRequest", got)
	}
	if imageReq.ResponseFormat != "url" {
		t.Fatalf("ResponseFormat = %q, want url", imageReq.ResponseFormat)
	}
}
