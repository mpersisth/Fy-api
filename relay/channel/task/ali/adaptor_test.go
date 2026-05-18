package ali

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/gin-gonic/gin"
)

func makeMappedInfo(upstreamModel string) *relaycommon.RelayInfo {
	return &relaycommon.RelayInfo{
		ChannelMeta: &relaycommon.ChannelMeta{
			IsModelMapped:     upstreamModel != "",
			UpstreamModelName: upstreamModel,
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{},
	}
}

func TestConvertToAliRequest_Wan26DefaultsTo720P(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name          string
		info          *relaycommon.RelayInfo
		model         string
		expectedModel string
	}{
		{
			name:          "direct model",
			info:          &relaycommon.RelayInfo{},
			model:         "wan2.6-i2v",
			expectedModel: "wan2.6-i2v",
		},
		{
			name:          "mapped alias",
			info:          makeMappedInfo("wan2.6-i2v"),
			model:         "wan26-image-video",
			expectedModel: "wan2.6-i2v",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			adaptor := &TaskAdaptor{}
			req := relaycommon.TaskSubmitReq{
				Prompt:         "make a video",
				Model:          tc.model,
				InputReference: "https://example.com/frame.png",
			}

			aliReq, err := adaptor.convertToAliRequest(tc.info, req)
			if err != nil {
				t.Fatalf("convertToAliRequest() error = %v", err)
			}

			if got, want := aliReq.Model, tc.expectedModel; got != want {
				t.Fatalf("model = %q, want %q", got, want)
			}
			if got, want := aliReq.Parameters.Resolution, "720P"; got != want {
				t.Fatalf("resolution = %q, want %q", got, want)
			}
		})
	}
}

func TestConvertToAliRequest_Wan26R2VFrames(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name          string
		info          *relaycommon.RelayInfo
		model         string
		expectedModel string
	}{
		{
			name:          "direct model",
			info:          &relaycommon.RelayInfo{},
			model:         "wan2.6-r2v",
			expectedModel: "wan2.6-r2v",
		},
		{
			name:          "mapped alias",
			info:          makeMappedInfo("wan2.6-r2v"),
			model:         "wan26-transition-video",
			expectedModel: "wan2.6-r2v",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			adaptor := &TaskAdaptor{}
			req := relaycommon.TaskSubmitReq{
				Prompt:         "transition video",
				Model:          tc.model,
				InputReference: "https://example.com/first.png",
				Metadata: map[string]interface{}{
					"last_frame_url": "https://example.com/last.png",
					"img_url":        "https://example.com/should-not-win.png",
				},
			}

			aliReq, err := adaptor.convertToAliRequest(tc.info, req)
			if err != nil {
				t.Fatalf("convertToAliRequest() error = %v", err)
			}
			if got, want := aliReq.Model, tc.expectedModel; got != want {
				t.Fatalf("model = %q, want %q", got, want)
			}
			if got, want := aliReq.Input.FirstFrameURL, "https://example.com/first.png"; got != want {
				t.Fatalf("first_frame_url = %q, want %q", got, want)
			}
			if got := aliReq.Input.ImgURL; got != "" {
				t.Fatalf("img_url = %q, want empty", got)
			}
			if got, want := aliReq.Input.LastFrameURL, "https://example.com/last.png"; got != want {
				t.Fatalf("last_frame_url = %q, want %q", got, want)
			}
		})
	}
}

func TestValidateRequestAndSetAction_RejectsUnsupportedWan26Resolution(t *testing.T) {
	t.Parallel()
	gin.SetMode(gin.TestMode)

	testCases := []struct {
		name string
		info *relaycommon.RelayInfo
		body string
	}{
		{
			name: "invalid top level size",
			info: &relaycommon.RelayInfo{TaskRelayInfo: &relaycommon.TaskRelayInfo{}},
			body: `{
				"prompt":"make a video",
				"model":"wan2.6-i2v",
				"input_reference":"https://example.com/frame.png",
				"size":"2K"
			}`,
		},
		{
			name: "invalid metadata resolution on mapped alias",
			info: makeMappedInfo("wan2.6-r2v"),
			body: `{
				"prompt":"transition video",
				"model":"wan26-transition-video",
				"input_reference":"https://example.com/first.png",
				"metadata":{
					"last_frame_url":"https://example.com/last.png",
					"parameters":{"resolution":"2K"}
				}
			}`,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			req := httptest.NewRequest(http.MethodPost, "/v1/videos", bytes.NewReader([]byte(tc.body)))
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			c, _ := gin.CreateTestContext(w)
			c.Request = req

			adaptor := &TaskAdaptor{}
			taskErr := adaptor.ValidateRequestAndSetAction(c, tc.info)
			if taskErr == nil {
				t.Fatal("ValidateRequestAndSetAction() error = nil, want invalid resolution error")
			}
			if got, want := taskErr.StatusCode, http.StatusBadRequest; got != want {
				t.Fatalf("status = %d, want %d", got, want)
			}
		})
	}
}

func TestAdjustBillingOnComplete_UsesActualAliDuration(t *testing.T) {
	t.Parallel()

	task := &model.Task{
		Properties: model.Properties{OriginModelName: "wan2.6-i2v"},
		PrivateData: model.TaskPrivateData{
			BillingContext: &model.TaskBillingContext{
				ModelRatio: 0.082,
				GroupRatio: 1,
				OtherRatios: map[string]float64{
					"seconds":          5,
					"resolution-1080P": 1 / 0.6,
				},
			},
		},
	}
	task.Data = []byte(`{
		"output":{"task_status":"SUCCEEDED","video_url":"https://example.com/video.mp4"},
		"usage":{"duration":3}
	}`)

	adaptor := &TaskAdaptor{}
	actualQuota := adaptor.AdjustBillingOnComplete(task, &relaycommon.TaskInfo{
		Status: model.TaskStatusSuccess,
	})

	expectedBaseQuota := int(0.082 / 2 * common.QuotaPerUnit * 1)
	expectedQuota := int(float64(expectedBaseQuota) * 3 * (1 / 0.6))
	if actualQuota != expectedQuota {
		t.Fatalf("actualQuota = %d, want %d", actualQuota, expectedQuota)
	}
}
