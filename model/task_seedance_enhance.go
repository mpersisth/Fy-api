package model

type VideoRequestAnalysis struct {
	RequestedResolution string  `json:"requested_resolution,omitempty"`
	Ratio               string  `json:"ratio,omitempty"`
	ReferenceCount      int     `json:"reference_count,omitempty"`
	HasVideoReference   bool    `json:"has_video_reference,omitempty"`
	Storyboard          bool    `json:"storyboard,omitempty"`
	ShotCount           int     `json:"shot_count,omitempty"`
	MotionClass         string  `json:"motion_class,omitempty"`
	AnalysisConfidence  float64 `json:"analysis_confidence,omitempty"`
	ReasonCode          string  `json:"reason_code,omitempty"`
}

type SeedanceEnhancePipeline struct {
	Pipeline                string               `json:"pipeline,omitempty"`
	Status                  string               `json:"status,omitempty"`
	RequestedResolution     string               `json:"requested_resolution,omitempty"`
	GenerationResolution    string               `json:"generation_resolution,omitempty"`
	EnhanceTargetResolution string               `json:"enhance_target_resolution,omitempty"`
	Ratio                   string               `json:"ratio,omitempty"`
	Strategy                string               `json:"strategy,omitempty"`
	StrategyVersion         string               `json:"strategy_version,omitempty"`
	Fallback                string               `json:"fallback,omitempty"`
	Analysis                VideoRequestAnalysis `json:"analysis,omitempty"`
	MatchedGenerationPolicy string               `json:"matched_generation_policy,omitempty"`
	MatchedEnhancePolicy    string               `json:"matched_enhance_policy,omitempty"`
	MatchReason             string               `json:"match_reason,omitempty"`
	MappedFields            []string             `json:"mapped_fields,omitempty"`
	DroppedFields           []string             `json:"dropped_fields,omitempty"`
	UserRequestedModel      string               `json:"user_requested_model,omitempty"`
	GenerationProvider      string               `json:"generation_provider,omitempty"`
	GenerationModel         string               `json:"generation_model,omitempty"`
	GenerationChannelID     int                  `json:"generation_channel_id,omitempty"`
	GenerationTaskID        string               `json:"generation_task_id,omitempty"`
	GenerationVideoURL      string               `json:"generation_video_url,omitempty"`
	EnhanceProvider         string               `json:"enhance_provider,omitempty"`
	EnhanceTaskID           string               `json:"enhance_task_id,omitempty"`
	EnhancedVideoURL        string               `json:"enhanced_video_url,omitempty"`
	EnhanceRequestID        string               `json:"enhance_request_id,omitempty"`
	EnhanceError            string               `json:"enhance_error,omitempty"`
	ActualDurationSeconds   float64              `json:"actual_duration_seconds,omitempty"`
	ActualFPS               float64              `json:"actual_fps,omitempty"`
	GenerationCostQuota     int                  `json:"generation_cost_quota,omitempty"`
	EnhanceCostQuota        int                  `json:"enhance_cost_quota,omitempty"`
	PipelineProviderCost    int                  `json:"pipeline_provider_cost_quota,omitempty"`
	UserBilledQuota         int                  `json:"user_billed_quota,omitempty"`
}
