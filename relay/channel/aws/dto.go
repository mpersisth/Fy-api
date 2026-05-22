package aws

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/logger"
)

var bedrockAnthropicBetaCompatMap = map[string]string{
	"advanced-tool-use-2025-11-20":     "tool-search-tool-2025-10-19",
	"computer-use-2025-01-24":          "computer-use-2025-01-24",
	"context-1m-2025-08-07":            "context-1m-2025-08-07",
	"context-management-2025-06-27":    "context-management-2025-06-27",
	"dev-full-thinking-2025-05-14":     "dev-full-thinking-2025-05-14",
	"effort-2025-11-24":                "effort-2025-11-24",
	"interleaved-thinking-2025-05-14":  "interleaved-thinking-2025-05-14",
	"output-128k-2025-02-19":           "output-128k-2025-02-19",
	"token-efficient-tools-2025-02-19": "token-efficient-tools-2025-02-19",
	"tool-examples-2025-10-29":         "tool-examples-2025-10-29",
	"tool-search-tool-2025-10-19":      "tool-search-tool-2025-10-19",
}

type AwsClaudeRequest struct {
	// AnthropicVersion should be "bedrock-2023-05-31"
	AnthropicVersion string              `json:"anthropic_version"`
	AnthropicBeta    json.RawMessage     `json:"anthropic_beta,omitempty"`
	System           any                 `json:"system,omitempty"`
	Messages         []dto.ClaudeMessage `json:"messages"`
	MaxTokens        uint                `json:"max_tokens,omitempty"`
	Temperature      *float64            `json:"temperature,omitempty"`
	TopP             float64             `json:"top_p,omitempty"`
	TopK             int                 `json:"top_k,omitempty"`
	StopSequences    []string            `json:"stop_sequences,omitempty"`
	Tools            any                 `json:"tools,omitempty"`
	ToolChoice       any                 `json:"tool_choice,omitempty"`
	Thinking         *dto.Thinking       `json:"thinking,omitempty"`
	OutputConfig     json.RawMessage     `json:"output_config,omitempty"`
	//Metadata         json.RawMessage     `json:"metadata,omitempty"`
}

func formatRequest(requestBody io.Reader, requestHeader http.Header) (*AwsClaudeRequest, error) {
	var awsClaudeRequest AwsClaudeRequest
	err := common.DecodeJson(requestBody, &awsClaudeRequest)
	if err != nil {
		return nil, err
	}
	awsClaudeRequest.AnthropicVersion = "bedrock-2023-05-31"
	sanitizeAwsClaudeRequestForBedrock(&awsClaudeRequest)

	// Fy-api overlay: normalize Anthropic beta flags to the subset Bedrock accepts.
	// Direct Anthropic supports more beta headers than Bedrock. Passing unsupported
	// values through here makes Bedrock reject the whole request with
	// "ValidationException: invalid beta flag".
	anthropicBetaValues := requestHeader.Get("anthropic-beta")
	if len(anthropicBetaValues) > 0 {
		tempArray := normalizeBedrockAnthropicBeta(anthropicBetaValues)
		if len(tempArray) > 0 {
			betaJson, err := json.Marshal(tempArray)
			if err != nil {
				return nil, err
			}
			awsClaudeRequest.AnthropicBeta = betaJson
		}
	}
	logger.LogJson(context.Background(), "json", awsClaudeRequest)
	return &awsClaudeRequest, nil
}

func sanitizeAwsClaudeRequestForBedrock(request *AwsClaudeRequest) {
	if request == nil {
		return
	}
	// Fy-api overlay: Bedrock rejects beta flags supplied in the body unless
	// they are in AWS' smaller supported set. Always rebuild anthropic_beta
	// from the normalized request header instead of trusting client body input.
	request.AnthropicBeta = nil
	sanitizeBedrockClaudeRawFieldsFromStruct(request)
}

func sanitizeBedrockClaudeRawFieldsFromStruct(request *AwsClaudeRequest) {
	if request == nil {
		return
	}
	// Fy-api overlay: Anthropic's output_config beta shape is not accepted by
	// Bedrock Claude Messages today. Drop it at the AWS boundary so upstream
	// schema changes do not leak through pass-through or native Claude calls.
	request.OutputConfig = nil
	filterBedrockToolsFromStruct(request)
}

func normalizeBedrockAnthropicBeta(value string) []string {
	if value == "" {
		return nil
	}
	seen := make(map[string]struct{})
	result := make([]string, 0)
	for _, raw := range strings.Split(value, ",") {
		token := strings.TrimSpace(raw)
		if token == "" {
			continue
		}
		normalized, ok := bedrockAnthropicBetaCompatMap[token]
		if !ok || normalized == "" {
			continue
		}
		if _, exists := seen[normalized]; exists {
			continue
		}
		seen[normalized] = struct{}{}
		result = append(result, normalized)
	}
	return result
}

// NovaMessage Nova模型使用messages-v1格式
type NovaMessage struct {
	Role    string        `json:"role"`
	Content []NovaContent `json:"content"`
}

type NovaContent struct {
	Text string `json:"text"`
}

type NovaRequest struct {
	SchemaVersion   string               `json:"schemaVersion"`             // 请求版本，例如 "1.0"
	Messages        []NovaMessage        `json:"messages"`                  // 对话消息列表
	InferenceConfig *NovaInferenceConfig `json:"inferenceConfig,omitempty"` // 推理配置，可选
}

type NovaInferenceConfig struct {
	MaxTokens     int      `json:"maxTokens,omitempty"`     // 最大生成的 token 数
	Temperature   float64  `json:"temperature,omitempty"`   // 随机性 (默认 0.7, 范围 0-1)
	TopP          float64  `json:"topP,omitempty"`          // nucleus sampling (默认 0.9, 范围 0-1)
	TopK          int      `json:"topK,omitempty"`          // 限制候选 token 数 (默认 50, 范围 0-128)
	StopSequences []string `json:"stopSequences,omitempty"` // 停止生成的序列
}

// 转换OpenAI请求为Nova格式
func convertToNovaRequest(req *dto.GeneralOpenAIRequest) *NovaRequest {
	novaMessages := make([]NovaMessage, len(req.Messages))
	for i, msg := range req.Messages {
		novaMessages[i] = NovaMessage{
			Role:    msg.Role,
			Content: []NovaContent{{Text: msg.StringContent()}},
		}
	}

	novaReq := &NovaRequest{
		SchemaVersion: "messages-v1",
		Messages:      novaMessages,
	}

	// 设置推理配置
	if (req.MaxTokens != nil && *req.MaxTokens != 0) || (req.Temperature != nil && *req.Temperature != 0) || (req.TopP != nil && *req.TopP != 0) || (req.TopK != nil && *req.TopK != 0) || req.Stop != nil {
		novaReq.InferenceConfig = &NovaInferenceConfig{}
		if req.MaxTokens != nil && *req.MaxTokens != 0 {
			novaReq.InferenceConfig.MaxTokens = int(*req.MaxTokens)
		}
		if req.Temperature != nil && *req.Temperature != 0 {
			novaReq.InferenceConfig.Temperature = *req.Temperature
		}
		if req.TopP != nil && *req.TopP != 0 {
			novaReq.InferenceConfig.TopP = *req.TopP
		}
		if req.TopK != nil && *req.TopK != 0 {
			novaReq.InferenceConfig.TopK = *req.TopK
		}
		if req.Stop != nil {
			if stopSequences := parseStopSequences(req.Stop); len(stopSequences) > 0 {
				novaReq.InferenceConfig.StopSequences = stopSequences
			}
		}
	}

	return novaReq
}

// parseStopSequences 解析停止序列，支持字符串或字符串数组
func parseStopSequences(stop any) []string {
	if stop == nil {
		return nil
	}

	switch v := stop.(type) {
	case string:
		if v != "" {
			return []string{v}
		}
	case []string:
		return v
	case []interface{}:
		var sequences []string
		for _, item := range v {
			if str, ok := item.(string); ok && str != "" {
				sequences = append(sequences, str)
			}
		}
		return sequences
	}
	return nil
}
