package system_setting

import (
	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/setting/config"
)

// Fy-api overlay: TraceNex ships only the classic frontend (see CLAUDE.md
// "Frontend theme: classic-only"). The default frontend is built for
// upstream parity but cannot be selected at runtime. To revert to upstream
// behavior (allow operators to switch via the admin UI), delete the
// `classicTheme` constant and remove the override in syncThemeToCommon /
// UpdateAndSyncTheme below; also drop the writer guard in
// controller/option.go.
const classicTheme = "classic"

type ThemeSettings struct {
	Frontend string `json:"frontend"`
}

var themeSettings = ThemeSettings{
	Frontend: classicTheme,
}

func init() {
	config.GlobalConfig.Register("theme", &themeSettings)
	syncThemeToCommon()
}

func syncThemeToCommon() {
	// Fy-api overlay: force the active theme to "classic" regardless of
	// what config.GlobalConfig loaded from the DB. This survives a manual
	// UPDATE on the option row, the admin UI, or a stale cached value.
	themeSettings.Frontend = classicTheme
	common.SetTheme(classicTheme)
}

func GetThemeSettings() *ThemeSettings {
	return &themeSettings
}

// UpdateAndSyncTheme syncs the theme config to common after DB load.
func UpdateAndSyncTheme() {
	syncThemeToCommon()
}
