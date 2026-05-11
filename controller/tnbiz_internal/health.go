// Copyright 2026 TraceNex Partner OVERLAY
//
// Package internal 提供 /api/internal/* 内部 API 的 controllers。
// 全部端点契约见 integration-design §2 OpenAPI；HMAC 鉴权 + 幂等中间件
// 在 router/api-internal-router.go 统一挂载。
package tnbiz_internal

import (
	"net/http"

	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/setting/overlay_flag"

	"github.com/gin-gonic/gin"
)

// healthResponse 用于 GET /api/internal/health（HMAC 自检）。
type healthResponse struct {
	Status         string `json:"status"`
	OverlayInternal bool  `json:"overlay_internal_api"`
	OverlayHMAC    bool   `json:"overlay_hmac_keystore"`
	OverlayOutbox  string `json:"overlay_outbox"`
}

// Health 自检：通过 HMAC 后才会走到这里，所以 200 就证明鉴权链工作。
func Health(c *gin.Context) {
	c.JSON(http.StatusOK, healthResponse{
		Status:          "ok",
		OverlayInternal: overlay_flag.IsInternalAPIEnabled(),
		OverlayHMAC:     overlay_flag.IsHMACKeystoreEnabled(),
		OverlayOutbox:   overlay_flag.OutboxMode(),
	})
}

// respondJSON 统一封装：partner-api 期望 envelope { success, data, message }。
func respondJSON(c *gin.Context, status int, data any) {
	c.JSON(status, gin.H{
		"success": status >= 200 && status < 300,
		"data":    data,
	})
}

func respondError(c *gin.Context, status int, code, msg string) {
	c.JSON(status, gin.H{
		"success": false,
		"error": gin.H{
			"code":    code,
			"message": msg,
		},
	})
}

// userExists 工具：partner 调用时常需要 user 实在。
func userExists(userId int) (*model.User, bool) {
	u, err := model.GetUserById(userId, false)
	if err != nil {
		return nil, false
	}
	return u, true
}
