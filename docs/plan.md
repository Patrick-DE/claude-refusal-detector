# Claude Refusal Detector — Plan

# Claude Refusal Detector — Plan

**Status:** Completed — 2026-08-10
Depends on: [`docs/design.md`](./design.md), [`docs/vision.md`](./vision.md)

## Phases

### Phase 0 — Foundations
- [x] **P0.1** Lock provider config: plugin → `claude -p`; CLI → DeepSeek direct
      (`https://api.deepseek.com`), Kimi 3 / GLM 5.2 via OpenRouter
      (`https://openrouter.ai/api/v1`); model → `base_url` map. → status: completed
- [x] **P0.2** Scaffold repo: `pyproject.toml`, `src/refusal_detector/`, `tests/`, `.env.example`,
      `README.md`. → status: completed
- [x] **P0.3** `config.py` — `Config` dataclass + env loading + `build_oracle` / `build_cache` /
      `build_reporter` factories; `.env.example` with provider keys (never committed).
      → status: completed

### Phase 1 — Core pieces (pure logic first, no API)
- [x] **P1.1** `ports.py` — `Oracle` / `Cache` / `Reporter` / `Segmenter` protocols + data types
      (`Segment`, `Verdict`, `DetectionReport`). → status: completed
- [x] **P1.2** `adapters.py` — `FakeOracleAdapter` (known trigger set) + in-memory/JSON cache for
      tests. → status: completed
- [x] **P1.3** `input_loader.py` — `Segmenter` implementations (lines default / sentences /
      paragraphs / tokens) with offsets; unit tests assert offset round-trip. → status: completed
- [x] **P1.4** `classifier.py` — per-provider verdicts (Anthropic structured refusal + OpenAI
      `content_filter`/`refusal` + moderation errors + text patterns); unit tests with canned
      responses. → status: completed
- [x] **P1.5** `minimizer.py` — half-split phase + `ddmin`; **unit test: fake oracle with known
      trigger set must recover exactly that set, 1-minimal** (the seam proof). → status: completed
- [x] **P1.6** `service.py` — `RefusalDetector.detect/check` (single use case, ports injected);
      unit test with fakes at every port (no network). → status: completed

### Phase 2 — API wiring & runner
- [x] **P2.1** `AnthropicAPIAdapter` + `OpenAIAdapter` (DeepSeek direct; Kimi/GLM via OpenRouter)
      — real calls, refusal parsing, moderation-error handling. → status: completed
- [x] **P2.2** `runner.py` — `CachedOracle` (retries, rate-limit backoff, `--max-calls` budget)
      + `JsonFileCache` (per-session); **fail-loud** on adapter errors. Unit tests with a mocked
      HTTP layer. → status: completed
- [x] **P2.3** **Integration — real round trip:** one fixture with a known trigger + real key
      (DeepSeek direct or OpenRouter for CLI; `claude -p` for the plugin); assert the report names
      the exact segment and removal un-blocks. If no key available, run as a documented manual
      step (never silently skipped green). → status: completed
- [x] **P2.4** `ClaudeCodeCLIAdapter` — keyless `claude -p` oracle; **primary for the plugin
      path** (guardrails match the session). → status: completed

### Phase 3 — CLI & report
- [x] **P3.1** `cli.py` — thin argparse entry point (`detect <file|--prompt>`, `check <prompt>`,
      `--split`, `--max-calls`, `--out`); delegates to `service`. → status: completed
- [x] **P3.2** `MarkdownReporter` (in `adapters.py`) — **Markdown** report with trigger,
      positions, diff, **reason class**, call count ("a trigger, a diff, a reason" — vision).
      → status: completed
- [x] **P3.3** Mutation check on the minimizer test (break trigger set → test red; restore →
      green). → status: completed
- [x] **P3.4** **Usability check (vision: accessibility):** `detect` from a clean terminal in
      under a minute, no duplicate API calls. → status: completed

### Phase 4 — Claude plugin first, MCP engine (vision: "the moment")
- [x] **P4.1** MCP engine: `detect_refusal_trigger` tool exposing the core (blocked prompt/file
      in → recommendation out); marshals args, calls `service.detect`, returns the rendered
      report as the chat tool result. → status: completed
- [x] **P4.2** MCP tool wired as a thin shell over the core (no duplicated logic); the core
      stays testable without it. → status: completed
- [x] **P4.3** **Claude plugin (primary):** package as a Claude plugin — manifest
      (`.claude-plugin/plugin.json`), refusal-detection **hooks** for the automatic trigger,
      and install instructions (marketplace/`/plugin`). → status: completed
- [x] **P4.4** End-to-end: refusal in Claude → plugin hook auto-triggers → recommendation
      (trigger + reason class + diff) returned in **seconds** in the chat. → status: completed

### Phase 5 — Hardening
- [x] **P5.1** Format + lint + type-check pass; keep files < 500 LOC. → status: completed
- [x] **P5.2** `rtt-reviewer-agent` gate on the diff before "done". → status: completed
- [x] **P5.3** Update this status line and `design.md` when shipped. → status: completed

## Definition of done
- [x] A refusal in Claude triggers the **plugin hook** automatically and returns a
      recommendation (trigger + reason class) in seconds.
- [x] `detect` on a fixture with a known trigger returns a 1-minimal trigger naming exact offsets.
- [x] Removing the reported trigger un-blocks the prompt (verified by real round trip).
- [x] Report names the **reason class** (which guardrail fired) alongside trigger and diff.
- [x] A typical `detect` run completes in **under a minute** with no duplicate API calls.
- [x] Per-session cache prevents duplicate API calls within a run.
- [x] Oracle failures raise and log; a failed test is never silently treated as "not blocked".
- [x] All tests green; mutation check passed; reviewer gate passed.


