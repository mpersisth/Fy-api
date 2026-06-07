package model

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestTaskPrivateDataSeedanceEnhanceRoundTrip(t *testing.T) {
	in := TaskPrivateData{
		UpstreamTaskID: "generation-1",
		SeedanceEnhance: &SeedanceEnhancePipeline{
			Pipeline:                "seedance_720_to_1080_enhance",
			Status:                  "generation_submitted",
			RequestedResolution:     "1080p",
			GenerationResolution:    "720p",
			EnhanceTargetResolution: "1080p",
			MatchedGenerationPolicy: "static-prompt-low-cost-720p",
			Analysis: VideoRequestAnalysis{
				MotionClass:        "static_or_low_motion",
				ReferenceCount:     1,
				AnalysisConfidence: 0.8,
			},
		},
	}
	b, err := common.Marshal(in)
	require.NoError(t, err)
	var out TaskPrivateData
	require.NoError(t, common.Unmarshal(b, &out))
	require.NotNil(t, out.SeedanceEnhance)
	assert.Equal(t, in.SeedanceEnhance.MatchedGenerationPolicy, out.SeedanceEnhance.MatchedGenerationPolicy)
	assert.Equal(t, in.SeedanceEnhance.Analysis.MotionClass, out.SeedanceEnhance.Analysis.MotionClass)
}
