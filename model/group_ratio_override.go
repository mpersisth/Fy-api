// Copyright 2026 TraceNex Partner OVERLAY
//
// GroupRatioOverride: partner 维度的 group_ratio 覆盖（OVERLAY B-15）。
// 每个 (partner_kid, group) 对应一个 ratio override；hot path 读 user 缓存里
// 已 resolve 的 override 值，不每次回库。
package model

import (
	"errors"
	"fmt"

	"gorm.io/gorm"
)

// GroupRatioOverride 落 fy_api_db.group_ratio_override。
type GroupRatioOverride struct {
	Id         int     `json:"id" gorm:"primaryKey;autoIncrement"`
	PartnerKid string  `json:"partner_kid" gorm:"type:varchar(64);not null;index:idx_gro,unique,priority:1"`
	UserId     int     `json:"user_id" gorm:"type:int;index:idx_gro,unique,priority:2"`
	Group      string  `json:"group" gorm:"type:varchar(64);not null;index:idx_gro,unique,priority:3"`
	Ratio      float64 `json:"ratio" gorm:"type:double;not null"`
	Status     int     `json:"status" gorm:"type:int;default:1"`
	CreatedAt  int64   `json:"created_at" gorm:"autoCreateTime;column:created_at"`
	UpdatedAt  int64   `json:"updated_at" gorm:"autoUpdateTime;column:updated_at"`
}

func (GroupRatioOverride) TableName() string {
	return "group_ratio_override"
}

// UpsertGroupRatioOverride 由 /api/internal/group_ratio_override/upsert 调用。
func UpsertGroupRatioOverride(rec *GroupRatioOverride) error {
	if rec == nil || rec.PartnerKid == "" || rec.Group == "" {
		return errors.New("partner_kid and group required")
	}
	if rec.Ratio <= 0 {
		return errors.New("ratio must be > 0")
	}

	var existing GroupRatioOverride
	q := DB.Where("partner_kid = ? AND user_id = ? AND "+commonGroupCol+" = ?",
		rec.PartnerKid, rec.UserId, rec.Group)
	err := q.First(&existing).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return DB.Create(rec).Error
	}
	if err != nil {
		return fmt.Errorf("lookup override: %w", err)
	}
	existing.Ratio = rec.Ratio
	existing.Status = rec.Status
	return DB.Save(&existing).Error
}

// LookupUserOverride 用于 distributor 阶段把 ratio 写进 RelayInfo。
// 返回 (ratio, found)。
func LookupUserOverride(userId int, group string) (float64, bool) {
	if userId <= 0 || group == "" {
		return 0, false
	}
	var rec GroupRatioOverride
	err := DB.Where("user_id = ? AND "+commonGroupCol+" = ? AND status = 1", userId, group).
		Order("updated_at DESC").
		First(&rec).Error
	if err != nil {
		return 0, false
	}
	return rec.Ratio, true
}
