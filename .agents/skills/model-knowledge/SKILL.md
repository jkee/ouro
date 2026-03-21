---
name: "Model Knowledge"
description: "Guide for choosing the right LLM — capabilities, cost tiers, and when to switch"
version: "1.0.0"
tags: ["models", "llm", "budget", "switch-model"]
auto_activate: true
last_updated: "2026-03-18"
---

# Model Knowledge

Decision guide for `switch_model`. Your three model slots (Main/Code/Light) are configured via env vars. Use `switch_model()` with no args to see what's currently available.

## Quick Decision Matrix

| Task Type | Use | Why |
|-----------|-----|-----|
| Simple Q&A, status checks, formatting | **Light** | Fast, cheapest |
| Summarization, log parsing | **Light** | Doesn't need deep reasoning |
| General conversation, most tasks | **Main** | Good balance of quality and cost |
| Bug diagnosis (moderate) | **Main** | Usually sufficient |
| Complex multi-file code changes | **Code** | Best reasoning and code quality |
| Architecture decisions, refactoring | **Code** | Needs deep context tracking |
| Evolution / self-modification cycles | **Code** | Self-modification needs highest capability |
| Bug diagnosis (hard, cross-module) | **Code** | Complex root cause analysis |

**Rule of thumb:** Start on Main. Switch to Light for simple stuff, Code for hard stuff. Switch back after the hard part is done.

## Model Families (via OpenRouter)

### Anthropic Claude (current defaults)

**`anthropic/claude-opus-4-6`** — Top-tier (typical Code slot)
- Best-in-class coding, deep reasoning, and agentic tool use
- 200K context, vision support
- Cost: **expensive** (~$5/$25 per M input/output tokens)
- Use for: complex code, architecture, self-modification, multi-step reasoning

**`anthropic/claude-sonnet-4.6`** — Strong all-rounder (typical Main slot)
- Excellent coding and reasoning at ~5x cheaper than Opus
- 200K context, vision support
- Cost: **mid** (~$3/$15)
- Use for: most tasks, general conversation, moderate code changes

**`anthropic/claude-sonnet-4`** / **`anthropic/claude-sonnet-4.5`** — Previous Sonnet generations
- Similar capabilities and pricing to Sonnet 4.6
- Sonnet 4.5 was the first with extended thinking support

### OpenAI

**`openai/gpt-5.2`** — Latest GPT generation
- Strong general-purpose, large context, competitive pricing
- Cost: **mid** (~$1.75/$14)
- Good at: instruction following, creative tasks, broad knowledge

**`openai/gpt-5.2-codex`** — Code-specialized GPT-5.2
- Optimized for code generation and editing
- Same pricing as gpt-5.2

**`openai/o3`** — Reasoning model
- Extended thinking / chain-of-thought by design
- Cost: **mid** (~$2/$8)
- Good at: math, logic, complex multi-step reasoning

**`openai/o3-pro`** — Heavy reasoning
- Maximum reasoning depth, very slow
- Cost: **very expensive** (~$20/$80)
- Only for extremely hard reasoning tasks

**`openai/o4-mini`** — Budget reasoning
- Lighter reasoning model, good speed
- Cost: **cheap** (~$1.10/$4.40)
- Good at: reasoning tasks where o3 is overkill

**`openai/gpt-4.1`** — Previous generation
- Solid general-purpose model
- Cost: **mid** (~$2/$8)

### Google Gemini

**`google/gemini-3-pro-preview`** — Current gen (typical Light slot)
- Very large context window (1M+ tokens — largest available)
- Cost: **mid** (~$2/$12)
- Good at: processing large codebases, long documents, summarization

**`google/gemini-2.5-pro-preview`** — Previous gen
- Also huge context (1M+), slightly cheaper
- Cost: **mid-cheap** (~$1.25/$10)
- Strong reasoning with extended thinking support

### Budget Options

**`x-ai/grok-3-mini`** — Very cheap
- Cost: **very cheap** (~$0.30/$0.50)
- Good for: simple tasks where quality bar is low

**`qwen/qwen3.5-plus-02-15`** — Budget multilingual
- Cost: **cheap** (~$0.40/$2.40)
- Good multilingual support, decent for simple tasks

## Reasoning Effort Levels

The `effort` parameter on `switch_model` controls extended thinking depth:

| Level | When to use |
|-------|-------------|
| `low` | Simple lookups, formatting, status checks |
| `medium` | Default. Most tasks. |
| `high` | Complex multi-step problems, deep debugging |
| `xhigh` | Maximum depth. Rarely needed, significantly more expensive. |

Not all models support effort levels — models without extended thinking ignore this parameter.

## Cost-Aware Switching Rules

1. **Default to Main** — start every task on Main model
2. **Drop to Light** for: summarization, formatting, simple file reads, status messages, log parsing
3. **Escalate to Code** for: multi-file edits, architecture, debugging complex issues, evolution cycles
4. **Switch back** after completing the hard part — don't stay on Code for the wrap-up
5. **Check budget** in runtime context before switching to expensive models
6. **Batch cheap work** — if you have several simple tasks, stay on Light for all of them

## Staleness Warning

This skill was last verified on **2026-03-18**. The model landscape changes fast.

If you suspect information is outdated (model returns errors, unexpected behavior, new models available):
1. Use `web_search` to check OpenRouter's current model offerings
2. Update this skill via `claude_code_edit` with corrected information
3. Update the `last_updated` field in frontmatter

Note: exact pricing is fetched live from OpenRouter API at runtime (`loop.py`). Cost tiers here are approximate guidance only.
