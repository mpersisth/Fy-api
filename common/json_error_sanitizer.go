// Fy-api overlay: stdlib JSON unmarshal errors leak Go struct paths
// (e.g. "json: cannot unmarshal string into Go struct field
// GeneralOpenAIRequest.max_tokens of type uint") which is both unfriendly
// to API clients and a minor information disclosure. This file converts
// those errors into stable, user-safe messages while preserving the JSON
// field name and the expected type semantics.
//
// Upstream new-api hands the raw stdlib error back to the client (see
// controller/relay.go top-level dispatcher). This sanitizer is wired into
// common.UnmarshalBodyReusable so every relay path benefits.
package common

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
)

// goStructFieldPattern matches the full Go-path tail emitted by encoding/json:
//
//	"... Go struct field <Type>.<json_tag> of type <go_type>"
//
// We keep <json_tag> (the field as the API client sees it) and the go_type,
// and drop the <Type> prefix. Group 1 is the json tag, group 2 is the go type.
var goStructFieldPattern = regexp.MustCompile(
	`\s*Go struct field (?:[A-Za-z0-9_]+\.)*([A-Za-z0-9_]+) of type ([A-Za-z0-9_*\[\]]+)`,
)

// jsonTypeFriendly maps Go reflect type kinds back to JSON type names that
// API clients actually understand.
var jsonTypeFriendly = map[string]string{
	"int":     "integer",
	"int8":    "integer",
	"int16":   "integer",
	"int32":   "integer",
	"int64":   "integer",
	"uint":    "non-negative integer",
	"uint8":   "non-negative integer",
	"uint16":  "non-negative integer",
	"uint32":  "non-negative integer",
	"uint64":  "non-negative integer",
	"float32": "number",
	"float64": "number",
	"bool":    "boolean",
	"string":  "string",
}

// SanitizeJSONUnmarshalError converts a stdlib encoding/json error into a
// user-safe message. Returns the original error untouched if it isn't a
// recognised JSON error.
//
// Examples:
//
//	`json: cannot unmarshal string into Go struct field GeneralOpenAIRequest.max_tokens of type uint`
//	→ `invalid type for field "max_tokens": expected non-negative integer, got string`
//
//	`json: cannot unmarshal number 1.5 into Go struct field GeneralOpenAIRequest.n of type int`
//	→ `invalid type for field "n": expected integer, got number`
//
//	`unexpected end of JSON input`
//	→ unchanged (already user-safe)
func SanitizeJSONUnmarshalError(err error) error {
	if err == nil {
		return nil
	}

	// 1) Typed UnmarshalTypeError — best case, we have structured fields.
	var typeErr *json.UnmarshalTypeError
	if errors.As(err, &typeErr) {
		field := typeErr.Field
		if field == "" {
			field = "(root)"
		}
		expected := goTypeToJSONType(typeErr.Type.String())
		return fmt.Errorf(
			`invalid type for field %q: expected %s, got %s`,
			field, expected, typeErr.Value,
		)
	}

	// 2) Syntax error — keep the offset hint, drop nothing (no Go path inside).
	var synErr *json.SyntaxError
	if errors.As(err, &synErr) {
		return fmt.Errorf("invalid JSON: %s (offset %d)", synErr.Error(), synErr.Offset)
	}

	// 3) String-match fallback for wrapped errors that arrive as
	//    plain `error` (e.g. when bytedance/sonic or another decoder
	//    re-wraps the message).
	msg := err.Error()
	if m := goStructFieldPattern.FindStringSubmatch(msg); m != nil {
		// m[1] = json tag, m[2] = go type
		jsonTag := m[1]
		expected := goTypeToJSONType(m[2])
		// Drop the matched Go-path tail and append a clean suffix.
		head := strings.TrimSpace(goStructFieldPattern.ReplaceAllString(msg, ""))
		return fmt.Errorf("%s field %q (expected %s)", head, jsonTag, expected)
	}

	return err
}

func goTypeToJSONType(goType string) string {
	if friendly, ok := jsonTypeFriendly[goType]; ok {
		return friendly
	}
	// Composite types like "[]string" or "*int" — strip pointer/slice
	// markers, then look up.
	clean := strings.TrimLeft(goType, "*[]")
	if friendly, ok := jsonTypeFriendly[clean]; ok {
		return friendly
	}
	return goType
}
