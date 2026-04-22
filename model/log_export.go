package model

// log_export.go is a Fy-api overlay file (not from upstream new-api).
// It provides bulk log query functions used by the CSV export endpoints.
// Keeping these functions in a separate file minimizes merge conflicts
// when pulling new versions of upstream new-api's model/log.go.

import (
	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/types"
)

// GetAllLogsForExport returns up to common.MaxLogExportItems logs matching
// the admin filter parameters. It mirrors GetAllLogs but without paging and
// without the total-count step, since the export is capped by MaxLogExportItems.
// The `requestId` filter is also supported so admins can export a single
// request's full trail for audit purposes.
func GetAllLogsForExport(logType int, startTimestamp int64, endTimestamp int64,
	modelName string, username string, tokenName string,
	channel int, group string, requestId string) (logs []*Log, err error) {

	tx := LOG_DB.Model(&Log{})
	if logType != LogTypeUnknown {
		tx = tx.Where("logs.type = ?", logType)
	}
	if modelName != "" {
		tx = tx.Where("logs.model_name like ?", modelName)
	}
	if username != "" {
		tx = tx.Where("logs.username = ?", username)
	}
	if tokenName != "" {
		tx = tx.Where("logs.token_name = ?", tokenName)
	}
	if requestId != "" {
		tx = tx.Where("logs.request_id = ?", requestId)
	}
	if startTimestamp != 0 {
		tx = tx.Where("logs.created_at >= ?", startTimestamp)
	}
	if endTimestamp != 0 {
		tx = tx.Where("logs.created_at <= ?", endTimestamp)
	}
	if channel != 0 {
		tx = tx.Where("logs.channel_id = ?", channel)
	}
	if group != "" {
		tx = tx.Where("logs."+logGroupCol+" = ?", group)
	}

	err = tx.Order("logs.id desc").Limit(common.MaxLogExportItems).Find(&logs).Error
	if err != nil {
		return nil, err
	}
	attachChannelNames(logs)
	return logs, nil
}

// GetUserLogsForExport returns up to common.MaxLogExportItems logs for the
// given user matching the filter parameters. It mirrors GetUserLogs but
// without paging. The `requestId` filter is also supported.
func GetUserLogsForExport(userId int, logType int, startTimestamp int64, endTimestamp int64,
	modelName string, tokenName string, group string, requestId string) (logs []*Log, err error) {

	var tx = LOG_DB.Where("logs.user_id = ?", userId)
	if logType != LogTypeUnknown {
		tx = tx.Where("logs.type = ?", logType)
	}
	if modelName != "" {
		modelNamePattern, perr := sanitizeLikePattern(modelName)
		if perr != nil {
			return nil, perr
		}
		tx = tx.Where("logs.model_name LIKE ? ESCAPE '!'", modelNamePattern)
	}
	if tokenName != "" {
		tx = tx.Where("logs.token_name = ?", tokenName)
	}
	if requestId != "" {
		tx = tx.Where("logs.request_id = ?", requestId)
	}
	if startTimestamp != 0 {
		tx = tx.Where("logs.created_at >= ?", startTimestamp)
	}
	if endTimestamp != 0 {
		tx = tx.Where("logs.created_at <= ?", endTimestamp)
	}
	if group != "" {
		tx = tx.Where("logs."+logGroupCol+" = ?", group)
	}

	err = tx.Order("logs.id desc").Limit(common.MaxLogExportItems).Find(&logs).Error
	if err != nil {
		return nil, err
	}
	// Redact any fields that should not be exposed outside the admin view.
	// formatUserLogs requires the starting index just to assign a display id;
	// for exports we pass 0 since the exported id is purely informational.
	formatUserLogs(logs, 0)
	return logs, nil
}

// attachChannelNames fills in the ChannelName field on each log by looking
// up channel names in bulk (memory cache first, then DB fallback), matching
// the behavior of GetAllLogs.
func attachChannelNames(logs []*Log) {
	channelIds := types.NewSet[int]()
	for _, log := range logs {
		if log.ChannelId != 0 {
			channelIds.Add(log.ChannelId)
		}
	}
	if channelIds.Len() == 0 {
		return
	}
	var channels []struct {
		Id   int    `gorm:"column:id"`
		Name string `gorm:"column:name"`
	}
	if common.MemoryCacheEnabled {
		for _, channelId := range channelIds.Items() {
			if cacheChannel, cerr := CacheGetChannel(channelId); cerr == nil {
				channels = append(channels, struct {
					Id   int    `gorm:"column:id"`
					Name string `gorm:"column:name"`
				}{
					Id:   channelId,
					Name: cacheChannel.Name,
				})
			}
		}
	} else {
		_ = DB.Table("channels").Select("id, name").
			Where("id IN ?", channelIds.Items()).Find(&channels).Error
	}
	channelMap := make(map[int]string, len(channels))
	for _, channel := range channels {
		channelMap[channel.Id] = channel.Name
	}
	for i := range logs {
		logs[i].ChannelName = channelMap[logs[i].ChannelId]
	}
}
