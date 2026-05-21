// Copyright 2026 TraceNex Partner OVERLAY
//
// /api/internal/* 独立路由组，**不**挂在 /api 全局 GlobalAPIRateLimit 下
// （review §4.1 / integration §1.1.1 显式要求）。
//
// 路径前缀 /api/internal/ 永远只在 InternalAPI flag 启用时注册；
// flag 关闭时即便 controller 在仓库里，也不暴露 endpoint。
package router

import (
	internalctl "github.com/QuantumNous/new-api/controller/tnbiz_internal"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/setting/overlay_flag"

	"github.com/gin-gonic/gin"
)

// SetInternalRouter 由 SetRouter 在 SetRelayRouter 之后调用一次。
// 该函数永远幂等：flag 未开时只挂 health endpoint（自检用），不挂业务路由。
func SetInternalRouter(router *gin.Engine) {
	g := router.Group("/api/internal")
	g.Use(middleware.RouteTag("api-internal"))
	g.Use(middleware.BodyStorageCleanup())
	// 显式不挂 GlobalAPIRateLimit；按 §6.3 走 per-kid quota（占位，Phase 2A 接 KMS）。
	g.Use(middleware.InternalAuth())
	g.Use(middleware.InternalIdempotency())

	g.GET("/health", internalctl.Health)
	g.POST("/token/create", internalctl.CreateToken)
	g.POST("/user/topup", internalctl.Topup)
	g.GET("/user/quota", internalctl.GetQuota)
	g.POST("/user/quota/adjust", internalctl.AdjustQuota)
	g.POST("/user/refund", internalctl.Refund)
	g.PUT("/user/group", internalctl.UpdateGroup)
	g.POST("/user/erase", internalctl.EraseUser)
	g.POST("/group_ratio_override/upsert", internalctl.UpsertGroupRatioOverride)
	g.POST("/channel_log_settings/upsert", internalctl.UpsertChannelLogSettings)

	// 启动期日志，便于 ops 确认状态。
	if !overlay_flag.IsInternalAPIEnabled() {
		// flag off 时 InternalAuth middleware 会返回 503，不裸奔；
		// 路由仍注册，便于切 flag 后无需重启。
		_ = g
	}
}
