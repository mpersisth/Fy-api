package aws

import (
	"testing"

	"github.com/QuantumNous/new-api/dto"
)

func TestStripCacheControlScopeFromBlocks(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name   string
		blocks []interface{}
		want   []interface{}
	}{
		{
			name: "strips scope from cache_control",
			blocks: []interface{}{
				map[string]interface{}{
					"type": "text",
					"text": "hello",
					"cache_control": map[string]interface{}{
						"type":  "ephemeral",
						"scope": "turn",
					},
				},
			},
			want: []interface{}{
				map[string]interface{}{
					"type": "text",
					"text": "hello",
					"cache_control": map[string]interface{}{
						"type": "ephemeral",
					},
				},
			},
		},
		{
			name: "no cache_control unchanged",
			blocks: []interface{}{
				map[string]interface{}{
					"type": "text",
					"text": "hello",
				},
			},
			want: []interface{}{
				map[string]interface{}{
					"type": "text",
					"text": "hello",
				},
			},
		},
		{
			name: "cache_control without scope unchanged",
			blocks: []interface{}{
				map[string]interface{}{
					"type":          "text",
					"text":          "hello",
					"cache_control": map[string]interface{}{"type": "ephemeral"},
				},
			},
			want: []interface{}{
				map[string]interface{}{
					"type":          "text",
					"text":          "hello",
					"cache_control": map[string]interface{}{"type": "ephemeral"},
				},
			},
		},
		{
			name:   "nil blocks no panic",
			blocks: nil,
			want:   nil,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			stripCacheControlScopeFromBlocks(tt.blocks)
			if tt.blocks == nil && tt.want == nil {
				return
			}
			for i, block := range tt.blocks {
				got := block.(map[string]interface{})
				expected := tt.want[i].(map[string]interface{})
				cc, hasCC := got["cache_control"]
				expectedCC, wantCC := expected["cache_control"]
				if hasCC != wantCC {
					t.Errorf("cache_control presence mismatch")
				}
				if hasCC {
					gotMap := cc.(map[string]interface{})
					expectedMap := expectedCC.(map[string]interface{})
					if _, hasScope := gotMap["scope"]; hasScope {
						t.Errorf("scope should have been removed")
					}
					if gotMap["type"] != expectedMap["type"] {
						t.Errorf("type mismatch: got %v, want %v", gotMap["type"], expectedMap["type"])
					}
				}
			}
		})
	}
}

func TestStripCacheControlScopeFromStruct(t *testing.T) {
	t.Parallel()
	req := &AwsClaudeRequest{
		System: []interface{}{
			map[string]interface{}{
				"type": "text",
				"text": "system prompt",
				"cache_control": map[string]interface{}{
					"type":  "ephemeral",
					"scope": "turn",
				},
			},
		},
		Messages: []dto.ClaudeMessage{
			{Content: []interface{}{
				map[string]interface{}{
					"type": "text",
					"text": "user msg",
					"cache_control": map[string]interface{}{
						"type":  "ephemeral",
						"scope": "turn",
					},
				},
			}},
		},
	}
	stripCacheControlScopeFromStruct(req)

	sysBlocks := req.System.([]interface{})
	sysCC := sysBlocks[0].(map[string]interface{})["cache_control"].(map[string]interface{})
	if _, has := sysCC["scope"]; has {
		t.Error("system cache_control.scope should be removed")
	}
	if sysCC["type"] != "ephemeral" {
		t.Error("system cache_control.type should be preserved")
	}

	msgBlocks := req.Messages[0].Content.([]interface{})
	msgCC := msgBlocks[0].(map[string]interface{})["cache_control"].(map[string]interface{})
	if _, has := msgCC["scope"]; has {
		t.Error("message cache_control.scope should be removed")
	}
}

func TestStripCacheControlScopeFromStruct_StringContent(t *testing.T) {
	t.Parallel()
	req := &AwsClaudeRequest{
		System:   "just a string",
		Messages: []dto.ClaudeMessage{{Content: "string content"}},
	}
	stripCacheControlScopeFromStruct(req)
	if req.System != "just a string" {
		t.Error("string system should be unchanged")
	}
}

func TestFilterEmptyTextBlocks(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name     string
		blocks   []interface{}
		wantLen  int
		wantText []string
	}{
		{
			name: "removes empty text block",
			blocks: []interface{}{
				map[string]interface{}{"type": "text", "text": "hello"},
				map[string]interface{}{"type": "text", "text": ""},
				map[string]interface{}{"type": "text", "text": "world"},
			},
			wantLen:  2,
			wantText: []string{"hello", "world"},
		},
		{
			name: "preserves non-text blocks",
			blocks: []interface{}{
				map[string]interface{}{"type": "image", "source": map[string]interface{}{}},
				map[string]interface{}{"type": "tool_use", "id": "t1"},
			},
			wantLen: 2,
		},
		{
			name: "all empty text blocks removed",
			blocks: []interface{}{
				map[string]interface{}{"type": "text", "text": ""},
			},
			wantLen: 0,
		},
		{
			name:    "empty input",
			blocks:  []interface{}{},
			wantLen: 0,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := filterEmptyTextBlocks(tt.blocks)
			if len(result) != tt.wantLen {
				t.Errorf("got len %d, want %d", len(result), tt.wantLen)
			}
			for i, text := range tt.wantText {
				got := result[i].(map[string]interface{})["text"].(string)
				if got != text {
					t.Errorf("block %d: got %q, want %q", i, got, text)
				}
			}
		})
	}
}

func TestFilterEmptyTextBlocksFromStruct(t *testing.T) {
	t.Parallel()
	req := &AwsClaudeRequest{
		System: []interface{}{
			map[string]interface{}{"type": "text", "text": ""},
			map[string]interface{}{"type": "text", "text": "keep"},
		},
		Messages: []dto.ClaudeMessage{
			{Content: []interface{}{
				map[string]interface{}{"type": "text", "text": ""},
				map[string]interface{}{"type": "image", "source": map[string]interface{}{}},
				map[string]interface{}{"type": "text", "text": "msg"},
			}},
		},
	}
	filterEmptyTextBlocksFromStruct(req)

	sysBlocks := req.System.([]interface{})
	if len(sysBlocks) != 1 {
		t.Fatalf("system: got %d blocks, want 1", len(sysBlocks))
	}

	msgBlocks := req.Messages[0].Content.([]interface{})
	if len(msgBlocks) != 2 {
		t.Fatalf("messages: got %d blocks, want 2", len(msgBlocks))
	}
}

func TestFilterEmptyTextBlocksRaw(t *testing.T) {
	t.Parallel()
	data := map[string]interface{}{
		"system": []interface{}{
			map[string]interface{}{"type": "text", "text": "sys"},
			map[string]interface{}{"type": "text", "text": ""},
		},
		"messages": []interface{}{
			map[string]interface{}{
				"role": "user",
				"content": []interface{}{
					map[string]interface{}{"type": "text", "text": ""},
					map[string]interface{}{"type": "text", "text": "hi"},
				},
			},
		},
	}
	filterEmptyTextBlocksRaw(data)

	sys := data["system"].([]interface{})
	if len(sys) != 1 {
		t.Fatalf("system: got %d, want 1", len(sys))
	}
	msgs := data["messages"].([]interface{})
	msg := msgs[0].(map[string]interface{})
	content := msg["content"].([]interface{})
	if len(content) != 1 {
		t.Fatalf("content: got %d, want 1", len(content))
	}
}

func TestStripCacheControlScopeRaw(t *testing.T) {
	t.Parallel()
	data := map[string]interface{}{
		"system": []interface{}{
			map[string]interface{}{
				"type":          "text",
				"text":          "sys",
				"cache_control": map[string]interface{}{"type": "ephemeral", "scope": "turn"},
			},
		},
		"messages": []interface{}{
			map[string]interface{}{
				"role": "user",
				"content": []interface{}{
					map[string]interface{}{
						"type":          "text",
						"text":          "hi",
						"cache_control": map[string]interface{}{"type": "ephemeral", "scope": "turn"},
					},
				},
			},
		},
	}
	stripCacheControlScopeRaw(data)

	sys := data["system"].([]interface{})
	sysCC := sys[0].(map[string]interface{})["cache_control"].(map[string]interface{})
	if _, has := sysCC["scope"]; has {
		t.Error("system scope should be removed")
	}
	msgs := data["messages"].([]interface{})
	content := msgs[0].(map[string]interface{})["content"].([]interface{})
	msgCC := content[0].(map[string]interface{})["cache_control"].(map[string]interface{})
	if _, has := msgCC["scope"]; has {
		t.Error("message scope should be removed")
	}
}
