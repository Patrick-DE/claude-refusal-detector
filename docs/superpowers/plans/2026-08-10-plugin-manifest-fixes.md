# Plugin Manifest & Hook Contract Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `claude-refusal-detector` Claude Code plugin actually load and run — today the hook is unreachable, its schema/payload/output contract don't match Claude Code's real hook system, the marketplace manifest can't install, and the working MCP server is never registered.

**Architecture:** No design change. `docs/design.md` already specifies the right shape (Stop hook auto-triggers `RefusalDetector.detect` on the blocked prompt; MCP server is the engine). This plan only fixes the *implementation* to match Claude Code's actual plugin/hook/manifest contract, verified directly against the `plugin-dev` skill's reference docs and four real, currently-installed plugins (`superpowers`, `hookify`, `fp-check`, `claude-adapt-rules`).

**Tech Stack:** Python 3.10+, pytest, Claude Code plugin manifest format (`.claude-plugin/plugin.json`, `hooks/hooks.json`, `.mcp.json`).

## Global Constraints

- Every manifest path fix must be verified against real evidence, not the invented schema the current code uses — see "Verified schema facts" below.
- No new third-party dependencies. Stdlib only (`json`, `sys`, `pathlib`) for the hook/MCP entry-point fixes.
- Preserve `RefusalDetector`/`RefusalClassifier`/`config`/`service` APIs exactly as they exist today — this plan only fixes the plugin *wiring* around them, not the core detection engine (which already passed its own review in `docs/review-report.md`).
- Keep files under 500 LOC (none of these changes approach that).
- All new/changed Python files must pass the existing `pytest` suite (`pyproject.toml`: `testpaths = ["tests"]`, `pythonpath = ["src"]`) with no regressions.

### Verified schema facts (evidence, not guesses)

Gathered by reading `plugin-dev`'s `hook-development`/`plugin-structure` skill docs *and* the real `hooks.json`/`plugin.json`/`.mcp.json` files of four to six currently-installed, active plugins on this machine. Cited inline per fact:

1. **Hook events (real set):** `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Stop`, `SubagentStop`, `SessionStart`, `SessionEnd`, `PreCompact`, `Notification` (`hook-development/SKILL.md`). Real plugins also use event names outside this documented list (e.g. `claude-security`'s `UserPromptExpansion`) — so this set is a floor, not a closed enum. `PostInvocation` (what the current code uses) is not a real event anywhere.
2. **Stop-event payload fields:** `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, `reason` (`hook-development/SKILL.md` "Hook Input Format"). No `userPrompt`/`lastResponse`/`force_detect` fields exist.
3. **Stop-event output:** `{"decision": "approve|block", "reason": "...", "systemMessage": "..."}`, or the standard `{"continue", "suppressOutput", "systemMessage"}` shape. No `injectSteps`/`ephemeralMessage` field exists anywhere in the docs or in any installed plugin.
4. **Standalone `hooks/hooks.json` file format is *wrapped*:** `{"description": "...", "hooks": {"Stop": [...]}}`. Confirmed directly in the files of **4 real installed plugins**: `superpowers-dev/superpowers/6.2.0/hooks/hooks.json`, `claude-plugins-official/hookify/.../hooks/hooks.json`, `trailofbits/fp-check/1.0.3/hooks/hooks.json`, `claude-plugins-official/claude-security/0.10.0/hooks/hooks.json`. (The plugin-dev skill's own `examples/standard-plugin.md` shows an *unwrapped* example — that example contradicts the skill's own `SKILL.md` prose and all real-world evidence, so it is not followed here.)
4a. **Command-type hook steps use a single combined `command` string, not a `command`+`args` split.** A repo-wide search of every cached plugin manifest found exactly one counterexample (`claude-adapt-rules`, one author, all its own versions) using `"command": "python", "args": [...]`; every other real plugin with command hooks (superpowers, hookify, fp-check, claude-security, skill-improver, claude-idle-shutdown, claude-mem, vercel-plugin) and every official `hook-development` doc example uses one string, e.g. `"command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/stop.py\""`. No official doc shows `args` on a hook step (it exists only in the unrelated `mcpServers` schema). The single-string form is used here — it loses no functionality and removes a real risk (a Task 1 task-reviewer pass flagged the `args` form as plausibly non-functional: Claude Code may execute bare `python` with the path silently dropped).
5. **Component files (`hooks/hooks.json`, `.mcp.json`) must NOT live inside `.claude-plugin/`** — that directory holds only `plugin.json` and (for self-hosted marketplaces) `marketplace.json`. Default discovery paths are `./hooks/hooks.json` and `./.mcp.json` at the plugin root (`plugin-structure/references/manifest-reference.md` "Resolution Order").
6. **`plugin.json` should NOT set an explicit `hooks`/`mcpServers` path** when the files already sit at their default discovery location — confirmed by `hookify`, `fp-check`, and `superpowers`'s real `plugin.json` files, none of which set either field despite all three shipping hooks.
7. **`marketplace.json` schema:** top-level `owner` is an **object** (`{"name": ..., "url": ...}`), `description` is **top-level** (never nested under `metadata`), and a **self-hosted** plugin entry (marketplace root == plugin root, as in this repo) uses `"source": "./"` with no `path` key — confirmed against `secdude-plugins` and `caveman`'s real `marketplace.json` files.
8. **`${CLAUDE_PLUGIN_ROOT}` is the portable-path variable** for hook/MCP commands, confirmed in every real example read.
9. **Real Claude Code transcript JSONL record shape** (confirmed by directly inspecting this session's own transcript file): a human-typed prompt is a record with top-level `"type": "user"` and `message.content` as a plain **string**. A tool-result turn is also `"type": "user"` but `message.content` is an **array** of blocks (e.g. `{"type": "tool_result", ...}`) — must be excluded when looking for the human's prompt. An assistant reply is `"type": "assistant"` with `message.content` as an **array** of blocks; visible reply text lives in blocks where `block["type"] == "text"` (other block types seen: `thinking`, `tool_use`).

---

### Task 1: Fix the three manifest files (`plugin.json`, `hooks.json`, `marketplace.json`)

**Files:**
- Create: `tests/test_manifest_schema.py`
- Modify: `.claude-plugin/plugin.json`
- Create: `hooks/hooks.json`
- Delete: `.claude-plugin/hooks.json`
- Modify: `.claude-plugin/marketplace.json`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `hooks/hooks.json` with a `Stop` event whose command args reference `${CLAUDE_PLUGIN_ROOT}/src/refusal_detector/hooks/refusal_hook.py` — Task 2 must create a script at exactly that path with a stdin-JSON-in / stdout-JSON-out CLI contract. Produces a `.claude-plugin/plugin.json` with **no** `hooks` or `mcpServers` key — Task 3 relies on this (adding `.mcp.json` at the default discovery path, no manifest edit needed).

- [ ] **Step 1: Write the failing manifest schema test**

Create `tests/test_manifest_schema.py`:

```python
"""Structural validation tests for the plugin's manifest files against Claude Code's real schema."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_json_relies_on_default_discovery_for_hooks_and_mcp():
    plugin = _load_json(REPO_ROOT / ".claude-plugin" / "plugin.json")
    assert "hooks" not in plugin
    assert "mcpServers" not in plugin


def test_no_component_files_nested_under_claude_plugin_dir():
    claude_plugin_dir = REPO_ROOT / ".claude-plugin"
    allowed = {"plugin.json", "marketplace.json"}
    nested_files = {p.name for p in claude_plugin_dir.iterdir() if p.is_file()}
    assert nested_files <= allowed, f"Unexpected files nested in .claude-plugin/: {nested_files - allowed}"


def test_hooks_json_lives_at_default_discovery_path():
    assert (REPO_ROOT / "hooks" / "hooks.json").is_file()


def test_hooks_json_uses_wrapped_plugin_format_with_stop_event():
    hooks_config = _load_json(REPO_ROOT / "hooks" / "hooks.json")
    assert "hooks" in hooks_config, "plugin hooks.json must use the wrapped {description, hooks} format"
    assert hooks_config.get("description"), "wrapped format's description field should be populated"

    stop_groups = hooks_config["hooks"]["Stop"]
    assert isinstance(stop_groups, list) and stop_groups
    first_group = stop_groups[0]
    assert first_group["matcher"] == "*"
    steps = first_group["hooks"]
    assert steps and steps[0]["type"] == "command"


def test_hooks_json_stop_command_is_portable():
    hooks_config = _load_json(REPO_ROOT / "hooks" / "hooks.json")
    step = hooks_config["hooks"]["Stop"][0]["hooks"][0]
    assert "args" not in step, "real hook command steps use one combined command string, not command+args"
    assert "${CLAUDE_PLUGIN_ROOT}" in step["command"], "Stop hook must locate its script via ${CLAUDE_PLUGIN_ROOT}"


def test_marketplace_json_owner_is_an_object_with_a_name():
    marketplace = _load_json(REPO_ROOT / ".claude-plugin" / "marketplace.json")
    assert isinstance(marketplace["owner"], dict)
    assert marketplace["owner"]["name"]


def test_marketplace_json_description_is_top_level_not_nested_in_metadata():
    marketplace = _load_json(REPO_ROOT / ".claude-plugin" / "marketplace.json")
    assert "description" in marketplace
    assert "metadata" not in marketplace


def test_marketplace_json_self_hosted_plugin_source_points_at_repo_root():
    marketplace = _load_json(REPO_ROOT / ".claude-plugin" / "marketplace.json")
    plugin_entry = marketplace["plugins"][0]
    assert plugin_entry["source"] == "./"
    assert "path" not in plugin_entry
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_manifest_schema.py -v`
Expected: Multiple FAILs — `hooks/hooks.json` doesn't exist yet, `plugin.json` still has the old `"hooks": "hooks.json"` key, `marketplace.json` still has `owner` as a string and `metadata.description`.

- [ ] **Step 3: Rewrite `.claude-plugin/plugin.json`**

Replace the full file contents with:

```json
{
  "name": "claude-refusal-detector",
  "version": "0.1.0",
  "description": "Claude Plugin that automatically detects LLM safety triggers and pinpoints minimal refusal text in seconds.",
  "author": {
    "name": "Claude Refusal Detector Contributors"
  },
  "homepage": "https://github.com/Patrick-DE/claude-refusal-detector#readme",
  "repository": "https://github.com/Patrick-DE/claude-refusal-detector",
  "license": "MIT",
  "keywords": [
    "refusal",
    "safety",
    "delta-debugging",
    "ddmin",
    "diagnosis"
  ]
}
```

- [ ] **Step 4: Create `hooks/hooks.json` (repo root, NOT under `.claude-plugin/`)**

```json
{
  "description": "Detects when Claude's last reply is a refusal and surfaces the minimal trigger diagnosis for the prompt that caused it.",
  "hooks": {
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/src/refusal_detector/hooks/refusal_hook.py\"",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 5: Delete the old `.claude-plugin/hooks.json`**

```bash
git rm .claude-plugin/hooks.json
```

- [ ] **Step 6: Rewrite `.claude-plugin/marketplace.json`**

Replace the full file contents with:

```json
{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "claude-refusal-detector-marketplace",
  "description": "Marketplace manifest for the Claude Refusal Detector plugin.",
  "owner": {
    "name": "Patrick-DE",
    "url": "https://github.com/Patrick-DE"
  },
  "plugins": [
    {
      "name": "claude-refusal-detector",
      "source": "./",
      "description": "Automatically detects LLM safety triggers and pinpoints minimal refusal text in seconds using delta debugging.",
      "version": "0.1.0",
      "author": {
        "name": "Claude Refusal Detector Contributors"
      },
      "keywords": [
        "refusal",
        "safety",
        "delta-debugging",
        "ddmin",
        "diagnosis"
      ]
    }
  ]
}
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `pytest tests/test_manifest_schema.py -v`
Expected: All 8 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add tests/test_manifest_schema.py .claude-plugin/plugin.json .claude-plugin/marketplace.json hooks/hooks.json
git commit -m "fix: correct plugin manifest schema (plugin.json, hooks.json location+schema, marketplace.json)"
```

---

### Task 2: Rewrite the Stop-event hook against the real payload/output contract

**Files:**
- Modify: `src/refusal_detector/hooks/refusal_hook.py`
- Modify: `tests/test_plugin_hook.py`

**Interfaces:**
- Consumes: `hooks/hooks.json`'s Stop command, which invokes this file directly via `${CLAUDE_PLUGIN_ROOT}/src/refusal_detector/hooks/refusal_hook.py` (Task 1) — the script must be runnable as a standalone file (not just as an installed module) and must read one JSON object from stdin, write one JSON object to stdout. Consumes `RefusalClassifier.classify_text(text) -> Verdict` (`classifier.py`, unchanged) and `RefusalDetector.detect(prompt_or_path) -> DetectionReport` / `.render_report(report) -> str` (`service.py`, unchanged).
- Produces: `process_hook_payload(payload: dict) -> dict` and `_extract_last_exchange(transcript_path: str) -> tuple[str | None, str | None]` — both module-level functions in `refusal_detector.hooks.refusal_hook`, used only by this file's own tests.

- [ ] **Step 1: Write the failing tests**

Replace the full contents of `tests/test_plugin_hook.py`:

```python
"""Unit tests for the Claude plugin Stop-event refusal hook."""

import json
from unittest.mock import patch

from refusal_detector.hooks.refusal_hook import _extract_last_exchange, process_hook_payload


def _write_transcript(tmp_path, records: list[dict]) -> str:
    transcript_path = tmp_path / "transcript.jsonl"
    with open(transcript_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    return str(transcript_path)


def _simple_transcript(tmp_path, user_text: str, assistant_text: str) -> str:
    return _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": user_text}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "internal reasoning", "signature": "sig"},
                        {"type": "text", "text": assistant_text},
                    ],
                },
            },
        ],
    )


def test_process_hook_payload_refusal_auto_trigger(tmp_path):
    transcript_path = _simple_transcript(
        tmp_path,
        user_text="Line 1: Safe\nLine 2: DANGEROUS_WORD payload",
        assistant_text="I'm sorry, but I cannot assist with that request.",
    )
    payload = {
        "session_id": "abc123",
        "transcript_path": transcript_path,
        "cwd": str(tmp_path),
        "hook_event_name": "Stop",
        "reason": "end_turn",
    }

    with patch("refusal_detector.hooks.refusal_hook.RefusalDetector") as mock_detector_cls:
        instance = mock_detector_cls.return_value
        instance.detect.return_value = "MockReport"
        instance.render_report.return_value = "# Auto-Trigger Diagnostic Report"

        result = process_hook_payload(payload)

        assert result == {"systemMessage": "# Auto-Trigger Diagnostic Report"}
        instance.detect.assert_called_once_with("Line 1: Safe\nLine 2: DANGEROUS_WORD payload")


def test_process_hook_payload_unblocked_pass_through(tmp_path):
    transcript_path = _simple_transcript(
        tmp_path,
        user_text="What is the capital of France?",
        assistant_text="The capital of France is Paris.",
    )
    payload = {
        "session_id": "abc123",
        "transcript_path": transcript_path,
        "cwd": str(tmp_path),
        "hook_event_name": "Stop",
        "reason": "end_turn",
    }

    assert process_hook_payload(payload) == {}


def test_process_hook_payload_missing_transcript_path_is_a_noop():
    assert process_hook_payload({"session_id": "abc123", "hook_event_name": "Stop"}) == {}


def test_process_hook_payload_nonexistent_transcript_file_is_a_noop(tmp_path):
    payload = {"transcript_path": str(tmp_path / "does-not-exist.jsonl")}
    assert process_hook_payload(payload) == {}


def test_extract_last_exchange_skips_tool_result_user_records(tmp_path):
    transcript_path = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "Real prompt text"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "file contents"}],
                },
            },
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Final reply text."}]},
            },
        ],
    )

    user_prompt, assistant_reply = _extract_last_exchange(transcript_path)
    assert user_prompt == "Real prompt text"
    assert assistant_reply == "Final reply text."
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_plugin_hook.py -v`
Expected: FAIL — `process_hook_payload` still reads `payload.get("userPrompt")` etc, `_extract_last_exchange` doesn't exist yet.

- [ ] **Step 3: Rewrite `src/refusal_detector/hooks/refusal_hook.py`**

Replace the full file contents:

```python
"""Stop-event hook: auto-detects a refusal in Claude's last reply and reports the minimal trigger."""

import json
import sys
from pathlib import Path
from typing import Any

_SRC_ROOT = Path(__file__).resolve().parents[2]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from refusal_detector.classifier import RefusalClassifier
from refusal_detector.logger import get_logger
from refusal_detector.service import RefusalDetector

logger = get_logger("refusal_hook")


def process_hook_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Process a Stop-event hook payload; return a systemMessage if the last reply was a refusal."""
    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return {}

    user_prompt, assistant_reply = _extract_last_exchange(transcript_path)
    if not user_prompt or not assistant_reply:
        return {}

    classifier = RefusalClassifier()
    verdict = classifier.classify_text(assistant_reply)
    if not verdict.blocked:
        return {}

    logger.info("Refusal auto-detected in Stop hook. Running RefusalDetector...")
    detector = RefusalDetector()
    report = detector.detect(user_prompt)
    rendered = detector.render_report(report)

    return {"systemMessage": rendered}


def _extract_last_exchange(transcript_path: str) -> tuple[str | None, str | None]:
    """Return (last real user prompt, last assistant reply text) from a transcript JSONL file."""
    user_prompt: str | None = None
    assistant_reply: str | None = None

    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("Could not read transcript file %s: %s", transcript_path, e)
        return None, None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        message = record.get("message")
        if not isinstance(message, dict):
            continue
        record_type = record.get("type")

        if assistant_reply is None and record_type == "assistant":
            content = message.get("content")
            if isinstance(content, list):
                text_blocks = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                if text_blocks:
                    assistant_reply = "".join(text_blocks)

        elif user_prompt is None and record_type == "user":
            content = message.get("content")
            if isinstance(content, str):
                user_prompt = content

        if user_prompt is not None and assistant_reply is not None:
            break

    return user_prompt, assistant_reply


def main() -> None:
    """CLI entry point: read the hook payload from stdin, write the hook output JSON to stdout."""
    try:
        input_data = sys.stdin.read()
        if not input_data.strip():
            print("{}")
            return

        payload = json.loads(input_data)
        result = process_hook_payload(payload)
        print(json.dumps(result))
    except Exception as e:
        logger.error("Error running refusal hook: %s", e)
        print("{}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_plugin_hook.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `pytest -v`
Expected: Same pass count as before this task, plus the new tests; no new failures.

- [ ] **Step 6: Commit**

```bash
git add src/refusal_detector/hooks/refusal_hook.py tests/test_plugin_hook.py
git commit -m "fix: rewrite Stop-event hook against Claude Code's real payload/output contract"
```

---

### Task 3: Wire the MCP server into the plugin

**Files:**
- Create: `.mcp.json`
- Modify: `src/refusal_detector/desktop_plugin.py`
- Modify: `tests/test_manifest_schema.py`

**Interfaces:**
- Consumes: `.claude-plugin/plugin.json` having no `mcpServers` key (Task 1) — this task relies on default discovery at `./.mcp.json`, no manifest edit needed. Consumes the existing `mcp = FastMCP("Claude Refusal Detector")` object and `detect_refusal_trigger` tool in `desktop_plugin.py` (unchanged).
- Produces: `.mcp.json` registering a `refusal-detector` stdio server — terminal artifact, nothing else in-repo depends on it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_manifest_schema.py`:

```python


def test_mcp_json_registers_the_desktop_plugin_server():
    mcp_config = _load_json(REPO_ROOT / ".mcp.json")
    servers = mcp_config["mcpServers"]
    assert servers, "no MCP servers registered"
    entry = next(iter(servers.values()))
    assert any("${CLAUDE_PLUGIN_ROOT}" in a for a in entry.get("args", []))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_manifest_schema.py::test_mcp_json_registers_the_desktop_plugin_server -v`
Expected: FAIL — `.mcp.json` does not exist yet.

- [ ] **Step 3: Create `.mcp.json` at the repo root**

```json
{
  "mcpServers": {
    "refusal-detector": {
      "type": "stdio",
      "command": "python",
      "args": ["${CLAUDE_PLUGIN_ROOT}/src/refusal_detector/desktop_plugin.py"]
    }
  }
}
```

- [ ] **Step 4: Make `desktop_plugin.py` runnable as a standalone script**

In `src/refusal_detector/desktop_plugin.py`, insert this block immediately after the module docstring (before `from mcp.server.fastmcp import FastMCP`):

```python
import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))
```

The full updated file header becomes:

```python
"""MCP Server plugin for Claude Desktop integration."""

import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from mcp.server.fastmcp import FastMCP

from refusal_detector.config import Config
from refusal_detector.logger import get_logger
from refusal_detector.service import RefusalDetector

logger = get_logger("desktop_plugin")

mcp = FastMCP("Claude Refusal Detector")
```

(Everything below `mcp = FastMCP(...)` — the `detect_refusal_trigger` tool and `main()` — is unchanged.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_manifest_schema.py -v`
Expected: All 9 tests PASS.

- [ ] **Step 6: Verify the standalone script actually runs**

Run: `python src/refusal_detector/desktop_plugin.py < /dev/null`

This directly invokes the file the way `${CLAUDE_PLUGIN_ROOT}/...` will (not `python -m`), with stdin closed so the stdio server sees immediate EOF instead of hanging. Expected: no `ImportError`/`ModuleNotFoundError` traceback — the process starts (and then exits on EOF, or is safe to interrupt with Ctrl+C) confirms the sys.path bootstrap resolved the `refusal_detector` package correctly.

- [ ] **Step 7: Run the full suite to check for regressions**

Run: `pytest -v`
Expected: Same pass count as Task 2's end, plus the new MCP test; no new failures.

- [ ] **Step 8: Commit**

```bash
git add .mcp.json src/refusal_detector/desktop_plugin.py tests/test_manifest_schema.py
git commit -m "feat: register the MCP server in the plugin manifest via .mcp.json"
```

---

### Task 4: Repo housekeeping (gitignore, drop tracked bytecode, add LICENSE, document the fix)

**Files:**
- Create: `.gitignore`
- Create: `LICENSE`
- Modify: `docs/review-report.md`
- Delete (untrack): `src/refusal_detector/__pycache__/*`, `src/refusal_detector/hooks/__pycache__/*`, `tests/__pycache__/*`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: nothing consumed elsewhere — terminal cleanup task.

- [ ] **Step 1: Create `.gitignore`**

```
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.coverage
htmlcov/
.venv/
venv/
.env
*.log
.superpowers/
```

- [ ] **Step 2: Untrack the 24 already-committed `.pyc` files**

```bash
git rm --cached -r src/refusal_detector/__pycache__ src/refusal_detector/hooks/__pycache__ tests/__pycache__
```

Expected: removes exactly the files already confirmed tracked (`git ls-files | grep -i pyc` before this step listed exactly these 24 paths under these 3 directories).

- [ ] **Step 3: Verify they're gone from tracking and gitignore catches new ones**

Run: `git ls-files | grep -c pyc`
Expected: `0`

Run: `pytest -q >/dev/null 2>&1; git status --short | grep pycache`
Expected: no output (regenerated `.pyc` files are ignored, not shown as untracked).

- [ ] **Step 4: Add `LICENSE`**

`pyproject.toml` already declares `license = { text = "MIT" }`; add the matching file at the repo root:

```
MIT License

Copyright (c) 2026 Claude Refusal Detector Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 5: Append a dated section to `docs/review-report.md`**

Append after the file's final line (`**Files to touch for the blockers:** ...`):

```markdown

---

## Plugin-layer re-check (2026-08-10) — `plugin-dev` validation

M9 ("plugin packaging") was marked fixed above on 2026-08-10, but that check never verified the
plugin against Claude Code's actual hook/manifest contract — it only confirmed the files existed.
A `plugin-dev:plugin-validator` pass the same day found the plugin was a **no-op**: the hook lived
in a disallowed location and pointed at a non-existent event (`PostInvocation`), its payload/output
schema didn't match any real Claude Code hook contract, and `marketplace.json` had three schema
deviations that would have broken the documented install command.

| Finding | Status | Evidence |
|---|---|---|
| Hook unreachable (`.claude-plugin/hooks.json`, no `./` prefix, wrong dir) | Fixed | Moved to `hooks/hooks.json`; `plugin.json` relies on default discovery; `test_hooks_json_lives_at_default_discovery_path` |
| Invalid hooks.json schema (`PostInvocation`, missing `matcher`/`hooks` wrapper) | Fixed | Rewritten as `Stop` event, wrapped format; `test_hooks_json_uses_wrapped_plugin_format_with_stop_event` |
| Hook payload/output used invented fields | Fixed | Rewritten against the real `transcript_path`/`systemMessage` contract; `test_plugin_hook.py` |
| `marketplace.json` schema (owner string, nested description, wrong source) | Fixed | `test_marketplace_json_*` in `test_manifest_schema.py` |
| MCP server never registered | Fixed | `.mcp.json`; `test_mcp_json_registers_the_desktop_plugin_server` |
| Non-portable hook command, no LICENSE, tracked bytecode | Fixed | `${CLAUDE_PLUGIN_ROOT}`-relative invocation + stdlib sys.path bootstrap; `.gitignore`; `LICENSE` |

**Suite:** see `pytest -v` output after Task 4, Step 3 above for the current pass count.
```

- [ ] **Step 6: Run the full suite one final time**

Run: `pytest -v`
Expected: All tests pass, no regressions from the starting count.

- [ ] **Step 7: Commit**

```bash
git add .gitignore LICENSE docs/review-report.md
git commit -m "chore: add .gitignore + LICENSE, untrack compiled bytecode, document plugin-layer fix"
```
