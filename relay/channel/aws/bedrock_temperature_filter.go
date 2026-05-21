package aws

// Fy-api overlay: Bedrock rejects the `temperature` parameter for certain
// models with "ValidationException: `temperature` is deprecated for this
// model." Strip it at the AWS boundary so clients don't need to know which
// models have this restriction.

import "strings"

// bedrockTemperatureDeprecatedModels lists model ID substrings for which
// Bedrock no longer accepts the temperature parameter.
var bedrockTemperatureDeprecatedModels = []string{
	"claude-opus-4",
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

func stripBedrockDeprecatedTemperature(modelName string, request *AwsClaudeRequest) {
	if request == nil || !isTemperatureDeprecatedForBedrock(modelName) {
		return
	}
	request.Temperature = nil
}

func stripBedrockDeprecatedTemperatureRaw(modelName string, data map[string]interface{}) {
	if data == nil || !isTemperatureDeprecatedForBedrock(modelName) {
		return
	}
	delete(data, "temperature")
}
