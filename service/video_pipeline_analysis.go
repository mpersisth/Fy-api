package service

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
)

func AnalyzeVideoRequest(req relaycommon.TaskSubmitReq) (model.VideoRequestAnalysis, error) {
	resolution, ratio, err := resolveVideoResolutionAndRatio(req)
	if err != nil {
		return model.VideoRequestAnalysis{}, err
	}
	refCount, hasVideoRef := countReferences(req)
	storyboard, shotCount := detectStoryboard(req)
	motion, confidence, reason := classifyPromptMotion(req.Prompt)
	return model.VideoRequestAnalysis{
		RequestedResolution: resolution,
		Ratio:               ratio,
		ReferenceCount:      refCount,
		HasVideoReference:   hasVideoRef,
		Storyboard:          storyboard,
		ShotCount:           shotCount,
		MotionClass:         motion,
		AnalysisConfidence:  confidence,
		ReasonCode:          reason,
	}, nil
}

func resolveVideoResolutionAndRatio(req relaycommon.TaskSubmitReq) (string, string, error) {
	sizeResolution, sizeRatio, sizeKnown := resolutionFromSize(req.Size)
	metaResolution := strings.ToLower(strings.TrimSpace(metadataString(req.Metadata, "resolution")))
	metaRatio := strings.TrimSpace(metadataString(req.Metadata, "ratio"))

	if metaResolution != "" {
		metaResolution = strings.ToLower(metaResolution)
		if metaResolution == "720p" || metaResolution == "720P" {
			metaResolution = "720p"
		}
		if metaResolution == "1080p" || metaResolution == "1080P" {
			metaResolution = "1080p"
		}
	}
	if sizeKnown && metaResolution != "" && metaResolution != sizeResolution {
		return "", "", fmt.Errorf("size conflicts with metadata.resolution")
	}
	if sizeKnown && sizeRatio != "" && metaRatio != "" && metaRatio != sizeRatio {
		return "", "", fmt.Errorf("size conflicts with metadata.ratio")
	}
	if sizeKnown {
		if sizeRatio == "" {
			sizeRatio = metaRatio
		}
		if sizeRatio == "" {
			sizeRatio = "16:9"
		}
		return sizeResolution, sizeRatio, nil
	}
	if metaResolution == "720p" || metaResolution == "1080p" {
		if metaRatio == "" {
			metaRatio = "16:9"
		}
		return metaResolution, metaRatio, nil
	}
	if req.Size == "" && metaResolution == "" {
		return "720p", "16:9", nil
	}
	return "", "", fmt.Errorf("unsupported video size or resolution")
}

func resolutionFromSize(size string) (string, string, bool) {
	switch strings.TrimSpace(size) {
	case "1920x1080":
		return "1080p", "16:9", true
	case "1080x1920":
		return "1080p", "9:16", true
	case "1280x720":
		return "720p", "16:9", true
	case "720x1280":
		return "720p", "9:16", true
	case "1080p", "1080P":
		return "1080p", "", true
	case "720p", "720P":
		return "720p", "", true
	default:
		return "", "", false
	}
}

func countReferences(req relaycommon.TaskSubmitReq) (int, bool) {
	count := 0
	if req.InputReference != "" {
		count++
	}
	if req.Image != "" {
		count++
	}
	count += len(req.Images)
	hasVideo := false
	for _, item := range req.Media {
		if strings.EqualFold(item.Type, "video") || strings.EqualFold(item.Type, "video_url") {
			hasVideo = true
		}
		if item.URL != "" {
			count++
		}
	}
	return count, hasVideo
}

func detectStoryboard(req relaycommon.TaskSubmitReq) (bool, int) {
	if metadataBool(req.Metadata, "storyboard") || metadataBool(req.Metadata, "story_board") {
		return true, metadataInt(req.Metadata, "shot_count")
	}
	if n := metadataInt(req.Metadata, "shot_count"); n > 1 {
		return true, n
	}
	return false, 0
}

func classifyPromptMotion(prompt string) (string, float64, string) {
	p := strings.ToLower(prompt)
	dynamicTerms := []string{"run", "running", "fast", "explosion", "camera move", "tracking shot", "dolly", "奔跑", "快速", "爆炸", "镜头移动", "运镜", "转场"}
	for _, term := range dynamicTerms {
		if strings.Contains(p, term) {
			return "dynamic", 0.8, "motion_keyword"
		}
	}
	staticTerms := []string{"still", "static", "fixed camera", "product", "portrait", "poster", "静物", "固定镜头", "产品展示", "肖像", "海报"}
	for _, term := range staticTerms {
		if strings.Contains(p, term) {
			return "static_or_low_motion", 0.8, "static_keyword"
		}
	}
	return "unknown", 0, "no_fast_path_match"
}

func metadataString(metadata map[string]interface{}, key string) string {
	if metadata == nil {
		return ""
	}
	v, ok := metadata[key]
	if !ok {
		return ""
	}
	switch t := v.(type) {
	case string:
		return t
	default:
		return fmt.Sprint(t)
	}
}

func metadataBool(metadata map[string]interface{}, key string) bool {
	if metadata == nil {
		return false
	}
	v, ok := metadata[key]
	if !ok {
		return false
	}
	switch t := v.(type) {
	case bool:
		return t
	case string:
		b, _ := strconv.ParseBool(t)
		return b
	default:
		return false
	}
}

func metadataInt(metadata map[string]interface{}, key string) int {
	if metadata == nil {
		return 0
	}
	v, ok := metadata[key]
	if !ok {
		return 0
	}
	switch t := v.(type) {
	case int:
		return t
	case int64:
		return int(t)
	case float64:
		return int(t)
	case string:
		n, _ := strconv.Atoi(t)
		return n
	default:
		return 0
	}
}
