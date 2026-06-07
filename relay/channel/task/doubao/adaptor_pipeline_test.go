package doubao

import (
	"io"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestBuildRequestBodyAppliesVideoPipelinePlan(t *testing.T) {
	gin.SetMode(gin.TestMode)
	t.Setenv("SEEDANCE_PIPELINE_ENABLED", "true")
	t.Setenv("SEEDANCE_PIPELINE_TRAFFIC_PERCENT", "100")

	body := `{"model":"doubao-seedance-2-0-260128","prompt":"固定镜头的产品展示","seconds":"5","size":"1920x1080","metadata":{"fy_enhance_force":true}}`
	req := httptest.NewRequest("POST", "/v1/videos", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req

	info := &relaycommon.RelayInfo{
		OriginModelName: "doubao-seedance-2-0-260128",
		ChannelMeta:     &relaycommon.ChannelMeta{},
		TaskRelayInfo:   &relaycommon.TaskRelayInfo{},
	}
	adaptor := &TaskAdaptor{}
	taskErr := adaptor.ValidateRequestAndSetAction(c, info)
	require.Nil(t, taskErr)

	reader, err := adaptor.BuildRequestBody(c, info)
	require.NoError(t, err)
	raw, err := io.ReadAll(reader)
	require.NoError(t, err)
	var payload map[string]any
	require.NoError(t, common.Unmarshal(raw, &payload))

	assert.Equal(t, "doubao-seedance-1-5-pro-251215", payload["model"])
	assert.Equal(t, "720p", payload["resolution"])
	plan, ok := service.GetVideoPipelinePlan(c)
	require.True(t, ok)
	assert.Equal(t, "static-prompt-low-cost-720p", plan.MatchedGenerationPolicy)
}
