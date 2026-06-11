package service

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
)

type MediaKitClient struct {
	BaseURL string
	APIKey  string
	Client  *http.Client
}

type MediaKitSubmitRequest struct {
	VideoURL    string `json:"video_url"`
	Scene       string `json:"scene,omitempty"`
	ToolVersion string `json:"tool_version,omitempty"`
	Resolution  string `json:"resolution,omitempty"`
}

type MediaKitSubmitResponse struct {
	Success   bool   `json:"success"`
	TaskID    string `json:"task_id"`
	RequestID string `json:"request_id"`
}

type MediaKitTaskResponse struct {
	Success  bool   `json:"success"`
	TaskID   string `json:"task_id"`
	TaskType string `json:"task_type"`
	Status   string `json:"status"`
	Result   struct {
		Duration    float64 `json:"duration"`
		FPS         float64 `json:"fps"`
		Resolution  string  `json:"resolution"`
		ToolVersion string  `json:"tool_version"`
		VideoURL    string  `json:"video_url"`
	} `json:"result"`
	RequestID string `json:"request_id"`
	Error     struct {
		Code    string `json:"code"`
		Message string `json:"message"`
	} `json:"error"`
}

func NewMediaKitClientFromEnv() *MediaKitClient {
	baseURL := strings.TrimRight(os.Getenv("VOLCENGINE_MEDIAKIT_BASE_URL"), "/")
	if baseURL == "" {
		baseURL = "https://mediakit.cn-beijing.volces.com"
	}
	return &MediaKitClient{
		BaseURL: baseURL,
		APIKey:  os.Getenv("VOLCENGINE_MEDIAKIT_API_KEY"),
		Client:  &http.Client{Timeout: 30 * time.Second},
	}
}

func (c *MediaKitClient) SubmitEnhanceVideo(ctx context.Context, req MediaKitSubmitRequest) (*MediaKitSubmitResponse, error) {
	if c == nil || c.APIKey == "" {
		return nil, fmt.Errorf("volcengine mediakit api key is empty")
	}
	payload, err := common.Marshal(req)
	if err != nil {
		return nil, err
	}
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/api/v1/tools/enhance-video", bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+c.APIKey)
	resp, err := c.client().Do(httpReq)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("mediakit submit failed status=%d body=%s", resp.StatusCode, string(body))
	}
	var out MediaKitSubmitResponse
	if err := common.Unmarshal(body, &out); err != nil {
		return nil, err
	}
	if !out.Success || out.TaskID == "" {
		return nil, fmt.Errorf("mediakit submit returned invalid response: %s", string(body))
	}
	return &out, nil
}

func (c *MediaKitClient) GetEnhanceTask(ctx context.Context, taskID string) (*MediaKitTaskResponse, error) {
	if c == nil || c.APIKey == "" {
		return nil, fmt.Errorf("volcengine mediakit api key is empty")
	}
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+"/api/v1/tasks/"+taskID, nil)
	if err != nil {
		return nil, err
	}
	httpReq.Header.Set("Authorization", "Bearer "+c.APIKey)
	resp, err := c.client().Do(httpReq)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("mediakit query failed status=%d body=%s", resp.StatusCode, string(body))
	}
	var out MediaKitTaskResponse
	if err := common.Unmarshal(body, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

func (c *MediaKitClient) client() *http.Client {
	if c.Client != nil {
		return c.Client
	}
	return &http.Client{Timeout: 30 * time.Second}
}
