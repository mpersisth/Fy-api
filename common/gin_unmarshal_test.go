package common

import (
	"bytes"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

// Verify that UnmarshalBodyReusable surfaces a sanitized error to callers.
// This is the actual path used by relay handlers and therefore the path
// that ultimately controls what clients see in the HTTP response body.
func TestUnmarshalBodyReusable_SanitizesStdlibJSONErrors(t *testing.T) {
	gin.SetMode(gin.TestMode)

	type req struct {
		MaxTokens *uint `json:"max_tokens,omitempty"`
	}

	body := `{"max_tokens":"abc"}`
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(body))
	c.Request.Header.Set("Content-Type", "application/json")

	var r req
	err := UnmarshalBodyReusable(c, &r)
	if err == nil {
		t.Fatal("expected error from invalid JSON type")
	}
	got := err.Error()
	if strings.Contains(got, "Go struct field") {
		t.Errorf("Go struct path leaked back to caller: %s", got)
	}
	if !strings.Contains(got, "max_tokens") {
		t.Errorf("expected field name to survive sanitization; got: %s", got)
	}
	if !strings.Contains(got, "non-negative integer") &&
		!strings.Contains(got, "expected") {
		t.Errorf("expected friendly type description; got: %s", got)
	}
}
