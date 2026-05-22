// Copyright 2026 TraceNex Partner OVERLAY
//
// internal_api_key: HMAC keystore for /api/internal/* — partner-api 持有 (key_id, secret)
// 用 HMAC-SHA256 签名调用，Fy-api 这一侧通过本表查 secret 验签。
//
// 落库的 secret 必须用 common.CryptoSecret 作为 KEK 做 AES-GCM 加密；
// 明文永不入库（Round-2 §11.5 / Security 红线）。
package model

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"time"

	"github.com/QuantumNous/new-api/common"
)

const (
	InternalAPIKeyStatusEnabled  = 1
	InternalAPIKeyStatusDisabled = 0
)

// InternalAPIKey 落 fy_api_db.internal_api_key 表（Phase 1 = 主 DB）。
type InternalAPIKey struct {
	Id               int    `json:"id" gorm:"primaryKey;autoIncrement"`
	KeyId            string `json:"key_id" gorm:"type:varchar(64);uniqueIndex;not null"`
	SecretCipher     string `json:"-" gorm:"type:text;not null;column:secret_cipher"`
	Region           string `json:"region" gorm:"type:varchar(8);not null;default:'cn';index"`
	Status           int    `json:"status" gorm:"type:int;default:1;index"`
	AllowedEndpoints string `json:"allowed_endpoints" gorm:"type:text"` // JSON-encoded []string，空 = 全部
	Remark           string `json:"remark" gorm:"type:varchar(255)"`
	CreatedAt        int64  `json:"created_at" gorm:"autoCreateTime;column:created_at"`
	RotatedAt        int64  `json:"rotated_at" gorm:"column:rotated_at;default:0"`
}

// TableName 显式声明，避免 gorm pluralize 出 internal_api_keys 与文档不一致。
func (InternalAPIKey) TableName() string {
	return "internal_api_key"
}

// LookupInternalAPIKey 按 key_id 查记录；未找到 / 已禁用都返回 ErrInvalidInternalAPIKey。
func LookupInternalAPIKey(keyId string) (*InternalAPIKey, error) {
	if keyId == "" {
		return nil, ErrInvalidInternalAPIKey
	}
	row := &InternalAPIKey{}
	err := DB.Where("key_id = ?", keyId).First(row).Error
	if err != nil {
		return nil, ErrInvalidInternalAPIKey
	}
	if row.Status != InternalAPIKeyStatusEnabled {
		return nil, ErrInvalidInternalAPIKey
	}
	return row, nil
}

// DecryptSecret 用 CryptoSecret 派生的 32 字节 AES key 解密 secret_cipher。
func (k *InternalAPIKey) DecryptSecret() ([]byte, error) {
	if k == nil || k.SecretCipher == "" {
		return nil, errors.New("internal api key cipher empty")
	}
	plain, err := decryptAESGCM(k.SecretCipher, deriveKEK(common.CryptoSecret))
	if err != nil {
		return nil, fmt.Errorf("decrypt internal api key: %w", err)
	}
	return plain, nil
}

// CreateInternalAPIKey 由 admin 接口调用；调用方传入明文 secret。
func CreateInternalAPIKey(keyId, region, allowedEndpointsJSON, remark string, secret []byte) (*InternalAPIKey, error) {
	if keyId == "" || len(secret) < 32 {
		return nil, errors.New("key_id required and secret must be >= 32 bytes")
	}
	cipherText, err := encryptAESGCM(secret, deriveKEK(common.CryptoSecret))
	if err != nil {
		return nil, fmt.Errorf("encrypt internal api key: %w", err)
	}
	k := &InternalAPIKey{
		KeyId:            keyId,
		SecretCipher:     cipherText,
		Region:           region,
		Status:           InternalAPIKeyStatusEnabled,
		AllowedEndpoints: allowedEndpointsJSON,
		Remark:           remark,
		RotatedAt:        time.Now().Unix(),
	}
	if err := DB.Create(k).Error; err != nil {
		return nil, fmt.Errorf("insert internal api key: %w", err)
	}
	return k, nil
}

// ErrInvalidInternalAPIKey 对外只回单一错（避免 key 探测）。
var ErrInvalidInternalAPIKey = errors.New("invalid internal api key")

// deriveKEK 用 sha256(CryptoSecret || "tnbiz/internal-api/v1") 派生 32 字节 AES key。
func deriveKEK(seed string) []byte {
	h := sha256.New()
	h.Write([]byte(seed))
	h.Write([]byte("tnbiz/internal-api/v1"))
	return h.Sum(nil)
}

func encryptAESGCM(plain, key []byte) (string, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return "", fmt.Errorf("aes new cipher: %w", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", fmt.Errorf("aes gcm: %w", err)
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err := io.ReadFull(rand.Reader, nonce); err != nil {
		return "", fmt.Errorf("read nonce: %w", err)
	}
	out := gcm.Seal(nonce, nonce, plain, nil)
	return base64.StdEncoding.EncodeToString(out), nil
}

func decryptAESGCM(cipherText string, key []byte) ([]byte, error) {
	raw, err := base64.StdEncoding.DecodeString(cipherText)
	if err != nil {
		return nil, fmt.Errorf("base64 decode: %w", err)
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, fmt.Errorf("aes new cipher: %w", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, fmt.Errorf("aes gcm: %w", err)
	}
	if len(raw) < gcm.NonceSize() {
		return nil, errors.New("cipher too short")
	}
	nonce, body := raw[:gcm.NonceSize()], raw[gcm.NonceSize():]
	plain, err := gcm.Open(nil, nonce, body, nil)
	if err != nil {
		return nil, fmt.Errorf("gcm open: %w", err)
	}
	return plain, nil
}
