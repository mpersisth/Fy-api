// Copyright 2026 TraceNex Partner OVERLAY
//
// consume_log_outbox: 计费事件 outbox 表（OVERLAY B-16/B-18）。
// 由 RecordConsumeLog 在同事务中写入；后台 publisher goroutine 把事件推到
// 阿里云 MNS（按 integration §1.6.3 选型）。
//
// data_region 字段是 region 隔离的硬约束：cn 的事件不可被 SG 消费者拉走，
// publisher 必须按 region 过滤（参 §6 cross-border guard）。
package model

import (
	"errors"
	"fmt"
	"time"

	"gorm.io/gorm"
)

const (
	OutboxStatusPending    = "pending"
	OutboxStatusInFlight   = "in_flight"
	OutboxStatusPublished  = "published"
	OutboxStatusFailed     = "failed"
	OutboxStatusDeadLetter = "dead_letter"

	OutboxRegionCN = "cn"
	OutboxRegionSG = "sg"

	OutboxMaxRetry = 10
)

// ConsumeLogOutbox 落 LOG_DB.consume_log_outbox（同 logs 表所在库，便于同事务）。
type ConsumeLogOutbox struct {
	Id           int64  `json:"id" gorm:"primaryKey;autoIncrement"`
	LogId        int    `json:"log_id" gorm:"type:int;not null;index"`
	UserId       int    `json:"user_id" gorm:"type:int;not null;index"`
	ChannelId    int    `json:"channel_id" gorm:"type:int"`
	ModelName    string `json:"model_name" gorm:"type:varchar(64)"`
	Quota        int    `json:"quota" gorm:"type:int"`
	DataRegion   string `json:"data_region" gorm:"type:varchar(8);not null;index:idx_outbox_region_status,priority:1"`
	Status       string `json:"status" gorm:"type:varchar(16);not null;index:idx_outbox_region_status,priority:2;index:idx_outbox_status_locked,priority:1"`
	LockedUntil  int64  `json:"locked_until" gorm:"type:bigint;default:0;index:idx_outbox_status_locked,priority:2"`
	RetryCount   int    `json:"retry_count" gorm:"type:int;default:0"`
	LastError    string `json:"last_error" gorm:"type:text"`
	Payload      string `json:"payload" gorm:"type:text"` // JSON event body
	CreatedAt    int64  `json:"created_at" gorm:"autoCreateTime;column:created_at;index"`
	PublishedAt  int64  `json:"published_at" gorm:"column:published_at;default:0"`
}

func (ConsumeLogOutbox) TableName() string {
	return "consume_log_outbox"
}

// InsertOutboxInTx 在传入 TX 内插入 outbox 记录；调用方负责 commit / rollback。
// 失败时调用方 TX 应回滚整批（含 logs.Create）—— 这是 §1.5.3 设计意图。
func InsertOutboxInTx(tx *gorm.DB, rec *ConsumeLogOutbox) error {
	if tx == nil || rec == nil {
		return errors.New("nil tx or record")
	}
	if rec.DataRegion == "" {
		return errors.New("data_region required")
	}
	if rec.Status == "" {
		rec.Status = OutboxStatusPending
	}
	if err := tx.Create(rec).Error; err != nil {
		return fmt.Errorf("insert outbox: %w", err)
	}
	return nil
}

// LeaseOutboxBatch 由 publisher goroutine 调用：取一批 status=pending 的事件，
// 用乐观锁（status + locked_until）防多实例重复拉取。
func LeaseOutboxBatch(region string, limit int, leaseTTL time.Duration) ([]*ConsumeLogOutbox, error) {
	if region == "" || limit <= 0 {
		return nil, errors.New("region and limit required")
	}
	now := time.Now().Unix()
	leaseUntil := time.Now().Add(leaseTTL).Unix()

	// 三方言安全：先 SELECT 再 UPDATE，由 (status, locked_until) 索引兜底。
	var rows []*ConsumeLogOutbox
	err := LOG_DB.Where(
		"data_region = ? AND status IN (?,?) AND locked_until < ?",
		region, OutboxStatusPending, OutboxStatusInFlight, now,
	).Order("id ASC").Limit(limit).Find(&rows).Error
	if err != nil {
		return nil, fmt.Errorf("lease outbox find: %w", err)
	}
	if len(rows) == 0 {
		return nil, nil
	}

	ids := make([]int64, 0, len(rows))
	for _, r := range rows {
		ids = append(ids, r.Id)
	}
	upd := LOG_DB.Model(&ConsumeLogOutbox{}).
		Where("id IN ? AND locked_until < ?", ids, now).
		Updates(map[string]any{
			"status":       OutboxStatusInFlight,
			"locked_until": leaseUntil,
		})
	if upd.Error != nil {
		return nil, fmt.Errorf("lease outbox update: %w", upd.Error)
	}
	// 再读一次，确认拿到 lease 的真实集合。
	var leased []*ConsumeLogOutbox
	if err := LOG_DB.Where("id IN ? AND locked_until = ?", ids, leaseUntil).Find(&leased).Error; err != nil {
		return nil, fmt.Errorf("lease outbox refind: %w", err)
	}
	return leased, nil
}

// MarkOutboxPublished 由 publisher 在 MNS 推送成功后调用。
func MarkOutboxPublished(id int64) error {
	res := LOG_DB.Model(&ConsumeLogOutbox{}).Where("id = ?", id).Updates(map[string]any{
		"status":       OutboxStatusPublished,
		"published_at": time.Now().Unix(),
		"locked_until": 0,
		"last_error":   "",
	})
	if res.Error != nil {
		return fmt.Errorf("mark published: %w", res.Error)
	}
	return nil
}

// MarkOutboxFailed retry_count++，超过 OutboxMaxRetry 进 dead_letter。
func MarkOutboxFailed(id int64, errMsg string) error {
	var rec ConsumeLogOutbox
	if err := LOG_DB.Where("id = ?", id).First(&rec).Error; err != nil {
		return fmt.Errorf("find for fail: %w", err)
	}
	rec.RetryCount++
	rec.LastError = truncate(errMsg, 1024)
	rec.LockedUntil = 0
	if rec.RetryCount >= OutboxMaxRetry {
		rec.Status = OutboxStatusDeadLetter
	} else {
		rec.Status = OutboxStatusPending
	}
	return LOG_DB.Save(&rec).Error
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}
