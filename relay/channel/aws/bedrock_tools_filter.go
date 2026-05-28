package aws

// Fy-api overlay: Bedrock rejects tool types outside its supported set with a
// 400 ValidationException. This filter strips unsupported tools before they
// reach the Bedrock API, preventing hard failures for clients that include
// Anthropic-only tool types (e.g. web_search, advisor).

var bedrockSupportedToolTypes = map[string]struct{}{
	"bash_20250124":                 {},
	"computer_20250124":             {},
	"custom":                        {},
	"memory_20250818":               {},
	"text_editor_20250124":          {},
	"text_editor_20250429":          {},
	"text_editor_20250728":          {},
	"tool_search_tool_bm25":         {},
	"tool_search_tool_bm25_20251119": {},
	"tool_search_tool_regex":         {},
	"tool_search_tool_regex_20251119": {},
}

// filterBedrockToolsRaw removes unsupported tool types from the tools array
// in a pass-through (map[string]interface{}) request body.
func filterBedrockToolsRaw(data map[string]interface{}) {
	toolsRaw, ok := data["tools"]
	if !ok || toolsRaw == nil {
		return
	}
	tools, ok := toolsRaw.([]interface{})
	if !ok || len(tools) == 0 {
		return
	}
	filtered := make([]interface{}, 0, len(tools))
	for _, t := range tools {
		tool, ok := t.(map[string]interface{})
		if !ok {
			filtered = append(filtered, t)
			continue
		}
		toolType, _ := tool["type"].(string)
		if toolType == "" {
			filtered = append(filtered, t)
			continue
		}
		if _, supported := bedrockSupportedToolTypes[toolType]; supported {
			filtered = append(filtered, t)
		}
	}
	if len(filtered) == 0 {
		delete(data, "tools")
	} else {
		data["tools"] = filtered
	}
}

// filterBedrockToolsFromStruct removes unsupported tool types from the Tools
// field of an AwsClaudeRequest (normal/converted path).
func filterBedrockToolsFromStruct(request *AwsClaudeRequest) {
	if request == nil || request.Tools == nil {
		return
	}
	tools, ok := request.Tools.([]interface{})
	if !ok {
		return
	}
	filtered := make([]interface{}, 0, len(tools))
	for _, t := range tools {
		tool, ok := t.(map[string]interface{})
		if !ok {
			filtered = append(filtered, t)
			continue
		}
		toolType, _ := tool["type"].(string)
		if toolType == "" {
			filtered = append(filtered, t)
			continue
		}
		if _, supported := bedrockSupportedToolTypes[toolType]; supported {
			filtered = append(filtered, t)
		}
	}
	if len(filtered) == 0 {
		request.Tools = nil
	} else {
		request.Tools = filtered
	}
}
