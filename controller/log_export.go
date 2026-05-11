package controller

// log_export.go is a Fy-api overlay file (not from upstream new-api).
// It exposes /api/log/export (admin) and /api/log/self/export (user) for
// downloading the currently filtered logs as a UTF-8 CSV with BOM so that
// Excel on Windows can open it without mojibake.
//
// The CSV column layout intentionally mirrors the web console's Usage Logs
// table and additionally includes request_id, which lets operators trace an
// exported row back to the originating request header X-Oneapi-Request-Id.

import (
	"encoding/csv"
	"fmt"
	"strconv"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/setting/operation_setting"

	"github.com/gin-gonic/gin"
)

var csvHeader = []string{
	"时间", "渠道", "用户", "令牌", "分组", "类型", "模型",
	"用时/首字", "输入", "输出", "Quota", "人民币", "美金", "IP", "重试", "request_id", "详情",
}

// writeLogsCSV streams the given logs as a CSV file in the HTTP response.
// UTF-8 BOM is prepended so that Excel opens the file correctly on Windows.
func writeLogsCSV(c *gin.Context, logs []*model.Log) {
	c.Header("Content-Disposition", "attachment; filename=logs.csv")
	c.Header("Content-Type", "text/csv; charset=utf-8")

	// BOM
	_, _ = c.Writer.Write([]byte("\xEF\xBB\xBF"))

	w := csv.NewWriter(c.Writer)
	if err := w.Write(csvHeader); err != nil {
		w.Flush()
		return
	}
	for _, log := range logs {
		timeStr := ""
		if log.CreatedAt != 0 {
			timeStr = time.Unix(log.CreatedAt, 0).Format("2006-01-02 15:04:05")
		}
		channelStr := ""
		if log.ChannelId != 0 || log.ChannelName != "" {
			channelStr = strconv.Itoa(log.ChannelId)
			if log.ChannelName != "" {
				channelStr = channelStr + " - " + log.ChannelName
			}
		}
		useTimeStr := ""
		if log.UseTime > 0 {
			useTimeStr = strconv.Itoa(log.UseTime)
		}
		promptStr := ""
		if log.PromptTokens > 0 {
			promptStr = strconv.Itoa(log.PromptTokens)
		}
		completionStr := ""
		if log.CompletionTokens > 0 {
			completionStr = strconv.Itoa(log.CompletionTokens)
		}
		quotaStr := ""
		cnyStr := ""
		usdStr := ""
		if log.Quota != 0 {
			quotaStr = strconv.Itoa(log.Quota)
			usdAmount := float64(log.Quota) / common.QuotaPerUnit
			cnyAmount := usdAmount * operation_setting.USDExchangeRate
			usdStr = fmt.Sprintf("%.6f", usdAmount)
			cnyStr = fmt.Sprintf("%.6f", cnyAmount)
		}
		row := []string{
			timeStr,                // 时间
			channelStr,             // 渠道
			log.Username,           // 用户
			log.TokenName,          // 令牌
			log.Group,              // 分组
			strconv.Itoa(log.Type), // 类型
			log.ModelName,          // 模型
			useTimeStr,             // 用时/首字
			promptStr,              // 输入
			completionStr,          // 输出
			quotaStr,               // Quota
			cnyStr,                 // 人民币
			usdStr,                 // 美金
			log.Ip,                 // IP
			"",                     // 重试（前端从 other 计算，CSV 暂留空）
			log.RequestId,          // request_id
			log.Content,            // 详情
		}
		_ = w.Write(row)
	}
	w.Flush()
}

// ExportAllLogs (admin) exports up to MaxLogExportItems logs matching the
// admin filter parameters, as CSV by default, or JSON when ?format=json.
func ExportAllLogs(c *gin.Context) {
	logType, _ := strconv.Atoi(c.Query("type"))
	startTimestamp, _ := strconv.ParseInt(c.Query("start_timestamp"), 10, 64)
	endTimestamp, _ := strconv.ParseInt(c.Query("end_timestamp"), 10, 64)
	username := c.Query("username")
	tokenName := c.Query("token_name")
	modelName := c.Query("model_name")
	channel, _ := strconv.Atoi(c.Query("channel"))
	group := c.Query("group")
	requestId := c.Query("request_id")

	logs, err := model.GetAllLogsForExport(logType, startTimestamp, endTimestamp,
		modelName, username, tokenName, channel, group, requestId)
	if err != nil {
		common.ApiError(c, err)
		return
	}
	if c.Query("format") == "json" {
		common.ApiSuccess(c, logs)
		return
	}
	writeLogsCSV(c, logs)
}

// ExportUserLogs (user) exports up to MaxLogExportItems of the caller's own
// logs matching the filter parameters, as CSV by default, or JSON when
// ?format=json.
func ExportUserLogs(c *gin.Context) {
	userId := c.GetInt("id")
	logType, _ := strconv.Atoi(c.Query("type"))
	startTimestamp, _ := strconv.ParseInt(c.Query("start_timestamp"), 10, 64)
	endTimestamp, _ := strconv.ParseInt(c.Query("end_timestamp"), 10, 64)
	tokenName := c.Query("token_name")
	modelName := c.Query("model_name")
	group := c.Query("group")
	requestId := c.Query("request_id")

	logs, err := model.GetUserLogsForExport(userId, logType, startTimestamp, endTimestamp,
		modelName, tokenName, group, requestId)
	if err != nil {
		common.ApiError(c, err)
		return
	}
	if c.Query("format") == "json" {
		common.ApiSuccess(c, logs)
		return
	}
	writeLogsCSV(c, logs)
}
