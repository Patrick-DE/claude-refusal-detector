# Claude Refusal Detector — Vision

**Status:** Active — 2026-08-10

## The vision

Getting blocked by an AI assistant should not be a dead end — it should be a diagnosis.

Every day, people hit a wall when Claude refuses a prompt: a code comment, a documentation page,
a security writeup, a scene from a novel. They get a generic "I can't help with that" and zero
explanation of *what* triggered it. We want to change that.

**We want a world where a refusal comes with an answer:** *these exact words, at these exact
positions, are why this was blocked — and here is what it looks like without them.*

**The moment we're designing for.** You're working in Claude Desktop. You paste a prompt — a
code comment, a page of documentation, a scene from a novel — and Claude starts answering. Then
it stops: *"Sorry, I can't help with that."*

That's where our tool takes over. The plugin sees the refusal, checks what was blocked, and
tests the prompt — narrowing it down word by word, sentence by sentence — until it finds the
trigger. Then it comes back with a recommendation: *"This sentence is what tripped the
guardrail. Here's your prompt without it."*

No guessing, no dead end — just a diagnosis you can act on.

## What we want

- **Clarity.** Anyone whose legitimate prompt gets refused can see precisely which part of their
  input caused the block, and what kind of guardrail fired.
- **Resolution.** False positives become a solvable problem instead of a frustrating guessing
  game — fix it in seconds, or file a bug with a precise repro.
- **Trust.** The tool proves every claim it makes; nothing is "should work."
- **Accessibility.** It is fast, costs pennies to run, and explains itself in plain output — a
  trigger, a diff, a reason.

## Who it's for

- Developers and writers hitting refusals on legitimate content — docs, code comments, security
  writeups, fiction, medical and legal text.
- Model teams red-teaming guardrails to find and fix false positives.

## What it is

A diagnostic companion that lives in **Claude Desktop**: when a refusal happens, it picks up the
blocked prompt, narrows it down to the minimal set of words or phrases responsible, and
recommends what to remove. It also runs standalone from a terminal. Diagnosis only — it explains
what triggered a block, and that is all.

## What success looks like

- A refusal in Claude Desktop triggers the detector automatically; seconds later it recommends
  the exact word or sentence to remove.
- A blocked prompt goes in; a precise trigger with exact positions comes out.
- Removing the trigger un-blocks the request — verified, not assumed.
- No duplicate work: identical prompts are never tested twice, and there is a hard budget.
- Anyone can run it from a terminal in under a minute.
