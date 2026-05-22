package aws

import (
	"testing"
)

func TestFilterBedrockToolsRaw(t *testing.T) {
	tests := []struct {
		name     string
		input    map[string]interface{}
		wantLen  int
		wantNil  bool
	}{
		{
			name:    "nil data",
			input:   nil,
			wantLen: 0,
		},
		{
			name:    "no tools key",
			input:   map[string]interface{}{"model": "claude"},
			wantLen: 0,
		},
		{
			name: "all supported tools kept",
			input: map[string]interface{}{
				"tools": []interface{}{
					map[string]interface{}{"type": "custom", "name": "my_tool"},
					map[string]interface{}{"type": "bash_20250124"},
				},
			},
			wantLen: 2,
		},
		{
			name: "unsupported tools removed",
			input: map[string]interface{}{
				"tools": []interface{}{
					map[string]interface{}{"type": "custom", "name": "my_tool"},
					map[string]interface{}{"type": "web_search_20250305"},
					map[string]interface{}{"type": "advisor_20260301"},
					map[string]interface{}{"type": "text_editor_20250728"},
				},
			},
			wantLen: 2,
		},
		{
			name: "all unsupported removes tools key",
			input: map[string]interface{}{
				"tools": []interface{}{
					map[string]interface{}{"type": "web_search_20250305"},
					map[string]interface{}{"type": "advisor_20260301"},
				},
			},
			wantLen: 0,
			wantNil: true,
		},
		{
			name: "tool without type kept",
			input: map[string]interface{}{
				"tools": []interface{}{
					map[string]interface{}{"name": "no_type_tool"},
				},
			},
			wantLen: 1,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			filterBedrockToolsRaw(tt.input)
			if tt.input == nil {
				return
			}
			tools, exists := tt.input["tools"]
			if tt.wantNil {
				if exists {
					t.Errorf("expected tools key deleted, got %v", tools)
				}
				return
			}
			if tt.wantLen == 0 && !exists {
				return
			}
			arr := tools.([]interface{})
			if len(arr) != tt.wantLen {
				t.Errorf("want %d tools, got %d", tt.wantLen, len(arr))
			}
		})
	}
}

func TestFilterBedrockToolsFromStruct(t *testing.T) {
	req := &AwsClaudeRequest{
		Tools: []interface{}{
			map[string]interface{}{"type": "custom", "name": "calc"},
			map[string]interface{}{"type": "web_search_20250305"},
			map[string]interface{}{"type": "text_editor_20250429"},
		},
	}
	filterBedrockToolsFromStruct(req)
	tools := req.Tools.([]interface{})
	if len(tools) != 2 {
		t.Fatalf("want 2 tools, got %d", len(tools))
	}
}
