package controller

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/logger"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
)

type relayRetryBackoffState struct {
	retriesWaited int
	totalWait     time.Duration
}

type relayRetryBackoffConfig struct {
	base  time.Duration
	max   time.Duration
	total time.Duration
}

func normalizedRelayRetryBackoffConfig() relayRetryBackoffConfig {
	baseMs := common.RetryBaseIntervalMs
	maxMs := common.RetryMaxIntervalMs
	totalMs := common.RetryTotalTimeoutMs

	if baseMs < 0 {
		baseMs = 0
	}
	if maxMs < 0 {
		maxMs = 0
	}
	if totalMs < 0 {
		totalMs = 0
	}
	if maxMs < baseMs {
		maxMs = baseMs
	}

	return relayRetryBackoffConfig{
		base:  time.Duration(baseMs) * time.Millisecond,
		max:   time.Duration(maxMs) * time.Millisecond,
		total: time.Duration(totalMs) * time.Millisecond,
	}
}

func nextRelayRetryBackoffDelay(state *relayRetryBackoffState, cfg relayRetryBackoffConfig) (time.Duration, bool) {
	if cfg.total <= 0 || state.totalWait >= cfg.total {
		return 0, false
	}

	delay := cfg.base
	if delay > 0 && state.retriesWaited > 0 {
		for range state.retriesWaited {
			if delay >= cfg.max {
				delay = cfg.max
				break
			}
			if delay > cfg.max-delay {
				delay = cfg.max
				break
			}
			delay *= 2
		}
	}
	if cfg.max > 0 && delay > cfg.max {
		delay = cfg.max
	}
	if state.totalWait+delay > cfg.total {
		return 0, false
	}
	state.retriesWaited++
	state.totalWait += delay
	return delay, true
}

func waitForRelayRetryBackoff(ctx context.Context, delay time.Duration) error {
	if delay <= 0 {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
			return nil
		}
	}

	timer := time.NewTimer(delay)
	defer timer.Stop()

	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func waitBeforeNextRelayRetry(c *gin.Context, state *relayRetryBackoffState, statusCode int, retryCurrent int, retryLimit int) *types.NewAPIError {
	delay, ok := nextRelayRetryBackoffDelay(state, normalizedRelayRetryBackoffConfig())
	if !ok {
		return types.NewErrorWithStatusCode(errors.New("retry backoff total timeout exceeded"), types.ErrorCodeDoRequestFailed, http.StatusRequestTimeout, types.ErrOptionWithSkipRetry())
	}

	logger.LogInfo(c, fmt.Sprintf("重试退避: request_id=%s, retry=%d/%d, wait=%dms, status_code=%d", c.GetString(common.RequestIdKey), retryCurrent, retryLimit, delay.Milliseconds(), statusCode))

	if err := waitForRelayRetryBackoff(c.Request.Context(), delay); err != nil {
		return types.NewErrorWithStatusCode(err, types.ErrorCodeDoRequestFailed, http.StatusRequestTimeout, types.ErrOptionWithSkipRetry())
	}
	return nil
}

func waitBeforeNextTaskRelayRetry(c *gin.Context, state *relayRetryBackoffState, statusCode int, retryCurrent int, retryLimit int) *dto.TaskError {
	delay, ok := nextRelayRetryBackoffDelay(state, normalizedRelayRetryBackoffConfig())
	if !ok {
		return service.TaskErrorWrapperLocal(errors.New("retry backoff total timeout exceeded"), "retry_backoff_total_timeout_exceeded", http.StatusRequestTimeout)
	}

	logger.LogInfo(c, fmt.Sprintf("任务重试退避: request_id=%s, retry=%d/%d, wait=%dms, status_code=%d", c.GetString(common.RequestIdKey), retryCurrent, retryLimit, delay.Milliseconds(), statusCode))

	if err := waitForRelayRetryBackoff(c.Request.Context(), delay); err != nil {
		return service.TaskErrorWrapperLocal(err, "retry_backoff_context_canceled", http.StatusRequestTimeout)
	}
	return nil
}
