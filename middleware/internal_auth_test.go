// Copyright 2026 TraceNex Partner OVERLAY
package middleware

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"strings"
	"testing"
)

func TestComputeHMACDeterministic(t *testing.T) {
	secret := []byte("test-secret-32-bytes!!!!!!!!!!!!!!")
	msg := BuildCanonical("GET", "/api/internal/health", "", "123", "nonce-abcd1234",
		hex.EncodeToString(sha256.New().Sum(nil)))

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

// TestCanonicalIncludesAllFields 覆盖 §1.1.3 6 段 canonical 串。
func TestCanonicalIncludesAllFields(t *testing.T) {
	canonical := BuildCanonical("post", "/api/internal/x", "", "123", "n1", "deadbeef")
	parts := strings.Split(canonical, "\n")
	if len(parts) != 6 {
		t.Fatalf("canonical must have 6 segments (got %d)", len(parts))
	}
	if parts[0] != "POST" {
		t.Errorf("method must be uppercased, got %q", parts[0])
	}
}

// TestCanonicalQuerySorted 验证 query 段字典序 + RFC3986 编码（§1.1.3 canonical_query）。
func TestCanonicalQuerySorted(t *testing.T) {
	got := canonicalQuery("z=2&a=1&a=0&b=hello+world")
	want := "a=0&a=1&b=hello+world&z=2"
	if got != want {
		t.Errorf("canonicalQuery=%q want %q", got, want)
	}
}

// TestParseTimestamp 验证 unix epoch 与 RFC3339 双格式（§1.1.3 备注 LOW-r2-1）。
func TestParseTimestamp(t *testing.T) {
	if v, err := parseTimestamp("1700000000"); err != nil || v != 1700000000 {
		t.Errorf("parseTimestamp(unix) v=%d err=%v", v, err)
	}
	if v, err := parseTimestamp("2024-01-01T00:00:00Z"); err != nil || v != 1704067200 {
		t.Errorf("parseTimestamp(rfc3339) v=%d err=%v", v, err)
	}
	if _, err := parseTimestamp("not-a-time"); err == nil {
		t.Errorf("parseTimestamp must reject garbage")
	}
}

// TestSignVerifyRoundtrip 模拟 partner-api client 签 → middleware 同算法验签。
//
// 这是 CRIT-A1 的核心 invariant：partner-api client 与 Fy-api middleware 必须用
// 同一份 BuildCanonical / base64 编码，否则会出现 round-trip 401。
func TestSignVerifyRoundtrip(t *testing.T) {
	secret := []byte("shared-secret-bytes-32-len-min!")
	method, path, query := "POST", "/api/internal/user/topup", "x=1"
	body := []byte(`{"user_id":42,"quota":1000}`)
	ts, nonce := "1700000000", "11111111-2222-3333-4444-555555555555"

	bodyHash := sha256.Sum256(body)
	canonical := BuildCanonical(method, path, query, ts, nonce, hex.EncodeToString(bodyHash[:]))
	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(canonical))
	clientSig := base64.StdEncoding.EncodeToString(mac.Sum(nil))

	// server side: 同样 BuildCanonical + 同样 base64 → 应当相等
	serverMac := computeHMAC(secret, canonical)
	serverSig := base64.StdEncoding.EncodeToString(serverMac)
	if clientSig != serverSig {
		t.Fatalf("client/server sig mismatch:\nclient=%q\nserver=%q", clientSig, serverSig)
	}
}
