package tencent

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/setting/system_setting"

	"github.com/gin-gonic/gin"
)

func TestIsTencentAIArtImageGeneration(t *testing.T) {
	t.Parallel()

	info := &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeImagesGenerations,
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelBaseUrl: "https://aiart.tencentcloudapi.com",
		},
	}

	if !isTencentAIArtImageGeneration(info) {
		t.Fatalf("expected AIArt image generation branch")
	}

	info.RelayMode = relayconstant.RelayModeChatCompletions
	if isTencentAIArtImageGeneration(info) {
		t.Fatalf("chat completions must not use AIArt image branch")
	}
}

func TestTencentAIArtImageRequestConversion(t *testing.T) {
	t.Parallel()

	request := dto.ImageRequest{
		Model:          "gpt-image-2",
		Prompt:         "a glass tea house in the mountains",
		Size:           "1024x1024",
		Quality:        "high",
		ResponseFormat: "url",
		N:              uintPtr(1),
		ExtraFields:    json.RawMessage(`{"NegativePrompt":"low quality","RspImgType":"base64","LogoAdd":1}`),
	}

	got, err := tencentAIArtImageRequestFromOpenAI(request)
	if err != nil {
		t.Fatalf("tencentAIArtImageRequestFromOpenAI returned error: %v", err)
	}

	if got.Model != "gpt-image-2" {
		t.Fatalf("Model = %q, want gpt-image-2", got.Model)
	}
	if got.Prompt != request.Prompt {
		t.Fatalf("Prompt = %q, want %q", got.Prompt, request.Prompt)
	}
	if got.Resolution != "" {
		t.Fatalf("Resolution = %q, want empty (GPT image API does not accept Resolution)", got.Resolution)
	}
	if got.NegativePrompt != "low quality" {
		t.Fatalf("NegativePrompt = %q, want low quality", got.NegativePrompt)
	}
	if got.RspImgType != "base64" {
		t.Fatalf("RspImgType = %q, want extra_fields override base64", got.RspImgType)
	}
	body, err := common.Marshal(got)
	if err != nil {
		t.Fatalf("common.Marshal returned error: %v", err)
	}
	if !strings.Contains(string(body), `"LogoAdd":1`) {
		t.Fatalf("marshaled body = %s, want extra Tencent field LogoAdd", string(body))
	}
}

func TestAdaptorConvertImageRequestUsesAIArtForAIArtHost(t *testing.T) {
	t.Parallel()

	adaptor := &Adaptor{}
	info := &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeImagesGenerations,
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelBaseUrl: "https://aiart.tencentcloudapi.com",
		},
	}
	request := dto.ImageRequest{
		Model:  "gpt-image-2",
		Prompt: "a small observatory above the sea",
		Size:   "1536x1024",
	}

	got, err := adaptor.ConvertImageRequest(gin.CreateTestContextOnly(httptest.NewRecorder(), gin.New()), info, request)
	if err != nil {
		t.Fatalf("ConvertImageRequest returned error: %v", err)
	}
	aiartReq, ok := got.(*tencentAIArtImageRequest)
	if !ok {
		t.Fatalf("ConvertImageRequest returned %T, want *tencentAIArtImageRequest", got)
	}
	if aiartReq.Resolution != "" {
		t.Fatalf("Resolution = %q, want empty (GPT image API does not accept Resolution)", aiartReq.Resolution)
	}
}

func TestTencentAIArtSignUsesAIArtService(t *testing.T) {
	t.Parallel()

	payload := []byte(`{"Prompt":"hello"}`)
	got := buildTencentTC3Authorization(tencentTC3SignInput{
		SecretID:  "sid",
		SecretKey: "skey",
		Service:   "aiart",
		Host:      tencentAIArtHost,
		Action:    tencentAIArtSubmitAction,
		Timestamp: time.Unix(1700000000, 0).Unix(),
		Payload:   payload,
	})

	if !strings.Contains(got, "Credential=sid/2023-11-14/aiart/tc3_request") {
		t.Fatalf("authorization = %q, want aiart credential scope", got)
	}
	if !strings.Contains(got, "TC3-HMAC-SHA256") {
		t.Fatalf("authorization = %q, want TC3 algorithm", got)
	}
}

func TestTencentAIArtImageResponseConversion(t *testing.T) {
	t.Parallel()

	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)

	info := &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeImagesGenerations,
		StartTime: time.Unix(1700000000, 0),
		Request: &dto.ImageRequest{
			ResponseFormat: "url",
		},
	}
	resp := &http.Response{
		StatusCode: http.StatusOK,
		Header:     make(http.Header),
		Body:       io.NopCloser(strings.NewReader(`{"Response":{"JobStatusCode":"5","ResultImage":["https://example.com/tencent.png"],"RequestId":"req-1"}}`)),
	}

	usage, err := writeTencentAIArtImageResponse(c, resp, info)
	if err != nil {
		t.Fatalf("writeTencentAIArtImageResponse returned error: %v", err)
	}
	if usage == nil {
		t.Fatalf("usage is nil")
	}

	body := recorder.Body.String()
	if !strings.Contains(body, `"url":"https://example.com/tencent.png"`) {
		t.Fatalf("response body = %s, want OpenAI image url", body)
	}
	if strings.Contains(body, "JobStatusCode") {
		t.Fatalf("response body = %s, should not expose raw Tencent payload", body)
	}
}

func TestTencentAIArtImageResponseConvertsURLToBase64WhenRequested(t *testing.T) {
	t.Parallel()

	imageServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "image/png")
		_, _ = w.Write([]byte{
			0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
			0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
			0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
			0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
			0xde, 0x00, 0x00, 0x00, 0x0c, 0x49, 0x44, 0x41,
			0x54, 0x78, 0x9c, 0x63, 0xf8, 0xcf, 0xc0, 0x00,
			0x00, 0x03, 0x01, 0x01, 0x00, 0x18, 0xdd, 0x8d,
			0xb0, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e,
			0x44, 0xae, 0x42, 0x60, 0x82,
		})
	}))
	defer imageServer.Close()

	service.InitHttpClient()
	oldMaxFileDownloadMB := constant.MaxFileDownloadMB
	constant.MaxFileDownloadMB = 1
	fetchSetting := system_setting.GetFetchSetting()
	oldAllowPrivateIP := fetchSetting.AllowPrivateIp
	oldAllowedPorts := append([]string(nil), fetchSetting.AllowedPorts...)
	fetchSetting.AllowPrivateIp = true
	fetchSetting.AllowedPorts = []string{"1-65535"}
	t.Cleanup(func() {
		constant.MaxFileDownloadMB = oldMaxFileDownloadMB
		fetchSetting.AllowPrivateIp = oldAllowPrivateIP
		fetchSetting.AllowedPorts = oldAllowedPorts
	})

	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)

	info := &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeImagesGenerations,
		Request: &dto.ImageRequest{
			ResponseFormat: "b64_json",
		},
	}
	resp := &http.Response{
		StatusCode: http.StatusOK,
		Header:     make(http.Header),
		Body:       io.NopCloser(strings.NewReader(`{"Response":{"JobStatusCode":"5","ResultImage":["` + imageServer.URL + `"],"RequestId":"req-1"}}`)),
	}

	usage, err := writeTencentAIArtImageResponse(c, resp, info)
	if err != nil {
		t.Fatalf("writeTencentAIArtImageResponse returned error: %v", err)
	}
	if usage == nil {
		t.Fatalf("usage is nil")
	}
	body := recorder.Body.String()
	if !strings.Contains(body, `"b64_json":"iVBORw0KGgo`) {
		t.Fatalf("response body = %s, want downloaded base64 PNG", body)
	}
	if strings.Contains(body, imageServer.URL) {
		t.Fatalf("response body = %s, should not include original image URL when b64_json requested", body)
	}
}

func TestTencentAIArtDoRequestSubmitsPollsAndConverts(t *testing.T) {
	t.Parallel()

	var calls int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		call := atomic.AddInt32(&calls, 1)
		action := r.Header.Get("X-TC-Action")
		switch call {
		case 1:
			if action != tencentAIArtSubmitAction {
				t.Fatalf("first action = %q, want %q", action, tencentAIArtSubmitAction)
			}
			_, _ = w.Write([]byte(`{"Response":{"JobId":"job-1","RequestId":"submit-req"}}`))
		case 2:
			if action != tencentAIArtDescribeAction {
				t.Fatalf("second action = %q, want %q", action, tencentAIArtDescribeAction)
			}
			_, _ = w.Write([]byte(`{"Response":{"JobStatusCode":"5","ResultImage":["https://example.com/final.png"],"RequestId":"describe-req"}}`))
		default:
			t.Fatalf("unexpected call %d", call)
		}
	}))
	defer server.Close()

	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/images/generations", nil)
	common.SetContextKey(c, constant.ContextKeyChannelKey, "123456|sid|skey")
	service.InitHttpClient()

	info := &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeImagesGenerations,
		StartTime: time.Unix(1700000000, 0),
		Request: &dto.ImageRequest{
			ResponseFormat: "url",
		},
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelBaseUrl: server.URL,
		},
	}

	adaptor := &Adaptor{}
	resp, err := adaptor.doTencentAIArtImageRequest(c, info, strings.NewReader(`{"Prompt":"hello"}`))
	if err != nil {
		t.Fatalf("doTencentAIArtImageRequest returned error: %v", err)
	}
	usage, apiErr := writeTencentAIArtImageResponse(c, resp, info)
	if apiErr != nil {
		t.Fatalf("writeTencentAIArtImageResponse returned error: %v", apiErr)
	}
	if usage == nil {
		t.Fatalf("usage is nil")
	}
	if got := atomic.LoadInt32(&calls); got != 2 {
		t.Fatalf("calls = %d, want 2", got)
	}
	if !strings.Contains(recorder.Body.String(), `"url":"https://example.com/final.png"`) {
		t.Fatalf("response body = %s", recorder.Body.String())
	}
}

func TestTencentAIArtPostUsesProvidedContext(t *testing.T) {
	t.Parallel()

	var calls int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
	}))
	defer server.Close()

	service.InitHttpClient()
	gin.SetMode(gin.TestMode)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/images/generations", nil)

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	info := &relaycommon.RelayInfo{
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelBaseUrl: server.URL,
		},
	}

	_, err := (&Adaptor{}).tencentAIArtPost(ctx, c, info, tencentAIArtSubmitAction, []byte(`{}`), "sid", "skey")
	if err == nil {
		t.Fatalf("expected canceled context error")
	}
	if got := atomic.LoadInt32(&calls); got != 0 {
		t.Fatalf("server calls = %d, want 0", got)
	}
}

func uintPtr(v uint) *uint {
	return &v
}
