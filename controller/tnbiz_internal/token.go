// Copyright 2026 TraceNex Partner OVERLAY
package tnbiz_internal

import (
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"

	"github.com/gin-gonic/gin"
)

// CreateTokenRequest: partner 替 customer 申领一个 sk-key。
// **partner 永远不可见明文** — 接口仅返回 token_id + masked_key + 一次性发放凭据。
type CreateTokenRequest struct {
	UserId         int      `json:"user_id" binding:"required"`
	Name           string   `json:"name" binding:"required"`
	Group          string   `json:"group"`
	UnlimitedQuota bool     `json:"unlimited_quota"`
	RemainQuota    int      `json:"remain_quota"`
	ExpiredAt      int64    `json:"expired_at"` // unix; -1 / 0 = never
	ModelLimits    []string `json:"model_limits"`
}

type CreateTokenResponse struct {
	TokenId   int    `json:"token_id"`
	MaskedKey string `json:"masked_key"`
	// DeliveryHandle 一次性凭据：partner 用此 handle 拉取明文 sk-key，仅可用 1 次 / 5 分钟。
	DeliveryHandle string `json:"delivery_handle"`
}

// CreateToken 实现 POST /api/internal/token/create。
func CreateToken(c *gin.Context) {
	var req CreateTokenRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		respondError(c, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	if req.UserId <= 0 || strings.TrimSpace(req.Name) == "" {
		respondError(c, http.StatusBadRequest, "invalid_request", "user_id and name required")
		return
	}
	if _, ok := userExists(req.UserId); !ok {
		respondError(c, http.StatusNotFound, "user_not_found", "user_id does not exist")
		return
	}

	plain, err := common.GenerateKey()
	if err != nil {
		respondError(c, http.StatusInternalServerError, "key_gen", err.Error())
		return
	}

	expired := req.ExpiredAt
	if expired == 0 {
		expired = -1
	}
	tok := &model.Token{
		UserId:         req.UserId,
		Key:            plain,
		Status:         1,
		Name:           req.Name,
		CreatedTime:    time.Now().Unix(),
		AccessedTime:   time.Now().Unix(),
		ExpiredTime:    expired,
		RemainQuota:    req.RemainQuota,
		UnlimitedQuota: req.UnlimitedQuota,
		Group:          req.Group,
	}
	if len(req.ModelLimits) > 0 {
		tok.ModelLimitsEnabled = true
		tok.ModelLimits = strings.Join(req.ModelLimits, ",")
	}
	if err := tok.Insert(); err != nil {
		respondError(c, http.StatusInternalServerError, "insert", err.Error())
		return
	}

	handle, err := stashPlaintextKey(c.Request.Context(), tok.Id, plain)
	if err != nil {
		// 即便 redis 不可用，token 已落库；返回 500 提示重新申领。
		respondError(c, http.StatusInternalServerError, "stash_key", err.Error())
		return
	}

	resp := CreateTokenResponse{
		TokenId:        tok.Id,
		MaskedKey:      model.MaskTokenKey(plain),
		DeliveryHandle: handle,
	}
	respondJSON(c, http.StatusOK, resp)

	if body, err := common.Marshal(gin.H{"success": true, "data": resp}); err == nil {
		_ = middleware.SaveIdempotencyResponse(c, http.StatusOK, string(body))
	}
}

// stashPlaintextKey 把 sk-key 短期暂存 redis，仅可用 1 次。
// partner 拿 handle 调 GET /api/internal/token/deliver/:handle 取明文（端点不在本 PR 范围，
// 由后续 PR 或人工运维流程 owner，此处只放 stash 接口，预留交付路径）。
func stashPlaintextKey(ctx interface{ Done() <-chan struct{} }, tokenId int, plain string) (string, error) {
	if !common.RedisEnabled || common.RDB == nil {
		return "", errors.New("redis required for one-shot key delivery")
	}
	handle := common.GetRandomString(48)
	c := contextOrBackground(ctx)
	if err := common.RDB.Set(c, "tnbiz:tokenkey:"+handle, plain, 5*time.Minute).Err(); err != nil {
		return "", err
	}
	return handle, nil
}
