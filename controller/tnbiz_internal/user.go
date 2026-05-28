// Copyright 2026 TraceNex Partner OVERLAY
package tnbiz_internal

import (
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"

	"github.com/gin-gonic/gin"
)

// TopupRequest: partner 替 customer 充值额度（绝对增量，正数才允许）。
type TopupRequest struct {
	UserId int    `json:"user_id" binding:"required"`
	Quota  int    `json:"quota" binding:"required"`
	Reason string `json:"reason"`
}

// TopupResponse 返回最新余额。
type TopupResponse struct {
	UserId    int `json:"user_id"`
	NewQuota  int `json:"new_quota"`
	UsedQuota int `json:"used_quota"`
}

// Topup 实现 POST /api/internal/user/topup。
func Topup(c *gin.Context) {
	var req TopupRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		respondError(c, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	if req.Quota <= 0 {
		respondError(c, http.StatusBadRequest, "invalid_request", "quota must be > 0; use /quota/adjust for negatives")
		return
	}
	user, ok := userExists(req.UserId)
	if !ok {
		respondError(c, http.StatusNotFound, "user_not_found", "user_id does not exist")
		return
	}
	if err := model.IncreaseUserQuota(req.UserId, req.Quota, true); err != nil {
		respondError(c, http.StatusInternalServerError, "topup_failed", err.Error())
		return
	}
	// re-fetch 拿到 fresh quota（cache invalidation 由 model 层负责）。
	if u, err := model.GetUserById(req.UserId, false); err == nil {
		user = u
	}
	resp := TopupResponse{
		UserId:    user.Id,
		NewQuota:  user.Quota,
		UsedQuota: user.UsedQuota,
	}
	respondJSON(c, http.StatusOK, resp)
	persistIdem(c, http.StatusOK, resp)
}

// QuotaResponse 用于 GET /api/internal/user/quota。
type QuotaResponse struct {
	UserId    int `json:"user_id"`
	Quota     int `json:"quota"`
	UsedQuota int `json:"used_quota"`
	AffQuota  int `json:"aff_quota"`
}

// GetQuota 实现 GET /api/internal/user/quota?user_id=123。
func GetQuota(c *gin.Context) {
	uidStr := c.Query("user_id")
	uid, err := strconv.Atoi(uidStr)
	if err != nil || uid <= 0 {
		respondError(c, http.StatusBadRequest, "invalid_request", "user_id query param required")
		return
	}
	user, ok := userExists(uid)
	if !ok {
		respondError(c, http.StatusNotFound, "user_not_found", "user_id does not exist")
		return
	}
	respondJSON(c, http.StatusOK, QuotaResponse{
		UserId:    user.Id,
		Quota:     user.Quota,
		UsedQuota: user.UsedQuota,
		AffQuota:  user.AffQuota,
	})
}

// AdjustQuotaRequest: 增 / 减绝对值；负数代表扣减（saga compensate 路径）。
type AdjustQuotaRequest struct {
	UserId int    `json:"user_id" binding:"required"`
	Delta  int    `json:"delta" binding:"required"`
	Reason string `json:"reason"`
	SagaId string `json:"saga_id"`
}

// AdjustQuota 实现 POST /api/internal/user/quota/adjust。
func AdjustQuota(c *gin.Context) {
	var req AdjustQuotaRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		respondError(c, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	if req.Delta == 0 {
		respondError(c, http.StatusBadRequest, "invalid_request", "delta must be non-zero")
		return
	}
	if _, ok := userExists(req.UserId); !ok {
		respondError(c, http.StatusNotFound, "user_not_found", "user_id does not exist")
		return
	}
	var err error
	if req.Delta > 0 {
		err = model.IncreaseUserQuota(req.UserId, req.Delta, true)
	} else {
		err = model.DecreaseUserQuota(req.UserId, -req.Delta, true)
	}
	if err != nil {
		respondError(c, http.StatusInternalServerError, "adjust_failed", fmt.Errorf("adjust: %w", err).Error())
		return
	}
	user, _ := model.GetUserById(req.UserId, false)
	if user == nil {
		respondError(c, http.StatusInternalServerError, "post_adjust_lookup", "user vanished after adjust")
		return
	}
	common.SysLog(fmt.Sprintf("internal_adjust uid=%d delta=%d saga=%s reason=%s", req.UserId, req.Delta, req.SagaId, req.Reason))
	resp := TopupResponse{
		UserId:    user.Id,
		NewQuota:  user.Quota,
		UsedQuota: user.UsedQuota,
	}
	respondJSON(c, http.StatusOK, resp)
	persistIdem(c, http.StatusOK, resp)
}

// RefundRequest 退款 saga：对一段已扣 quota 的反向补偿。
type RefundRequest struct {
	UserId   int    `json:"user_id" binding:"required"`
	Quota    int    `json:"quota" binding:"required"`
	OrderRef string `json:"order_ref"`
	SagaId   string `json:"saga_id"`
}

// Refund 实现 POST /api/internal/user/refund。
func Refund(c *gin.Context) {
	var req RefundRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		respondError(c, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	if req.Quota <= 0 {
		respondError(c, http.StatusBadRequest, "invalid_request", "quota must be > 0")
		return
	}
	if _, ok := userExists(req.UserId); !ok {
		respondError(c, http.StatusNotFound, "user_not_found", "user_id does not exist")
		return
	}
	if err := model.IncreaseUserQuota(req.UserId, req.Quota, true); err != nil {
		respondError(c, http.StatusInternalServerError, "refund_failed", err.Error())
		return
	}
	common.SysLog(fmt.Sprintf("internal_refund uid=%d quota=%d order=%s saga=%s", req.UserId, req.Quota, req.OrderRef, req.SagaId))
	user, err := model.GetUserById(req.UserId, false)
	if err != nil {
		respondError(c, http.StatusInternalServerError, "post_refund_lookup", err.Error())
		return
	}
	resp := TopupResponse{
		UserId:    user.Id,
		NewQuota:  user.Quota,
		UsedQuota: user.UsedQuota,
	}
	respondJSON(c, http.StatusOK, resp)
	persistIdem(c, http.StatusOK, resp)
}

// UpdateGroupRequest：客户切换渠道商后，同步更新 Fy-api 用户分组。
type UpdateGroupRequest struct {
	UserId int    `json:"user_id" binding:"required"`
	Group  string `json:"group" binding:"required"`
	Reason string `json:"reason"`
}

// UpdateGroup 实现 PUT /api/internal/user/group。
func UpdateGroup(c *gin.Context) {
	var req UpdateGroupRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		respondError(c, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	req.Group = strings.TrimSpace(req.Group)
	if req.UserId <= 0 || req.Group == "" {
		respondError(c, http.StatusBadRequest, "invalid_request", "user_id 和 group 必填")
		return
	}
	user, ok := userExists(req.UserId)
	if !ok {
		respondError(c, http.StatusNotFound, "user_not_found", "用户不存在")
		return
	}
	if user.Group == req.Group {
		resp := gin.H{"user_id": user.Id, "group": user.Group}
		respondJSON(c, http.StatusOK, resp)
		persistIdem(c, http.StatusOK, resp)
		return
	}
	if err := model.DB.Model(&model.User{}).Where("id = ?", req.UserId).Update("group", req.Group).Error; err != nil {
		respondError(c, http.StatusInternalServerError, "update_group_failed", err.Error())
		return
	}
	if common.RedisEnabled && common.RDB != nil {
		if err := model.UpdateUserGroupCache(req.UserId, req.Group); err != nil {
			common.SysLog("internal_update_group cache failed: " + err.Error())
		}
	}
	common.SysLog(fmt.Sprintf("internal_update_group uid=%d old_group=%s new_group=%s reason=%s", req.UserId, user.Group, req.Group, req.Reason))
	resp := gin.H{"user_id": req.UserId, "group": req.Group}
	respondJSON(c, http.StatusOK, resp)
	persistIdem(c, http.StatusOK, resp)
}

// EraseUserRequest：PIPL 删除 / 去标识化路径。
type EraseUserRequest struct {
	UserId int    `json:"user_id" binding:"required"`
	Reason string `json:"reason"`
}

// EraseUser 实现 POST /api/internal/user/erase。
//
// 这里使用 Fy-api 现有软删除能力：用户不可再登录和调用，但历史用量、账单、
// 审计日志保留，用于对账和合规留痕。TraceNexBiz 侧负责把自己的客户资料做
// 删除 / 去标识化处理。
func EraseUser(c *gin.Context) {
	var req EraseUserRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		respondError(c, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	if req.UserId <= 0 {
		respondError(c, http.StatusBadRequest, "invalid_request", "user_id 必填")
		return
	}
	if _, ok := userExists(req.UserId); !ok {
		respondError(c, http.StatusNotFound, "user_not_found", "用户不存在或已删除")
		return
	}
	if err := model.DeleteUserById(req.UserId); err != nil {
		respondError(c, http.StatusInternalServerError, "erase_failed", err.Error())
		return
	}
	common.SysLog(fmt.Sprintf("internal_erase_user uid=%d reason=%s", req.UserId, req.Reason))
	resp := gin.H{"user_id": req.UserId, "erased": true}
	respondJSON(c, http.StatusOK, resp)
	persistIdem(c, http.StatusOK, resp)
}

func persistIdem(c *gin.Context, status int, payload any) {
	body, err := common.Marshal(gin.H{"success": true, "data": payload})
	if err != nil {
		return
	}
	if err := middleware.SaveIdempotencyResponse(c, status, string(body)); err != nil && !errors.Is(err, model.ErrIdempotencyConflict) {
		common.SysLog("save idempotency failed: " + err.Error())
	}
}
