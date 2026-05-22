// Copyright 2026 TraceNex Partner OVERLAY
//
// override_lookup.go: B-15 RelayInfo 构造期的 GroupRatioOverride 查询点。
//
// 用 callback 注册避开 relay/common -> model 的潜在导入循环。
// 由 main.go 启动期通过 SetOverrideLookup(model.LookupUserOverride) 注入。
package common

import "sync/atomic"

// OverrideLookupFunc: (userId, group) -> (ratio, found)
type OverrideLookupFunc func(userId int, group string) (float64, bool)

var overrideLookupFn atomic.Value

// SetOverrideLookup 由 main.go 注入。fn=nil 即恢复 noop。
func SetOverrideLookup(fn OverrideLookupFunc) {
	if fn == nil {
		overrideLookupFn.Store(OverrideLookupFunc(noopOverride))
		return
	}
	overrideLookupFn.Store(fn)
}

func init() {
	overrideLookupFn.Store(OverrideLookupFunc(noopOverride))
}

func noopOverride(userId int, group string) (float64, bool) {
	return 0, false
}

// overrideLookup 是 relay_info.go 实际调用的入口。
func overrideLookup(userId int, group string) (float64, bool) {
	v := overrideLookupFn.Load()
	if v == nil {
		return 0, false
	}
	fn, ok := v.(OverrideLookupFunc)
	if !ok || fn == nil {
		return 0, false
	}
	return fn(userId, group)
}
