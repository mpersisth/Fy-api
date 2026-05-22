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

// sanitizeBedrockSamplingParams applies both restrictions:
// 1. Strip temperature for models where it's deprecated
// 2. Strip top_p when both temperature and top_p are present
// TODO(review): 临时参数兼容处理，Bedrock 侧策略可能变化，需定期 review 是否仍需要。
func sanitizeBedrockSamplingParams(modelName string, request *AwsClaudeRequest) {
	if request == nil {
		return
	}
	if isTemperatureDeprecatedForBedrock(modelName) {
		request.Temperature = nil
		return
	}
	if request.Temperature != nil && request.TopP != 0 {
		request.TopP = 0
	}
}

// sanitizeBedrockSamplingParamsRaw applies the same logic for pass-through.
// TODO(review): 同上，临时参数兼容处理，需定期 review。
func sanitizeBedrockSamplingParamsRaw(modelName string, data map[string]interface{}) {
	if data == nil {
		return
	}
	if isTemperatureDeprecatedForBedrock(modelName) {
		delete(data, "temperature")
		return
	}
	_, hasTemp := data["temperature"]
	_, hasTopP := data["top_p"]
	if hasTemp && hasTopP {
		delete(data, "top_p")
	}
}
