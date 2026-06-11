package service

import (
	"hash/fnv"
	"os"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"

	"github.com/gin-gonic/gin"
)

const (
	videoPipelineContextKey = "fy_video_pipeline_plan"

	VideoPipelineNameSeedanceEnhance       = "seedance_720_to_1080_enhance"
	VideoPipelineStatusGenerationSubmitted = "generation_submitted"

	VideoPipelineFallbackReturnGeneration = "return_generation_result"

	VideoPipelineProviderDoubaoVideo        = "doubao-video"
	VideoPipelineProviderVolcengineMediaKit = "volcengine-mediakit"
)

type VideoPipelinePlan struct {
	StrategyName            string
	StrategyVersion         string
	Analysis                model.VideoRequestAnalysis
	UserRequestedModel      string
	RequestedResolution     string
	RequestedRatio          string
	Generation              VideoGenerationPlan
	Enhance                 *VideoEnhancePlan
	Fallback                VideoPipelineFallback
	MatchedGenerationPolicy string
	MatchedEnhancePolicy    string
	MatchReason             string
	MappedFields            []string
	DroppedFields           []string
}

type VideoGenerationPlan struct {
	Provider   string
	Model      string
	Resolution string
	Ratio      string
	ChannelID  int
	Mapper     string
}

type VideoEnhancePlan struct {
	Provider    string
	Scene       string
	ToolVersion string
	Resolution  string
	FPS         string
}

type VideoPipelineFallback struct {
	EnhanceFailed    string
	GenerationFailed string
}

func StoreVideoPipelinePlan(c *gin.Context, plan *VideoPipelinePlan) {
	if c == nil || plan == nil {
		return
	}
	c.Set(videoPipelineContextKey, plan)
}

func GetVideoPipelinePlan(c *gin.Context) (*VideoPipelinePlan, bool) {
	if c == nil {
		return nil, false
	}
	v, ok := c.Get(videoPipelineContextKey)
	if !ok {
		return nil, false
	}
	plan, ok := v.(*VideoPipelinePlan)
	return plan, ok && plan != nil
}

func BuildVideoPipelinePlan(c *gin.Context, info *relaycommon.RelayInfo, req relaycommon.TaskSubmitReq) (*VideoPipelinePlan, error) {
	analysis, err := AnalyzeVideoRequest(req)
	if err != nil {
		return nil, err
	}
	if !isSeedancePipelineCandidate(info, req, analysis) {
		return nil, nil
	}
	allowRequestOverride := requestStrategyOverrideAllowed()
	if allowRequestOverride && metadataBool(req.Metadata, "fy_enhance_bypass") {
		return nil, nil
	}
	force := allowRequestOverride && metadataBool(req.Metadata, "fy_enhance_force")
	if !force && !seedancePipelineEnabled() {
		return nil, nil
	}
	if !force && !rolloutMatched(info, analysis) {
		return nil, nil
	}

	gen := selectGenerationPolicy(info, req, analysis)
	enhance := selectEnhancePolicy(analysis, gen.Generation)
	plan := &VideoPipelinePlan{
		StrategyName:        VideoPipelineNameSeedanceEnhance,
		StrategyVersion:     "v1",
		Analysis:            analysis,
		UserRequestedModel:  req.Model,
		RequestedResolution: analysis.RequestedResolution,
		RequestedRatio:      analysis.Ratio,
		Generation:          gen.Generation,
		Enhance:             enhance,
		Fallback: VideoPipelineFallback{
			EnhanceFailed:    VideoPipelineFallbackReturnGeneration,
			GenerationFailed: "fail_task",
		},
		MatchedGenerationPolicy: gen.Name,
		MatchReason:             gen.Reason,
		MappedFields:            mappedFieldsForMapper(gen.Generation.Mapper),
		DroppedFields:           droppedFieldsForMapper(gen.Generation.Mapper, req.Metadata),
	}
	if enhance != nil {
		plan.MatchedEnhancePolicy = "enhance-720-to-1080-standard"
	} else {
		plan.MatchedEnhancePolicy = "no-enhance-when-target-met"
	}
	return plan, nil
}

func ApplyVideoPipelineSubmitSnapshot(c *gin.Context, task *model.Task, info *relaycommon.RelayInfo) {
	plan, ok := GetVideoPipelinePlan(c)
	if !ok || task == nil || plan == nil {
		return
	}
	p := &model.SeedanceEnhancePipeline{
		Pipeline:                VideoPipelineNameSeedanceEnhance,
		Status:                  VideoPipelineStatusGenerationSubmitted,
		RequestedResolution:     plan.RequestedResolution,
		GenerationResolution:    plan.Generation.Resolution,
		Ratio:                   plan.RequestedRatio,
		Strategy:                plan.StrategyName,
		StrategyVersion:         plan.StrategyVersion,
		Fallback:                plan.Fallback.EnhanceFailed,
		Analysis:                plan.Analysis,
		MatchedGenerationPolicy: plan.MatchedGenerationPolicy,
		MatchedEnhancePolicy:    plan.MatchedEnhancePolicy,
		MatchReason:             plan.MatchReason,
		MappedFields:            plan.MappedFields,
		DroppedFields:           plan.DroppedFields,
		UserRequestedModel:      plan.UserRequestedModel,
		GenerationProvider:      plan.Generation.Provider,
		GenerationModel:         plan.Generation.Model,
		GenerationTaskID:        task.PrivateData.UpstreamTaskID,
		EnhanceProvider:         "",
		UserBilledQuota:         task.Quota,
	}
	if plan.Enhance != nil {
		p.EnhanceProvider = plan.Enhance.Provider
		p.EnhanceTargetResolution = plan.Enhance.Resolution
	}
	if info != nil && info.ChannelMeta != nil {
		p.GenerationChannelID = info.ChannelId
	}
	task.PrivateData.SeedanceEnhance = p
}

func isSeedancePipelineCandidate(info *relaycommon.RelayInfo, req relaycommon.TaskSubmitReq, analysis model.VideoRequestAnalysis) bool {
	modelName := req.Model
	if modelName == "" && info != nil {
		modelName = info.OriginModelName
	}
	if !isSeedance2Model(modelName) {
		return false
	}
	return analysis.RequestedResolution == "1080p"
}

func isSeedance2Model(modelName string) bool {
	return modelName == "doubao-seedance-2-0-260128" || modelName == "doubao-seedance-2-0-fast-260128"
}

func seedancePipelineEnabled() bool {
	return strings.EqualFold(os.Getenv("SEEDANCE_PIPELINE_ENABLED"), "true")
}

func requestStrategyOverrideAllowed() bool {
	return strings.EqualFold(os.Getenv("SEEDANCE_PIPELINE_ALLOW_REQUEST_OVERRIDE"), "true")
}

func rolloutMatched(info *relaycommon.RelayInfo, analysis model.VideoRequestAnalysis) bool {
	percent := 0
	if v := strings.TrimSpace(os.Getenv("SEEDANCE_PIPELINE_TRAFFIC_PERCENT")); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			percent = n
		}
	}
	if percent <= 0 {
		return false
	}
	if percent >= 100 {
		return true
	}
	key := analysis.RequestedResolution
	if info != nil {
		key = strconv.Itoa(info.UserId) + ":" + strconv.Itoa(info.TokenId) + ":" + info.OriginModelName + ":" + analysis.RequestedResolution
	}
	h := fnv.New32a()
	_, _ = h.Write([]byte(key))
	return int(h.Sum32()%10000) < percent*100
}
