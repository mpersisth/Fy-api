// Copyright 2026 TraceNex Partner OVERLAY
//
// log_outbox_integration_test.go: B-16 RecordConsumeLog TX wrap 集成测试。
// 用 sqlite in-memory 验证「flag on → log + outbox 同事务；任一失败回滚」。
package model

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/setting/overlay_flag"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func setupTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	// 每个测试用独立 DSN，避免 cache=shared 把表跨测试漏出。
	dsn := "file:" + t.Name() + "?mode=memory&cache=private"
	db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{
		Logger: logger.Default.LogMode(logger.Silent),
	})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	common.UsingSQLite = true
	commonGroupCol = "`group`"
	commonKeyCol = "`key`"
	if err := db.AutoMigrate(&Log{}, &ConsumeLogOutbox{}); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	return db
}

func TestOutboxTxWriteWhenFlagOn(t *testing.T) {
	db := setupTestDB(t)
	prevDB := LOG_DB
	LOG_DB = db
	defer func() { LOG_DB = prevDB }()

	overlay_flag.SetForTest(overlay_flag.FlagOutboxTx, "true")
	overlay_flag.SetForTest(overlay_flag.FlagOutbox, overlay_flag.OutboxShadow)
	defer func() {
		overlay_flag.SetForTest(overlay_flag.FlagOutboxTx, "false")
		overlay_flag.SetForTest(overlay_flag.FlagOutbox, overlay_flag.OutboxOff)
	}()

	logRow := &Log{
		UserId:    42,
		Type:      LogTypeConsume,
		ModelName: "gpt-test",
		Quota:     100,
		CreatedAt: 1700000000,
	}
	params := RecordConsumeLogParams{
		ChannelId:    1,
		ModelName:    "gpt-test",
		Quota:        100,
		PromptTokens: 10,
	}
	if err := recordConsumeLogWithOutbox(nil, logRow, 42, params); err != nil {
		t.Fatalf("tx wrap failed: %v", err)
	}

	var logs []Log
	if err := db.Find(&logs).Error; err != nil {
		t.Fatalf("query logs: %v", err)
	}
	if len(logs) != 1 {
		t.Fatalf("want 1 log got %d", len(logs))
	}
	var outboxes []ConsumeLogOutbox
	if err := db.Find(&outboxes).Error; err != nil {
		t.Fatalf("query outbox: %v", err)
	}
	if len(outboxes) != 1 {
		t.Fatalf("want 1 outbox row got %d", len(outboxes))
	}
	if outboxes[0].UserId != 42 || outboxes[0].DataRegion == "" {
		t.Fatalf("outbox row malformed: %+v", outboxes[0])
	}
}

func TestOutboxTxBypassWhenFlagOff(t *testing.T) {
	db := setupTestDB(t)
	prevDB := LOG_DB
	LOG_DB = db
	defer func() { LOG_DB = prevDB }()

	overlay_flag.SetForTest(overlay_flag.FlagOutboxTx, "false")
	overlay_flag.SetForTest(overlay_flag.FlagOutbox, overlay_flag.OutboxOff)

	logRow := &Log{UserId: 7, Type: LogTypeConsume, ModelName: "m", Quota: 1, CreatedAt: 1700000000}
	if err := recordConsumeLogWithOutbox(nil, logRow, 7, RecordConsumeLogParams{ModelName: "m", Quota: 1}); err != nil {
		t.Fatalf("bypass path failed: %v", err)
	}
	var outboxes []ConsumeLogOutbox
	_ = db.Find(&outboxes)
	if len(outboxes) != 0 {
		t.Fatalf("flag off must not write outbox; got %d rows", len(outboxes))
	}
}
