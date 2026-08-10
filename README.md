# Claude Refusal Detector

Pinpoint the exact words, phrases, or sections of a prompt that trigger a Claude refusal —
so you can understand *why* something is blocked and rephrase legitimate content.

Black-box tool: Claude is closed-weight, so this uses **delta debugging** (Zeller's `ddmin`
plus a coarse half-split pass) against LLM endpoints or keyless Claude CLI, not internal activations.

## Quick Start

### Installation

```bash
pip install -e .
```

### CLI Usage

```bash
# Detect minimal refusal trigger in a file or prompt string
refusal-detector detect prompt.txt --out report.md

# Quick refusal check (blocked / unblocked only)
refusal-detector check "Your prompt string here"

# Choose segmentation granularity and provider
refusal-detector detect prompt.txt --split sentences --provider anthropic --model claude-3-5-sonnet-20241022
```

**Exit codes:** `0` = success (`check`: not blocked; `detect`: report produced) · `1` = `check`:
prompt is blocked · `2` = execution error.

**Call budget:** `--max-calls` limits distinct prompt evaluations. Retries of the same prompt
(rate-limit/timeout) do not consume the budget.

### Claude Plugin (primary) & MCP Engine

The **Claude Plugin** is the primary integration surface: it auto-detects refusals via lifecycle hooks (`.claude-plugin/plugin.json` & `.claude-plugin/hooks.json`). When a prompt is refused in Claude, the `PostInvocation` hook fires automatically and injects the recommendation report directly into the chat session.

#### Plugin Manifest & Hooks Structure
```text
.claude-plugin/
├── plugin.json       # Plugin manifest
└── hooks.json        # Refusal auto-detection lifecycle hook
```

#### Standalone MCP Engine Execution

The plugin embeds the **MCP server** (`detect_refusal_trigger` tool) as its underlying engine. To run the MCP server standalone over stdio:

```bash
python -m refusal_detector.desktop_plugin
```

## Docs
- [Vision](docs/vision.md) — why we built this and what success looks like
- [Design](docs/design.md) — architecture, algorithm, classification, scope
- [Plan](docs/plan.md) — phased checklist with status

## Status
Completed — 2026-08-10. Core engine & Claude plugin packaging complete — see [plan.md](docs/plan.md).


