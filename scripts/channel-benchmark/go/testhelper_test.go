package main

import (
	"context"
	"testing"
)

// testContext returns a context that's cancelled when the test ends.
func testContext(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	return ctx
}
