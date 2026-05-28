package aws

// Fy-api overlay: Bedrock enforces stricter schema validation than Anthropic
// native API on two fronts:
// 1. cache_control.scope — Bedrock rejects the "scope" field even though
//    Anthropic native API accepts it for prompt caching.
// 2. Empty text content blocks — Bedrock rejects content[] entries where
//    type="text" and text="".

// stripCacheControlScopeFromBlocks removes the "scope" key from any
// cache_control object found in a slice of content blocks.
func stripCacheControlScopeFromBlocks(blocks []interface{}) {
	for _, block := range blocks {
		b, ok := block.(map[string]interface{})
		if !ok {
			continue
		}
		cc, exists := b["cache_control"]
		if !exists || cc == nil {
			continue
		}
		ccMap, ok := cc.(map[string]interface{})
		if !ok {
			continue
		}
		delete(ccMap, "scope")
	}
}

// stripCacheControlScopeFromStruct strips cache_control.scope from the
// System and Messages fields of an AwsClaudeRequest.
func stripCacheControlScopeFromStruct(request *AwsClaudeRequest) {
	if request == nil {
		return
	}
	if blocks, ok := request.System.([]interface{}); ok {
		stripCacheControlScopeFromBlocks(blocks)
	}
	for i := range request.Messages {
		if blocks, ok := request.Messages[i].Content.([]interface{}); ok {
			stripCacheControlScopeFromBlocks(blocks)
		}
	}
}

// stripCacheControlScopeRaw strips cache_control.scope from a pass-through
// request body (map[string]interface{}).
func stripCacheControlScopeRaw(data map[string]interface{}) {
	if data == nil {
		return
	}
	if system, ok := data["system"].([]interface{}); ok {
		stripCacheControlScopeFromBlocks(system)
	}
	messages, ok := data["messages"].([]interface{})
	if !ok {
		return
	}
	for _, msg := range messages {
		m, ok := msg.(map[string]interface{})
		if !ok {
			continue
		}
		if blocks, ok := m["content"].([]interface{}); ok {
			stripCacheControlScopeFromBlocks(blocks)
		}
	}
}

// filterEmptyTextBlocks returns a new slice with empty text blocks removed.
// A block is considered empty when type=="text" and text=="".
func filterEmptyTextBlocks(blocks []interface{}) []interface{} {
	filtered := make([]interface{}, 0, len(blocks))
	for _, block := range blocks {
		b, ok := block.(map[string]interface{})
		if !ok {
			filtered = append(filtered, block)
			continue
		}
		blockType, _ := b["type"].(string)
		if blockType != "text" {
			filtered = append(filtered, block)
			continue
		}
		text, _ := b["text"].(string)
		if text != "" {
			filtered = append(filtered, block)
		}
	}
	return filtered
}

// filterEmptyTextBlocksFromStruct removes empty text content blocks from
// the Messages and System fields of an AwsClaudeRequest.
func filterEmptyTextBlocksFromStruct(request *AwsClaudeRequest) {
	if request == nil {
		return
	}
	if blocks, ok := request.System.([]interface{}); ok && len(blocks) > 0 {
		request.System = filterEmptyTextBlocks(blocks)
	}
	for i := range request.Messages {
		if blocks, ok := request.Messages[i].Content.([]interface{}); ok && len(blocks) > 0 {
			request.Messages[i].Content = filterEmptyTextBlocks(blocks)
		}
	}
}

// filterEmptyTextBlocksRaw removes empty text content blocks from a
// pass-through request body (map[string]interface{}).
func filterEmptyTextBlocksRaw(data map[string]interface{}) {
	if data == nil {
		return
	}
	if system, ok := data["system"].([]interface{}); ok && len(system) > 0 {
		data["system"] = filterEmptyTextBlocks(system)
	}
	messages, ok := data["messages"].([]interface{})
	if !ok {
		return
	}
	for _, msg := range messages {
		m, ok := msg.(map[string]interface{})
		if !ok {
			continue
		}
		blocks, ok := m["content"].([]interface{})
		if !ok || len(blocks) == 0 {
			continue
		}
		m["content"] = filterEmptyTextBlocks(blocks)
	}
}
