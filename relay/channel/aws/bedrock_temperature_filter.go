package aws

// Fy-api overlay: Bedrock has two sampling-parameter restrictions:
// 1. Certain models fully deprecate `temperature` ("ValidationException:
//    `temperature` is deprecated for this model").
// 2. Some models reject requests that specify both `temperature` and `top_p`
//    simultaneously ("ValidationException: `temperature` and `top_p` cannot
//    both be specified for this model").
// Strip the offending fields at the AWS boundary so clients don't need to
// know which models have these restrictions.

import "strings"

// bedrockTemperatureDeprecatedModels lists model ID substrings for which
// Bedrock no longer accepts the temperature parameter at all.
// TODO(review): 临时黑名单，随 Bedrock 侧策略变化定期 review，确认是否需要增减条目。
var bedrockTemperatureDeprecatedModels = []string{
	"claude-opus-4-7",
}

func isTemperatureDeprecatedForBedrock(modelName string) bool {
	lower := strings.ToLower(modelName)
	for _, substr := range bedrockTemperatureDeprecatedModels {
		if strings.Contains(lower, substr) {
			return true
		}
	}
	return false
}

// sanitizeBedrockSamplingParams applies Bedrock sampling-parameter restrictions:
// 1. Strip temperature for models where it's fully deprecated
// 2. Clamp temperature to [0, 1] (Bedrock range) for all other models
// 3. Strip top_p when both temperature and top_p are present
func sanitizeBedrockSamplingParams(modelName string, request *AwsClaudeRequest) {
	if request == nil {
		return
	}
	if isTemperatureDeprecatedForBedrock(modelName) {
		request.Temperature = nil
		return
	}
	if request.Temperature != nil {
		if *request.Temperature > 1.0 {
			clamped := 1.0
			request.Temperature = &clamped
		}
		if request.TopP != 0 {
			request.TopP = 0
		}
	}
}

// sanitizeBedrockSamplingParamsRaw applies the same logic for pass-through.
func sanitizeBedrockSamplingParamsRaw(modelName string, data map[string]any) {
	if data == nil {
		return
	}
	if isTemperatureDeprecatedForBedrock(modelName) {
		delete(data, "temperature")
		return
	}
	if temp, hasTemp := data["temperature"]; hasTemp {
		if tempFloat, ok := toFloat64(temp); ok && tempFloat > 1.0 {
			data["temperature"] = 1.0
		}
		delete(data, "top_p")
	}
}

func toFloat64(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case float32:
		return float64(n), true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	default:
		return 0, false
	}
}
