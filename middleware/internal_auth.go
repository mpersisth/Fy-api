// Copyright 2026 TraceNex Partner OVERLAY
//
// HMAC-SHA256 鉴权中间件，用于 /api/internal/* 路由组。
// 契约：integration-design §2 / §1.1.3。
//
//	header X-Tnb-Key-Id      非空
//	header X-Tnb-Timestamp   unix seconds，clock skew ≤ 5min
//	header X-Tnb-Nonce       UUID-like 字符串；24h Redis SETNX 防重放
//	header X-Tnb-Signature   HMAC-SHA256 hex(secret, canonical)
//	canonical = METHOD\nPATH\nTIMESTAMP\nNONCE\nKEY_ID\nsha256(body)
//
// 失败统一 401 / 403，**绝不**回显 key_id 是否存在（防探测）。
package middleware

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/setting/overlay_flag"

	"github.com/gin-gonic/gin"
)

const (
	hmacHeaderKeyId     = "X-Tnb-Key-Id"
	hmacHeaderTimestamp = "X-Tnb-Timestamp"
	hmacHeaderNonce     = "X-Tnb-Nonce"
	hmacHeaderSignature = "X-Tnb-Signature"

	hmacClockSkew    = 5 * time.Minute
	hmacNonceTTL     = 24 * time.Hour
	hmacMaxBodyBytes = 1 << 20 // 1MB cap on request body for signing
)

// ContextKeyInternalKeyId 在 controller 里取已通过校验的 key_id。
const ContextKeyInternalKeyId = "tnbiz_internal_key_id"

// InternalAuth 返回鉴权 middleware；未启用 flag 时直接 503，确保即便误挂路由也不裸奔。
func InternalAuth() gin.HandlerFunc {
	return func(c *gin.Context) {
		if !overlay_flag.IsInternalAPIEnabled() || !overlay_flag.IsHMACKeystoreEnabled() {
			c.AbortWithStatusJSON(http.StatusServiceUnavailable, gin.H{
				"error": "internal api disabled",
			})
			return
		}
		if err := verifyHMAC(c); err != nil {
			// 不区分 401 / 403，避免攻击者枚举 key_id；只在 server log 留细节。
			common.SysLog("internal HMAC verify failed: " + err.Error())
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{
				"error": "unauthorized",
			})
			return
		}
		c.Next()
	}
}

// verifyHMAC 把校验逻辑独立出来，便于单测。
func verifyHMAC(c *gin.Context) error {
	keyId := c.GetHeader(hmacHeaderKeyId)
	tsStr := c.GetHeader(hmacHeaderTimestamp)
	nonce := c.GetHeader(hmacHeaderNonce)
	sig := c.GetHeader(hmacHeaderSignature)
	if keyId == "" || tsStr == "" || nonce == "" || sig == "" {
		return errors.New("missing hmac headers")
	}

	ts, err := strconv.ParseInt(tsStr, 10, 64)
	if err != nil {
		return errors.New("invalid timestamp")
	}
	now := time.Now().Unix()
	if abs64(now-ts) > int64(hmacClockSkew.Seconds()) {
		return errors.New("timestamp out of window")
	}

	if len(nonce) < 8 || len(nonce) > 128 {
		return errors.New("invalid nonce length")
	}

	// nonce SETNX 防重放（24h TTL）—— go-redis/v8 ctx-first
	if common.RedisEnabled && common.RDB != nil {
		ctx, cancel := context.WithTimeout(c.Request.Context(), 2*time.Second)
		defer cancel()
		ok, rerr := common.RDB.SetNX(ctx, "tnbiz:nonce:"+nonce, "1", hmacNonceTTL).Result()
		if rerr != nil {
			// fail-closed: redis 故障时拒绝，避免重放窗口被放开。
			return errors.New("nonce store unavailable")
		}
		if !ok {
			return errors.New("nonce reused")
		}
	}

	row, err := model.LookupInternalAPIKey(keyId)
	if err != nil {
		return err
	}
	secret, err := row.DecryptSecret()
	if err != nil {
		return err
	}

	// endpoint allowlist (case-sensitive 精确匹配，防前缀绕过)
	if row.AllowedEndpoints != "" {
		var allowed []string
		if jerr := common.UnmarshalJsonStr(row.AllowedEndpoints, &allowed); jerr != nil {
			return errors.New("allowed endpoints invalid")
		}
		path := c.Request.URL.Path
		match := false
		for _, p := range allowed {
			if p == path {
				match = true
				break
			}
		}
		if !match {
			return errors.New("endpoint not allowed for key")
		}
	}

	body, err := readAndRestoreBody(c)
	if err != nil {
		return err
	}
	bodyHash := sha256.Sum256(body)

	canonical := strings.Join([]string{
		strings.ToUpper(c.Request.Method),
		c.Request.URL.Path,
		tsStr,
		nonce,
		keyId,
		hex.EncodeToString(bodyHash[:]),
	}, "\n")

	expected := computeHMAC(secret, canonical)
	givenBytes, err := hex.DecodeString(sig)
	if err != nil {
		return errors.New("signature format")
	}
	if subtle.ConstantTimeCompare(expected, givenBytes) != 1 {
		return errors.New("signature mismatch")
	}

	// 暴露 key_id 给 controller（写 log / per-kid 限流 / idem）
	c.Set(ContextKeyInternalKeyId, keyId)
	return nil
}

// readAndRestoreBody 读出 body，再放回去给 controller 用（gin 默认 body 是 io.ReadCloser，单读）。
func readAndRestoreBody(c *gin.Context) ([]byte, error) {
	if c.Request.Body == nil {
		return []byte{}, nil
	}
	limited := io.LimitReader(c.Request.Body, hmacMaxBodyBytes+1)
	buf, err := io.ReadAll(limited)
	if err != nil {
		return nil, errors.New("read body")
	}
	if len(buf) > hmacMaxBodyBytes {
		return nil, errors.New("body too large")
	}
	c.Request.Body = io.NopCloser(bytes.NewReader(buf))
	return buf, nil
}

func computeHMAC(secret []byte, msg string) []byte {
	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(msg))
	return mac.Sum(nil)
}

func abs64(x int64) int64 {
	if x < 0 {
		return -x
	}
	return x
}
