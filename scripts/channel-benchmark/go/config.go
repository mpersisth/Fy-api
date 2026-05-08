package main

import (
	"fmt"
	"os"
	"regexp"
	"strings"

	"gopkg.in/yaml.v3"
)

// BenchmarkConfig is the top-level YAML schema.
//
// The gateway section uses BOTH an admin token (to list channels via
// /api/channel/) and a user token (to send real chat requests via
// /v1/chat/completions). They may reference the same user when that user is an
// admin; they are kept separate so a smoke job can run under a dedicated
// low-privilege user whose quota acts as a budget cap.
type BenchmarkConfig struct {
	Gateway GatewayConfig   `yaml:"gateway"`
	Test    TestConfig      `yaml:"test"`
	Channels []ChannelConfig `yaml:"channels"`
	Export   ExportConfig    `yaml:"export"`
	Metrics  MetricsConfig   `yaml:"metrics"`
}

type GatewayConfig struct {
	BaseURL string `yaml:"base_url"`

	// AdminToken is a personal access token for any admin user. Used for
	// GET /api/channel/ only. Sent as `Authorization: <token>` (no Bearer).
	AdminToken string `yaml:"admin_token"`

	// AdminUserID is required by the Fy-api AdminAuth middleware alongside
	// AdminToken (New-Api-User header).
	AdminUserID string `yaml:"admin_user_id"`

	// UserToken is a regular user's API key (sk-... style) used to hit
	// /v1/chat/completions like real traffic would. Billed to that user.
	UserToken string `yaml:"user_token"`
}

type TestConfig struct {
	Concurrency   int  `yaml:"concurrency"`
	TimeoutSec    int  `yaml:"timeout_seconds"`
	RepsPerCase   int  `yaml:"reps_per_case"`
	Stream        bool `yaml:"stream"`
	NonStream     bool `yaml:"non_stream"`
	MaxTokens     int  `yaml:"max_tokens"`
	Prompt        string `yaml:"prompt"`
}

// ChannelConfig picks a channel by ID and lists the models to test on it.
// An empty or missing TestModels means "skip this channel" — we never fall
// back to a magic default, to avoid the bad habit of silently billing runs.
type ChannelConfig struct {
	ID         int      `yaml:"id"`
	Name       string   `yaml:"name"`
	TestModels []string `yaml:"test_models"`
}

type ExportConfig struct {
	Formats   []string `yaml:"formats"`
	OutputDir string   `yaml:"output_dir"`
}

type MetricsConfig struct {
	LatencyPercentiles []float64 `yaml:"latency_percentiles"`
}

// envVarPattern matches ${VAR} and ${VAR:-default} syntaxes.
var envVarPattern = regexp.MustCompile(`\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}`)

// expandEnv replaces ${VAR} or ${VAR:-default} in s with env values.
// Unknown variables without a default expand to "" and we return an error
// listing them so the user sees the problem at startup rather than mid-run.
//
// Lines whose first non-whitespace char is '#' are YAML comments and are
// left untouched — otherwise `# set ${FOO}` in documentation would falsely
// trigger a "missing env var" error.
func expandEnv(s string) (string, []string) {
	var missing []string
	lines := strings.Split(s, "\n")
	for i, line := range lines {
		if isCommentLine(line) {
			continue
		}
		lines[i] = envVarPattern.ReplaceAllStringFunc(line, func(match string) string {
			sub := envVarPattern.FindStringSubmatch(match)
			name := sub[1]
			def := sub[3]
			if v, ok := os.LookupEnv(name); ok {
				return v
			}
			if def != "" {
				return def
			}
			missing = append(missing, name)
			return ""
		})
	}
	return strings.Join(lines, "\n"), missing
}

func isCommentLine(line string) bool {
	trimmed := strings.TrimLeft(line, " \t")
	return strings.HasPrefix(trimmed, "#")
}

// LoadConfig reads a YAML file, expands ${ENV} variables, then applies defaults.
func LoadConfig(path string) (*BenchmarkConfig, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read config: %w", err)
	}

	expanded, missing := expandEnv(string(raw))
	if len(missing) > 0 {
		return nil, fmt.Errorf("config references undefined environment variables: %v", missing)
	}

	cfg := &BenchmarkConfig{}
	if err := yaml.Unmarshal([]byte(expanded), cfg); err != nil {
		return nil, fmt.Errorf("parse config: %w", err)
	}

	applyDefaults(cfg)
	return cfg, nil
}

func applyDefaults(cfg *BenchmarkConfig) {
	if cfg.Test.Concurrency <= 0 {
		cfg.Test.Concurrency = 4
	}
	if cfg.Test.TimeoutSec <= 0 {
		cfg.Test.TimeoutSec = 60
	}
	if cfg.Test.RepsPerCase <= 0 {
		cfg.Test.RepsPerCase = 3
	}
	if cfg.Test.MaxTokens <= 0 {
		cfg.Test.MaxTokens = 64
	}
	if cfg.Test.Prompt == "" {
		cfg.Test.Prompt = "Reply with the single word: pong."
	}
	// If user specifies neither, default to BOTH — a smoke run should exercise
	// the streaming path since that's what real clients hit.
	if !cfg.Test.Stream && !cfg.Test.NonStream {
		cfg.Test.Stream = true
		cfg.Test.NonStream = true
	}
	if len(cfg.Export.Formats) == 0 {
		cfg.Export.Formats = []string{"json"}
	}
	if cfg.Export.OutputDir == "" {
		cfg.Export.OutputDir = "benchmark-results"
	}
	if len(cfg.Metrics.LatencyPercentiles) == 0 {
		cfg.Metrics.LatencyPercentiles = []float64{50, 95, 99}
	}
}

// Validate runs explicit checks that can't be expressed as defaults.
func (c *BenchmarkConfig) Validate() error {
	if c.Gateway.BaseURL == "" {
		return fmt.Errorf("gateway.base_url is required")
	}
	if c.Gateway.AdminToken == "" {
		return fmt.Errorf("gateway.admin_token is required (used for GET /api/channel/)")
	}
	if c.Gateway.AdminUserID == "" {
		return fmt.Errorf("gateway.admin_user_id is required (New-Api-User header)")
	}
	if c.Gateway.UserToken == "" {
		return fmt.Errorf("gateway.user_token is required (used for /v1/chat/completions)")
	}
	if len(c.Channels) == 0 {
		return fmt.Errorf("no channels configured")
	}
	for i, ch := range c.Channels {
		if ch.ID <= 0 {
			return fmt.Errorf("channels[%d]: id must be > 0", i)
		}
		if len(ch.TestModels) == 0 {
			return fmt.Errorf("channels[%d] (id=%d): test_models is empty — specify at least one model", i, ch.ID)
		}
	}
	return nil
}
