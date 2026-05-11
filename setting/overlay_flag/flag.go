// Copyright 2026 TraceNex Partner OVERLAY
//
// Package overlay_flag 实现 OVERLAY B-14 feature flag 框架。
//
// 背景：Round-2 §11.5 要求每个 OVERLAY PR 都有独立 feature flag，
// prod 默认 off / staging 默认 on / dev 默认 on，便于灰度回滚。
//
// 实现：基于 biz_setting (option 表) + 5-15s polling 兜底刷新；
//
//	写路径走 model.UpdateOption（既有），读路径只读 in-memory 缓存。
package overlay_flag

import (
	"context"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/QuantumNous/new-api/common"
)

// 5 个 OVERLAY flag key — 与 integration §14 / OVERLAY.md B-12..B-18 对齐。
const (
	FlagInternalAPI         = "overlay.internal_api_enabled"
	FlagHMACKeystore        = "overlay.hmac_keystore_enabled"
	FlagOutbox              = "overlay.outbox_mode"               // shadow / enabled / off
	FlagGroupRatioOverride  = "overlay.group_ratio_override"      // bool
	FlagOutboxTx            = "overlay.outbox_tx_enabled"         // bool
)

// Outbox 三档值。
const (
	OutboxOff      = "off"
	OutboxShadow   = "shadow"
	OutboxEnabled  = "enabled"
)

// store 持有当前 flag 值。原子读，写带锁；不阻塞 hot path。
type store struct {
	mu     sync.RWMutex
	values map[string]string
	// 高频 bool flag 走 atomic 单独缓存，避开 map 读锁。
	internalAPI         atomic.Bool
	hmacKeystore        atomic.Bool
	groupRatioOverride  atomic.Bool
	outboxTx            atomic.Bool
	outboxMode          atomic.Value // string
}

var (
	globalStore = &store{values: map[string]string{}}
	// pollInterval 默认 10s，落在 §14.1 推荐的 5-15s 区间。
	pollInterval = 10 * time.Second
)

// defaults 决定 flag 在 option 未设置时的回落值；
// prod 部署应通过 option_map 显式设置，env 仅为 dev / CI 兜底。
//
// 默认值矩阵（per task §"各 PR 的 Feature flag 框架"）：
//
//	dev / staging = on，prod = off
//
// 这里实现 prod-safe 默认（all off / shadow），让 prod 即使忘改 option 也是安全态；
// dev / staging 在启动脚本里覆盖即可。
var defaults = map[string]string{
	FlagInternalAPI:        "false",
	FlagHMACKeystore:       "false",
	FlagOutbox:             OutboxOff,
	FlagGroupRatioOverride: "false",
	FlagOutboxTx:           "false",
}

// allKeys 列出所有受管 flag。
func allKeys() []string {
	return []string{
		FlagInternalAPI,
		FlagHMACKeystore,
		FlagOutbox,
		FlagGroupRatioOverride,
		FlagOutboxTx,
	}
}

// loader 抽象 option 读取，便于单测注入。
type loader func(key string) (string, bool)

var optionLoader loader = readFromOptionMap

// SetLoader 让单测注入 fake loader。
func SetLoader(l loader) {
	if l == nil {
		optionLoader = readFromOptionMap
		return
	}
	optionLoader = l
}

// readFromOptionMap 从 common.OptionMap 读 biz_setting key。
// 不存在时回落 defaults。
func readFromOptionMap(key string) (string, bool) {
	common.OptionMapRWMutex.RLock()
	defer common.OptionMapRWMutex.RUnlock()
	if v, ok := common.OptionMap[key]; ok {
		return v, true
	}
	return "", false
}

// Reload 显式触发一次重读（启动期 / 测试用）。
func Reload(ctx context.Context) {
	for _, k := range allKeys() {
		v, ok := optionLoader(k)
		if !ok {
			v = defaults[k]
		}
		globalStore.set(k, v)
	}
}

// StartPoller 启动后台轮询 goroutine，5-15s 兜底刷新。
// ctx 取消时停止。
func StartPoller(ctx context.Context) {
	Reload(ctx)
	go func() {
		t := time.NewTicker(pollInterval)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				Reload(ctx)
			}
		}
	}()
}

func (s *store) set(key, value string) {
	s.mu.Lock()
	s.values[key] = value
	s.mu.Unlock()

	// 高频 flag 同步进 atomic 缓存。
	switch key {
	case FlagInternalAPI:
		s.internalAPI.Store(parseBool(value))
	case FlagHMACKeystore:
		s.hmacKeystore.Store(parseBool(value))
	case FlagGroupRatioOverride:
		s.groupRatioOverride.Store(parseBool(value))
	case FlagOutboxTx:
		s.outboxTx.Store(parseBool(value))
	case FlagOutbox:
		s.outboxMode.Store(value)
	}
}

func parseBool(v string) bool {
	b, err := strconv.ParseBool(v)
	if err != nil {
		return false
	}
	return b
}

// IsInternalAPIEnabled 用于 router 注册和 controller hot path。
func IsInternalAPIEnabled() bool {
	return globalStore.internalAPI.Load()
}

// IsHMACKeystoreEnabled 用于 middleware/internal_auth.go。
func IsHMACKeystoreEnabled() bool {
	return globalStore.hmacKeystore.Load()
}

// IsGroupRatioOverrideEnabled 用于 billing hot path。
func IsGroupRatioOverrideEnabled() bool {
	return globalStore.groupRatioOverride.Load()
}

// IsOutboxTxEnabled 用于 RecordConsumeLog 同事务写 outbox。
func IsOutboxTxEnabled() bool {
	return globalStore.outboxTx.Load()
}

// OutboxMode 返回当前 outbox 模式（off / shadow / enabled）。
func OutboxMode() string {
	v := globalStore.outboxMode.Load()
	if v == nil {
		return OutboxOff
	}
	s, _ := v.(string)
	if s == "" {
		return OutboxOff
	}
	return s
}

// SetForTest 仅供单测使用。
func SetForTest(key, value string) {
	globalStore.set(key, value)
}

// SetPollIntervalForTest 仅供单测使用。
func SetPollIntervalForTest(d time.Duration) {
	pollInterval = d
}
