// Copyright 2026 TraceNex Partner OVERLAY
package tnbiz_internal

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/setting/overlay_flag"

	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func TestHealthEndpointReturnsFlagsState(t *testing.T) {
	gin.SetMode(gin.TestMode)
	overlay_flag.SetForTest(overlay_flag.FlagInternalAPI, "true")
	overlay_flag.SetForTest(overlay_flag.FlagHMACKeystore, "true")
	overlay_flag.SetForTest(overlay_flag.FlagOutbox, overlay_flag.OutboxShadow)
	defer func() {
		overlay_flag.SetForTest(overlay_flag.FlagInternalAPI, "false")
		overlay_flag.SetForTest(overlay_flag.FlagHMACKeystore, "false")
		overlay_flag.SetForTest(overlay_flag.FlagOutbox, overlay_flag.OutboxOff)
	}()

	r := gin.New()
	r.GET("/health", Health)

	req, _ := http.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d want 200", w.Code)
	}
	var got map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if got["status"] != "ok" {
		t.Fatalf("status field = %v want ok", got["status"])
	}
	if got["overlay_internal_api"] != true {
		t.Fatalf("overlay_internal_api flag not surfaced")
	}
	if got["overlay_outbox"] != overlay_flag.OutboxShadow {
		t.Fatalf("overlay_outbox = %v want %q", got["overlay_outbox"], overlay_flag.OutboxShadow)
	}
}

func TestRespondErrorEnvelope(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/x", func(c *gin.Context) {
		respondError(c, http.StatusBadRequest, "oops", "bad")
	})
	req, _ := http.NewRequest(http.MethodGet, "/x", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("status %d", w.Code)
	}
	var got map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &got)
	if got["success"] != false {
		t.Fatalf("envelope success not false")
	}
	errObj, ok := got["error"].(map[string]any)
	if !ok || errObj["code"] != "oops" || errObj["message"] != "bad" {
		t.Fatalf("error envelope malformed: %v", got)
	}
}

func setupUserEndpointTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=private"), &gorm.Config{
		Logger: logger.Default.LogMode(logger.Silent),
	})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	common.UsingSQLite = true
	if err := db.AutoMigrate(&model.User{}); err != nil {
		t.Fatalf("migrate user: %v", err)
	}
	prevDB := model.DB
	prevRedisEnabled := common.RedisEnabled
	model.DB = db
	common.RedisEnabled = false
	t.Cleanup(func() {
		model.DB = prevDB
		common.RedisEnabled = prevRedisEnabled
	})
	return db
}

func TestUpdateGroupUpdatesUserAndCache(t *testing.T) {
	gin.SetMode(gin.TestMode)
	db := setupUserEndpointTestDB(t)
	if err := db.Create(&model.User{
		Username: "customer-a",
		Password: "password",
		Group:    "old_group",
		Status:   common.UserStatusEnabled,
	}).Error; err != nil {
		t.Fatalf("create user: %v", err)
	}

	r := gin.New()
	r.PUT("/user/group", UpdateGroup)
	req, _ := http.NewRequest(http.MethodPut, "/user/group", bytes.NewBufferString(`{"user_id":1,"group":"partner_1_tier_a","reason":"测试切换渠道商"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", w.Code, w.Body.String())
	}
	var user model.User
	if err := db.First(&user, 1).Error; err != nil {
		t.Fatalf("load user: %v", err)
	}
	if user.Group != "partner_1_tier_a" {
		t.Fatalf("group=%q", user.Group)
	}
}

func TestEraseUserSoftDeletesUser(t *testing.T) {
	gin.SetMode(gin.TestMode)
	db := setupUserEndpointTestDB(t)
	if err := db.Create(&model.User{
		Username: "customer-b",
		Password: "password",
		Group:    "default",
		Status:   common.UserStatusEnabled,
	}).Error; err != nil {
		t.Fatalf("create user: %v", err)
	}

	r := gin.New()
	r.POST("/user/erase", EraseUser)
	req, _ := http.NewRequest(http.MethodPost, "/user/erase", bytes.NewBufferString(`{"user_id":1,"reason":"PIPL 删除测试"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", w.Code, w.Body.String())
	}
	var count int64
	if err := db.Model(&model.User{}).Where("id = ?", 1).Count(&count).Error; err != nil {
		t.Fatalf("count active: %v", err)
	}
	if count != 0 {
		t.Fatalf("user should be soft-deleted, active count=%d", count)
	}
	if err := db.Unscoped().First(&model.User{}, 1).Error; err != nil {
		t.Fatalf("soft-deleted user should remain for audit: %v", err)
	}
}
