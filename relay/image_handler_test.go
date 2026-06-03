package relay

import (
	"errors"
	"net/http"
	"testing"

	"github.com/QuantumNous/new-api/types"
)

func TestImageConvertErrorModerationBlocked(t *testing.T) {
	t.Parallel()

	err := imageConvertError(errors.New("moderation_blocked: Tencent AIArt prompt violates violence policy"))
	if err == nil {
		t.Fatalf("imageConvertError returned nil")
	}
	if got, want := err.StatusCode, http.StatusBadRequest; got != want {
		t.Fatalf("StatusCode = %d, want %d", got, want)
	}
	openAIError := err.ToOpenAIError()
	if openAIError.Code != "moderation_blocked" {
		t.Fatalf("Code = %v, want moderation_blocked", openAIError.Code)
	}
	if openAIError.Type != "image_generation_user_error" {
		t.Fatalf("Type = %q, want image_generation_user_error", openAIError.Type)
	}
	if err.GetErrorCode() != types.ErrorCode("moderation_blocked") {
		t.Fatalf("error code = %q, want moderation_blocked", err.GetErrorCode())
	}
}
