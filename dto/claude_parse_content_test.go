// Fy-api overlay: regression tests for the v1.6 hot-fix on the /v1/messages
// (Anthropic Messages API) path.
//
// Three classes of bug surfaced by the 2026-05-09 fy-conformance baseline:
//
//  1. content=42 / content=true (scalar where array|string) → 500 + Go struct
//     leak ("json: cannot unmarshal number into Go value of type
//     []***.ClaudeMediaMessage") via ClaudeMessage.ParseContent →
//     common.Any2Type.
//  2. content=[{type:"text", text:42}] → 500 + leak ("Go struct field
//     ***.text of type string") via the same re-unmarshal path.
//  3. content=[{type:"image"}] (image block missing source) → 500 PANIC
//     in service/convert.go (covered by service-level integration via
//     fy-conformance, not unit-testable from dto package).
//
// This file covers (1) and (2): the leak surface is closed by wrapping
// the inner json.Unmarshal in common.Any2Type with
// SanitizeJSONUnmarshalError. The controller status-code mapping (500 →
// 400) is exercised end-to-end by the Python fy-conformance suite.
package dto

import (
	"strings"
	"testing"
)

// containsLeakMarker returns the first leak marker found in s. Any non-empty
// return means we're leaking Go-internal type information to API clients.
func containsLeakMarker(s string) string {
	for _, marker := range []string{
		"Go struct field",
		"json: cannot unmarshal",
		"GeneralOpenAIRequest",
		"ClaudeMediaMessage",
		"ClaudeRequest",
		"ClaudeMessage",
	} {
		if strings.Contains(s, marker) {
			return marker
		}
	}
	return ""
}

// Bug 1+2: content=<scalar> trips the slice unmarshal in ParseContent.
func TestClaudeMessage_ParseContent_ScalarContent_NoLeak(t *testing.T) {
	t.Parallel()

	for _, tc := range []struct {
		name    string
		content any
	}{
		{"number", float64(42)},
		{"bool-true", true},
		{"bool-false", false},
	} {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			msg := ClaudeMessage{Role: "user", Content: tc.content}
			_, err := msg.ParseContent()
			if err == nil {
				t.Fatalf("expected an error parsing content=%v, got nil", tc.content)
			}
			if leak := containsLeakMarker(err.Error()); leak != "" {
				t.Fatalf("error message for content=%v leaks %q: %s", tc.content, leak, err)
			}
		})
	}
}

// Bug 3 (the dto-layer half): text block whose `text` field has the wrong
// type. Leak guard only — the validation responsibility is upstream.
func TestClaudeMessage_ParseContent_TextBlockBadTextType_NoLeak(t *testing.T) {
	t.Parallel()
	msg := ClaudeMessage{
		Role: "user",
		Content: []any{
			map[string]any{"type": "text", "text": float64(42)},
		},
	}
	_, err := msg.ParseContent()
	if err == nil {
		t.Fatal("expected an error parsing text block with text=42, got nil")
	}
	if leak := containsLeakMarker(err.Error()); leak != "" {
		t.Fatalf("error message leaks %q: %s", leak, err)
	}
}

// Happy path: legal text block parses cleanly. Defends against the safety
// nets accidentally rejecting well-formed input.
func TestClaudeMessage_ParseContent_HappyPath(t *testing.T) {
	t.Parallel()
	msg := ClaudeMessage{
		Role: "user",
		Content: []any{
			map[string]any{"type": "text", "text": "hi"},
		},
	}
	contents, err := msg.ParseContent()
	if err != nil {
		t.Fatalf("unexpected error on legal text block: %v", err)
	}
	if len(contents) != 1 {
		t.Fatalf("expected 1 content block, got %d", len(contents))
	}
	if contents[0].Type != "text" {
		t.Fatalf("expected type=text, got %q", contents[0].Type)
	}
	if got := contents[0].GetText(); got != "hi" {
		t.Fatalf("expected text=hi, got %q", got)
	}
}
