package service

import (
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
)

type generationPolicyMatch struct {
	Name       string
	Reason     string
	Generation VideoGenerationPlan
}

func selectGenerationPolicy(info *relaycommon.RelayInfo, req relaycommon.TaskSubmitReq, analysis model.VideoRequestAnalysis) generationPolicyMatch {
	base := VideoGenerationPlan{
		Provider:   VideoPipelineProviderDoubaoVideo,
		Model:      req.Model,
		Resolution: "720p",
		Ratio:      analysis.Ratio,
		Mapper:     "seedance_basic_mapper",
	}
	if info != nil && info.ChannelMeta != nil && info.ChannelId > 0 {
		base.ChannelID = info.ChannelId
	}
	if analysis.Storyboard {
		base.Model = "doubao-seedance-1-5-pro-251215"
		base.Mapper = "seedance_storyboard_legacy_mapper"
		return generationPolicyMatch{Name: "storyboard-compat-seedance-legacy", Reason: "storyboard_request", Generation: base}
	}
	if analysis.ReferenceCount >= 2 {
		base.Model = "doubao-seedance-2-0-260128"
		base.Mapper = "seedance_multi_reference_mapper"
		return generationPolicyMatch{Name: "multi-reference-stable-generation", Reason: "reference_count_ge_2", Generation: base}
	}
	if analysis.MotionClass == "static_or_low_motion" && analysis.AnalysisConfidence >= 0.7 {
		base.Model = "doubao-seedance-1-5-pro-251215"
		return generationPolicyMatch{Name: "static-prompt-low-cost-720p", Reason: analysis.ReasonCode, Generation: base}
	}
	if base.Model == "" {
		base.Model = "doubao-seedance-2-0-260128"
	}
	return generationPolicyMatch{Name: "dynamic-default-seedance-2-720p", Reason: "default_1080p_pipeline", Generation: base}
}

func selectEnhancePolicy(analysis model.VideoRequestAnalysis, generation VideoGenerationPlan) *VideoEnhancePlan {
	if analysis.RequestedResolution == generation.Resolution {
		return nil
	}
	if analysis.RequestedResolution == "1080p" && generation.Resolution == "720p" {
		return &VideoEnhancePlan{
			Provider:    VideoPipelineProviderVolcengineMediaKit,
			Scene:       "aigc",
			ToolVersion: "standard",
			Resolution:  "1080p",
			FPS:         "keep_source",
		}
	}
	return nil
}
