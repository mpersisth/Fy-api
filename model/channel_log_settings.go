// Copyright 2026 TraceNex Partner OVERLAY
//
// channel_log_settings: partner 维度的 channel log 配置（OVERLAY B-13）。
// Phase 1 schema-only：仅落库 / 查询，不接入 channel 路由实际生效；
// 实际生效路径留给 Phase 2A（避免本 PR 触碰 channel hot path）。
package model

import (
	"errors"

	"gorm.io/gorm"
)

type ChannelLogSetting struct {
	Id         int    `json:"id" gorm:"primaryKey;autoIncrement"`
	PartnerKid string `json:"partner_kid" gorm:"type:varchar(64);not null;index:idx_cls,unique,priority:1"`
	ChannelId  int    `json:"channel_id" gorm:"type:int;not null;index:idx_cls,unique,priority:2"`
	Settings   string `json:"settings" gorm:"type:text"` // JSON-encoded
	UpdatedAt  int64  `json:"updated_at" gorm:"autoUpdateTime;column:updated_at"`
}

func (ChannelLogSetting) TableName() string {
	return "channel_log_settings"
}

// UpsertChannelLogSetting upsert per (partner_kid, channel_id)。
func UpsertChannelLogSetting(rec *ChannelLogSetting) error {
	if rec == nil || rec.PartnerKid == "" || rec.ChannelId <= 0 {
		return errors.New("partner_kid and channel_id required")
	}
	var existing ChannelLogSetting
	err := DB.Where("partner_kid = ? AND channel_id = ?", rec.PartnerKid, rec.ChannelId).
		First(&existing).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return DB.Create(rec).Error
	}
	if err != nil {
		return err
	}
	existing.Settings = rec.Settings
	return DB.Save(&existing).Error
}
