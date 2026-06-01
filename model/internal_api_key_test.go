// Copyright 2026 TraceNex Partner OVERLAY
package model

import (
	"crypto/sha256"
	"strings"
	"testing"
)

func TestEncryptDecryptRoundtrip(t *testing.T) {
	key := sha256.Sum256([]byte("test-seed"))
	plain := []byte("super-secret-hmac-key-32-bytes-min!")
	cipher, err := encryptAESGCM(plain, key[:])
	if err != nil {
		t.Fatalf("encrypt: %v", err)
	}
	if strings.Contains(cipher, string(plain)) {
		t.Fatalf("plaintext leaked in cipher")
	}
	got, err := decryptAESGCM(cipher, key[:])
	if err != nil {
		t.Fatalf("decrypt: %v", err)
	}
	if string(got) != string(plain) {
		t.Fatalf("roundtrip mismatch: got %q want %q", got, plain)
	}
}

func TestDecryptWithWrongKey(t *testing.T) {
	k1 := sha256.Sum256([]byte("k1"))
	k2 := sha256.Sum256([]byte("k2"))
	plain := []byte("attacker-must-not-recover-this-secret")
	cipher, err := encryptAESGCM(plain, k1[:])
	if err != nil {
		t.Fatalf("encrypt: %v", err)
	}
	if _, err := decryptAESGCM(cipher, k2[:]); err == nil {
		t.Fatalf("expected error decrypting with wrong key")
	}
}

func TestDeriveKEKDeterministic(t *testing.T) {
	a := deriveKEK("seed")
	b := deriveKEK("seed")
	if string(a) != string(b) {
		t.Fatalf("kek derivation must be deterministic")
	}
	if len(a) != 32 {
		t.Fatalf("kek must be 32 bytes (got %d)", len(a))
	}
}

func TestDecryptCipherTooShort(t *testing.T) {
	k := sha256.Sum256([]byte("k"))
	if _, err := decryptAESGCM("AAAA", k[:]); err == nil {
		t.Fatalf("expected error for short cipher")
	}
}
