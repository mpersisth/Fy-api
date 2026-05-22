// Copyright 2026 TraceNex Partner OVERLAY
package tnbiz_internal

import (
	"net/http"
	"strings"

	"github.com/QuantumNous/new-api/model"

	"github.com/gin-gonic/gin"
)

// UpsertGroupRatioOverrideRequest matches OpenAPI §2 spec.
type UpsertGroupRatioOverrideRequest struct {
	PartnerKid string  `json:"partner_kid"`
	UserId     int     `json:"user_id"`
	Group      string  `json:"group" binding:"required"`
	Ratio      float64 `json:"ratio" binding:"required"`
}

// UpsertGroupRatioOverride 实现 POST /api/internal/group_ratio_override/upsert。
func UpsertGroupRatioOverride(c *gin.Context) {
	var req UpsertGroupRatioOverrideRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		respondError(c, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	if strings.TrimSpace(req.Group) == "" || req.Ratio <= 0 {
		respondError(c, http.StatusBadRequest, "invalid_request", "group and positive ratio required")
		return
	}
	// partner_kid 缺省由鉴权后的 key_id 注入。
	if req.PartnerKid == "" {
		req.PartnerKid = c.GetString("tnbiz_internal_key_id")
	}
	rec := &model.GroupRatioOverride{
		PartnerKid: req.PartnerKid,
		UserId:     req.UserId,
		Group:      req.Group,
		Ratio:      req.Ratio,
		Status:     1,
	}
	if err := model.UpsertGroupRatioOverride(rec); err != nil {
		respondError(c, http.StatusInternalServerError, "upsert_failed", err.Error())
		return
	}
	respondJSON(c, http.StatusOK, gin.H{
		"partner_kid": rec.PartnerKid,
		"user_id":     rec.UserId,
		"group":       rec.Group,
		"ratio":       rec.Ratio,
	})
}

// UpsertChannelLogSettingsRequest matches OpenAPI §2 spec.
type UpsertChannelLogSettingsRequest struct {
	PartnerKid string `json:"partner_kid"`
	ChannelId  int    `json:"channel_id" binding:"required"`
	Settings   string `json:"settings"`
}

// UpsertChannelLogSettings 实现 POST /api/internal/channel_log_settings/upsert。
func UpsertChannelLogSettings(c *gin.Context) {
	var req UpsertChannelLogSettingsRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		respondError(c, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	if req.ChannelId <= 0 {
		respondError(c, http.StatusBadRequest, "invalid_request", "channel_id required")
		return
	}
	if req.PartnerKid == "" {
		req.PartnerKid = c.GetString("tnbiz_internal_key_id")
	}
	rec := &model.ChannelLogSetting{
		PartnerKid: req.PartnerKid,
		ChannelId:  req.ChannelId,
		Settings:   req.Settings,
	}
	if err := model.UpsertChannelLogSetting(rec); err != nil {
		respondError(c, http.StatusInternalServerError, "upsert_failed", err.Error())
		return
	}
	respondJSON(c, http.StatusOK, gin.H{
		"partner_kid": rec.PartnerKid,
		"channel_id":  rec.ChannelId,
	})
}
