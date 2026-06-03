package tencent

import (
	"bytes"
	"context"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/relay/channel"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/types"

	"github.com/gin-gonic/gin"
)

const (
	tencentAIArtHost           = "aiart.tencentcloudapi.com"
	tencentAIArtService        = "aiart"
	tencentAIArtVersion        = "2022-12-29"
	tencentAIArtSubmitAction   = "SubmitContentToImageGPTJob"
	tencentAIArtDescribeAction = "DescribeContentToImageGPTJob"
	tencentAIArtPollInterval   = 5 * time.Second
	tencentAIArtPollTimeout    = 10 * time.Minute
)

type tencentAIArtImageRequest struct {
	Model          string         `json:"Model,omitempty"`
	Prompt         string         `json:"Prompt,omitempty"`
	NegativePrompt string         `json:"NegativePrompt,omitempty"`
	Resolution     string         `json:"Resolution,omitempty"`
	RspImgType     string         `json:"RspImgType,omitempty"`
	Quality        string         `json:"Quality,omitempty"`
	N              int            `json:"N,omitempty"`
	Extra          map[string]any `json:"-"`
}

type tencentAIArtSubmitResponse struct {
	Response struct {
		JobID     string             `json:"JobId"`
		JobId     string             `json:"JobID"`
		RequestID string             `json:"RequestId"`
		Error     *tencentAIArtError `json:"Error,omitempty"`
	} `json:"Response"`
}

type tencentAIArtDescribeRequest struct {
	JobID string `json:"JobId"`
}

type tencentAIArtDescribeResponse struct {
	Response tencentAIArtJobResponse `json:"Response"`
}

type tencentAIArtJobResponse struct {
	JobStatusCode string             `json:"JobStatusCode,omitempty"`
	Status        string             `json:"Status,omitempty"`
	ResultImage   tencentAIArtImages `json:"ResultImage,omitempty"`
	ResultImages  tencentAIArtImages `json:"ResultImages,omitempty"`
	ImageUrls     tencentAIArtImages `json:"ImageUrls,omitempty"`
	Images        tencentAIArtImages `json:"Images,omitempty"`
	RequestID     string             `json:"RequestId,omitempty"`
	Error         *tencentAIArtError `json:"Error,omitempty"`
}

type tencentAIArtImages []string

func (images *tencentAIArtImages) UnmarshalJSON(data []byte) error {
	if len(data) == 0 || string(data) == "null" {
		return nil
	}
	var list []string
	if err := common.Unmarshal(data, &list); err == nil {
		*images = append((*images)[:0], list...)
		return nil
	}
	var single string
	if err := common.Unmarshal(data, &single); err == nil {
		if single != "" {
			*images = append((*images)[:0], single)
		}
		return nil
	}
	var object map[string]any
	if err := common.Unmarshal(data, &object); err != nil {
		return err
	}
	collected := collectTencentAIArtImageValues(object)
	*images = append((*images)[:0], collected...)
	return nil
}

type tencentAIArtError struct {
	Code    string `json:"Code,omitempty"`
	Message string `json:"Message,omitempty"`
}

type tencentTC3SignInput struct {
	SecretID  string
	SecretKey string
	Service   string
	Host      string
	Action    string
	Timestamp int64
	Payload   []byte
}

func isTencentAIArtImageGeneration(info *relaycommon.RelayInfo) bool {
	if info == nil || info.RelayMode != relayconstant.RelayModeImagesGenerations || info.ChannelMeta == nil {
		return false
	}
	u, err := url.Parse(info.ChannelBaseUrl)
	if err != nil {
		return false
	}
	return strings.EqualFold(u.Hostname(), tencentAIArtHost)
}

func tencentAIArtImageRequestFromOpenAI(request dto.ImageRequest) (*tencentAIArtImageRequest, error) {
	if blocked, category := tencentAIArtPromptBlocked(request.Prompt); blocked {
		return nil, fmt.Errorf("moderation_blocked: Tencent AIArt prompt violates %s policy", category)
	}
	imageReq := &tencentAIArtImageRequest{
		Model:  request.Model,
		Prompt: request.Prompt,
	}
	if len(request.ExtraFields) > 0 {
		if err := common.Unmarshal(request.ExtraFields, imageReq); err != nil {
			return nil, fmt.Errorf("invalid extra_fields for Tencent AIArt image request: %w", err)
		}
		if err := common.Unmarshal(request.ExtraFields, &imageReq.Extra); err != nil {
			return nil, fmt.Errorf("invalid extra_fields for Tencent AIArt image request: %w", err)
		}
	}
	return imageReq, nil
}

func (r tencentAIArtImageRequest) MarshalJSON() ([]byte, error) {
	type Alias tencentAIArtImageRequest
	alias := Alias(r)
	alias.Extra = nil

	base, err := common.Marshal(alias)
	if err != nil {
		return nil, err
	}
	var payload map[string]any
	if err := common.Unmarshal(base, &payload); err != nil {
		return nil, err
	}
	for key, value := range r.Extra {
		payload[key] = value
	}
	return common.Marshal(payload)
}

func buildTencentTC3Authorization(input tencentTC3SignInput) string {
	host := input.Host
	if host == "" {
		host = tencentAIArtHost
	}
	serviceName := input.Service
	if serviceName == "" {
		serviceName = tencentAIArtService
	}
	httpRequestMethod := "POST"
	canonicalURI := "/"
	canonicalQueryString := ""
	canonicalHeaders := fmt.Sprintf("content-type:%s\nhost:%s\nx-tc-action:%s\n",
		"application/json", host, strings.ToLower(input.Action))
	signedHeaders := "content-type;host;x-tc-action"
	hashedRequestPayload := sha256hex(string(input.Payload))
	canonicalRequest := fmt.Sprintf("%s\n%s\n%s\n%s\n%s\n%s",
		httpRequestMethod,
		canonicalURI,
		canonicalQueryString,
		canonicalHeaders,
		signedHeaders,
		hashedRequestPayload)

	algorithm := "TC3-HMAC-SHA256"
	t := time.Unix(input.Timestamp, 0).UTC()
	date := t.Format("2006-01-02")
	credentialScope := fmt.Sprintf("%s/%s/tc3_request", date, serviceName)
	hashedCanonicalRequest := sha256hex(canonicalRequest)
	string2sign := fmt.Sprintf("%s\n%d\n%s\n%s",
		algorithm,
		input.Timestamp,
		credentialScope,
		hashedCanonicalRequest)

	secretDate := hmacSha256(date, "TC3"+input.SecretKey)
	secretService := hmacSha256(serviceName, secretDate)
	secretKey := hmacSha256("tc3_request", secretService)
	signature := hex.EncodeToString([]byte(hmacSha256(string2sign, secretKey)))

	return fmt.Sprintf("%s Credential=%s/%s, SignedHeaders=%s, Signature=%s",
		algorithm,
		input.SecretID,
		credentialScope,
		signedHeaders,
		signature)
}

func (a *Adaptor) doTencentAIArtImageRequest(c *gin.Context, info *relaycommon.RelayInfo, requestBody io.Reader) (*http.Response, error) {
	body, err := io.ReadAll(requestBody)
	if err != nil {
		return nil, fmt.Errorf("read Tencent AIArt request body failed: %w", err)
	}
	apiKey := tencentAIArtAPIKey(c, info)
	_, secretID, secretKey, err := parseTencentConfig(apiKey)
	if err != nil {
		return nil, err
	}
	a.Region = parseTencentRegion(apiKey)

	ctx, cancel := context.WithTimeout(c.Request.Context(), tencentAIArtPollTimeout)
	defer cancel()

	submitBody, err := a.tencentAIArtPost(ctx, c, info, tencentAIArtSubmitAction, body, secretID, secretKey)
	if err != nil {
		return nil, err
	}
	jobID, err := parseTencentAIArtSubmitJobID(submitBody)
	if err != nil {
		return nil, err
	}

	ticker := time.NewTicker(tencentAIArtPollInterval)
	defer ticker.Stop()

	for {
		describeReq := tencentAIArtDescribeRequest{JobID: jobID}
		describeBody, err := common.Marshal(describeReq)
		if err != nil {
			return nil, err
		}
		resultBody, err := a.tencentAIArtPost(ctx, c, info, tencentAIArtDescribeAction, describeBody, secretID, secretKey)
		if err != nil {
			return nil, err
		}
		if done, err := tencentAIArtJobDone(resultBody); done || err != nil {
			if err != nil {
				return nil, err
			}
			return &http.Response{
				StatusCode: http.StatusOK,
				Header:     make(http.Header),
				Body:       io.NopCloser(bytes.NewReader(resultBody)),
			}, nil
		}

		select {
		case <-ctx.Done():
			return nil, fmt.Errorf("Tencent AIArt image job %s timed out after %s", jobID, tencentAIArtPollTimeout)
		case <-ticker.C:
		}
	}
}

func (a *Adaptor) tencentAIArtPost(ctx context.Context, c *gin.Context, info *relaycommon.RelayInfo, action string, body []byte, secretID, secretKey string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(info.ChannelBaseUrl, "/")+"/", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	timestamp := common.GetTimestamp()
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Host", tencentAIArtHost)
	req.Header.Set("X-TC-Action", action)
	req.Header.Set("X-TC-Version", tencentAIArtVersion)
	req.Header.Set("X-TC-Timestamp", strconv.FormatInt(timestamp, 10))
	if a.Region != "" {
		req.Header.Set("X-TC-Region", a.Region)
	}
	req.Header.Set("Authorization", buildTencentTC3Authorization(tencentTC3SignInput{
		SecretID:  secretID,
		SecretKey: secretKey,
		Service:   tencentAIArtService,
		Host:      tencentAIArtHost,
		Action:    action,
		Timestamp: timestamp,
		Payload:   body,
	}))

	resp, err := channel.DoRequest(c, req, info)
	if err != nil {
		return nil, err
	}
	defer service.CloseResponseBodyGracefully(resp)
	responseBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("Tencent AIArt %s failed with status %d: %s", action, resp.StatusCode, string(responseBody))
	}
	return responseBody, nil
}

func parseTencentAIArtSubmitJobID(body []byte) (string, error) {
	var submit tencentAIArtSubmitResponse
	if err := common.Unmarshal(body, &submit); err != nil {
		return "", fmt.Errorf("decode Tencent AIArt submit response failed: %w", err)
	}
	if submit.Response.Error != nil {
		return "", errors.New(submit.Response.Error.Message)
	}
	if submit.Response.JobID != "" {
		return submit.Response.JobID, nil
	}
	if submit.Response.JobId != "" {
		return submit.Response.JobId, nil
	}
	return "", fmt.Errorf("Tencent AIArt submit response missing JobId")
}

func tencentAIArtJobDone(body []byte) (bool, error) {
	var result tencentAIArtDescribeResponse
	if err := common.Unmarshal(body, &result); err != nil {
		return false, fmt.Errorf("decode Tencent AIArt describe response failed: %w", err)
	}
	if result.Response.Error != nil {
		return false, errors.New(result.Response.Error.Message)
	}
	if len(collectTencentAIArtImages(result.Response)) > 0 {
		return true, nil
	}
	status := strings.ToLower(strings.TrimSpace(result.Response.JobStatusCode))
	if status == "" {
		status = strings.ToLower(strings.TrimSpace(result.Response.Status))
	}
	switch status {
	case "4", "5", "success", "succeeded", "done", "completed":
		return true, nil
	case "6", "failed", "fail", "error":
		return false, fmt.Errorf("Tencent AIArt image job failed")
	default:
		return false, nil
	}
}

func writeTencentAIArtImageResponse(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) (*dto.Usage, *types.NewAPIError) {
	responseBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, types.NewOpenAIError(err, types.ErrorCodeReadResponseBodyFailed, http.StatusInternalServerError)
	}
	service.CloseResponseBodyGracefully(resp)

	var result tencentAIArtDescribeResponse
	if err := common.Unmarshal(responseBody, &result); err != nil {
		return nil, types.NewOpenAIError(fmt.Errorf("decode Tencent AIArt image response failed: %w", err), types.ErrorCodeBadResponseBody, http.StatusInternalServerError)
	}
	if result.Response.Error != nil {
		return nil, types.WithOpenAIError(types.OpenAIError{
			Message: result.Response.Error.Message,
			Type:    "tencent_aiart_error",
			Code:    result.Response.Error.Code,
		}, http.StatusBadGateway)
	}

	images := collectTencentAIArtImages(result.Response)
	if len(images) == 0 {
		return nil, types.NewOpenAIError(errors.New("Tencent AIArt image response contains no images"), types.ErrorCodeEmptyResponse, http.StatusBadGateway)
	}

	wantsBase64 := false
	if info != nil {
		if imageReq, ok := info.Request.(*dto.ImageRequest); ok {
			wantsBase64 = imageReq.ResponseFormat == "b64_json"
		}
	}

	imageResponse := dto.ImageResponse{
		Created: common.GetTimestamp(),
		Data:    make([]dto.ImageData, 0, len(images)),
	}
	if info != nil && !info.StartTime.IsZero() {
		imageResponse.Created = info.StartTime.Unix()
	}

	for _, image := range images {
		if image == "" {
			continue
		}
		if wantsBase64 && strings.HasPrefix(image, "http") {
			_, b64, err := service.GetImageFromUrl(image)
			if err != nil {
				return nil, types.NewOpenAIError(fmt.Errorf("download Tencent AIArt image failed: %w", err), types.ErrorCodeBadResponse, http.StatusBadGateway)
			}
			imageResponse.Data = append(imageResponse.Data, dto.ImageData{B64Json: b64})
		} else if wantsBase64 || !strings.HasPrefix(image, "http") {
			imageResponse.Data = append(imageResponse.Data, dto.ImageData{B64Json: image})
		} else {
			imageResponse.Data = append(imageResponse.Data, dto.ImageData{Url: image})
		}
	}
	if len(imageResponse.Data) == 0 {
		return nil, types.NewOpenAIError(errors.New("Tencent AIArt image response contains no usable images"), types.ErrorCodeEmptyResponse, http.StatusBadGateway)
	}

	jsonResponse, err := common.Marshal(imageResponse)
	if err != nil {
		return nil, types.NewError(err, types.ErrorCodeBadResponseBody)
	}
	c.Writer.Header().Set("Content-Type", "application/json")
	c.Writer.WriteHeader(http.StatusOK)
	_, _ = c.Writer.Write(jsonResponse)
	return &dto.Usage{}, nil
}

func collectTencentAIArtImages(response tencentAIArtJobResponse) []string {
	images := make([]string, 0)
	images = append(images, response.ResultImage...)
	images = append(images, response.ResultImages...)
	images = append(images, response.ImageUrls...)
	images = append(images, response.Images...)
	return images
}

func collectTencentAIArtImageValues(value any) []string {
	images := make([]string, 0)
	switch typed := value.(type) {
	case string:
		if typed != "" {
			images = append(images, typed)
		}
	case []any:
		for _, item := range typed {
			images = append(images, collectTencentAIArtImageValues(item)...)
		}
	case map[string]any:
		preferredKeys := []string{
			"Url", "URL", "url",
			"ImageUrl", "ImageURL", "image_url", "imageUrl",
			"ResultImage", "ResultImages",
			"B64Json", "b64_json", "Base64", "base64",
		}
		for _, key := range preferredKeys {
			if child, ok := typed[key]; ok {
				images = append(images, collectTencentAIArtImageValues(child)...)
			}
		}
		if len(images) == 0 {
			for _, child := range typed {
				images = append(images, collectTencentAIArtImageValues(child)...)
			}
		}
	}
	return images
}

func tencentAIArtPromptBlocked(prompt string) (bool, string) {
	checkText := strings.ToLower(prompt)
	sexualWords := []string{"色情", "裸体", "裸露", "sexual", "nude", "nudity", "porn"}
	for _, word := range sexualWords {
		if strings.Contains(checkText, word) {
			return true, "sexual"
		}
	}
	violenceWords := []string{"血腥", "暴力", "violence", "violent", "gore", "bloody"}
	for _, word := range violenceWords {
		if strings.Contains(checkText, word) {
			return true, "violence"
		}
	}
	return false, ""
}

func tencentAIArtAPIKey(c *gin.Context, info *relaycommon.RelayInfo) string {
	apiKey := common.GetContextKeyString(c, constant.ContextKeyChannelKey)
	if apiKey == "" && info != nil && info.ChannelMeta != nil {
		apiKey = info.ApiKey
	}
	return strings.TrimPrefix(apiKey, "Bearer ")
}
