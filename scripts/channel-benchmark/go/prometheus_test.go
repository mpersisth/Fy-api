package main

import (
	"bytes"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestExpositionEmpty(t *testing.T) {
	r := NewMetricsRegistry()
	var buf bytes.Buffer
	r.WriteExposition(&buf)
	out := buf.String()

	mustContain := []string{
		"# TYPE channel_benchmark_request_total counter",
		"# TYPE channel_benchmark_run_age_seconds gauge",
		"channel_benchmark_run_age_seconds 0",
		"channel_benchmark_last_run_unix_seconds 0",
		"channel_benchmark_consecutive_runs_ok 0",
	}
	for _, want := range mustContain {
		if !strings.Contains(out, want) {
			t.Errorf("empty exposition missing %q\nfull output:\n%s", want, out)
		}
	}
}

func TestExpositionAfterReplace(t *testing.T) {
	r := NewMetricsRegistry()

	streamed := Aggregate{
		CaseKey: CaseKey{
			ChannelID: 1, ChannelName: "openai", Model: "gpt-4o-mini", Streamed: true,
		},
		Total:          5,
		OK:             4,
		Failed:         1,
		SuccessRatePct: 80.0,
		E2E:            LatencyStats{Samples: 4, P50Ms: 320, P95Ms: 510, P99Ms: 600},
		TTFT:           LatencyStats{Samples: 4, P50Ms: 90, P95Ms: 140, P99Ms: 180},
		TokensPerSec:   ThroughputStats{Samples: 4, Avg: 87.5},
		ErrorBreakdown: map[string]int{"HTTP 500: x": 1},
	}
	nonstream := Aggregate{
		CaseKey: CaseKey{
			ChannelID: 1, ChannelName: "openai", Model: "gpt-4o-mini", Streamed: false,
		},
		Total:          3,
		OK:             3,
		SuccessRatePct: 100.0,
		E2E:            LatencyStats{Samples: 3, P50Ms: 280, P95Ms: 305, P99Ms: 310},
		TokensPerSec:   ThroughputStats{Samples: 3, Avg: 42.0},
	}
	r.Replace([]Aggregate{streamed, nonstream}, nil)

	var buf bytes.Buffer
	r.WriteExposition(&buf)
	out := buf.String()

	// Counter accumulated correctly per outcome.
	mustContain := []string{
		`channel_benchmark_request_total{channel="openai",model="gpt-4o-mini",outcome="ok",streamed="true"} 4`,
		`channel_benchmark_request_total{channel="openai",model="gpt-4o-mini",outcome="fail",streamed="true"} 1`,
		`channel_benchmark_request_total{channel="openai",model="gpt-4o-mini",outcome="ok",streamed="false"} 3`,
		`channel_benchmark_success_rate{channel="openai",model="gpt-4o-mini",streamed="true"} 0.8`,
		`channel_benchmark_success_rate{channel="openai",model="gpt-4o-mini",streamed="false"} 1`,
		`channel_benchmark_e2e_seconds{channel="openai",model="gpt-4o-mini",quantile="0.95",streamed="true"} 0.51`,
		`channel_benchmark_ttft_seconds{channel="openai",model="gpt-4o-mini",quantile="0.5",streamed="true"} 0.09`,
		`channel_benchmark_tokens_per_sec{channel="openai",model="gpt-4o-mini",streamed="true"} 87.5`,
		`channel_benchmark_consecutive_runs_ok 1`,
	}
	for _, want := range mustContain {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q\nfull output:\n%s", want, out)
		}
	}

	// TTFT should NOT appear for the non-streaming case.
	if strings.Contains(out, `ttft_seconds{channel="openai",model="gpt-4o-mini",quantile="0.5",streamed="false"}`) {
		t.Errorf("ttft emitted for non-streaming case:\n%s", out)
	}
}

func TestCounterAccumulatesAcrossReplaces(t *testing.T) {
	r := NewMetricsRegistry()
	for i := 0; i < 3; i++ {
		r.Replace([]Aggregate{
			{
				CaseKey: CaseKey{ChannelID: 1, ChannelName: "ch", Model: "m", Streamed: false},
				Total:   2, OK: 2, SuccessRatePct: 100,
			},
		}, nil)
	}

	var buf bytes.Buffer
	r.WriteExposition(&buf)
	want := `channel_benchmark_request_total{channel="ch",model="m",outcome="ok",streamed="false"} 6`
	if !strings.Contains(buf.String(), want) {
		t.Errorf("expected accumulated counter, got:\n%s", buf.String())
	}
}

func TestRunErrorResetsConsecutiveOK(t *testing.T) {
	r := NewMetricsRegistry()
	r.Replace([]Aggregate{}, nil)
	r.Replace([]Aggregate{}, nil)
	var buf bytes.Buffer
	r.WriteExposition(&buf)
	if !strings.Contains(buf.String(), "channel_benchmark_consecutive_runs_ok 2") {
		t.Fatalf("expected consecutive_runs_ok=2 after two ok runs:\n%s", buf.String())
	}

	r.Replace([]Aggregate{}, errors.New("nope"))
	buf.Reset()
	r.WriteExposition(&buf)
	if !strings.Contains(buf.String(), "channel_benchmark_consecutive_runs_ok 0") {
		t.Errorf("expected consecutive_runs_ok=0 after a failed run:\n%s", buf.String())
	}
}

func TestLabelEscaping(t *testing.T) {
	r := NewMetricsRegistry()
	r.Replace([]Aggregate{
		{
			CaseKey: CaseKey{
				ChannelID: 1, ChannelName: `weird "quoted"`+"\n"+`name`,
				Model: "m", Streamed: false,
			},
			Total: 1, OK: 1, SuccessRatePct: 100,
			E2E: LatencyStats{Samples: 1, P50Ms: 100},
		},
	}, nil)
	var buf bytes.Buffer
	r.WriteExposition(&buf)
	want := `channel="weird \"quoted\"\nname"`
	if !strings.Contains(buf.String(), want) {
		t.Errorf("expected escaped label %q in output:\n%s", want, buf.String())
	}
}

func TestHandlerServesMetricsPath(t *testing.T) {
	r := NewMetricsRegistry()
	r.Replace([]Aggregate{
		{
			CaseKey: CaseKey{ChannelID: 1, ChannelName: "ch", Model: "m", Streamed: false},
			Total:   1, OK: 1, SuccessRatePct: 100,
		},
	}, nil)

	srv := httptest.NewServer(r.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/metrics")
	if err != nil {
		t.Fatalf("get /metrics: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("expected 200, got %d", resp.StatusCode)
	}
	if got := resp.Header.Get("Content-Type"); !strings.HasPrefix(got, "text/plain") {
		t.Errorf("expected text/plain content-type, got %q", got)
	}
	body, _ := io.ReadAll(resp.Body)
	if !strings.Contains(string(body), "channel_benchmark_request_total") {
		t.Errorf("expected metric name in body, got:\n%s", body)
	}

	resp2, err := http.Get(srv.URL + "/other")
	if err != nil {
		t.Fatalf("get /other: %v", err)
	}
	defer resp2.Body.Close()
	if resp2.StatusCode != 404 {
		t.Errorf("expected 404 on non-/metrics path, got %d", resp2.StatusCode)
	}
}

func TestFormatFloatTrim(t *testing.T) {
	cases := []struct {
		in   float64
		want string
	}{
		{0, "0"},
		{1, "1"},
		{1.5, "1.5"},
		{0.123456, "0.123456"},
		{0.1234567, "0.123457"}, // rounded to 6 dp
		{42.0, "42"},
		{-0.5, "-0.5"},
	}
	for _, c := range cases {
		got := formatFloat(c.in)
		if got != c.want {
			t.Errorf("formatFloat(%v) = %q want %q", c.in, got, c.want)
		}
	}
}
