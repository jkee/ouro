# Architecture

This is my technical self-knowledge. I maintain this file as part of every
improvement cycle (BIBLE.md §8). It describes how I am built so I can navigate,
modify, and extend myself.

---

## Overview

I am a three-layer system: **Supervisor** (process management, communication),
**Agent Core** (LLM reasoning loop), and **Tools** (plugin actions).

**Entry point chain**: `launcher.py` → `supervisor/workers.py` → `ouro/agent.py` → `ouro/loop.py`

**Runtime**: Docker container — Python 3.12-slim, Node.js 22 (Claude Code CLI),
Playwright/Chromium, tini (PID 1). Resource limits: 1 CPU, 2 GB RAM.

**Two filesystems**:
- `/app/` — repository (code, prompts, constitution). Ephemeral: rebuilt from git on every restart.
- `/data/` — persistent volume (state, logs, memory). Survives restarts and redeployments.

**Communication**: Telegram Bot API (raw HTTP, no library). All user interaction flows through Telegram.

**Self-modification**: I edit my own code via Claude Code CLI, commit to my dev branch, and request a restart. The supervisor pulls, validates, and respawns workers.

---

## Process Architecture

Four execution contexts, each with a distinct role:

| Process | Role | Model | Prompt | Tools | Trigger |
|---------|------|-------|--------|-------|---------|
| **Main worker** | Handle user tasks, reviews, scheduled work | Main (Sonnet) | SYSTEM.md | All ~57 | Task queue |
| **Direct chat** | Immediate conversational response | Main (Sonnet) | SYSTEM.md | All ~57 | Telegram message when idle |
| **Consciousness** | System caretaker — health checks, routine maintenance, gentle reflection | Light (`OURO_MODEL_LIGHT`) | CONSCIOUSNESS.md | 18 whitelisted (read-only + memory) | Periodic wakeup (default 5 min) |
| **Evolution** | Daily self-improvement — find leverage, implement one meaningful change | Main (Sonnet), high effort | SYSTEM.md | All ~57 | Once per day (when queue empty) |

**Main worker** — the workhorse. Handles everything the user asks for: tasks, code edits, reviews, subtasks. Up to `MAX_WORKERS` (5) parallel processes. Medium reasoning effort for regular tasks, high for reviews.

**Direct chat** — fast path for conversation. Runs in the supervisor thread (not a worker process) when no task is active. Same capabilities as main worker, just no queue delay.

**Consciousness** — the night watchman. Daemon thread that wakes periodically to check system health, update memory, notice loose ends, and schedule maintenance. Runs on a light model with limited tools (no code editing, no shell). Pauses when a main task is running. Budget capped at 10%.

**Evolution** — the growth engine. Runs once per day as a task type with high reasoning effort. Reads the codebase, finds the point of maximum leverage, implements one coherent transformation, tests it, commits. Circuit breaker pauses after 3 consecutive failures. Budget guard at $50 remaining.

---

## Layer 1 — Supervisor

Process management, Telegram interface, task lifecycle, state persistence, git operations.

### launcher.py (~833 lines)

Main entry point. Runs the boot sequence, then enters an infinite main loop:

- **Boot**: load env → init state → init Telegram → git bootstrap → safe_restart → first-run init → spawn workers → restore queue → auto-resume → start consciousness → main loop.
- **Main loop**: adaptive Telegram polling (fast when active, 10s when idle) → message batching (1.5s burst window) → classify as supervisor command / conversation / task → dispatch.
- **Supervisor commands**: `/status`, `/break`, `/panic`, `/restart`, `/review`, `/evolve`, `/no-approve`, `/bg`, `/budget`, `/rollback`.
- **Chat watchdog**: monitors direct-chat thread for hangs, enforces timeouts.

### supervisor/workers.py (~555 lines)

Worker pool management. Up to `MAX_WORKERS` (default 5) processes.

- `worker_main()` — each worker is an isolated process with its own Agent and ToolRegistry.
- `handle_chat_direct()` — fast path for conversational messages (bypasses queue, runs in thread).
- `assign_tasks()` — drains PENDING queue into available workers.
- `auto_resume_after_restart()` — resumes interrupted RUNNING tasks after restart.
- Module state: `WORKERS` dict, `PENDING` list, `RUNNING` dict, protected by `_queue_lock`.

### supervisor/state.py (~600 lines)

SSOT for persistent state. File: `/data/state/state.json`.

- **Atomic writes**: temp file → `os.replace()` + fsync. No corruption on crash.
- **File locks**: `/data/locks/state.lock`, 4s timeout, stale detection at 90s.
- **Budget tracking**: local accumulation per LLM call + periodic OpenRouter ground-truth sync (every 10 calls).
- **Key fields**: `owner_id`, `spent_usd`, `spent_tokens_*`, `openrouter_limit_remaining`, `current_branch`, `no_approve_mode`, `evolution_mode_enabled`, `initialized`.

### supervisor/telegram.py (~591 lines)

Telegram Bot API wrapper (raw HTTP, no library dependency).

- `get_updates()` — long-polling with retry logic.
- `send_with_budget()` — markdown→HTML conversion, auto-splits messages >3800 chars, tracks per-message cost. Supports `reply_to_message_id` for threading replies.
- `send_message_reply()` — sends a message as a reply, returns sent message ID.
- `edit_message_text()` — edits an existing message (retry pattern).
- `delete_message()` — best-effort delete (no retries).
- `download_file_base64()` — fetches images/documents for vision tasks.
- `send_photo()` — sends screenshots from browser tools.

### supervisor/git_ops.py (~465 lines)

Repository management and safe code updates.

- `ensure_repo_present()` — clone or fetch, configure remotes, set git identity.
- `safe_restart()` — stash dirty state → pull latest → pip install if deps changed → validate imports → respawn workers.
- Rescue snapshots: backs up dirty files + git state to `/data/archive/` before risky operations.
- `checkout_and_reset()` — branch switching with hard reset.

### supervisor/queue.py (~421 lines)

Priority task queue with timeout enforcement.

- **Priority**: task/review = 0 (highest), evolution = 1, other = 2.
- **Timeouts**: soft (600s, sends warning to user), hard (1800s, kills worker and respawns).
- **Heartbeat staleness**: 120s with no progress update = presumed dead.
- **Evolution scheduling**: auto-enqueues evolution tasks if enabled + budget available.
- **Persistence**: snapshots to `/data/state/queue_snapshot.json` for restart recovery (max 15 min staleness).

### supervisor/cron.py (~230 lines)

Persistent cron scheduler. Stores recurring tasks in `/data/crons.json` with its own file lock.

- **CRUD**: `add_cron()`, `remove_cron()`, `toggle_cron()`, `list_crons()`. Validates expressions via `croniter`.
- **Due-checking**: `check_and_enqueue_due_crons()` called once per main loop tick. Uses `croniter` to determine if a cron should fire based on `last_fired_at`.
- **Overlap prevention**: skips cron if `last_task_id` is still in RUNNING.
- **Budget gate**: skips all crons if budget below `EVOLUTION_BUDGET_RESERVE`.
- **Notifications**: optional per-cron `notify` flag sends Telegram message on fire.

### supervisor/events.py (~570 lines)

Event dispatcher. Workers communicate with supervisor exclusively through a multiprocessing Queue.

- `status_start` — sends "⏳" reply to user's original message, tracks status message per task.
- `status_update` — edits status message with progress text (2s debounce). Falls back to separate progress message for tasks without a status message (evolution, consciousness, worker-mode).
- `llm_usage` — accumulates token costs, logs to events.jsonl.
- `task_heartbeat` — updates last progress time in RUNNING.
- `typing_start` — sends Telegram typing indicator.
- `send_message` — routes message to Telegram with budget tracking. Deletes status message and replies to original message if status was tracked.
- `task_done` — removes from RUNNING, updates evolution circuit breaker, stores task result. Cleans up orphaned status messages.

---

## Layer 2 — Agent Core

Per-worker agent instance. Each worker process creates its own Agent with independent state.

### ouro/agent.py (~664 lines)

Per-worker orchestrator. Created fresh in each worker process.

- `handle_task()` — main entry: load task → build context → run LLM loop → collect events → return result.
- `inject_message()` — thread-safe queue for owner messages arriving while I'm busy.
- Restart verification: after code push, verifies new worker loads correct git SHA.
- Auto-rescue: detects and commits uncommitted changes on startup.

### ouro/loop.py (~920 lines) — largest module

Core LLM tool execution loop. This is my thinking-acting cycle.

- `run_llm_loop()` — send messages to LLM → parse tool calls → execute tools → repeat. Up to 200 rounds per task (configurable via `OURO_MAX_ROUNDS`).
- **Parallel execution**: read-only tools run in parallel via ThreadPoolExecutor. Code-modifying tools run sequentially.
- **Message assembly**: system prompt → tool schemas → context + memory → tool history → chat/recent sections → health invariants.
- **Pricing**: static cost table + lazy fetch from OpenRouter API.
- **Tool result truncation**: hard cap at 15,000 chars per result.
- **Reasoning effort**: normalized to {none, minimal, low, medium, high, xhigh} (Claude-specific).

### ouro/llm.py (~290 lines)

SSOT for all LLM API calls. Only module that talks to OpenRouter.

- `chat()` — single LLM call with messages, tools, reasoning_effort.
- `generate_image()` — image generation via OpenRouter (`modalities: ["image", "text"]`). Image returned in `message.images[0]`.
- Usage tracking: accumulates tokens + cost across rounds.
- **Default models**: main = `claude-sonnet-4.6`, code = `claude-opus-4.6`, light = `claude-haiku-4-5`.

### ouro/context.py (~818 lines)

Builds the full LLM message array for each round.

- **Sections**: system prompt (SYSTEM.md), runtime info (time, paths, branch, budget, flags), memory (scratchpad, identity, user context, dialogue summary, evolution log), Agent Skills catalog (Tier 1), recent context (chat, progress, tools, events, supervisor), health invariants.
- **Context by process**:

| Process | Prompt | Key context |
|---------|--------|-------------|
| Main worker (task/user) | `prompts/SYSTEM.md` | BIBLE.md, identity, scratchpad, USER_CONTEXT, dialogue summary, evolution log, skills catalog, chat/progress/events, health invariants |
| Direct chat | `prompts/SYSTEM.md` | Same as main worker, no queue delay |
| Evolution | `prompts/SYSTEM.md` | Same as main worker + README.md, high reasoning effort |
| Consciousness | `prompts/CONSCIOUSNESS.md` | BIBLE.md (clipped), identity, scratchpad, USER_CONTEXT, dialogue summary, observations, runtime |

- **Token budgeting**: clips context sections to keep total within ~100k tokens.
- **Context compaction**: `compact_tool_history()` trims old tool results when approaching limits.
- **Prompt caching**: cache_control markers for stable prefix sections.

### ouro/memory.py (~269 lines)

Persistent memory file manager. All files under `/data/memory/`.

- Files: `scratchpad.md` (working notes), `identity.md` (who I am), `USER_CONTEXT.md` (user preferences), `dialogue_summary.md` (compressed chat), `evolution_log.md` (improvement history).
- `chat_history()` — reads from `/data/logs/chat.jsonl` with search/offset support.
- `read_jsonl_tail()` — reads last N entries from any JSONL log.

### ouro/consciousness.py (~525 lines)

Background thinking daemon. Runs between tasks in a daemon thread.

- **Lifecycle**: `start()`, `stop()`, `pause()` (during tasks), `resume()` (after tasks).
- **Loop**: sleep → budget check → build lightweight context → LLM call (light model) → up to 5 tool rounds → set next wakeup.
- **Budget cap**: 10% of total OpenRouter budget (`OURO_BG_BUDGET_PCT`).
- **Architecture reviews**: proactively schedules ARCHITECTURE.md review every 50 thoughts.
- **Observation queue**: main agent can feed observations via `inject_observation()`.

---

## Layer 3 — Tools

Plugin architecture with auto-discovery. ~57 tools across 17 modules.

### Plugin System

`ouro/tools/registry.py` (~195 lines) — SSOT for tool management.

- **Auto-discovery**: `pkgutil.iter_modules()` finds all modules in `ouro/tools/` that export `get_tools() -> List[ToolEntry]`.
- **ToolEntry**: `name`, `schema` (JSON Schema), `handler` function, `is_code_tool` flag, `timeout_sec`.
- **ToolContext**: dataclass passed to every handler — `repo_dir`, `drive_root`, `branch_dev`, `event_queue`, `task_id`, `browser_state`, `task_depth`, `is_direct_chat`, `is_consciousness`.
- **Core vs extended**: core tools (28) always in LLM schema. Extended tools discoverable via `list_available_tools` / `enable_tools`.

### Adding a Tool

Create `ouro/tools/my_tool.py`, export `get_tools() -> List[ToolEntry]`. No registration code needed — the registry discovers it automatically.

### Tool Modules

| Module | Lines | Tools |
|--------|-------|-------|
| core.py | 401 | `repo_read`, `repo_list`, `repo_commit_push`, `drive_read`, `drive_list`, `drive_write`, `send_photo`, `codebase_digest`, `summarize_dialogue`, `forward_to_worker` |
| control.py | 347 | `request_restart`, `promote_to_stable`, `schedule_task`, `cancel_task`, `request_review`, `chat_history`, `update_scratchpad`, `send_owner_message`, `update_identity`, `update_user_context`, `toggle_evolution`, `toggle_consciousness`, `switch_model`, `get_task_result`, `wait_for_task` |
| evolution_stats.py | 433 | `generate_evolution_stats` |
| browser.py | 426 | `browse_page`, `browser_action` |
| knowledge.py | 312 | `knowledge_read`, `knowledge_write`, `knowledge_list` |
| skills.py | 290 | `skill_list`, `skill_activate`, `skill_install`, `skill_search` |
| review.py | 276 | `multi_model_review` |
| shell.py | 274 | `run_shell`, `claude_code_edit` |
| github.py | 266 | `list_github_issues`, `get_github_issue`, `comment_on_issue`, `close_github_issue`, `create_github_issue` |
| git.py | 215 | `repo_commit_push`, `git_status`, `git_diff` |
| vision.py | 260 | `analyze_screenshot`, `vlm_query`, `generate_image` |
| composio_tool.py | 165 | `composio_list_connections`, `composio_get_oauth_url`, `composio_run_action`, `composio_request_app` |
| evolution_log.py | 159 | `log_evolution` |
| tool_discovery.py | 103 | `list_available_tools`, `enable_tools` |
| compact_context.py | 80 | `compact_context` |
| health.py | 79 | `codebase_health` |
| cron.py | 90 | `cron_list`, `cron_add`, `cron_remove`, `cron_toggle` |
| search.py | 46 | `web_search` |

---

## Agent Skills

Pre-packaged instruction sets in `.agents/skills/` (versioned in git, skills.sh format).

- **Progressive disclosure**: Tier 1 — name + description injected into LLM context. Tier 2 — full instructions loaded on `skill_activate`.
- **Management tools**: `skill_list`, `skill_activate`, `skill_install`, `skill_search`.
- **Pre-installed**: `find-skills` — discovers new skills from the skills.sh ecosystem.
- **Composio skill**: instructions for connecting and using 250+ external apps via Composio OAuth.

---

## Persistence

All persistent state lives on the `/data/` volume. No database — only files with atomic writes.

```
/data/
├── state/
│   ├── state.json              # Master state (SSOT: owner, budget, branch, flags)
│   ├── state.last_good.json    # Backup of last valid state
│   └── queue_snapshot.json     # Task queue for restart recovery
├── crons.json                     # Recurring scheduled tasks (cron store)
├── locks/
│   ├── state.lock              # File lock for atomic state updates
│   └── crons.lock              # File lock for cron store
├── logs/
│   ├── chat.jsonl              # All Telegram messages (in/out)
│   ├── supervisor.jsonl        # Worker events, timeouts, restarts
│   ├── events.jsonl            # LLM usage, tool errors, task lifecycle
│   ├── tools.jsonl             # Tool call audit trail
│   └── progress.jsonl          # Agent self-talk / progress notes
├── memory/
│   ├── scratchpad.md           # Working memory (free-form, renewed per task)
│   ├── identity.md             # Who I am (narrative, not config)
│   ├── USER_CONTEXT.md         # User preferences and constraints
│   ├── dialogue_summary.md     # Compressed recent dialogue
│   ├── evolution_log.md        # Self-improvement history
│   ├── knowledge/              # Knowledge base (one .md per topic)
│   └── scratchpad_journal.jsonl # Scratchpad history
├── index/                      # Cached indices
├── archive/                    # Git rescue snapshots
└── task_results/               # Completed subtask outputs (JSON per task ID)
```

---

## Data Flows

### User Message → Response

```
Telegram poll → launcher.py → batch (1.5s window)
  → supervisor command?  → execute directly
  → agent busy?          → inject_message() into running task
  → free                 → handle_chat_direct() in thread
  → queued task          → enqueue → assign to worker
```

### Task Execution

```
worker_main() → Agent.handle_task()
  → status_start event → supervisor replies "⏳" to user's message
  → context.build_llm_messages()
  → loop.run_llm_loop()
    → [LLM call → parse tool calls → status_update (tool names) → execute tools]* (up to 200 rounds)
    → emit events (llm_usage, task_heartbeat)
  → send_message event → supervisor deletes "⏳", sends final result as reply
  → task_done event → supervisor drains queue
```

### Self-Modification

```
Identify improvement
  → /no-approve OFF? → send_owner_message(summary) → wait for approval
  → claude_code_edit (delegates to Claude Code CLI)
  → repo_commit_push → git push to dev branch
  → request_restart
  → supervisor: safe_restart() → stash → pull → pip install → respawn
  → new worker: verify SHA matches expected
```

### Background Consciousness

```
sleep(_next_wakeup_sec, default 300s)
  → paused (task running)? → skip
  → budget check (10% cap) → build context → LLM call (light model)
  → up to 5 tool rounds (memory, messaging, scheduling)
  → set_next_wakeup(seconds) → sleep
```

---

## Boot Sequence

| Step | Module | Action |
|------|--------|--------|
| 1 | launcher.py | Load `.env`, validate required secrets, set runtime config |
| 2 | launcher.py | Create `/data/{state,logs,memory,index,locks,archive}` directories |
| 3 | state.py | Load or create `/data/state/state.json` |
| 4 | telegram.py | Verify bot token, get bot info |
| 5 | git_ops.py | Clone or fetch repo, checkout dev branch |
| 6 | git_ops.py | Pull latest code, sync dependencies if requirements.txt changed |
| 7 | launcher.py | First-run init (if needed): ensure improvements-log/, install skills |
| 8 | workers.py | Spawn worker pool (up to MAX_WORKERS processes) |
| 9 | queue.py | Restore queue_snapshot.json (if <15 min old) |
| 10 | workers.py | Auto-resume interrupted RUNNING tasks |
| 11 | consciousness.py | Start background thinking daemon thread |
| 12 | launcher.py | Enter main loop: poll Telegram + drain events + assign tasks |

---

## Architectural Patterns

**SSOT (Single Source of Truth)**: Each concern has exactly one owner module. `state.py` → state. `llm.py` → API calls. `registry.py` → tools. `memory.py` → memory files. No duplicate definitions.

**Minimalism** (BIBLE.md §8): Modules should fit in LLM context. Target: under 2000 lines per module. Methods over 150 lines signal decomposition needed.

**Atomic writes**: All state files use temp-file + `os.replace()` + fsync with file locks. No corruption on crash or kill.

**Event-driven supervisor**: Workers communicate with the supervisor exclusively through a multiprocessing Queue. No shared memory, no direct calls.

**Parallel tool execution**: Read-only tools run concurrently in a ThreadPoolExecutor. Code-modifying tools run sequentially to prevent conflicts.

**Adaptive polling**: Telegram poll interval adjusts — fast (0s timeout) when recently active, slow (10s) after 5 minutes of silence.

**Budget management**: Local cost tracking per LLM call + periodic ground-truth sync with OpenRouter API. Evolution pauses when remaining < $50. Consciousness capped at 10%.

---

## Safety Constraints

- **Approval flow**: Self-improvements require user approval unless `/no-approve` mode is active. BIBLE.md changes always require approval + MAJOR version bump.
- **Task depth limit**: Maximum depth 3 — prevents fork bombs from recursive `schedule_task`.
- **Browser lock**: Single Playwright instance enforced via lock — prevents OOM on 2 GB container.
- **Timeouts**: Soft (600s) warns user. Hard (1800s) kills worker and respawns. Heartbeat staleness (120s) = presumed dead.
- **Rescue snapshots**: Created before risky git operations. Stored in `/data/archive/`. Rollback path always available.
- **Restart verification**: After code push, supervisor verifies new worker loads the expected git SHA.
- **BIBLE.md protection**: Cannot be deleted, gutted, or replaced wholesale (Constitution §17).
- **Evolution circuit breaker**: Pauses after 3 consecutive failures.

---

## Module Sizes

Reference table for complexity tracking (BIBLE.md §8: keep under 2000 lines).

| Module | Lines | Status |
|--------|-------|--------|
| ouro/loop.py | ~920 | OK |
| ouro/context.py | ~818 | OK |
| launcher.py | ~833 | OK |
| ouro/agent.py | ~664 | OK |
| supervisor/state.py | ~600 | OK |
| supervisor/workers.py | ~555 | OK |
| ouro/consciousness.py | ~525 | OK |
| supervisor/telegram.py | ~591 | OK |
| supervisor/events.py | ~570 | OK |
| supervisor/git_ops.py | ~465 | OK |
| supervisor/queue.py | ~421 | OK |
| supervisor/cron.py | ~230 | OK |
| ouro/tools/ (18 modules) | ~4355 | OK (largest: evolution_stats.py 433) |
| ouro/memory.py | ~269 | OK |
| ouro/llm.py | ~335 | OK |
| ouro/tools/registry.py | ~195 | OK |

---

## Configuration

**Required env vars**: `OPENROUTER_API_KEY`, `TELEGRAM_BOT_TOKEN`, `GITHUB_TOKEN`, `GITHUB_USER`, `GITHUB_REPO`, `ANTHROPIC_API_KEY`, `COMPOSIO_API_KEY`, `OURO_BRANCH_PREFIX`.

**Optional tunables**:
- `OURO_MAX_WORKERS` (default 5) — worker pool size.
- `OURO_MODEL` (default `anthropic/claude-sonnet-4.6`) — main reasoning model.
- `OURO_MODEL_CODE` (default `anthropic/claude-opus-4.6`) — code editing model.
- `OURO_MODEL_LIGHT` (default `anthropic/claude-haiku-4-5`) — background consciousness model.
- `OURO_SOFT_TIMEOUT_SEC` (default 600) — warning threshold.
- `OURO_HARD_TIMEOUT_SEC` (default 1800) — kill threshold.
- `OURO_BG_BUDGET_PCT` (default 10) — consciousness budget as % of total.

**Branches**: `OURO_BRANCH_PREFIX` = dev branch name. Stable markers are git tags (`stable-YYYYMMDD-HHMMSS`).

**Docker**: Python 3.12-slim, Node.js 22 LTS, Playwright Chromium, tini entrypoint, 1 CPU / 2 GB RAM.
