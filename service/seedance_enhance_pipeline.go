package service

import (
	"context"
	"fmt"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relay/channel/task/taskcommon"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
)

func AdvanceVideoPipelineIfNeeded(ctx context.Context, task *model.Task, taskResult *relaycommon.TaskInfo, responseBody []byte) (bool, error) {
	if task == nil || task.PrivateData.SeedanceEnhance == nil || taskResult == nil {
		return false, nil
	}
	p := task.PrivateData.SeedanceEnhance
	if p.EnhanceProvider == "" || p.EnhanceTargetResolution == "" {
		return false, nil
	}
	if taskResult.Status != model.TaskStatusSuccess && p.EnhanceTaskID == "" {
		return false, nil
	}
	snap := task.Snapshot()
	now := time.Now().Unix()

	if p.GenerationVideoURL == "" && taskResult.Url != "" {
		p.GenerationVideoURL = taskResult.Url
	}
	if len(responseBody) > 0 {
		task.Data = redactVideoResponseBody(responseBody)
	}

	client := NewMediaKitClientFromEnv()
	if p.EnhanceTaskID == "" {
		submit, err := client.SubmitEnhanceVideo(ctx, MediaKitSubmitRequest{
			VideoURL:    p.GenerationVideoURL,
			Scene:       "aigc",
			ToolVersion: "standard",
			Resolution:  p.EnhanceTargetResolution,
		})
		if err != nil {
			return completePipelineWithFallback(ctx, task, snap.Status, fmt.Sprintf("submit enhance failed: %s", err.Error()))
		}
		p.EnhanceTaskID = submit.TaskID
		p.EnhanceRequestID = submit.RequestID
		p.Status = "enhance_submitted"
		task.Status = model.TaskStatusInProgress
		task.Progress = "70%"
		if task.StartTime == 0 {
			task.StartTime = now
		}
		won, err := task.UpdateWithStatus(snap.Status)
		if err != nil || !won {
			return true, err
		}
		return true, nil
	}

	res, err := client.GetEnhanceTask(ctx, p.EnhanceTaskID)
	if err != nil {
		p.EnhanceError = err.Error()
		p.Status = "enhance_running"
		task.Status = model.TaskStatusInProgress
		task.Progress = "85%"
		_, updateErr := task.UpdateWithStatus(snap.Status)
		return true, updateErr
	}
	switch res.Status {
	case "completed", "success", "succeeded":
		p.Status = "enhance_succeeded"
		p.EnhancedVideoURL = res.Result.VideoURL
		p.ActualDurationSeconds = res.Result.Duration
		p.ActualFPS = res.Result.FPS
		p.EnhanceCostQuota = calculateMediaKitEnhanceCostQuota(res.Result.Duration, res.Result.FPS)
		p.PipelineProviderCost = p.GenerationCostQuota + p.EnhanceCostQuota
		task.PrivateData.ResultURL = res.Result.VideoURL
		task.Status = model.TaskStatusSuccess
		task.Progress = taskcommon.ProgressComplete
		if task.FinishTime == 0 {
			task.FinishTime = now
		}
	case "failed", "failure":
		return completePipelineWithFallback(ctx, task, snap.Status, "enhance failed")
	default:
		p.Status = "enhance_running"
		task.Status = model.TaskStatusInProgress
		task.Progress = "85%"
	}
	won, err := task.UpdateWithStatus(snap.Status)
	if err != nil || !won {
		return true, err
	}
	return true, nil
}

func completePipelineWithFallback(_ context.Context, task *model.Task, oldStatus model.TaskStatus, reason string) (bool, error) {
	p := task.PrivateData.SeedanceEnhance
	p.Status = "enhance_failed_fallback"
	p.EnhanceError = reason
	task.PrivateData.ResultURL = p.GenerationVideoURL
	task.Status = model.TaskStatusSuccess
	task.Progress = taskcommon.ProgressComplete
	task.FailReason = ""
	if task.FinishTime == 0 {
		task.FinishTime = time.Now().Unix()
	}
	if task.PrivateData.ResultURL == "" {
		task.PrivateData.ResultURL = taskcommon.BuildProxyURL(task.TaskID)
	}
	won, err := task.UpdateWithStatus(oldStatus)
	if err != nil || !won {
		return true, err
	}
	common.SysLog(fmt.Sprintf("video pipeline fallback task=%s reason=%s", task.TaskID, reason))
	return true, nil
}

func calculateMediaKitEnhanceCostQuota(durationSeconds, fps float64) int {
	if durationSeconds <= 0 {
		return 0
	}
	unitPerSecond := 0.025
	if fps > 30 {
		unitPerSecond = 0.05
	}
	return int(durationSeconds * unitPerSecond * common.QuotaPerUnit)
}
