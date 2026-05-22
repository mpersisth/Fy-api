// Copyright 2026 TraceNex Partner OVERLAY
//
// internal_idempotency: 内部 API 幂等表（OVERLAY B-18）。
// Phase 1 用明文 response_body TEXT；Phase 2A 视审计需要再切 KMS envelope。
package model

import (
	"errors"
	"fmt"
	"time"

	"gorm.io/gorm"
)

// 默认幂等记录保留 7 天，过期后由 leader-only cron 清理。
const InternalIdempotencyTTL = 7 * 24 * time.Hour

// InternalIdempotencyRecord 落 fy_api_db.internal_idempotency。
type InternalIdempotencyRecord struct {
	Id             int64  `json:"id" gorm:"primaryKey;autoIncrement"`
	AuthKid        string `json:"auth_kid" gorm:"type:varchar(64);not null;index:idx_internal_idem,unique,priority:1"`
	IdempotencyKey string `json:"idempotency_key" gorm:"type:varchar(128);not null;index:idx_internal_idem,unique,priority:2"`
	Endpoint       string `json:"endpoint" gorm:"type:varchar(128);not null;index:idx_internal_idem,unique,priority:3"`
	RequestHash    string `json:"request_hash" gorm:"type:varchar(64);not null"`
	ResponseStatus int    `json:"response_status" gorm:"type:int"`
	ResponseBody   string `json:"response_body" gorm:"type:text"`
	CreatedAt      int64  `json:"created_at" gorm:"autoCreateTime;column:created_at;index"`
}

func (InternalIdempotencyRecord) TableName() string {
	return "internal_idempotency"
}

// LookupIdempotency 查命中。未命中返回 nil, nil；命中返回 record, nil。
func LookupIdempotency(authKid, key, endpoint string) (*InternalIdempotencyRecord, error) {
	if authKid == "" || key == "" || endpoint == "" {
		return nil, nil
	}
	var rec InternalIdempotencyRecord
	err := DB.Where("auth_kid = ? AND idempotency_key = ? AND endpoint = ?", authKid, key, endpoint).
		First(&rec).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("lookup idempotency: %w", err)
	}
	return &rec, nil
}

// SaveIdempotency 用 unique 索引去重；若并发插入返回 ErrIdempotencyConflict。
func SaveIdempotency(rec *InternalIdempotencyRecord) error {
	if rec == nil {
		return errors.New("nil record")
	}
	if err := DB.Create(rec).Error; err != nil {
		// 三方言 unique 冲突错误码不同；用 lookup 重试一次区分语义。
		if existing, lerr := LookupIdempotency(rec.AuthKid, rec.IdempotencyKey, rec.Endpoint); lerr == nil && existing != nil {
			return ErrIdempotencyConflict
		}
		return fmt.Errorf("save idempotency: %w", err)
	}
	return nil
}

// CleanupExpiredIdempotency 定期清理过期记录（leader-only cron 调）。
func CleanupExpiredIdempotency(ttl time.Duration) (int64, error) {
	cutoff := time.Now().Add(-ttl).Unix()
	res := DB.Where("created_at < ?", cutoff).Delete(&InternalIdempotencyRecord{})
	if res.Error != nil {
		return 0, fmt.Errorf("cleanup idempotency: %w", res.Error)
	}
	return res.RowsAffected, nil
}

// ErrIdempotencyConflict 用于 middleware 返回 409 Conflict。
var ErrIdempotencyConflict = errors.New("idempotency conflict")
