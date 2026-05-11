// Copyright 2026 TraceNex Partner OVERLAY
package tnbiz_internal

import "context"

// contextOrBackground 把 gin 的 Request.Context() 或任意 done-able context
// 折成标准 context.Context；nil-safe，落不到时回 background。
func contextOrBackground(ctx interface{ Done() <-chan struct{} }) context.Context {
	if c, ok := ctx.(context.Context); ok && c != nil {
		return c
	}
	return context.Background()
}
