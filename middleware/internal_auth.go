// Copyright 2026 TraceNex Partner OVERLAY
//
// HMAC-SHA256 鉴权中间件，用于 /api/internal/* 路由组。
// 契约：integration-design v1.2 §1.1.3（权威）。
//
//	header X-Auth-KeyId      非空（key_id）
//	header X-Auth-Timestamp  unix epoch seconds，clock skew ≤ 5min
//	header X-Auth-Nonce      UUIDv4；5min Redis SETNX 防重放
//	header X-Signature       base64( HMAC-SHA256(secret, canonical) )
//
//	canonical = METHOD + "\n" + PATH + "\n" + canonical_query +
//	            "\n" + ts(int) + "\n" + nonce + "\n" + sha256_hex(body)
//
// 失败统一 401，server log 留细节，不回显 key_id（防探测）。
package middleware

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/setting/overlay_flag"

	"github.com/gin-gonic/gin"
)

// 与 integration-design v1.2 §1.1.3 一致的 header 命名。
const (
	HeaderAuthKeyId     = "X-Auth-KeyId"
	HeaderAuthTimestamp = "X-Auth-Timestamp"
	HeaderAuthNonce     = "X-Auth-Nonce"
	HeaderSignature     = "X-Signature"
	HeaderTraceID       = "X-Oneapi-Request-Id"

	hmacClockSkew    = 5 * time.Minute
	hmacNonceTTL     = 5 * time.Minute // §1.1.3 NonceTTL
	hmacMaxBodyBytes = 1 << 20         // 1MB cap on request body for signing
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
	keyId := c.GetHeader(HeaderAuthKeyId)
	tsStr := c.GetHeader(HeaderAuthTimestamp)
	nonce := c.GetHeader(HeaderAuthNonce)
	sig := c.GetHeader(HeaderSignature)
	if keyId == "" || tsStr == "" || nonce == "" || sig == "" {
		return errors.New("missing hmac headers")
	}

	ts, err := parseTimestamp(tsStr)
	if err != nil {
		return err
	}
	now := time.Now().Unix()
	if abs64(now-ts) > int64(hmacClockSkew.Seconds()) {
		return errors.New("timestamp out of window")
	}

	if len(nonce) < 8 || len(nonce) > 128 {
		return errors.New("invalid nonce length")
	}

	// nonce SETNX 防重放（5min TTL，§1.1.3）—— go-redis/v8 ctx-first
	if common.RedisEnabled && common.RDB != nil {
		ctx, cancel := context.WithTimeout(c.Request.Context(), 2*time.Second)
		defer cancel()
		nonceKey := "internal:nonce:" + keyId + ":" + nonce
		ok, rerr := common.RDB.SetNX(ctx, nonceKey, "1", hmacNonceTTL).Result()
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

	canonical := BuildCanonical(
		c.Request.Method,
		c.Request.URL.Path,
		c.Request.URL.RawQuery,
		strconv.FormatInt(ts, 10),
		nonce,
		hex.EncodeToString(bodyHash[:]),
	)

	expected := computeHMAC(secret, canonical)
	expectedB64 := base64.StdEncoding.EncodeToString(expected)
	if !hmac.Equal([]byte(expectedB64), []byte(sig)) {
		return errors.New("signature mismatch")
	}

	// 暴露 key_id 给 controller（写 log / per-kid 限流 / idem）
	c.Set(ContextKeyInternalKeyId, keyId)
	if tid := c.GetHeader(HeaderTraceID); tid != "" {
		c.Set("trace_id", tid)
	}
	return nil
}

// BuildCanonical 构造 6 段 canonical 串（与 partner-api client 保持完全一致）。
//
//	canonical = METHOD\nPATH\ncanonical_query\nTS\nNONCE\nSHA256_HEX(body)
//
// METHOD 强制 uppercase；canonical_query 按 key 字典序，每对 RFC3986 编码。
func BuildCanonical(method, path, rawQuery, ts, nonce, bodyHashHex string) string {
	return strings.Join([]string{
		strings.ToUpper(method),
		path,
		canonicalQuery(rawQuery),
		ts,
		nonce,
		bodyHashHex,
	}, "\n")
}

// canonicalQuery 把 raw query 排序为字典序 + RFC3986 编码（§1.1.3 canonical_query）。
func canonicalQuery(raw string) string {
	if raw == "" {
		return ""
	}
	values, err := url.ParseQuery(raw)
	if err != nil {
		return ""
	}
	keys := make([]string, 0, len(values))
	for k := range values {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(values))
	for _, k := range keys {
		vs := values[k]
		sort.Strings(vs)
		ek := url.QueryEscape(k)
		for _, v := range vs {
			parts = append(parts, ek+"="+url.QueryEscape(v))
		}
	}
	return strings.Join(parts, "&")
}

// parseTimestamp 优先 unix epoch seconds；回落 RFC3339（§1.1.3 备注 LOW-r2-1）。
func parseTimestamp(raw string) (int64, error) {
	if v, err := strconv.ParseInt(raw, 10, 64); err == nil {
		return v, nil
	}
	if t, err := time.Parse(time.RFC3339, raw); err == nil {
		return t.Unix(), nil
	}
	return 0, errors.New("invalid timestamp")
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
