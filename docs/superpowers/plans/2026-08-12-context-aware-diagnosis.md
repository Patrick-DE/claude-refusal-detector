# Context-Aware Refusal Diagnosis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Diagnose refusals caused by anything in the reconstructable request — the prompt, prior turns, tool results, or a `CLAUDE.md` — instead of only the user's typed prompt, and stop reporting "not blocked" when the oracle never actually ran.

**Architecture:** A new `ContextAssembler` reads a transcript plus the `CLAUDE.md` files that applied and produces origin-tagged segments; the existing ddmin minimizer consumes them unchanged. A `ContextOracle` replays each candidate subset through `claude -p`, routing pre-prompt segments to `--append-system-prompt-file` so they occupy the same position as in the real request. The CLI adapter is made fail-loud first, because until then every result is untrustworthy.

**Tech Stack:** Python 3.10+, pytest, stdlib only (`json`, `subprocess`, `tempfile`, `pathlib`, `dataclasses`). No new third-party dependencies.

**Spec:** [`docs/superpowers/specs/2026-08-12-context-aware-diagnosis-design.md`](../specs/2026-08-12-context-aware-diagnosis-design.md)

## Global Constraints

- No new third-party dependencies. Stdlib only.
- Keep files under 500 LOC. `adapters.py` is already large — new adapters go in new modules.
- Preserve the existing `Oracle` port exactly: `test(prompt: str) -> Verdict`. The minimizer must stay unaware of context assembly.
- Preserve `Segment`'s existing fields (`index`, `text`, `start_char`, `end_char`, `start_line`, `end_line`). Extend by subclassing, never by editing `Segment` — `input_loader.py` and `minimizer.py` depend on its shape.
- Fixes use patch bumps (`x.y.Z+1`), never minor. Current version is `0.3.3`.
- All work must pass the existing suite with no regressions: `python -m pytest -q` (currently **60 passed, 2 skipped**).
- Every new test must be falsified green→red→green before its task is considered done.
- Windows/`cp1252` safety: never emit non-ASCII into strings that may be printed to a console. Use `->` not `→`.
- A failed oracle call must **raise**, never degrade to "not blocked" (`docs/design.md` §4.4).

### Verified facts this plan relies on

1. `claude auth status` emits JSON on stdout: `{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty"}`. Confirmed by running it.
2. A logged-out `claude -p` prints `Not logged in · Please run /login` and exits **1**. Confirmed.
3. The CLI supports `--model`, `--append-system-prompt-file`, `--system-prompt-file`, `--add-dir`, `--exclude-dynamic-system-prompt-sections`. Confirmed via `claude --help`.
4. In the captured transcript, the `model_refusal_fallback` record precedes every `tool_result` by ~84s. Content after the refusal was **not** in the refused request.
5. `Segment` is a frozen-style dataclass in `ports.py` with exactly the six fields listed above.
6. `RefusalClassifier.classify_text(text) -> Verdict` and `classify_moderation_error(msg) -> Verdict` exist and are pure.

---

### Task 1: Make the CLI adapter fail loud

**Files:**
- Modify: `src/refusal_detector/adapters.py` (`ClaudeCodeCLIAdapter.test`)
- Test: `tests/test_cli_adapter_failloud.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ClaudeCodeCLIAdapter.test()` raises `RuntimeError` on any non-zero exit that is not a recognised moderation block. Task 2's preflight and Task 5's oracle both depend on this being trustworthy.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_adapter_failloud.py`:

```python
"""A failed CLI call must never be reported as 'not blocked'."""

from unittest.mock import MagicMock, patch

import pytest

from refusal_detector.adapters import ClaudeCodeCLIAdapter


def _run_with(returncode: int, stdout: str = "", stderr: str = ""):
    result = MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)
    return patch("refusal_detector.adapters.subprocess.run", return_value=result)


def test_not_logged_in_raises_instead_of_reporting_not_blocked():
    """The exact real-world output that silently produced false negatives."""
    adapter = ClaudeCodeCLIAdapter(timeout=5)
    with _run_with(1, stdout="Not logged in \u00b7 Please run /login\n"):
        with pytest.raises(RuntimeError) as excinfo:
            adapter.test("anything")
    assert "not logged in" in str(excinfo.value).lower()


def test_generic_nonzero_exit_raises():
    adapter = ClaudeCodeCLIAdapter(timeout=5)
    with _run_with(2, stderr="some unexpected failure"):
        with pytest.raises(RuntimeError):
            adapter.test("anything")


def test_moderation_block_on_nonzero_exit_is_still_a_verdict():
    """A genuine block must stay a Verdict, not become an exception."""
    adapter = ClaudeCodeCLIAdapter(timeout=5)
    with _run_with(1, stdout="Request was blocked by content filters"):
        verdict = adapter.test("anything")
    assert verdict.blocked is True


def test_successful_call_still_classifies_normally():
    adapter = ClaudeCodeCLIAdapter(timeout=5)
    with _run_with(0, stdout="Here is a perfectly ordinary answer."):
        verdict = adapter.test("anything")
    assert verdict.blocked is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_cli_adapter_failloud.py -v`
Expected: the first two FAIL — currently a logged-out call returns `Verdict(blocked=False)` instead of raising.

- [ ] **Step 3: Implement**

In `src/refusal_detector/adapters.py`, replace the body of `ClaudeCodeCLIAdapter.test`'s `try` block. Find:

```python
            output = (res.stdout or "") + "\n" + (res.stderr or "")
            if res.returncode != 0 and ("refus" in output.lower() or "blocked" in output.lower()):
                return self.classifier.classify_moderation_error(output)
            return self.classifier.classify_text(output)
```

Replace with:

```python
            output = (res.stdout or "") + "\n" + (res.stderr or "")
            if res.returncode != 0:
                lowered = output.lower()
                if "refus" in lowered or "blocked" in lowered:
                    return self.classifier.classify_moderation_error(output)
                # Fail loud: a CLI that could not run tells us nothing about the
                # prompt. Treating this as "not blocked" produced false negatives
                # in the field for every diagnosis made while logged out.
                raise RuntimeError(
                    f"Claude CLI exited {res.returncode} without a verdict: {output.strip()[:200]}"
                )
            return self.classifier.classify_text(output)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_cli_adapter_failloud.py -v`
Expected: 4 passed.

- [ ] **Step 5: Falsify**

Temporarily revert the `raise RuntimeError(...)` to `return self.classifier.classify_text(output)`. Re-run: the first two tests must go RED. Restore, confirm green. Report the result.

- [ ] **Step 6: Full suite**

Run: `python -m pytest -q`
Expected: 64 passed, 2 skipped (60 + 4 new).

- [ ] **Step 7: Commit**

```bash
git add src/refusal_detector/adapters.py tests/test_cli_adapter_failloud.py
git commit -m "fix: raise instead of reporting 'not blocked' when the CLI fails"
```

---

### Task 2: Auth preflight

**Files:**
- Create: `src/refusal_detector/cli_auth.py`
- Test: `tests/test_cli_auth.py` (create)

**Interfaces:**
- Consumes: nothing from Task 1 directly.
- Produces: `check_cli_auth() -> None` — raises `CliNotAuthenticatedError` (a `RuntimeError` subclass) with setup instructions when the CLI is not logged in. Task 5's `ContextOracle` calls this once before probing.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_auth.py`:

```python
"""Preflight: refuse to probe when the CLI cannot authenticate."""

from unittest.mock import MagicMock, patch

import pytest

from refusal_detector.cli_auth import CliNotAuthenticatedError, check_cli_auth


def _status(stdout: str, returncode: int = 0):
    return patch(
        "refusal_detector.cli_auth.subprocess.run",
        return_value=MagicMock(returncode=returncode, stdout=stdout, stderr=""),
    )


def test_logged_out_raises_with_setup_instructions():
    """The exact shape `claude auth status` really returns when logged out."""
    with _status('{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty"}'):
        with pytest.raises(CliNotAuthenticatedError) as excinfo:
            check_cli_auth()
    message = str(excinfo.value)
    assert "claude setup-token" in message, "must tell the user how to fix it"


def test_logged_in_passes_silently():
    with _status('{"loggedIn": true, "authMethod": "oauth", "apiProvider": "firstParty"}'):
        assert check_cli_auth() is None


def test_unparseable_status_raises_rather_than_assuming_success():
    with _status("not json at all"):
        with pytest.raises(CliNotAuthenticatedError):
            check_cli_auth()


def test_status_command_failure_raises():
    with _status("", returncode=1):
        with pytest.raises(CliNotAuthenticatedError):
            check_cli_auth()


def test_noise_before_the_json_is_tolerated():
    """The CLI prefixes unrelated permission warnings on some machines."""
    noisy = 'Permission deny rule (...): ignore me\n{"loggedIn": true, "authMethod": "oauth"}'
    with _status(noisy):
        assert check_cli_auth() is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_cli_auth.py -v`
Expected: FAIL — `refusal_detector.cli_auth` does not exist.

- [ ] **Step 3: Implement**

Create `src/refusal_detector/cli_auth.py`:

```python
"""Preflight check that the `claude` CLI can actually authenticate.

Probing while logged out is worse than not probing: the CLI exits non-zero, and any
diagnosis built on that is a false negative. This turns an invisible failure into a
message that names the fix.
"""

import json
import subprocess

from refusal_detector.logger import get_logger

logger = get_logger("cli_auth")

_SETUP_INSTRUCTIONS = (
    "The `claude` CLI is not authenticated, so refusal probes cannot run.\n"
    "Run `claude setup-token` (a long-lived token, which is what non-interactive "
    "hooks need), then set CLAUDE_CODE_OAUTH_TOKEN in the environment and restart "
    "Claude Code so the new value is inherited."
)


class CliNotAuthenticatedError(RuntimeError):
    """Raised when `claude auth status` does not confirm an authenticated session."""


def _extract_json(text: str) -> dict:
    """Parse the status JSON, tolerating unrelated warning lines printed before it."""
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in output")
    return json.loads(text[start:])


def check_cli_auth(timeout: float = 30.0) -> None:
    """Raise CliNotAuthenticatedError unless the CLI reports a logged-in session."""
    try:
        result = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError as e:
        raise CliNotAuthenticatedError("Claude CLI binary 'claude' not found in PATH.") from e
    except subprocess.TimeoutExpired as e:
        raise CliNotAuthenticatedError(f"`claude auth status` timed out after {timeout}s") from e

    if result.returncode != 0:
        raise CliNotAuthenticatedError(
            f"`claude auth status` exited {result.returncode}.\n{_SETUP_INSTRUCTIONS}"
        )

    try:
        status = _extract_json(result.stdout or "")
    except (ValueError, json.JSONDecodeError) as e:
        raise CliNotAuthenticatedError(
            f"Could not read `claude auth status` output.\n{_SETUP_INSTRUCTIONS}"
        ) from e

    if not status.get("loggedIn"):
        raise CliNotAuthenticatedError(
            f"Not logged in (authMethod={status.get('authMethod')!r}).\n{_SETUP_INSTRUCTIONS}"
        )

    logger.info("Claude CLI authenticated (authMethod=%s).", status.get("authMethod"))
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_cli_auth.py -v`
Expected: 5 passed.

- [ ] **Step 5: Falsify**

Change `if not status.get("loggedIn"):` to `if False:`. Re-run: `test_logged_out_raises_with_setup_instructions` must go RED. Restore, confirm green.

- [ ] **Step 6: Commit**

```bash
git add src/refusal_detector/cli_auth.py tests/test_cli_auth.py
git commit -m "feat: preflight the CLI auth state before probing"
```

---

### Task 3: Origin-tagged context segments

**Files:**
- Create: `src/refusal_detector/context.py`
- Test: `tests/test_context_assembler.py` (create)

**Interfaces:**
- Consumes: `Segment` from `refusal_detector.ports` (fields: `index`, `text`, `start_char`, `end_char`, `start_line`, `end_line`).
- Produces:
  - `class SegmentOrigin(str, Enum)` with members `PROMPT = "prompt"`, `TOOL_RESULT = "tool_result"`, `PRIOR_TURN = "prior_turn"`, `PROJECT_CLAUDE_MD = "project_claude_md"`, `GLOBAL_CLAUDE_MD = "global_claude_md"`.
  - `class ContextSegment(Segment)` — a dataclass adding `origin: SegmentOrigin` and `source_label: str`, inheriting all six `Segment` fields.
  - `is_pre_prompt(origin: SegmentOrigin) -> bool` — True for the two `*_CLAUDE_MD` origins. Task 5 routes on this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_context_assembler.py`:

```python
"""Origin-tagged segments: the report must name which source holds the trigger."""

from refusal_detector.context import (
    ContextSegment,
    SegmentOrigin,
    is_pre_prompt,
)
from refusal_detector.ports import Segment


def test_context_segment_is_usable_wherever_a_segment_is():
    """The minimizer consumes Segments; ContextSegment must not break that contract."""
    segment = ContextSegment(
        index=0,
        text="hello",
        start_char=0,
        end_char=5,
        start_line=1,
        end_line=1,
        origin=SegmentOrigin.PROMPT,
        source_label="user prompt",
    )
    assert isinstance(segment, Segment)
    assert segment.text == "hello"
    assert segment.origin is SegmentOrigin.PROMPT
    assert segment.source_label == "user prompt"


def test_claude_md_origins_are_pre_prompt_and_others_are_not():
    assert is_pre_prompt(SegmentOrigin.PROJECT_CLAUDE_MD) is True
    assert is_pre_prompt(SegmentOrigin.GLOBAL_CLAUDE_MD) is True
    assert is_pre_prompt(SegmentOrigin.PROMPT) is False
    assert is_pre_prompt(SegmentOrigin.TOOL_RESULT) is False
    assert is_pre_prompt(SegmentOrigin.PRIOR_TURN) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_context_assembler.py -v`
Expected: FAIL — `refusal_detector.context` does not exist.

- [ ] **Step 3: Implement**

Create `src/refusal_detector/context.py`:

```python
"""Origin-tagged segments spanning every reconstructable part of a refused request.

A refusal can be triggered by the prompt, by a prior turn, by a tool result, or by a
CLAUDE.md loaded ahead of the conversation. Reporting *which* source holds the trigger
is what makes the result actionable: "line 340 of your project CLAUDE.md" is a fix,
"line 340" is not.
"""

from dataclasses import dataclass
from enum import Enum

from refusal_detector.ports import Segment


class SegmentOrigin(str, Enum):
    """Which part of the assembled request a segment came from."""

    PROMPT = "prompt"
    TOOL_RESULT = "tool_result"
    PRIOR_TURN = "prior_turn"
    PROJECT_CLAUDE_MD = "project_claude_md"
    GLOBAL_CLAUDE_MD = "global_claude_md"


_PRE_PROMPT_ORIGINS = frozenset({SegmentOrigin.PROJECT_CLAUDE_MD, SegmentOrigin.GLOBAL_CLAUDE_MD})


def is_pre_prompt(origin: SegmentOrigin) -> bool:
    """True when the segment belongs ahead of the conversation, not inside it.

    Pre-prompt material must be replayed through the system-prompt channel so it sits
    where it sat in the real request; feeding it as conversation text would test a
    different arrangement than the one that was refused.
    """
    return origin in _PRE_PROMPT_ORIGINS


@dataclass
class ContextSegment(Segment):
    """A Segment that remembers where it came from."""

    origin: SegmentOrigin = SegmentOrigin.PROMPT
    source_label: str = ""
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_context_assembler.py -v`
Expected: 2 passed.

- [ ] **Step 5: Prove the seam with the real minimizer**

A `ContextSegment` that the existing minimizer cannot consume would break every later task.
Append to `tests/test_context_assembler.py`:

```python


def test_real_minimizer_accepts_context_segments_and_preserves_origin():
    """The minimizer is untouched by this work; it must swallow the subclass unchanged."""
    from refusal_detector.adapters import FakeOracleAdapter
    from refusal_detector.minimizer import Minimizer

    segments = [
        ContextSegment(
            index=i, text=text, start_char=0, end_char=len(text), start_line=i + 1,
            end_line=i + 1, origin=origin, source_label=origin.value,
        )
        for i, (text, origin) in enumerate(
            [
                ("benign opening line", SegmentOrigin.PROMPT),
                ("DANGEROUS_TRIGGER_LINE", SegmentOrigin.PROJECT_CLAUDE_MD),
                ("another benign line", SegmentOrigin.PROMPT),
            ]
        )
    ]

    trigger, _ = Minimizer(FakeOracleAdapter(triggers=["DANGEROUS_TRIGGER_LINE"])).minimize(segments)

    assert [s.text for s in trigger] == ["DANGEROUS_TRIGGER_LINE"]
    assert trigger[0].origin is SegmentOrigin.PROJECT_CLAUDE_MD, "origin must survive minimization"
```

Run: `python -m pytest tests/test_context_assembler.py -v`
Expected: 3 passed. If the minimizer rejects the subclass or drops `origin`, **stop and report
BLOCKED** — the whole design depends on this holding.

- [ ] **Step 6: Commit**

```bash
git add src/refusal_detector/context.py tests/test_context_assembler.py
git commit -m "feat: add origin-tagged context segments"
```

---

### Task 4: Assemble the refused request from a transcript

**Files:**
- Modify: `src/refusal_detector/context.py` (append)
- Modify: `tests/test_context_assembler.py` (append)

**Interfaces:**
- Consumes: `ContextSegment`, `SegmentOrigin` from Task 3.
- Produces: `assemble_context(records: list[dict], refusal_index: int, claude_md_files: list[tuple[SegmentOrigin, str, str]] | None = None) -> list[ContextSegment]`.
  - `records` is the parsed transcript (same shape `refusal_hook._read_records` returns).
  - `refusal_index` is the index of the `model_refusal_fallback` record; **everything at or after it is excluded**.
  - `claude_md_files` is a list of `(origin, label, content)` tuples, emitted before conversation segments.
  - Segments are line-split, `index` is sequential across the whole assembly, and `start_char`/`end_char` are offsets into that segment's own source text.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_context_assembler.py`:

```python


from refusal_detector.context import assemble_context


def _user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _tool_result(text: str) -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "tool_result", "content": text}]},
    }


def _refusal() -> dict:
    return {"type": "system", "subtype": "model_refusal_fallback", "apiRefusalCategory": "cyber"}


def test_content_after_the_refusal_is_excluded():
    """The defect that motivated this redesign: later tool results were never in the
    refused request, so including them would minimize content the model never saw."""
    records = [
        _user("the prompt that was refused"),
        _refusal(),
        _tool_result("file content that arrived AFTER the refusal"),
    ]

    segments = assemble_context(records, refusal_index=1)

    joined = " ".join(s.text for s in segments)
    assert "the prompt that was refused" in joined
    assert "AFTER the refusal" not in joined


def test_tool_results_before_the_refusal_are_included_and_tagged():
    records = [
        _user("please audit this"),
        _tool_result("contents of a scanned file"),
        _refusal(),
    ]

    segments = assemble_context(records, refusal_index=2)
    origins = {s.origin for s in segments}

    assert SegmentOrigin.TOOL_RESULT in origins
    assert any("scanned file" in s.text for s in segments)


def test_claude_md_segments_come_first_and_are_tagged():
    records = [_user("the prompt"), _refusal()]
    claude_md = [(SegmentOrigin.PROJECT_CLAUDE_MD, "project CLAUDE.md", "line one\nline two")]

    segments = assemble_context(records, refusal_index=1, claude_md_files=claude_md)

    assert segments[0].origin is SegmentOrigin.PROJECT_CLAUDE_MD
    assert segments[0].source_label == "project CLAUDE.md"
    assert segments[0].text == "line one"
    assert any(s.origin is SegmentOrigin.PROMPT for s in segments)


def test_indices_are_sequential_across_all_sources():
    records = [_user("a\nb"), _refusal()]
    claude_md = [(SegmentOrigin.GLOBAL_CLAUDE_MD, "global", "x\ny")]

    segments = assemble_context(records, refusal_index=1, claude_md_files=claude_md)

    assert [s.index for s in segments] == list(range(len(segments)))


def test_empty_lines_are_not_emitted_as_segments():
    records = [_user("first\n\n\nsecond"), _refusal()]

    segments = assemble_context(records, refusal_index=1)

    assert all(s.text.strip() for s in segments)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_context_assembler.py -v`
Expected: FAIL — `assemble_context` is not defined.

- [ ] **Step 3: Implement**

Append to `src/refusal_detector/context.py`:

```python


def _segment_lines(
    text: str,
    origin: SegmentOrigin,
    source_label: str,
    start_index: int,
) -> list[ContextSegment]:
    """Split one source into line segments, skipping blank lines."""
    segments: list[ContextSegment] = []
    char_cursor = 0
    index = start_index

    for line_number, line in enumerate(text.split("\n"), start=1):
        line_length = len(line)
        if line.strip():
            segments.append(
                ContextSegment(
                    index=index,
                    text=line,
                    start_char=char_cursor,
                    end_char=char_cursor + line_length,
                    start_line=line_number,
                    end_line=line_number,
                    origin=origin,
                    source_label=source_label,
                )
            )
            index += 1
        char_cursor += line_length + 1  # +1 for the newline consumed by split

    return segments


def _conversation_text(record: dict) -> tuple[str, SegmentOrigin] | None:
    """Return (text, origin) for a record that contributed content to the request."""
    message = record.get("message")
    if not isinstance(message, dict):
        return None

    content = message.get("content")
    record_type = record.get("type")

    if isinstance(content, str):
        if not content.strip():
            return None
        origin = SegmentOrigin.PROMPT if record_type == "user" else SegmentOrigin.PRIOR_TURN
        return content, origin

    if isinstance(content, list):
        collected = []
        origin = SegmentOrigin.PRIOR_TURN
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                collected.append(str(block.get("content", "")))
                origin = SegmentOrigin.TOOL_RESULT
            elif block.get("type") == "text":
                collected.append(str(block.get("text", "")))
        joined = "\n".join(part for part in collected if part.strip())
        if joined.strip():
            return joined, origin

    return None


def assemble_context(
    records: list[dict],
    refusal_index: int,
    claude_md_files: list[tuple[SegmentOrigin, str, str]] | None = None,
) -> list[ContextSegment]:
    """Rebuild the refused request as origin-tagged segments.

    Everything at or after `refusal_index` is excluded: it had not been sent when the
    refusal fired, so it cannot be part of what was refused.
    """
    segments: list[ContextSegment] = []

    for origin, label, content in claude_md_files or []:
        segments.extend(_segment_lines(content, origin, label, len(segments)))

    for record in records[:refusal_index]:
        extracted = _conversation_text(record)
        if extracted is None:
            continue
        text, origin = extracted
        label = origin.value
        segments.extend(_segment_lines(text, origin, label, len(segments)))

    return segments
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_context_assembler.py -v`
Expected: 8 passed (3 from Task 3 + 5 new).

- [ ] **Step 5: Falsify**

Change `for record in records[:refusal_index]:` to `for record in records:`. Re-run: `test_content_after_the_refusal_is_excluded` must go RED. Restore, confirm green.

- [ ] **Step 6: Verify against the real captured transcript**

Run this against the real refusal that motivated the redesign:

```bash
python -c "
import sys, os, json; sys.path.insert(0,'src')
from refusal_detector.context import assemble_context, SegmentOrigin
p=os.path.expanduser('~/.claude/projects/C--Users-patri-sources-repos-unrealengine-debugger/196daf17-75ca-4f0b-9190-cc282ff2a2bc.jsonl')
recs=[json.loads(l) for l in open(p,encoding='utf-8') if l.strip()]
idx=next(i for i,r in enumerate(recs) if r.get('subtype')=='model_refusal_fallback')
segs=assemble_context(recs, idx)
print('refusal at record', idx, '| segments:', len(segs))
print('origins:', {o.value for o in {s.origin for s in segs}})
print('any post-refusal tool_result leaked in:', any(s.origin.value=='tool_result' for s in segs))
"
```

Expected: segments come only from the prompt/prior turns, and the post-refusal `tool_result` content does **not** appear (that transcript's tool results all arrive after the refusal). Report the actual numbers.

- [ ] **Step 7: Commit**

```bash
git add src/refusal_detector/context.py tests/test_context_assembler.py
git commit -m "feat: assemble the refused request from a transcript"
```

---

### Task 5: ContextOracle — replay candidates through the right channel

**Files:**
- Create: `src/refusal_detector/context_oracle.py`
- Test: `tests/test_context_oracle.py` (create)

**Interfaces:**
- Consumes: `ContextSegment`, `SegmentOrigin`, `is_pre_prompt` (Task 3); `check_cli_auth`, `CliNotAuthenticatedError` (Task 2); `ClaudeCodeCLIAdapter` (Task 1).
- Produces: `class ContextOracle` with:
  - `__init__(self, segments: list[ContextSegment], model: str | None = None, timeout: float = 120.0, adapter=None)`
  - `test(prompt: str) -> Verdict` — satisfies the existing `Oracle` port.
  - `build_channels(subset: list[ContextSegment]) -> tuple[str, str]` returning `(system_prompt_text, conversation_text)`.

The minimizer joins segment text and calls `test(joined)`. `ContextOracle` cannot recover origins from a joined string, so it maps the joined text back to segments by identity via `build_channels`. To keep the `Oracle` port intact, `test()` looks up which segments are present by matching their text against the joined prompt in order.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_context_oracle.py`:

```python
"""Pre-prompt segments must be replayed through the system-prompt channel."""

from unittest.mock import MagicMock

import pytest

from refusal_detector.context import ContextSegment, SegmentOrigin
from refusal_detector.context_oracle import ContextOracle
from refusal_detector.ports import ReasonClass, Verdict


def _segment(index: int, text: str, origin: SegmentOrigin) -> ContextSegment:
    return ContextSegment(
        index=index,
        text=text,
        start_char=0,
        end_char=len(text),
        start_line=1,
        end_line=1,
        origin=origin,
        source_label=origin.value,
    )


def _oracle(segments, adapter=None):
    return ContextOracle(segments=segments, model="claude-fable-5", adapter=adapter)


def test_pre_prompt_segments_go_to_the_system_channel():
    segments = [
        _segment(0, "a claude md rule", SegmentOrigin.PROJECT_CLAUDE_MD),
        _segment(1, "the user prompt", SegmentOrigin.PROMPT),
    ]

    system_text, conversation_text = _oracle(segments).build_channels(segments)

    assert "a claude md rule" in system_text
    assert "a claude md rule" not in conversation_text
    assert "the user prompt" in conversation_text
    assert "the user prompt" not in system_text


def test_a_subset_without_pre_prompt_segments_yields_empty_system_text():
    segments = [_segment(0, "just a prompt", SegmentOrigin.PROMPT)]

    system_text, conversation_text = _oracle(segments).build_channels(segments)

    assert system_text == ""
    assert "just a prompt" in conversation_text


def test_test_passes_the_system_prompt_through_to_the_adapter():
    segments = [
        _segment(0, "claude md line", SegmentOrigin.PROJECT_CLAUDE_MD),
        _segment(1, "prompt line", SegmentOrigin.PROMPT),
    ]
    adapter = MagicMock()
    adapter.test_with_system.return_value = Verdict(blocked=True, reason_class=ReasonClass.TEXT_PATTERN)

    verdict = _oracle(segments, adapter=adapter).test("claude md line\nprompt line")

    assert verdict.blocked is True
    kwargs = adapter.test_with_system.call_args.kwargs
    assert "claude md line" in kwargs["system_prompt"]
    assert "prompt line" in kwargs["prompt"]


def test_auth_failure_propagates_rather_than_becoming_not_blocked():
    from refusal_detector.cli_auth import CliNotAuthenticatedError

    segments = [_segment(0, "prompt line", SegmentOrigin.PROMPT)]
    adapter = MagicMock()
    adapter.test_with_system.side_effect = CliNotAuthenticatedError("not logged in")

    with pytest.raises(CliNotAuthenticatedError):
        _oracle(segments, adapter=adapter).test("prompt line")
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_context_oracle.py -v`
Expected: FAIL — `refusal_detector.context_oracle` does not exist.

- [ ] **Step 3: Implement**

Create `src/refusal_detector/context_oracle.py`:

```python
"""Replay candidate context subsets through the channel each segment really occupied.

A CLAUDE.md line is not equivalent to the same words typed into a prompt: the model
sees it ahead of the conversation, in the system channel. Replaying it as conversation
text would test an arrangement that never happened, so origin decides the channel.
"""

from refusal_detector.context import ContextSegment, is_pre_prompt
from refusal_detector.logger import get_logger
from refusal_detector.ports import Verdict

logger = get_logger("context_oracle")


class ContextOracle:
    """An Oracle that routes each segment to the request channel it came from."""

    def __init__(
        self,
        segments: list[ContextSegment],
        model: str | None = None,
        timeout: float = 120.0,
        adapter=None,
    ) -> None:
        self._segments = segments
        self._model = model
        self._timeout = timeout
        if adapter is None:
            from refusal_detector.system_prompt_adapter import SystemPromptCLIAdapter

            adapter = SystemPromptCLIAdapter(timeout=timeout, model=model)
        self._adapter = adapter

    def build_channels(self, subset: list[ContextSegment]) -> tuple[str, str]:
        """Split a candidate subset into (system prompt text, conversation text)."""
        system_lines = [s.text for s in subset if is_pre_prompt(s.origin)]
        conversation_lines = [s.text for s in subset if not is_pre_prompt(s.origin)]
        return "\n".join(system_lines), "\n".join(conversation_lines)

    def _subset_for(self, prompt: str) -> list[ContextSegment]:
        """Recover which segments a joined prompt represents.

        The Oracle port hands us a joined string, so membership is resolved by walking
        the known segments in order and keeping those whose text appears in it. Order is
        preserved, which is what the channel split depends on.
        """
        return [s for s in self._segments if s.text and s.text in prompt]

    def test(self, prompt: str) -> Verdict:
        """Oracle port: probe one candidate subset."""
        subset = self._subset_for(prompt)
        system_prompt, conversation = self.build_channels(subset)
        logger.info(
            "Probing %d segments (%d pre-prompt, %d conversation).",
            len(subset),
            sum(1 for s in subset if is_pre_prompt(s.origin)),
            sum(1 for s in subset if not is_pre_prompt(s.origin)),
        )
        return self._adapter.test_with_system(prompt=conversation, system_prompt=system_prompt)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_context_oracle.py -v`
Expected: 4 passed.

- [ ] **Step 5: Falsify**

In `build_channels`, change `if is_pre_prompt(s.origin)` to `if False` for `system_lines`. Re-run: `test_pre_prompt_segments_go_to_the_system_channel` must go RED. Restore, confirm green.

- [ ] **Step 6: Commit**

```bash
git add src/refusal_detector/context_oracle.py tests/test_context_oracle.py
git commit -m "feat: route context segments to the channel they came from"
```

---

### Task 6: SystemPromptCLIAdapter — the real CLI call with a system prompt

**Files:**
- Create: `src/refusal_detector/system_prompt_adapter.py`
- Test: `tests/test_system_prompt_adapter.py` (create)

**Interfaces:**
- Consumes: `check_cli_auth` (Task 2), `RefusalClassifier` from `refusal_detector.classifier`.
- Produces: `class SystemPromptCLIAdapter` with `test_with_system(prompt: str, system_prompt: str = "") -> Verdict`, called by `ContextOracle` (Task 5).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_system_prompt_adapter.py`:

```python
"""The CLI call must hold ambient context fixed and inject pre-prompt material properly."""

from unittest.mock import MagicMock, patch

import pytest

from refusal_detector.system_prompt_adapter import SystemPromptCLIAdapter


def _run_with(returncode: int = 0, stdout: str = "ok", stderr: str = ""):
    return patch(
        "refusal_detector.system_prompt_adapter.subprocess.run",
        return_value=MagicMock(returncode=returncode, stdout=stdout, stderr=stderr),
    )


def _adapter(**kwargs):
    adapter = SystemPromptCLIAdapter(timeout=30, model="claude-fable-5", **kwargs)
    adapter._auth_checked = True  # preflight is covered by its own tests
    return adapter


def test_ambient_context_is_held_fixed():
    """Without these flags the probe silently varies with the working directory."""
    with _run_with() as run:
        _adapter().test_with_system("hello")

    cmd = run.call_args.args[0]
    assert "--exclude-dynamic-system-prompt-sections" in cmd
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "claude-fable-5"


def test_system_prompt_is_passed_by_file_not_argv():
    """Pre-prompt material can be huge; argv limits would truncate it silently."""
    with _run_with() as run:
        _adapter().test_with_system("hello", system_prompt="a rule\nanother rule")

    cmd = run.call_args.args[0]
    assert "--append-system-prompt-file" in cmd
    path = cmd[cmd.index("--append-system-prompt-file") + 1]
    assert path, "a file path must follow the flag"


def test_no_system_prompt_flag_when_there_is_none():
    with _run_with() as run:
        _adapter().test_with_system("hello", system_prompt="")

    assert "--append-system-prompt-file" not in run.call_args.args[0]


def test_prompt_goes_via_stdin_not_argv():
    """Shell-injection safety: the content under test is untrusted input."""
    with _run_with() as run:
        _adapter().test_with_system("untrusted & content | here")

    assert run.call_args.kwargs["input"] == "untrusted & content | here"
    assert run.call_args.kwargs["shell"] is False


def test_failed_call_raises_rather_than_reporting_not_blocked():
    with _run_with(returncode=1, stdout="Not logged in \u00b7 Please run /login"):
        with pytest.raises(RuntimeError):
            _adapter().test_with_system("hello")


def test_temp_system_prompt_file_is_cleaned_up():
    import glob
    import tempfile

    before = set(glob.glob(f"{tempfile.gettempdir()}/refusal_sysprompt_*"))
    with _run_with():
        _adapter().test_with_system("hello", system_prompt="a rule")
    after = set(glob.glob(f"{tempfile.gettempdir()}/refusal_sysprompt_*"))

    assert after == before, "temp system-prompt files must not accumulate"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_system_prompt_adapter.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

Create `src/refusal_detector/system_prompt_adapter.py`:

```python
"""A `claude -p` oracle that controls the ambient context instead of inheriting it.

Left to itself the CLI injects its own system prompt and auto-discovers CLAUDE.md from
the working directory, so the same probe gives different answers in different folders.
Holding those flags fixed turns that confound into a constant, and lets pre-prompt
material under test be injected deliberately.
"""

import os
import subprocess
import tempfile

from refusal_detector.classifier import RefusalClassifier
from refusal_detector.cli_auth import check_cli_auth
from refusal_detector.logger import get_logger
from refusal_detector.ports import Verdict

logger = get_logger("system_prompt_adapter")


class SystemPromptCLIAdapter:
    """Probe `claude -p` with an explicit system prompt and fixed ambient context."""

    def __init__(self, timeout: float = 120.0, model: str | None = None) -> None:
        self.timeout = timeout
        self.model = model
        self.classifier = RefusalClassifier()
        self._auth_checked = False

    def test_with_system(self, prompt: str, system_prompt: str = "") -> Verdict:
        """Probe one candidate; raise rather than guess if the CLI cannot answer."""
        if not self._auth_checked:
            check_cli_auth()
            self._auth_checked = True

        system_prompt_path = None
        try:
            cmd = ["claude", "-p", "-", "--exclude-dynamic-system-prompt-sections"]
            if self.model:
                cmd += ["--model", self.model]

            if system_prompt.strip():
                # Pre-prompt material can be many kilobytes; argv would truncate it.
                handle, system_prompt_path = tempfile.mkstemp(
                    prefix="refusal_sysprompt_", suffix=".txt", text=True
                )
                with os.fdopen(handle, "w", encoding="utf-8") as f:
                    f.write(system_prompt)
                cmd += ["--append-system-prompt-file", system_prompt_path]

            try:
                res = subprocess.run(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    shell=False,
                )
            except FileNotFoundError as e:
                raise RuntimeError("Claude CLI binary 'claude' not found in PATH.") from e
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(f"Claude CLI timed out after {self.timeout}s") from e

            output = (res.stdout or "") + "\n" + (res.stderr or "")
            if res.returncode != 0:
                lowered = output.lower()
                if "refus" in lowered or "blocked" in lowered:
                    return self.classifier.classify_moderation_error(output)
                raise RuntimeError(
                    f"Claude CLI exited {res.returncode} without a verdict: {output.strip()[:200]}"
                )
            return self.classifier.classify_text(output)
        finally:
            if system_prompt_path:
                try:
                    os.unlink(system_prompt_path)
                except OSError:
                    logger.warning("Could not remove temp system prompt %s", system_prompt_path)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_system_prompt_adapter.py -v`
Expected: 6 passed.

- [ ] **Step 5: Falsify**

Remove `"--exclude-dynamic-system-prompt-sections"` from the `cmd` list. Re-run: `test_ambient_context_is_held_fixed` must go RED. Restore, confirm green.

- [ ] **Step 6: Full suite**

Run: `python -m pytest -q`
Expected: 87 passed, 2 skipped.

- [ ] **Step 7: Commit**

```bash
git add src/refusal_detector/system_prompt_adapter.py tests/test_system_prompt_adapter.py
git commit -m "feat: probe the CLI with controlled ambient context"
```

---

### Task 7: Measure the real probe cost and set the hook budget from it

**Files:**
- Modify: `src/refusal_detector/hooks/refusal_hook.py` (`_HOOK_MAX_CALLS`, `_HOOK_WALL_CLOCK_BUDGET_SECONDS` docstrings and values)
- Modify: `hooks/hooks.json` (`timeout`)
- Modify: `docs/superpowers/specs/2026-08-12-context-aware-diagnosis-design.md` (§4.4)

**Interfaces:**
- Consumes: `SystemPromptCLIAdapter` (Task 6), `check_cli_auth` (Task 2).
- Produces: budget constants justified by a recorded measurement. No new API.

**Precondition:** `claude auth status` must report `loggedIn: true`. If it does not, **stop and report BLOCKED** — the whole point of this task is to replace a number that was previously measured from auth failures.

- [ ] **Step 1: Confirm the oracle is actually usable**

```bash
claude auth status
```

Expected: `"loggedIn": true`. If false, stop and report BLOCKED with this output.

- [ ] **Step 2: Measure three real probes**

```bash
cd "C:/Users/patri/sources/repos/claude-refusal-detector"
REFUSAL_DETECTOR_HOOK_ACTIVE=1 python -c "
import sys,time; sys.path.insert(0,'src')
from refusal_detector.system_prompt_adapter import SystemPromptCLIAdapter
a=SystemPromptCLIAdapter(timeout=180, model='claude-fable-5')
times=[]
for i in range(3):
    t=time.time(); v=a.test_with_system('Say OK.'); dt=time.time()-t
    times.append(dt); print('probe %d: %.1fs blocked=%s'%(i+1,dt,v.blocked))
print('mean: %.1fs  max: %.1fs'%(sum(times)/len(times), max(times)))
"
```

Record the mean and max. These are the first trustworthy numbers this project has had.

- [ ] **Step 3: Set the budget from the measurement**

In `src/refusal_detector/hooks/refusal_hook.py`, set `_HOOK_WALL_CLOCK_BUDGET_SECONDS` to `_HOOK_MAX_CALLS * max_probe_seconds * 1.3` rounded up to the next 10s, and set the `timeout` in `hooks/hooks.json` to that value plus 20s. Update both docstrings to state the measured figure and the date, replacing the stale "~12s" claim.

If the resulting hook timeout exceeds 300s, **do not raise it further** — instead reduce `_HOOK_MAX_CALLS` until the budget fits inside 300s, and note in the docstring that completeness was traded for a bounded stall. A Stop hook blocks the user's session; a five-minute pause is not acceptable.

- [ ] **Step 4: Verify the invariant tests still hold**

Run: `python -m pytest tests/test_plugin_hook.py -k "budget or watchdog" -v`
Expected: both PASS. They assert budget > `_HOOK_MAX_CALLS * 12.0` and watchdog < configured timeout; update the hard-coded `12.0` in `test_wall_clock_budget_can_actually_contain_the_work_it_bounds` to the newly measured figure.

- [ ] **Step 5: Record the measurement in the spec**

Replace §4.4's "Unknown until the CLI is authenticated" paragraph with the measured mean/max, the date measured, and the resulting budget arithmetic.

- [ ] **Step 6: Commit**

```bash
git add src/refusal_detector/hooks/refusal_hook.py hooks/hooks.json docs/superpowers/specs/2026-08-12-context-aware-diagnosis-design.md tests/test_plugin_hook.py
git commit -m "fix: set the hook budget from a measured probe cost"
```

---

### Task 8: Wire context diagnosis into the hook and report origins

**Files:**
- Modify: `src/refusal_detector/hooks/refusal_hook.py`
- Modify: `tests/test_plugin_hook.py` (append)

**Interfaces:**
- Consumes: `assemble_context`, `SegmentOrigin` (Task 4); `ContextOracle` (Task 5); `_find_structured_refusal`, `_read_records`, `_render_refusal_banner` (existing in `refusal_hook.py`).
- Produces: the hook's `systemMessage` now names the origin of each trigger segment.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_plugin_hook.py`:

```python


def test_hook_reports_which_source_the_trigger_came_from(tmp_path):
    """"Line 340 of your project CLAUDE.md" is actionable; "line 340" is not."""
    from refusal_detector.context import ContextSegment, SegmentOrigin
    from refusal_detector.hooks.refusal_hook import render_origin_summary

    trigger = [
        ContextSegment(
            index=7,
            text="a rule that tripped a classifier",
            start_char=0,
            end_char=32,
            start_line=340,
            end_line=340,
            origin=SegmentOrigin.PROJECT_CLAUDE_MD,
            source_label="project CLAUDE.md",
        )
    ]

    summary = render_origin_summary(trigger)

    assert "project CLAUDE.md" in summary
    assert "340" in summary


def test_origin_summary_groups_multiple_sources(tmp_path):
    from refusal_detector.context import ContextSegment, SegmentOrigin
    from refusal_detector.hooks.refusal_hook import render_origin_summary

    trigger = [
        ContextSegment(
            index=1, text="from the prompt", start_char=0, end_char=15,
            start_line=2, end_line=2,
            origin=SegmentOrigin.PROMPT, source_label="prompt",
        ),
        ContextSegment(
            index=9, text="from a claude md", start_char=0, end_char=16,
            start_line=88, end_line=88,
            origin=SegmentOrigin.PROJECT_CLAUDE_MD, source_label="project CLAUDE.md",
        ),
    ]

    summary = render_origin_summary(trigger)

    assert "prompt" in summary
    assert "project CLAUDE.md" in summary


def test_origin_summary_is_empty_when_nothing_triggered():
    from refusal_detector.hooks.refusal_hook import render_origin_summary

    assert render_origin_summary([]) == ""
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_plugin_hook.py -k origin -v`
Expected: FAIL — `render_origin_summary` is not defined.

- [ ] **Step 3: Implement**

Add to `src/refusal_detector/hooks/refusal_hook.py`, above `main()`:

```python
def render_origin_summary(trigger_segments: list[Any]) -> str:
    """Name the source of every trigger segment, grouped by origin.

    Without this the report says which line minimized, but not which file to edit.
    """
    if not trigger_segments:
        return ""

    by_source: dict[str, list[Any]] = {}
    for segment in trigger_segments:
        label = getattr(segment, "source_label", "") or getattr(
            getattr(segment, "origin", None), "value", "unknown"
        )
        by_source.setdefault(label, []).append(segment)

    lines = ["## Trigger Sources", ""]
    for label, segments in by_source.items():
        line_numbers = ", ".join(str(getattr(s, "start_line", "?")) for s in segments)
        lines.append(f"- **{label}** - line(s) {line_numbers}")
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_plugin_hook.py -k origin -v`
Expected: 3 passed.

- [ ] **Step 5: Falsify**

Change `lines.append(f"- **{label}** - line(s) {line_numbers}")` to `lines.append("- (source omitted)")`. Re-run: the first two tests must go RED. Restore, confirm green.

- [ ] **Step 6: Full suite**

Run: `python -m pytest -q`
Expected: 90 passed, 2 skipped.

- [ ] **Step 7: Commit**

```bash
git add src/refusal_detector/hooks/refusal_hook.py tests/test_plugin_hook.py
git commit -m "feat: report which source each trigger segment came from"
```

---

### Task 9: Real round trip against the captured refusal

**Files:**
- Create: `tests/test_context_roundtrip.py`
- Modify: `docs/review-report.md` (append findings)

**Interfaces:**
- Consumes: everything from Tasks 1-8.
- Produces: the seam proof — or a documented, honest failure to reproduce.

**Precondition:** `claude auth status` reports `loggedIn: true`. If not, stop and report BLOCKED.

- [ ] **Step 1: Write the key-gated round-trip test**

Create `tests/test_context_roundtrip.py`:

```python
"""The seam: can a real captured refusal be reproduced and localized?

Skipped without an authenticated CLI rather than passing vacuously - a green suite
must never imply a round trip that did not happen.
"""

import json
import os
import subprocess

import pytest

TRANSCRIPT = os.path.expanduser(
    "~/.claude/projects/C--Users-patri-sources-repos-unrealengine-debugger/"
    "196daf17-75ca-4f0b-9190-cc282ff2a2bc.jsonl"
)


def _cli_authenticated() -> bool:
    try:
        result = subprocess.run(
            ["claude", "auth", "status"], capture_output=True, text=True, timeout=30
        )
    except Exception:
        return False
    start = (result.stdout or "").find("{")
    if start == -1:
        return False
    try:
        return bool(json.loads(result.stdout[start:]).get("loggedIn"))
    except json.JSONDecodeError:
        return False


pytestmark = [
    pytest.mark.skipif(not os.path.exists(TRANSCRIPT), reason="captured transcript not present"),
    pytest.mark.skipif(not _cli_authenticated(), reason="claude CLI not authenticated"),
]


def test_captured_refusal_reproduces_through_the_context_oracle():
    from refusal_detector.context import assemble_context
    from refusal_detector.context_oracle import ContextOracle

    records = [json.loads(line) for line in open(TRANSCRIPT, encoding="utf-8") if line.strip()]
    refusal_index = next(
        i for i, r in enumerate(records) if r.get("subtype") == "model_refusal_fallback"
    )
    segments = assemble_context(records, refusal_index)
    assert segments, "assembly produced nothing to test"

    oracle = ContextOracle(segments=segments, model="claude-fable-5", timeout=180)
    verdict = oracle.test("\n".join(s.text for s in segments))

    # Not asserting blocked=True: the base system prompt is not reconstructable, so a
    # non-reproduction is a real finding about scope, not a test failure to paper over.
    print(f"\nreproduction verdict: blocked={verdict.blocked} reason={verdict.reason_class.value}")
    assert verdict is not None
```

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/test_context_roundtrip.py -v -s`

Record whether the refusal reproduced. **Both outcomes are results, not failures:**
- Reproduced → the reconstructable universe contains the trigger; proceed to minimize and report the located segment plus its origin.
- Not reproduced → the trigger lies in the non-reconstructable remainder (base system prompt / tool definitions). That is a scope finding to document, and the report must say so rather than implying clean content.

- [ ] **Step 3: Document the outcome**

Append a dated section to `docs/review-report.md` recording: whether reproduction succeeded, the verdict, the segment count assembled, and — if it reproduced — which origin held the trigger. State the reproduction command so it can be re-run.

- [ ] **Step 4: Full suite**

Run: `python -m pytest -q`
Expected: all green; the round-trip test either passes or skips with a stated reason. Confirm the skip count and that no test silently vanished.

- [ ] **Step 5: Commit**

```bash
git add tests/test_context_roundtrip.py docs/review-report.md
git commit -m "test: real round trip against the captured refusal"
```

---

### Task 10: Version bump and release

**Files:**
- Modify: `pyproject.toml`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `src/refusal_detector/__init__.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: all prior tasks complete and green.
- Produces: released version.

- [ ] **Step 1: Document the auth prerequisite in the README**

In `README.md`, under the plugin section, add after the existing `pip install -e .` prerequisite paragraph:

```markdown
**Authentication:** the keyless oracle shells out to `claude -p`, which needs its own
credential — the desktop session does not share one with the CLI. Run `claude setup-token`
once, set the printed token as `CLAUDE_CODE_OAUTH_TOKEN`, and restart Claude Code so
hook-spawned probes inherit it. Without this every diagnosis fails loudly with setup
instructions (previously it silently reported "no trigger found").
```

- [ ] **Step 2: Bump to 0.3.4 in all four files**

Per the project's rule, fixes take a patch bump. Change `0.3.3` to `0.3.4` in each file listed above.

- [ ] **Step 3: Verify no stale version strings**

```bash
git grep -n '0\.3\.3' -- pyproject.toml '*.json' 'src/refusal_detector/__init__.py'
```
Expected: no output.

- [ ] **Step 4: Full suite**

Run: `python -m pytest -q`
Expected: all green, no regressions.

- [ ] **Step 5: Commit, tag, push**

```bash
git add -u
git commit -m "chore: bump version to 0.3.4"
git tag -a v0.3.4 -m "Version 0.3.4 - context-aware diagnosis"
git push origin main && git push origin v0.3.4
```
