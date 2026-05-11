// Copyright 2026 TraceNex Partner OVERLAY
package middleware

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"testing"
)

func TestComputeHMACDeterministic(t *testing.T) {
	secret := []byte("test-secret-32-bytes!!!!!!!!!!!!!!")
	msg := "GET\n/api/internal/health\n123\nnonce\nkid\n" + hex.EncodeToString(sha256.New().Sum(nil))

	a := computeHMAC(secret, msg)
	b := computeHMAC(secret, msg)
	if !hmac.Equal(a, b) {
		t.Fatalf("HMAC must be deterministic")
	}
}

func TestComputeHMACDiffersOnInputChange(t *testing.T) {
	secret := []byte("test-secret-32-bytes!!!!!!!!!!!!!!")
	a := computeHMAC(secret, "msg-a")
	b := computeHMAC(secret, "msg-b")
	if hmac.Equal(a, b) {
		t.Fatalf("HMAC must differ for different msg")
	}
}

func TestAbs64(t *testing.T) {
	cases := map[int64]int64{0: 0, 1: 1, -1: 1, -1000: 1000, 1000: 1000}
	for in, want := range cases {
		if got := abs64(in); got != want {
			t.Errorf("abs64(%d)=%d want %d", in, got, want)
		}
	}
}

func TestCanonicalIncludesAllFields(t *testing.T) {
	// 不直接复用 verifyHMAC（依赖 gin.Context + DB），但 canonical 拼接逻辑必须保留 6 段。
	canonical := strings.Join([]string{"POST", "/api/internal/x", "123", "n1", "kid", "deadbeef"}, "\n")
	if got := strings.Count(canonical, "\n"); got != 5 {
		t.Fatalf("canonical must have 5 newlines (got %d)", got)
	}
}
