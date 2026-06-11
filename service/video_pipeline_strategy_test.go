package service

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestAnalyzeVideoRequest_StaticPromptAndReferences(t *testing.T) {
	req := relaycommon.TaskSubmitReq{
		Model:          "doubao-seedance-2-0-260128",
		Prompt:         "固定镜头的产品展示，海报感",
		Size:           "1080x1920",
		InputReference: "https://example.com/a.png",
		Images:         []string{"https://example.com/b.png"},
		Metadata: map[string]interface{}{
			"storyboard": true,
			"shot_count": 3,
		},
	}

	analysis, err := AnalyzeVideoRequest(req)
	require.NoError(t, err)
	assert.Equal(t, "1080p", analysis.RequestedResolution)
	assert.Equal(t, "9:16", analysis.Ratio)
	assert.Equal(t, 2, analysis.ReferenceCount)
	assert.True(t, analysis.Storyboard)
	assert.Equal(t, 3, analysis.ShotCount)
	assert.Equal(t, "static_or_low_motion", analysis.MotionClass)
}

func TestBuildVideoPipelinePlan_SelectsStoryboardPolicy(t *testing.T) {
	t.Setenv("SEEDANCE_PIPELINE_ENABLED", "true")
	t.Setenv("SEEDANCE_PIPELINE_TRAFFIC_PERCENT", "100")

	req := relaycommon.TaskSubmitReq{
		Model:  "doubao-seedance-2-0-260128",
		Prompt: "一组多镜头故事版",
		Size:   "1920x1080",
		Metadata: map[string]interface{}{
			"storyboard": true,
			"shot_count": 2,
		},
	}
	info := &relaycommon.RelayInfo{UserId: 7, TokenId: 9, OriginModelName: req.Model}

	plan, err := BuildVideoPipelinePlan(nil, info, req)
	require.NoError(t, err)
	require.NotNil(t, plan)
	assert.Equal(t, "storyboard-compat-seedance-legacy", plan.MatchedGenerationPolicy)
	assert.Equal(t, "doubao-seedance-1-5-pro-251215", plan.Generation.Model)
	assert.Equal(t, "720p", plan.Generation.Resolution)
	require.NotNil(t, plan.Enhance)
	assert.Equal(t, "1080p", plan.Enhance.Resolution)
}

func TestBuildVideoPipelinePlan_IgnoresRequestForceUnlessAllowed(t *testing.T) {
	t.Setenv("SEEDANCE_PIPELINE_ENABLED", "false")
	t.Setenv("SEEDANCE_PIPELINE_ALLOW_REQUEST_OVERRIDE", "false")

	req := relaycommon.TaskSubmitReq{
		Model:  "doubao-seedance-2-0-260128",
		Prompt: "固定镜头的产品展示",
		Size:   "1920x1080",
		Metadata: map[string]interface{}{
			"fy_enhance_force": true,
		},
	}

	plan, err := BuildVideoPipelinePlan(nil, &relaycommon.RelayInfo{OriginModelName: req.Model}, req)
	require.NoError(t, err)
	assert.Nil(t, plan)
}

func TestAnalyzeVideoRequest_ResolutionSizeAllowsMetadataRatio(t *testing.T) {
	analysis, err := AnalyzeVideoRequest(relaycommon.TaskSubmitReq{
		Model:  "doubao-seedance-2-0-260128",
		Prompt: "test",
		Size:   "1080p",
		Metadata: map[string]interface{}{
			"ratio": "9:16",
		},
	})
	require.NoError(t, err)
	assert.Equal(t, "1080p", analysis.RequestedResolution)
	assert.Equal(t, "9:16", analysis.Ratio)
}

func TestMediaKitClientSubmitAndQuery(t *testing.T) {
	var submitted bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "Bearer test-key", r.Header.Get("Authorization"))
		switch r.URL.Path {
		case "/api/v1/tools/enhance-video":
			submitted = true
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"success":true,"task_id":"enhance-1","request_id":"req-1"}`))
		case "/api/v1/tasks/enhance-1":
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"success":true,"task_id":"enhance-1","status":"completed","result":{"duration":5,"fps":30,"resolution":"1080p","video_url":"https://cdn.example.com/enhanced.mp4"},"request_id":"req-2"}`))
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	client := &MediaKitClient{BaseURL: srv.URL, APIKey: "test-key", Client: srv.Client()}
	submit, err := client.SubmitEnhanceVideo(t.Context(), MediaKitSubmitRequest{VideoURL: "https://cdn.example.com/gen.mp4", Scene: "aigc", ToolVersion: "standard", Resolution: "1080p"})
	require.NoError(t, err)
	assert.True(t, submitted)
	assert.Equal(t, "enhance-1", submit.TaskID)

	task, err := client.GetEnhanceTask(t.Context(), "enhance-1")
	require.NoError(t, err)
	assert.Equal(t, "completed", task.Status)
	assert.Equal(t, "https://cdn.example.com/enhanced.mp4", task.Result.VideoURL)
}

func TestAdvanceVideoPipelineIfNeeded_SubmitThenComplete(t *testing.T) {
	truncate(t)
	t.Setenv("VOLCENGINE_MEDIAKIT_API_KEY", "test-key")

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/tools/enhance-video":
			_, _ = w.Write([]byte(`{"success":true,"task_id":"enhance-1","request_id":"req-1"}`))
		case "/api/v1/tasks/enhance-1":
			_, _ = w.Write([]byte(`{"success":true,"task_id":"enhance-1","status":"completed","result":{"duration":5,"fps":30,"resolution":"1080p","video_url":"https://cdn.example.com/enhanced.mp4"}}`))
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()
	t.Setenv("VOLCENGINE_MEDIAKIT_BASE_URL", srv.URL)

	task := &model.Task{
		TaskID:    "task_pipeline_e2e",
		UserId:    1,
		ChannelId: 1,
		Status:    model.TaskStatusInProgress,
		Progress:  "50%",
		Data:      []byte(`{}`),
		PrivateData: model.TaskPrivateData{
			UpstreamTaskID: "generation-1",
			SeedanceEnhance: &model.SeedanceEnhancePipeline{
				Pipeline:                VideoPipelineNameSeedanceEnhance,
				Status:                  VideoPipelineStatusGenerationSubmitted,
				RequestedResolution:     "1080p",
				GenerationResolution:    "720p",
				EnhanceTargetResolution: "1080p",
				EnhanceProvider:         VideoPipelineProviderVolcengineMediaKit,
			},
		},
	}
	require.NoError(t, model.DB.Create(task).Error)

	handled, err := AdvanceVideoPipelineIfNeeded(t.Context(), task, &relaycommon.TaskInfo{
		Status: model.TaskStatusSuccess,
		Url:    "https://cdn.example.com/generated.mp4",
	}, []byte(`{"status":"succeeded"}`))
	require.NoError(t, err)
	assert.True(t, handled)

	var reloaded model.Task
	require.NoError(t, model.DB.Where("task_id = ?", task.TaskID).First(&reloaded).Error)
	require.NotNil(t, reloaded.PrivateData.SeedanceEnhance)
	assert.Equal(t, "enhance-1", reloaded.PrivateData.SeedanceEnhance.EnhanceTaskID)
	assert.EqualValues(t, model.TaskStatusInProgress, reloaded.Status)

	handled, err = AdvanceVideoPipelineIfNeeded(t.Context(), &reloaded, &relaycommon.TaskInfo{
		Status: model.TaskStatusInProgress,
	}, nil)
	require.NoError(t, err)
	assert.True(t, handled)

	var done model.Task
	require.NoError(t, model.DB.Where("task_id = ?", task.TaskID).First(&done).Error)
	assert.EqualValues(t, model.TaskStatusSuccess, done.Status)
	assert.Equal(t, "https://cdn.example.com/enhanced.mp4", done.PrivateData.ResultURL)
	assert.Equal(t, "enhance_succeeded", done.PrivateData.SeedanceEnhance.Status)
	assert.Equal(t, int(5*0.025*common.QuotaPerUnit), done.PrivateData.SeedanceEnhance.EnhanceCostQuota)
}
