// Copyright 2026 TraceNex Partner OVERLAY
//
// Package outbox 实现 OVERLAY B-16 outbox publisher。
//
// 设计：
//
//	off     —— 不写表，不推送（应急回滚）
//	shadow  —— 写表但 publisher goroutine 不实际推 MNS（仅 simulate + log）
//	enabled —— 写表 + publisher 推 MNS
//
// 实际 MNS SDK 接入留给 Phase 2A（避免本 PR 引入 aliyun-sdk-go 新依赖；
// integration §1.6.3 选型理由要求 SDK 已就位才接，不强行引入）。
//
// 当前实现的 publisher 用一个 Publisher interface 抽象，
// shadow 模式 inject NoopPublisher，单测 inject FakePublisher。
package outbox

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/setting/overlay_flag"
)

// Publisher 抽象事件发布器（接 MNS / Kafka / Redis Stream 等）。
type Publisher interface {
	Publish(ctx context.Context, region, topic string, payload []byte) error
}

// NoopPublisher 在 shadow 模式或测试中使用，永远成功且不做任何 IO。
type NoopPublisher struct {
	mu       sync.Mutex
	Sent     int
	LastBody []byte
}

func (p *NoopPublisher) Publish(ctx context.Context, region, topic string, payload []byte) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.Sent++
	p.LastBody = append([]byte{}, payload...)
	return nil
}

const (
	defaultBatchSize = 50
	defaultLeaseTTL  = 30 * time.Second
	defaultInterval  = 2 * time.Second
)

// Runner 后台 publisher goroutine 的 owner。
type Runner struct {
	region    string
	topic     string
	publisher Publisher
	batch     int
	leaseTTL  time.Duration
	interval  time.Duration
}

// NewRunner 用合理默认值。
func NewRunner(region, topic string, publisher Publisher) *Runner {
	if publisher == nil {
		publisher = &NoopPublisher{}
	}
	return &Runner{
		region:    region,
		topic:     topic,
		publisher: publisher,
		batch:     defaultBatchSize,
		leaseTTL:  defaultLeaseTTL,
		interval:  defaultInterval,
	}
}

// Start 启动后台 goroutine；ctx 取消即停。
func (r *Runner) Start(ctx context.Context) {
	go r.loop(ctx)
}

func (r *Runner) loop(ctx context.Context) {
	t := time.NewTicker(r.interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			r.tick(ctx)
		}
	}
}

func (r *Runner) tick(ctx context.Context) {
	mode := overlay_flag.OutboxMode()
	if mode == overlay_flag.OutboxOff {
		return
	}

	rows, err := model.LeaseOutboxBatch(r.region, r.batch, r.leaseTTL)
	if err != nil {
		common.SysLog("outbox lease error: " + err.Error())
		return
	}
	for _, row := range rows {
		r.process(ctx, row, mode)
	}
}

func (r *Runner) process(ctx context.Context, row *model.ConsumeLogOutbox, mode string) {
	// shadow 模式：只计数 + log，不真发；publisher 是 NoopPublisher 也是同效果。
	publisher := r.publisher
	if mode == overlay_flag.OutboxShadow {
		publisher = noopShadow
	}

	if err := publisher.Publish(ctx, row.DataRegion, r.topic, []byte(row.Payload)); err != nil {
		_ = model.MarkOutboxFailed(row.Id, err.Error())
		common.SysLog(fmt.Sprintf("outbox publish failed id=%d err=%v", row.Id, err))
		return
	}
	if err := model.MarkOutboxPublished(row.Id); err != nil {
		common.SysLog(fmt.Sprintf("outbox mark published failed id=%d err=%v", row.Id, err))
	}
}

var noopShadow = &NoopPublisher{}
