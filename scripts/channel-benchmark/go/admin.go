package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

// Channel mirrors the subset of model.Channel we need for the smoke run.
// Full field list is in ../../model/channel.go — we intentionally parse only
// what we use so upstream schema changes don't break us.
type Channel struct {
	ID           int      `json:"id"`
	Type         int      `json:"type"`
	Name         string   `json:"name"`
	Status       int      `json:"status"`
	Group        string   `json:"group"`
	Models       string   `json:"models"`         // comma-separated list
	TestModel    *string  `json:"test_model"`
	ResponseTime int      `json:"response_time"`  // millis
	TestTime     int64    `json:"test_time"`
	Priority     *int64   `json:"priority"`
	Weight       *uint    `json:"weight"`
}

// channelListEnvelope matches common.ApiSuccess shape:
//   { "success": true, "message": "", "data": { "items": [...], "total": n, ... } }
type channelListEnvelope struct {
	Success bool   `json:"success"`
	Message string `json:"message"`
	Data    struct {
		Items    []Channel `json:"items"`
		Total    int64     `json:"total"`
		Page     int       `json:"page"`
		PageSize int       `json:"page_size"`
	} `json:"data"`
}

// AdminClient lists channels through the admin HTTP API.
// Auth contract (from middleware/auth.go, verified May 2026):
//   - Authorization: <access_token>   (no "Bearer " prefix!)
//   - New-Api-User:  <numeric user id>
type AdminClient struct {
	baseURL string
	token   string
	userID  string
	http    *http.Client
}

func NewAdminClient(baseURL, token, userID string, timeout time.Duration) *AdminClient {
	return &AdminClient{
		baseURL: baseURL,
		token:   token,
		userID:  userID,
		http:    &http.Client{Timeout: timeout},
	}
}

// ListChannels fetches all channels, paginating transparently.
// onlyEnabled=true skips status != 1 entries.
func (a *AdminClient) ListChannels(ctx context.Context, onlyEnabled bool) ([]Channel, error) {
	var all []Channel
	page := 1
	const pageSize = 200

	for {
		batch, total, err := a.fetchPage(ctx, page, pageSize)
		if err != nil {
			return nil, err
		}
		for _, ch := range batch {
			if onlyEnabled && ch.Status != 1 {
				continue
			}
			all = append(all, ch)
		}
		if int64(page*pageSize) >= total || len(batch) == 0 {
			break
		}
		page++
	}
	return all, nil
}

func (a *AdminClient) fetchPage(ctx context.Context, page, pageSize int) ([]Channel, int64, error) {
	u, err := url.Parse(a.baseURL + "/api/channel/")
	if err != nil {
		return nil, 0, fmt.Errorf("parse base_url: %w", err)
	}
	q := u.Query()
	q.Set("p", strconv.Itoa(page))
	q.Set("page_size", strconv.Itoa(pageSize))
	u.RawQuery = q.Encode()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return nil, 0, err
	}
	// CRITICAL: no "Bearer " prefix. See middleware/auth.go authHelper.
	req.Header.Set("Authorization", a.token)
	req.Header.Set("New-Api-User", a.userID)
	req.Header.Set("Accept", "application/json")

	resp, err := a.http.Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("admin GET /api/channel/: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, 0, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, 0, fmt.Errorf("admin GET /api/channel/ returned HTTP %d: %s", resp.StatusCode, truncate(string(body), 400))
	}

	var env channelListEnvelope
	if err := json.Unmarshal(body, &env); err != nil {
		return nil, 0, fmt.Errorf("parse channel list: %w (body=%s)", err, truncate(string(body), 200))
	}
	if !env.Success {
		return nil, 0, fmt.Errorf("admin API success=false: %s", env.Message)
	}
	return env.Data.Items, env.Data.Total, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "...(truncated)"
}
