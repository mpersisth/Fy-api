package common

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
)

// payload mirrors a representative subset of dto.GeneralOpenAIRequest fields
// so the test exercises the actual stdlib error format users hit in prod.
type payload struct {
	Model            string   `json:"model"`
	MaxTokens        *uint    `json:"max_tokens,omitempty"`
	N                *int     `json:"n,omitempty"`
	Temperature      *float64 `json:"temperature,omitempty"`
	TopP             *float64 `json:"top_p,omitempty"`
	FrequencyPenalty *float64 `json:"frequency_penalty,omitempty"`
	PresencePenalty  *float64 `json:"presence_penalty,omitempty"`
}

func TestSanitizeJSONUnmarshalError_TypedFromStdlib(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name   string
		body   string
		expect string // substring that must appear; Go-path artefacts must NOT
	}{
		{
			name:   "max_tokens=string -> non-negative integer",
			body:   `{"model":"x","max_tokens":"abc"}`,
			expect: `invalid type for field "max_tokens": expected non-negative integer, got string`,
		},
		{
			name:   "max_tokens=float -> non-negative integer",
			body:   `{"model":"x","max_tokens":1.5}`,
			expect: `invalid type for field "max_tokens": expected non-negative integer, got number`,
		},
		{
			name:   "max_tokens=bool -> non-negative integer",
			body:   `{"model":"x","max_tokens":true}`,
			expect: `invalid type for field "max_tokens": expected non-negative integer, got bool`,
		},
		{
			name:   "n=string -> integer",
			body:   `{"model":"x","n":"abc"}`,
			expect: `invalid type for field "n": expected integer, got string`,
		},
		{
			name:   "n=float -> integer",
			body:   `{"model":"x","n":1.5}`,
			expect: `invalid type for field "n": expected integer, got number`,
		},
		{
			name:   "temperature=string -> number",
			body:   `{"model":"x","temperature":"abc"}`,
			expect: `invalid type for field "temperature": expected number, got string`,
		},
		{
			name:   "temperature=bool -> number",
			body:   `{"model":"x","temperature":true}`,
			expect: `invalid type for field "temperature": expected number, got bool`,
		},
		{
			name:   "top_p=string -> number",
			body:   `{"model":"x","top_p":"abc"}`,
			expect: `invalid type for field "top_p": expected number, got string`,
		},
		{
			name:   "frequency_penalty=string -> number",
			body:   `{"model":"x","frequency_penalty":"abc"}`,
			expect: `invalid type for field "frequency_penalty": expected number, got string`,
		},
		{
			name:   "presence_penalty=bool -> number",
			body:   `{"model":"x","presence_penalty":true}`,
			expect: `invalid type for field "presence_penalty": expected number, got bool`,
		},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			var p payload
			rawErr := json.Unmarshal([]byte(tc.body), &p)
			if rawErr == nil {
				t.Fatalf("expected json.Unmarshal to fail")
			}
			// sanity: stdlib output really does leak a Go path
			if !strings.Contains(rawErr.Error(), "Go struct field") {
				t.Fatalf("test premise broken: stdlib error no longer contains Go struct field path: %v", rawErr)
			}

			cleaned := SanitizeJSONUnmarshalError(rawErr)
			if cleaned == nil {
				t.Fatal("sanitized error is nil")
			}
			got := cleaned.Error()
			if !strings.Contains(got, tc.expect) {
				t.Fatalf("expected substring %q\n  got: %s", tc.expect, got)
			}
			if strings.Contains(got, "Go struct field") {
				t.Errorf("Go path leaked into sanitized message: %s", got)
			}
			if strings.Contains(got, ".max_tokens") || strings.Contains(got, ".temperature") {
				t.Errorf("Go-style dotted path leaked: %s", got)
			}
		})
	}
}

func TestSanitizeJSONUnmarshalError_StringMatchFallback(t *testing.T) {
	t.Parallel()

	// Simulate a wrapped/re-stringified error (e.g. from a third-party
	// decoder that returns plain `error`).
	wrapped := errors.New(
		"json: cannot unmarshal string into Go struct field GeneralOpenAIRequest.max_tokens of type uint",
	)
	cleaned := SanitizeJSONUnmarshalError(wrapped)
	got := cleaned.Error()
	if strings.Contains(got, "Go struct field") {
		t.Errorf("string-match path didn't strip Go path: %s", got)
	}
	if !strings.Contains(got, "max_tokens") {
		t.Errorf("string-match path lost the field name: %s", got)
	}
}

func TestSanitizeJSONUnmarshalError_SyntaxError(t *testing.T) {
	t.Parallel()

	rawErr := json.Unmarshal([]byte(`{"model":`), &payload{})
	if rawErr == nil {
		t.Fatal("expected syntax error")
	}
	cleaned := SanitizeJSONUnmarshalError(rawErr)
	got := cleaned.Error()
	if !strings.HasPrefix(got, "invalid JSON:") {
		t.Errorf("syntax errors should be prefixed with 'invalid JSON:'; got: %s", got)
	}
}

func TestSanitizeJSONUnmarshalError_PassthroughForUnrelated(t *testing.T) {
	t.Parallel()

	orig := fmt.Errorf("totally unrelated")
	cleaned := SanitizeJSONUnmarshalError(orig)
	if cleaned == nil || cleaned.Error() != "totally unrelated" {
		t.Errorf("non-JSON errors should pass through; got: %v", cleaned)
	}
}

func TestSanitizeJSONUnmarshalError_NilSafe(t *testing.T) {
	t.Parallel()
	if SanitizeJSONUnmarshalError(nil) != nil {
		t.Error("expected nil for nil input")
	}
}

// nestedPayload mirrors the dto.ClaudeMessage shape: a generic Content
// field where the desired type is `[]ClaudeMediaMessage`. Tests that the
// sanitizer doesn't leak slice-of-package-qualified-type names like
// `[]dto.ClaudeMediaMessage` to API clients.
type contentBlock struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

func TestSanitizeJSONUnmarshalError_SliceOfStruct_NoPackageLeak(t *testing.T) {
	t.Parallel()
	// Force the stdlib to produce
	//   "json: cannot unmarshal number into Go value of type []common.contentBlock"
	rawErr := json.Unmarshal([]byte(`42`), new([]contentBlock))
	if rawErr == nil {
		t.Fatal("expected an error")
	}
	cleaned := SanitizeJSONUnmarshalError(rawErr)
	got := cleaned.Error()
	if strings.Contains(got, "contentBlock") {
		t.Errorf("package-qualified type leaked: %s", got)
	}
	if strings.Contains(got, "common.") {
		t.Errorf("package prefix leaked: %s", got)
	}
	if !strings.Contains(got, "array") {
		t.Errorf("expected slice to surface as 'array'; got: %s", got)
	}
}

func TestSanitizeJSONUnmarshalError_NestedStructField_NoPackageLeak(t *testing.T) {
	t.Parallel()
	// Force "json: cannot unmarshal number into Go struct field
	// contentBlock.text of type string".
	rawErr := json.Unmarshal([]byte(`[{"type":"text","text":42}]`), new([]contentBlock))
	if rawErr == nil {
		t.Fatal("expected an error")
	}
	cleaned := SanitizeJSONUnmarshalError(rawErr)
	got := cleaned.Error()
	if strings.Contains(got, "Go struct field") {
		t.Errorf("Go struct path leaked: %s", got)
	}
	if !strings.Contains(got, `"text"`) {
		t.Errorf("expected the json tag 'text' to survive; got: %s", got)
	}
}
