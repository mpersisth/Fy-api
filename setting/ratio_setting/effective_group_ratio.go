// Copyright 2026 TraceNex Partner OVERLAY
//
// effective_group_ratio.go: B-15 GroupRatioOverride hot-path resolver。
//
// 调用约定：在每个 hot path 调用 GetGroupRatio / GetGroupGroupRatio 之前，
// 先调 ApplyOverride(override, fallback)，命中 override 即用，否则走原 fallback。
//
// 把 override 检查集中在一个 in-memory 函数里，避免 hot path 6 调用站
// 各自重复 if-else（review §6.1）。Hot path 不会回库，全部数据走 RelayInfo。
package ratio_setting

import "github.com/QuantumNous/new-api/setting/overlay_flag"

// ApplyOverride 当 OVERLAY_GROUP_RATIO_OVERRIDE 启用 + override > 0 时返回 override；
// 否则返回 fallback（原 GetGroupRatio 结果）。
//
// 性能：1 atomic load + 1 float64 compare；review §6.1 实测 < 0.01ms 影响。
func ApplyOverride(override, fallback float64) float64 {
	if !overlay_flag.IsGroupRatioOverrideEnabled() {
		return fallback
	}
	if override > 0 {
		return override
	}
	return fallback
}
