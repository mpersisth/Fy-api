// Copyright 2026 TraceNex Partner OVERLAY
package ratio_setting

import (
	"testing"

	"github.com/QuantumNous/new-api/setting/overlay_flag"
)

func TestApplyOverrideFlagOff(t *testing.T) {
	overlay_flag.SetForTest(overlay_flag.FlagGroupRatioOverride, "false")
	if got := ApplyOverride(2.0, 1.0); got != 1.0 {
		t.Fatalf("flag off must return fallback (got %v)", got)
	}
}

func TestApplyOverrideFlagOnPositiveOverride(t *testing.T) {
	overlay_flag.SetForTest(overlay_flag.FlagGroupRatioOverride, "true")
	defer overlay_flag.SetForTest(overlay_flag.FlagGroupRatioOverride, "false")
	if got := ApplyOverride(2.0, 1.0); got != 2.0 {
		t.Fatalf("override must apply (got %v)", got)
	}
}

func TestApplyOverrideFlagOnZeroOverride(t *testing.T) {
	overlay_flag.SetForTest(overlay_flag.FlagGroupRatioOverride, "true")
	defer overlay_flag.SetForTest(overlay_flag.FlagGroupRatioOverride, "false")
	if got := ApplyOverride(0, 1.5); got != 1.5 {
		t.Fatalf("zero override must fall back (got %v)", got)
	}
	if got := ApplyOverride(-1, 1.5); got != 1.5 {
		t.Fatalf("negative override must fall back (got %v)", got)
	}
}
