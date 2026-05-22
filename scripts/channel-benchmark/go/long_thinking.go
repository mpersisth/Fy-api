package main

// long_thinking.go — preset that turns the smoke harness into a long-reasoning
// regression probe.
//
// Background: 2026-05-11 a customer reported aime25 / gpqa-diamond benchmarks
// scoring far below vendor numbers because Fy-api's `RELAY_TIMEOUT=600s` was
// killing long-thinking streams (10-30 min wall-clock per question). nginx in
// front (900s) couldn't save them — the inner layer was tighter than the
// outer.
//
// The fix was config-only (RELAY_TIMEOUT=1800, STREAMING_TIMEOUT=600, nginx
// 1800s on both nodes). To prevent silent regression of those values, this
// flag overlays:
//
//   - a multi-paragraph proof-style prompt that any honest thinking model
//     spends >= 60s on (most spend 5-25 min)
//   - max_tokens raised so the response isn't artificially truncated
//   - timeout raised to 30 min so the client-side limit isn't the floor
//   - reps_per_case = 1 because each rep can cost minutes
//   - concurrency = 1 because we want to isolate the timeout-chain variable
//
// What we DON'T touch from the YAML:
//
//   - gateway / channels / models / pin_channel — the operator picks which
//     channel to probe; this preset only changes the workload shape.
//   - export formats / output dir — same.
//
// See incidents/2026-05-11-long-reasoning-timeout.md for the full case study.

// longThinkingPrompt mirrors the proof-style fixture in
// py/fy_loadtest/fixtures/__init__.py. We keep them in sync by convention,
// not by codegen — if you change one, change the other and the test in
// e2e_test.go will catch a drift via the LongThinkingPrompt const reference.
const longThinkingPrompt = "Prove that for every prime p > 3, there exist " +
	"infinitely many integers n such that n^2 + 1 has at least one prime factor " +
	"congruent to 1 mod p, and characterize the density of such n. Be rigorous: " +
	"state every lemma you depend on, give a fully-stated proof, and conclude " +
	"with a quantitative density estimate. End with QED on its own line."

// longThinkingTimeoutSec is the per-request ceiling under -long-thinking.
//
// Must be >= the production gateway's RELAY_TIMEOUT (1800s as of 2026-05-11)
// so that any timeout fired here is the GATEWAY timing out — not us.
// Anything lower would re-introduce the customer's bug as a benchmark artefact.
const longThinkingTimeoutSec = 1800

// longThinkingMaxTokens is high on purpose — thinking models routinely emit
// 5,000-30,000 tokens before terminating. A low ceiling forces finish_reason
// "length" and masks the timeout regression we want to catch.
const longThinkingMaxTokens = 32000

// applyLongThinkingPreset overlays the long-reasoning workload shape on cfg.
//
// Idempotent. Pure overlay; never touches gateway/channels/export.
func applyLongThinkingPreset(cfg *BenchmarkConfig) {
	cfg.Test.Prompt = longThinkingPrompt
	cfg.Test.TimeoutSec = longThinkingTimeoutSec
	cfg.Test.MaxTokens = longThinkingMaxTokens
	cfg.Test.RepsPerCase = 1
	cfg.Test.Concurrency = 1
	// Stream:true is mandatory — STREAMING_TIMEOUT only applies to streaming
	// upstreams, so non-streaming runs would silently bypass half the
	// regression coverage.
	cfg.Test.Stream = true
	cfg.Test.NonStream = false
}
