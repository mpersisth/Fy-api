// Copyright 2026 TraceNex Partner OVERLAY
//
// log_outbox.go: B-16 RecordConsumeLog 的 TX wrap helper。
//
//	OUTBOX_TX flag = off  → 走原 LOG_DB.Create(log) 单语句（行为不变，向后兼容）
//	OUTBOX_TX flag = on   → 在 LOG_DB.Transaction 内同时 Create(log) + Create(outbox)
//	                        任一失败整批回滚（用户 quota 已扣 / log 缺失的失败模式
//	                        在 fy-api-review §6.2 已点出，配套 metric 见 §9.3）
//
// **注意**：本函数只在 LogTypeConsume 路径上调用；其他 LOG_DB.Create 调用站
// 一律不走 outbox（参见 model/log.go RecordConsumeLog 函数顶部注释）。
package model

import (
	"fmt"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/setting/overlay_flag"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

// dataRegion 当前从环境变量读，CN/SG ECS 启动时分别注入 cn / sg。
// 默认 cn（保守起见，给非 SG 部署兜底）。
func dataRegion() string {
	r := common.GetEnvOrDefaultString("DATA_REGION", "cn")
	if r != "sg" && r != "cn" {
		return "cn"
	}
	return r
}

// recordConsumeLogWithOutbox 在 OUTBOX_TX flag 启用时同事务写 outbox。
func recordConsumeLogWithOutbox(c *gin.Context, log *Log, userId int, params RecordConsumeLogParams) error {
	if !overlay_flag.IsOutboxTxEnabled() || overlay_flag.OutboxMode() == overlay_flag.OutboxOff {
		// flag off 或 outbox off → 行为不变，单语句 Create。
		return LOG_DB.Create(log).Error
	}
	return LOG_DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(log).Error; err != nil {
			return fmt.Errorf("create log: %w", err)
		}
		// outbox payload 用 common.Marshal 走仓库 JSON 包装。
		payload, err := common.Marshal(map[string]any{
			"log_id":            log.Id,
			"user_id":           userId,
			"channel_id":        params.ChannelId,
			"model_name":        params.ModelName,
			"quota":             params.Quota,
			"prompt_tokens":     params.PromptTokens,
			"completion_tokens": params.CompletionTokens,
			"request_id":        log.RequestId,
			"created_at":        log.CreatedAt,
		})
		if err != nil {
			return fmt.Errorf("marshal outbox payload: %w", err)
		}
		rec := &ConsumeLogOutbox{
			LogId:      log.Id,
			UserId:     userId,
			ChannelId:  params.ChannelId,
			ModelName:  params.ModelName,
			Quota:      params.Quota,
			DataRegion: dataRegion(),
			Status:     OutboxStatusPending,
			Payload:    string(payload),
		}
		return InsertOutboxInTx(tx, rec)
	})
}
