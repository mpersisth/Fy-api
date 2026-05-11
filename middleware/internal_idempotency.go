// Copyright 2026 TraceNex Partner OVERLAY
//
// 内部 idempotency middleware：基于 Idempotency-Key 头 + auth_kid + endpoint 三元组。
// 命中已存记录 → 直接回放；未命中 → 走业务 → 业务后由 controller 显式 SaveIdempotency。
package middleware

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"

	"github.com/QuantumNous/new-api/model"

	"github.com/gin-gonic/gin"
)

const (
	hdrIdempotencyKey         = "Idempotency-Key"
	contextKeyIdempotencyKey  = "tnbiz_idem_key"
	contextKeyIdempotencyHash = "tnbiz_idem_hash"
)

// InternalIdempotency 检查 (auth_kid, idem_key, endpoint) 是否已处理；
// 命中则直接 replay；未命中放行并让 controller 后续调用 SaveIdempotencyResponse。
func InternalIdempotency() gin.HandlerFunc {
	return func(c *gin.Context) {
		idemKey := c.GetHeader(hdrIdempotencyKey)
		if idemKey == "" {
			// 内部 API 不强制要求 idempotency key（GET / 查询接口可省）；
			// 写接口的 controller 应自行强制（reject if empty）。
			c.Next()
			return
		}
		authKid := c.GetString(ContextKeyInternalKeyId)
		if authKid == "" {
			// 没鉴权过，不应该走到这里；保险拒绝。
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "auth required"})
			return
		}

		body, err := readAndCacheBody(c)
		if err != nil {
			c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": "read body"})
			return
		}
		hash := sha256.Sum256(body)
		hashHex := hex.EncodeToString(hash[:])
		c.Set(contextKeyIdempotencyKey, idemKey)
		c.Set(contextKeyIdempotencyHash, hashHex)

		existing, err := model.LookupIdempotency(authKid, idemKey, c.Request.URL.Path)
		if err != nil {
			c.AbortWithStatusJSON(http.StatusInternalServerError, gin.H{"error": "idempotency lookup failed"})
			return
		}
		if existing != nil {
			if existing.RequestHash != hashHex {
				// 同 key 不同 body：契约违反 → 409。
				c.AbortWithStatusJSON(http.StatusConflict, gin.H{
					"error": "idempotency key reused with different payload",
				})
				return
			}
			// 命中且 body 一致 → 直接回放。
			c.Status(existing.ResponseStatus)
			c.Header("Content-Type", "application/json")
			c.Header("X-Tnb-Idempotent-Replay", "1")
			_, _ = c.Writer.WriteString(existing.ResponseBody)
			c.Abort()
			return
		}

		c.Next()
	}
}

// SaveIdempotencyResponse 由 controller 在业务成功后调用。
func SaveIdempotencyResponse(c *gin.Context, status int, body string) error {
	idemKey, _ := c.Get(contextKeyIdempotencyKey)
	hash, _ := c.Get(contextKeyIdempotencyHash)
	authKid := c.GetString(ContextKeyInternalKeyId)
	if idemKey == nil || hash == nil || authKid == "" {
		return nil // 没 key 就跳过
	}
	rec := &model.InternalIdempotencyRecord{
		AuthKid:        authKid,
		IdempotencyKey: idemKey.(string),
		Endpoint:       c.Request.URL.Path,
		RequestHash:    hash.(string),
		ResponseStatus: status,
		ResponseBody:   body,
	}
	return model.SaveIdempotency(rec)
}

func readAndCacheBody(c *gin.Context) ([]byte, error) {
	if c.Request.Body == nil {
		return []byte{}, nil
	}
	buf, err := io.ReadAll(c.Request.Body)
	if err != nil {
		return nil, err
	}
	c.Request.Body = io.NopCloser(bytes.NewReader(buf))
	return buf, nil
}
