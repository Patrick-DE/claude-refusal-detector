# Saturation Reporting, CLAUDE.md Discovery and Oracle Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the tool tell the truth about saturated content, find the `CLAUDE.md` files that caused a refusal without being handed them, and stop the keyless oracle from executing what it reads.

**Architecture:** Seven independent fixes, each closing a defect the first real end-to-end run exposed. Nothing here changes the ddmin algorithm or the `Oracle` port; the changes are at the edges — what gets assembled, how subsets are identified, how results are reported, and what the CLI probe is permitted to do.

**Tech Stack:** Python 3.10+, pytest, stdlib only. No new third-party dependencies.

## Evidence this plan is built on

Every item below came from a real run against a captured refusal on 2026-08-13, using an authenticated Messages API. Not inferred:

1. **Only `claude-fable-5` blocks the content.** Probed all four: `claude-haiku-4-5-20251001` -> not blocked, `claude-sonnet-5` -> not blocked, `claude-opus-4-8` -> not blocked, `claude-fable-5` -> blocked with `stop_reason='refusal'` in 2.3s. Guardrails are not monotonic by model capability, so a cheaper model is not a valid proxy oracle.
2. **The trigger was not the prompt.** Prompt + prior turn (654 chars) -> not blocked. Adding the project `CLAUDE.md` (13,143 chars total) -> blocked. The user must supply `claude_md_files` by hand today; nothing discovers them.
3. **The content is saturated.** ddmin found trigger groups at `CLAUDE.md` lines `[94, 96]` and `[59]`, each of which blocks on its own. Removing all three still leaves the remainder blocked (84 of 87 segments). Slice test: whole doc blocked, first half blocked, second half blocked, first quarter blocked, last quarter NOT blocked. Roughly three quarters of the document independently trips the classifier.
4. **The iterative loop does not terminate.** After two real groups, rounds 3 and 4 each returned a 0-segment "trigger", so `remaining` never shrank.
5. **An empty prompt raises HTTP 400** from the Messages API — so a degenerate empty candidate is an error, not a verdict.
6. **`claude -p` executes what it reads.** Replaying an agentic transcript through it produced 23 real tool calls (`git ls-files`, `wc -l`, 20 `Read`s) and a 180s timeout instead of a verdict. With `--disallowed-tools` and `--max-turns 1` it stopped acting but returned `Error: Reached max turns (1)`, exit 1 — safe, still not a classifier.
7. **`is_necessary` already exists** and was `False` for this run, but nothing warns the user. `service.py:61-65` computes it; `adapters.py:118` prints `Verified Necessary: No` as one line among many.

## Global Constraints

- No new third-party dependencies. Stdlib only.
- Keep files under 500 LOC. `adapters.py` is already large — new adapters go in new modules.
- Preserve the `Oracle` port exactly: `test(prompt: str) -> Verdict`.
- `ContextSegment` is `@dataclass(frozen=True)`; construct fully, never mutate.
- **No non-ASCII anywhere in source.** Verify with a byte check, not by eye — a rendered character and its escape are visually identical, which already cost three fix rounds on an earlier task.
- A failed oracle call must raise, never degrade to "not blocked".
- Fixes take patch bumps (`x.y.Z+1`), never minor. Current version is `0.3.3`.
- Baseline suite: **96 passed, 1 skipped** with a live `ANTHROPIC_API_KEY`; 95 passed, 2 skipped without one. Run `python -m pytest -q --ignore=tests/test_context_roundtrip.py` until Task 7 gates that file.
- Run tests with `python -m pytest`. A `python3` shim exists on this machine but shipped code must still say `python`.

---

### Task 1: Stop the minimizer returning an empty trigger

**Files:**
- Modify: `src/refusal_detector/minimizer.py` (`Minimizer.minimize`)
- Test: `tests/test_minimizer_empty_guard.py` (create)

**Interfaces:**
- Consumes: `Minimizer(oracle).minimize(segments) -> tuple[list[Segment], Verdict]` (existing signature, unchanged).
- Produces: `minimize` never returns an empty trigger list when the input was blocked. Task 2's saturation loop relies on this to terminate.

- [ ] **Step 1: Write the failing test**

Create `tests/test_minimizer_empty_guard.py`:

```python
"""A blocked input must never minimize to zero segments.

Observed in a real run: after two genuine trigger groups were found, further rounds
each returned a 0-segment "trigger", so the caller's remaining-set never shrank and the
loop spun. An empty candidate is also not probeable - the Messages API rejects an empty
prompt with HTTP 400.
"""

from refusal_detector.minimizer import Minimizer
from refusal_detector.ports import ReasonClass, Segment, Verdict


class _AlwaysBlockedOracle:
    """Worst case for ddmin: every candidate looks blocked, including tiny ones."""

    def __init__(self) -> None:
        self.calls = 0

    def test(self, prompt: str) -> Verdict:
        self.calls += 1
        return Verdict(blocked=True, reason_class=ReasonClass.STRUCTURED_REFUSAL)


def _segments(count: int) -> list[Segment]:
    return [
        Segment(index=i, text=f"line {i}", start_char=0, end_char=6, start_line=i + 1, end_line=i + 1)
        for i in range(count)
    ]


def test_blocked_input_never_minimizes_to_nothing():
    trigger, verdict = Minimizer(_AlwaysBlockedOracle()).minimize(_segments(8))

    assert trigger, "a blocked input must yield at least one segment, never an empty trigger"
    assert verdict.blocked is True


def test_empty_input_is_returned_untouched_without_probing():
    oracle = _AlwaysBlockedOracle()

    trigger, _ = Minimizer(oracle).minimize([])

    assert trigger == []
    assert oracle.calls == 0, "an empty candidate must never be sent to the oracle (HTTP 400)"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_minimizer_empty_guard.py -v`
Expected: `test_blocked_input_never_minimizes_to_nothing` FAILS — ddmin currently reduces to an empty list when every candidate reports blocked.

- [ ] **Step 3: Implement**

In `src/refusal_detector/minimizer.py`, at the very start of `Minimizer.minimize`, add the empty short-circuit:

```python
        if not segments:
            logger.info("Nothing to minimize: empty segment list.")
            return [], Verdict(blocked=False, reason_class=ReasonClass.NOT_BLOCKED)
```

Then at the end of `minimize`, immediately before its `return`, add the non-empty guard. Replace the existing final return with:

```python
        if not result_segments:
            # A blocked input that minimizes to nothing means every subset still looked
            # blocked - saturated content, not an empty cause. Returning [] would make a
            # caller's remaining-set never shrink, and an empty candidate cannot even be
            # probed (the Messages API rejects it with HTTP 400).
            logger.warning(
                "Minimization reduced to zero segments; content appears saturated. "
                "Returning the smallest non-empty candidate instead."
            )
            result_segments = segments[:1]
        return result_segments, final_verdict
```

Use whatever local names the existing code has for the reduced list and the verdict — do not rename them. If `ReasonClass` is not already imported in this module, add it to the existing `from refusal_detector.ports import ...` line.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_minimizer_empty_guard.py -v`
Expected: 2 passed.

- [ ] **Step 5: Falsify**

Remove the `if not result_segments:` guard. Re-run: `test_blocked_input_never_minimizes_to_nothing` must go RED. Restore, confirm green. Paste the real pytest output.

- [ ] **Step 6: Full suite**

Run: `python -m pytest -q --ignore=tests/test_context_roundtrip.py`
Expected: 98 passed, 1 skipped (96 + 2 new), or 97 passed/2 skipped without an API key.

- [ ] **Step 7: Commit**

```bash
git add src/refusal_detector/minimizer.py tests/test_minimizer_empty_guard.py
git commit -m "fix: never minimize a blocked input to an empty trigger"
```

---

### Task 2: Report saturation instead of implying a fix

**Files:**
- Modify: `src/refusal_detector/adapters.py` (`MarkdownReporter.render`, near the `Verified Necessary` line)
- Test: `tests/test_saturation_reporting.py` (create)

**Interfaces:**
- Consumes: `DetectionReport.is_necessary: bool` (existing, set in `service.py:61-65`), `DetectionReport.trigger_segments`, `DetectionReport.original_prompt`.
- Produces: the rendered Markdown contains an explicit warning block when `is_necessary` is `False`. Nothing else consumes this.

- [ ] **Step 1: Write the failing test**

Create `tests/test_saturation_reporting.py`:

```python
"""When removing the trigger does not unblock, the report must say so loudly.

Real case: ddmin isolated CLAUDE.md lines that each block on their own, but deleting all
of them left the content blocked - roughly three quarters of the document independently
tripped the classifier. A report that shows a trigger without flagging this invites the
user to make an edit that fixes nothing.
"""

from refusal_detector.adapters import MarkdownReporter
from refusal_detector.ports import DetectionReport, ReasonClass, Segment


def _report(is_necessary: bool) -> DetectionReport:
    segment = Segment(
        index=0, text="detour hooks live in hook-dll", start_char=0, end_char=29,
        start_line=59, end_line=59,
    )
    return DetectionReport(
        original_prompt="line one\ndetour hooks live in hook-dll\nline three",
        segments=[segment],
        trigger_segments=[segment],
        trigger_text="detour hooks live in hook-dll",
        diff_text="- detour hooks live in hook-dll",
        reason_class=ReasonClass.STRUCTURED_REFUSAL,
        total_calls=23,
        cache_hits=4,
        is_necessary=is_necessary,
    )


def test_non_necessary_trigger_is_flagged_as_saturated():
    rendered = MarkdownReporter().render(_report(is_necessary=False))

    assert "Saturated" in rendered, "a sufficient-but-not-necessary trigger must be called out"
    assert "removing it will not unblock" in rendered.lower()


def test_necessary_trigger_carries_no_saturation_warning():
    rendered = MarkdownReporter().render(_report(is_necessary=True))

    assert "Saturated" not in rendered
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_saturation_reporting.py -v`
Expected: `test_non_necessary_trigger_is_flagged_as_saturated` FAILS — the report currently prints only `Verified Necessary: No`.

- [ ] **Step 3: Implement**

In `src/refusal_detector/adapters.py`, find this line in `MarkdownReporter.render`:

```python
        lines.append(f"- **Verified Necessary:** `{'Yes' if report.is_necessary else 'No'}`")
```

Insert immediately after it:

```python
        if not report.is_necessary:
            lines.append("")
            lines.append("> **Saturated content - removing it will not unblock.**")
            lines.append(">")
            lines.append(
                "> The trigger below is enough to cause a refusal on its own, but deleting it "
                "leaves the content still blocked: other parts trigger it independently. "
                "Treat this as one example of what the classifier objects to, not as an edit "
                "that fixes the problem. A different model, or a rewrite of the whole passage, "
                "is more likely to help than a targeted deletion."
            )
            lines.append("")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_saturation_reporting.py -v`
Expected: 2 passed.

- [ ] **Step 5: Falsify**

Change the condition to `if False:`. Re-run: `test_non_necessary_trigger_is_flagged_as_saturated` must go RED. Restore, confirm green. Paste the real output.

- [ ] **Step 6: Commit**

```bash
git add src/refusal_detector/adapters.py tests/test_saturation_reporting.py
git commit -m "feat: warn when a trigger is sufficient but not necessary"
```

---

### Task 3: Discover the CLAUDE.md files that applied

**Files:**
- Create: `src/refusal_detector/claude_md.py`
- Test: `tests/test_claude_md_discovery.py` (create)

**Interfaces:**
- Consumes: `SegmentOrigin.PROJECT_CLAUDE_MD` and `SegmentOrigin.GLOBAL_CLAUDE_MD` from `refusal_detector.context`.
- Produces: `discover_claude_md(cwd: str, home: str | None = None) -> list[tuple[SegmentOrigin, str, str]]` returning `(origin, label, content)` tuples in the order `assemble_context` expects — global first, then project. Task 4 passes the result straight into `assemble_context(..., claude_md_files=...)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_claude_md_discovery.py`:

```python
"""Find the CLAUDE.md files that were loaded into the refused request.

This is what stood between the user and their answer: the trigger lived in a project
CLAUDE.md, but nothing discovered it - it had to be passed in by hand.
"""

from refusal_detector.claude_md import discover_claude_md
from refusal_detector.context import SegmentOrigin


def test_finds_project_claude_md_in_the_session_cwd(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("project rules here", encoding="utf-8")

    found = discover_claude_md(str(tmp_path), home=str(tmp_path / "nonexistent-home"))

    assert len(found) == 1
    origin, label, content = found[0]
    assert origin is SegmentOrigin.PROJECT_CLAUDE_MD
    assert "CLAUDE.md" in label
    assert content == "project rules here"


def test_finds_global_claude_md_and_orders_it_first(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text("global rules", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("project rules", encoding="utf-8")

    found = discover_claude_md(str(project), home=str(home))

    assert [o for o, _, _ in found] == [
        SegmentOrigin.GLOBAL_CLAUDE_MD,
        SegmentOrigin.PROJECT_CLAUDE_MD,
    ], "global applies before project, matching load order"


def test_missing_files_are_simply_absent(tmp_path):
    assert discover_claude_md(str(tmp_path), home=str(tmp_path / "no-home")) == []


def test_unreadable_file_is_skipped_rather_than_raising(tmp_path):
    # A directory named CLAUDE.md cannot be read as text; discovery must not explode.
    (tmp_path / "CLAUDE.md").mkdir()

    assert discover_claude_md(str(tmp_path), home=str(tmp_path / "no-home")) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_claude_md_discovery.py -v`
Expected: FAIL — `refusal_detector.claude_md` does not exist.

- [ ] **Step 3: Implement**

Create `src/refusal_detector/claude_md.py`:

```python
"""Locate the CLAUDE.md files that were part of a refused request.

A refusal can be caused by instructions the user never typed. In the case that motivated
this module, the prompt alone was clean and a project CLAUDE.md carried the trigger, so a
diagnosis that only ever sees the prompt reports "no trigger found" on a genuinely blocked
request.
"""

import os

from refusal_detector.context import SegmentOrigin
from refusal_detector.logger import get_logger

logger = get_logger("claude_md")


def _read(path: str) -> str | None:
    """Return file contents, or None if it cannot be read as text."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Could not read %s: %s", path, e)
        return None


def discover_claude_md(cwd: str, home: str | None = None) -> list[tuple[SegmentOrigin, str, str]]:
    """Return (origin, label, content) for each CLAUDE.md that applied to a session.

    Ordered global-then-project, matching the order they reach the model.
    """
    home_dir = home if home is not None else os.path.expanduser("~")
    discovered: list[tuple[SegmentOrigin, str, str]] = []

    global_path = os.path.join(home_dir, ".claude", "CLAUDE.md")
    global_content = _read(global_path) if os.path.isfile(global_path) else None
    if global_content:
        discovered.append((SegmentOrigin.GLOBAL_CLAUDE_MD, "global CLAUDE.md", global_content))

    project_path = os.path.join(cwd, "CLAUDE.md")
    project_content = _read(project_path) if os.path.isfile(project_path) else None
    if project_content:
        discovered.append((SegmentOrigin.PROJECT_CLAUDE_MD, "project CLAUDE.md", project_content))

    logger.info("Discovered %d CLAUDE.md file(s) for cwd=%s", len(discovered), cwd)
    return discovered
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_claude_md_discovery.py -v`
Expected: 4 passed.

- [ ] **Step 5: Falsify**

Swap the append order so project comes before global. Re-run: `test_finds_global_claude_md_and_orders_it_first` must go RED. Restore, confirm green. Paste the real output.

- [ ] **Step 6: Verify against the real files that produced the refusal**

```bash
python -c "
import sys; sys.path.insert(0,'src')
from refusal_detector.claude_md import discover_claude_md
found = discover_claude_md('C:/Users/patri/sources/repos/unrealengine-debugger')
for origin, label, content in found:
    print('%-22s %-20s %d chars' % (origin.value, label, len(content)))
"
```

Expected: both a global and a project CLAUDE.md, the project one around 12,710 characters — the file that actually carried the trigger. Report the real numbers.

- [ ] **Step 7: Commit**

```bash
git add src/refusal_detector/claude_md.py tests/test_claude_md_discovery.py
git commit -m "feat: discover the CLAUDE.md files that applied to a session"
```

---

### Task 4: Identify subsets by index, not by text

**Files:**
- Modify: `src/refusal_detector/context_oracle.py` (`ContextOracle._subset_for`, `ContextOracle.test`)
- Test: `tests/test_context_oracle_identity.py` (create)

**Interfaces:**
- Consumes: `ContextSegment` (frozen, has `index`), `ContextOracle.build_channels(subset)` (unchanged).
- Produces: `ContextOracle.test(prompt)` resolves the subset by segment identity. `build_channels` keeps its existing signature so its tests are untouched.

- [ ] **Step 1: Write the failing test**

Create `tests/test_context_oracle_identity.py`:

```python
"""Subsets must be identified by segment identity, not by text matching.

`s.text in prompt` over-selects whenever two segments share text or one is a substring of
another. Source files make that routine - `}`, blank-ish lines, repeated imports - and
this tool now minimizes file contents, so text-keyed membership corrupts the candidate
ddmin actually asked about.
"""

from unittest.mock import MagicMock

from refusal_detector.context import ContextSegment, SegmentOrigin
from refusal_detector.context_oracle import ContextOracle
from refusal_detector.ports import ReasonClass, Verdict


def _segment(index: int, text: str, origin: SegmentOrigin = SegmentOrigin.PROMPT) -> ContextSegment:
    return ContextSegment(
        index=index, text=text, start_char=0, end_char=len(text),
        start_line=index + 1, end_line=index + 1, origin=origin, source_label=origin.value,
    )


def _oracle_with(segments):
    adapter = MagicMock()
    adapter.test_with_system.return_value = Verdict(blocked=False, reason_class=ReasonClass.NOT_BLOCKED)
    return ContextOracle(segments=segments, model="claude-fable-5", adapter=adapter), adapter


def test_duplicate_lines_do_not_over_select():
    """Two identical lines: a candidate containing one must not pull in both."""
    segments = [_segment(0, "}"), _segment(1, "unique middle"), _segment(2, "}")]
    oracle, adapter = _oracle_with(segments)

    oracle.test_subset([segments[0], segments[1]])

    sent = adapter.test_with_system.call_args.kwargs["prompt"]
    assert sent.count("}") == 1, f"duplicate line was pulled in twice: {sent!r}"


def test_substring_segment_does_not_drag_in_its_superstring():
    segments = [_segment(0, "import os"), _segment(1, "import os, sys")]
    oracle, adapter = _oracle_with(segments)

    oracle.test_subset([segments[0]])

    sent = adapter.test_with_system.call_args.kwargs["prompt"]
    assert sent == "import os"


def test_pre_prompt_routing_still_holds_with_identity_selection():
    segments = [
        _segment(0, "a claude md rule", SegmentOrigin.PROJECT_CLAUDE_MD),
        _segment(1, "the user prompt"),
    ]
    oracle, adapter = _oracle_with(segments)

    oracle.test_subset(segments)

    kwargs = adapter.test_with_system.call_args.kwargs
    assert "a claude md rule" in kwargs["system_prompt"]
    assert "a claude md rule" not in kwargs["prompt"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_context_oracle_identity.py -v`
Expected: FAIL — `ContextOracle` has no `test_subset` method.

- [ ] **Step 3: Implement**

In `src/refusal_detector/context_oracle.py`, add a `test_subset` method that takes segments directly, and make `test()` delegate to it. Replace the existing `_subset_for` and `test` with:

```python
    def _subset_for(self, prompt: str) -> list[ContextSegment]:
        """Recover which segments a joined prompt represents, by identity where possible.

        The Oracle port hands us a joined string. Matching on text alone over-selects when
        segments repeat or nest, which is routine in source files, so each segment is
        consumed positionally: a segment counts as present only if its text appears at or
        after the point the previous one ended.
        """
        subset: list[ContextSegment] = []
        cursor = 0
        for segment in self._segments:
            if not segment.text:
                continue
            found = prompt.find(segment.text, cursor)
            if found != -1:
                subset.append(segment)
                cursor = found + len(segment.text)
        return subset

    def test_subset(self, subset: list[ContextSegment]) -> Verdict:
        """Probe an explicit set of segments - the precise path, with no text matching."""
        system_prompt, conversation = self.build_channels(subset)
        logger.info(
            "Probing %d segments (%d pre-prompt, %d conversation).",
            len(subset),
            sum(1 for s in subset if is_pre_prompt(s.origin)),
            sum(1 for s in subset if not is_pre_prompt(s.origin)),
        )
        return self._resolve_adapter().test_with_system(
            prompt=conversation, system_prompt=system_prompt
        )

    def test(self, prompt: str) -> Verdict:
        """Oracle port: probe one candidate subset recovered from a joined prompt."""
        return self.test_subset(self._subset_for(prompt))
```

Keep the existing `_resolve_adapter` exactly as it is.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_context_oracle_identity.py -v`
Expected: 3 passed.

- [ ] **Step 5: Falsify**

In `_subset_for`, change `prompt.find(segment.text, cursor)` to `prompt.find(segment.text)` (dropping the cursor). Re-run `python -m pytest tests/test_context_oracle_identity.py tests/test_context_oracle.py -v`: `test_duplicate_lines_do_not_over_select` must go RED. Restore, confirm green. Paste the real output.

- [ ] **Step 6: Full suite**

Run: `python -m pytest -q --ignore=tests/test_context_roundtrip.py`
Expected: all previously-passing tests still pass, plus 3 new.

- [ ] **Step 7: Commit**

```bash
git add src/refusal_detector/context_oracle.py tests/test_context_oracle_identity.py
git commit -m "fix: select candidate subsets by identity, not text matching"
```

---

### Task 5: Stop the keyless probe executing what it reads

**Files:**
- Modify: `src/refusal_detector/system_prompt_adapter.py` (the `cmd` list in `test_with_system`)
- Modify: `tests/test_system_prompt_adapter.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: every `claude -p` probe runs with tools disabled and a single turn. No signature change.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_system_prompt_adapter.py`:

```python


def test_probe_cannot_execute_tools():
    """A probe replays untrusted content; it must never be able to act on it.

    Measured: replaying an agentic transcript through an unrestricted `claude -p` produced
    23 real tool calls (git ls-files, wc -l, 20 Reads) and a 180s timeout instead of a
    verdict. The content under test is, by definition, material a safety system objected to.
    """
    with _run_with() as run:
        _adapter().test_with_system("hello")

    cmd = run.call_args.args[0]
    assert "--disallowed-tools" in cmd, "probes must not be able to run tools"
    disallowed = cmd[cmd.index("--disallowed-tools") + 1]
    for tool in ("Bash", "Write", "Edit", "Read"):
        assert tool in disallowed, f"{tool} must be disallowed during a probe"


def test_probe_is_limited_to_a_single_turn():
    with _run_with() as run:
        _adapter().test_with_system("hello")

    cmd = run.call_args.args[0]
    assert "--max-turns" in cmd
    assert cmd[cmd.index("--max-turns") + 1] == "1"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_system_prompt_adapter.py -v`
Expected: both new tests FAIL — neither flag is currently passed.

- [ ] **Step 3: Implement**

In `src/refusal_detector/system_prompt_adapter.py`, find:

```python
            cmd = ["claude", "-p", "-", "--exclude-dynamic-system-prompt-sections"]
```

Replace with:

```python
            # A probe replays content a safety system already objected to. Left
            # unrestricted, `claude -p` treats that content as instructions and carries
            # them out - measured at 23 real tool calls before timing out. Disabling tools
            # and capping turns makes the probe inert.
            cmd = [
                "claude",
                "-p",
                "-",
                "--exclude-dynamic-system-prompt-sections",
                "--disallowed-tools",
                "Bash,Read,Write,Edit,Glob,Grep,Task,WebFetch,WebSearch,NotebookEdit",
                "--max-turns",
                "1",
            ]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_system_prompt_adapter.py -v`
Expected: 8 passed (6 existing + 2 new).

- [ ] **Step 5: Falsify**

Remove `"--max-turns", "1",` from the list. Re-run: `test_probe_is_limited_to_a_single_turn` must go RED. Restore, confirm green. Paste the real output.

- [ ] **Step 6: Commit**

```bash
git add src/refusal_detector/system_prompt_adapter.py tests/test_system_prompt_adapter.py
git commit -m "fix: make the keyless probe inert - no tools, one turn"
```

---

### Task 6: Default the hook to the Messages API

**Files:**
- Modify: `src/refusal_detector/hooks/refusal_hook.py` (`process_hook_payload`)
- Modify: `tests/test_plugin_hook.py` (append)

**Interfaces:**
- Consumes: `Config.from_env(**overrides)` (existing).
- Produces: the hook builds its `Config` with `provider="anthropic"` when `ANTHROPIC_API_KEY` is present. No signature change.

**Explicitly NOT in this task — the remaining wiring gap.** The hook still calls
`detector.detect(prompt)`, a prompt-only path. It does not call `assemble_context`,
`ContextOracle`, or Task 3's `discover_claude_md`, so the CLAUDE.md that carried the real
trigger is still not part of an automatic diagnosis. Closing that means moving the hook off
`RefusalDetector.detect` and onto segment assembly, which changes what the hook reports and
needs its own plan. Task 3 delivers discovery as a tested, callable unit so that work is
unblocked; this task deliberately does not attempt it. Do not wire it in here — a half-done
version would be worse than the honest gap.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_plugin_hook.py`:

```python


def test_hook_prefers_the_api_oracle_when_a_key_is_available(tmp_path, monkeypatch):
    """The CLI oracle acts on what it reads; the API oracle only responds to it.

    Measured: the same agentic transcript produced 23 tool calls and a timeout through
    `claude -p`, versus a structured refusal verdict in 2.3s through the Messages API.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    transcript_path = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "Audit this parser."}},
            _refusal_fallback_record("cyber"),
        ],
    )

    with patch("refusal_detector.hooks.refusal_hook.RefusalDetector") as mock_detector_cls:
        mock_detector_cls.return_value.render_report.return_value = "# Report"

        process_hook_payload({"transcript_path": transcript_path, "cwd": str(tmp_path)})

        config = mock_detector_cls.call_args.kwargs["config"]
        assert config.provider == "anthropic", "an available API key must win over the CLI oracle"


def test_hook_falls_back_to_the_cli_oracle_without_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    transcript_path = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "Audit this parser."}},
            _refusal_fallback_record("cyber"),
        ],
    )

    with patch("refusal_detector.hooks.refusal_hook.RefusalDetector") as mock_detector_cls:
        mock_detector_cls.return_value.render_report.return_value = "# Report"

        process_hook_payload({"transcript_path": transcript_path, "cwd": str(tmp_path)})

        config = mock_detector_cls.call_args.kwargs["config"]
        assert config.provider == "claude_cli"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_plugin_hook.py -k "oracle" -v`
Expected: `test_hook_prefers_the_api_oracle_when_a_key_is_available` FAILS — the hook currently never sets `provider`, so it inherits the `claude_cli` default.

- [ ] **Step 3: Implement**

In `src/refusal_detector/hooks/refusal_hook.py`, find:

```python
    config = Config.from_env(max_calls=_HOOK_MAX_CALLS, cli_model=refusing_model)
```

Replace with:

```python
    # Prefer the Messages API when a key exists: it responds to the text it is given and
    # never acts on it, so a transcript containing an agent's own tool plans is diagnosed
    # rather than replayed. The keyless CLI oracle stays as the fallback.
    provider = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "claude_cli"
    config = Config.from_env(
        provider=provider,
        max_calls=_HOOK_MAX_CALLS,
        cli_model=refusing_model,
        model=refusing_model if provider == "anthropic" and refusing_model else None,
    )
```

`Config.from_env` ignores overrides whose value is `None`, so passing `model=None` leaves the configured default in place.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_plugin_hook.py -k "oracle" -v`
Expected: 2 passed.

- [ ] **Step 5: Falsify**

Change the provider line to `provider = "claude_cli"`. Re-run: `test_hook_prefers_the_api_oracle_when_a_key_is_available` must go RED. Restore, confirm green. Paste the real output.

- [ ] **Step 6: Full suite**

Run: `python -m pytest -q --ignore=tests/test_context_roundtrip.py`
Expected: all green, 2 more than before.

- [ ] **Step 7: Commit**

```bash
git add src/refusal_detector/hooks/refusal_hook.py tests/test_plugin_hook.py
git commit -m "feat: prefer the API oracle in the hook when a key is available"
```

---

### Task 7: Gate the slow round-trip test and release

**Files:**
- Modify: `tests/test_context_roundtrip.py`
- Modify: `README.md`
- Modify: `pyproject.toml`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `src/refusal_detector/__init__.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: released version. Terminal task.

- [ ] **Step 1: Gate the round-trip test behind an opt-in**

The CLI round-trip test makes a live 180s probe, so a plain `pytest` run ends in a three-minute failing tail. Add this to the existing `pytestmark` list in `tests/test_context_roundtrip.py`:

```python
    pytest.mark.skipif(
        not os.environ.get("REFUSAL_DETECTOR_LIVE_ROUNDTRIP"),
        reason="live round trip is opt-in: set REFUSAL_DETECTOR_LIVE_ROUNDTRIP=1",
    ),
```

- [ ] **Step 2: Confirm the suite is fast and green again**

Run: `python -m pytest -q`
Expected: completes in well under a minute, no failures, and the round-trip test reports as skipped with the stated reason. Report the exact counts — a test that vanished rather than skipped would be a silent loss.

- [ ] **Step 3: Document what the tool can and cannot do**

Append to `README.md`, immediately before the `## Docs` heading:

```markdown
### What a diagnosis can and cannot tell you

The tool isolates content that is **sufficient** to cause a refusal. It also checks whether
that content is **necessary** - whether removing it unblocks the rest.

When it is not necessary, the report says so. That case is real and common: in one measured
run, three separate lines of a project `CLAUDE.md` each triggered a refusal on their own, and
deleting all three left the content still blocked, because roughly three quarters of the
document tripped the classifier independently. There, no small edit fixes anything; a
different model or a rewritten passage does.

Guardrails also differ per model, and not in step with capability. In that same run only
`claude-fable-5` refused; `claude-haiku-4-5`, `claude-sonnet-5` and `claude-opus-4-8` all
accepted the identical content. So a cheaper model is not a valid stand-in for the model that
refused you - always probe with the model named in the refusal.
```

- [ ] **Step 4: Bump to 0.3.4 in all four files**

Change `0.3.3` to `0.3.4` in `pyproject.toml`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, and `src/refusal_detector/__init__.py`.

- [ ] **Step 5: Verify no stale version strings**

```bash
git grep -n '0\.3\.3' -- pyproject.toml '*.json' 'src/refusal_detector/__init__.py'
```
Expected: no output.

- [ ] **Step 6: Commit, tag, push**

```bash
git add -u
git commit -m "chore: bump version to 0.3.4"
git tag -a v0.3.4 -m "Version 0.3.4 - saturation reporting, CLAUDE.md discovery, inert probes"
git push origin main && git push origin v0.3.4
```
