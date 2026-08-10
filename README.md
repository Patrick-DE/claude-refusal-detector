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

### Claude Plugin & Marketplace Installation

The repository contains a marketplace catalog manifest ([`.claude-plugin/marketplace.json`](file:///c:/Users/patri/sources/repos/claude-refusal-detector/.claude-plugin/marketplace.json)) and plugin manifest ([`.claude-plugin/plugin.json`](file:///c:/Users/patri/sources/repos/claude-refusal-detector/.claude-plugin/plugin.json)).

To add this repository as a marketplace and install the plugin in Claude Code:

```bash
# Add marketplace catalog
/plugin marketplace add Patrick-DE/claude-refusal-detector

# Install refusal detector plugin
/plugin install claude-refusal-detector
```

#### Plugin Directory Structure
```text
.claude-plugin/
├── marketplace.json  # Marketplace catalog manifest
└── plugin.json       # Plugin manifest
hooks/
└── hooks.json         # Refusal auto-detection Stop hook
.mcp.json               # MCP server registration (detect_refusal_trigger)
```


#### MCP Engine

The plugin registers the **MCP server** (`detect_refusal_trigger` tool) via `.mcp.json`, so it starts
automatically once the plugin is installed — it is the engine both the Stop hook and manual use call
into. To run it standalone over stdio instead:

```bash
python -m refusal_detector.desktop_plugin
```

**Prerequisite:** whether run via the plugin or standalone, the hook and MCP server both need this
package's runtime dependencies installed into the `python` Claude Code resolves on `PATH` — run
`pip install -e .` (see Installation above) before installing the plugin. `/plugin install` does not
run this for you.

## Docs
- [Vision](docs/vision.md) — why we built this and what success looks like
- [Design](docs/design.md) — architecture, algorithm, classification, scope
- [Plan](docs/plan.md) — phased checklist with status

## Status
Completed — 2026-08-10. Core engine & Claude plugin packaging complete — see [plan.md](docs/plan.md).


