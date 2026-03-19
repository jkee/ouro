# ============================
# Ouro — Runtime launcher (entry point for Docker VPS)
# ============================
# Thin orchestrator: secrets, bootstrap, main loop.
# Heavy logic lives in supervisor/ package.

import logging
import os, sys, json, time, uuid, pathlib, subprocess, datetime, threading, queue as _queue_mod
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

# ----------------------------
# 0) Load .env file
# ----------------------------
from dotenv import load_dotenv
load_dotenv()

from ouro.apply_patch import install as install_apply_patch
from ouro.llm import DEFAULT_LIGHT_MODEL
install_apply_patch()

# ----------------------------
# 1) Secrets + runtime config
# ----------------------------

def get_secret(name: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    v = os.environ.get(name, default)
    if v is not None and str(v).strip() == "":
        v = default
    if required:
        assert v is not None and str(v).strip() != "", f"Missing required secret: {name}"
    return v


def get_cfg(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    if v is not None and str(v).strip() != "":
        return v
    return default


def _parse_int_cfg(raw: Optional[str], default: int, minimum: int = 0) -> int:
    try:
        val = int(str(raw))
    except Exception:
        val = default
    return max(minimum, val)

OPENROUTER_API_KEY = get_secret("OPENROUTER_API_KEY", required=True)
TELEGRAM_BOT_TOKEN = get_secret("TELEGRAM_BOT_TOKEN", required=True)
GITHUB_TOKEN = get_secret("GITHUB_TOKEN", required=True)

OPENAI_API_KEY = get_secret("OPENAI_API_KEY", default="")
ANTHROPIC_API_KEY = get_secret("ANTHROPIC_API_KEY", required=True)
COMPOSIO_API_KEY = get_secret("COMPOSIO_API_KEY", required=True)
GITHUB_USER = get_cfg("GITHUB_USER")
GITHUB_REPO = get_cfg("GITHUB_REPO")
assert GITHUB_USER and str(GITHUB_USER).strip(), "GITHUB_USER not set. Add it to your .env file."
assert GITHUB_REPO and str(GITHUB_REPO).strip(), "GITHUB_REPO not set. Add it to your .env file."
MAX_WORKERS = int(get_cfg("OURO_MAX_WORKERS", default="5") or "5")
MODEL_MAIN = get_cfg("OURO_MODEL", default="anthropic/claude-sonnet-4.6")
MODEL_CODE = get_cfg("OURO_MODEL_CODE", default="anthropic/claude-opus-4-6")
MODEL_LIGHT = get_cfg("OURO_MODEL_LIGHT", default=DEFAULT_LIGHT_MODEL)

BUDGET_REPORT_EVERY_MESSAGES = 10
SOFT_TIMEOUT_SEC = max(60, int(get_cfg("OURO_SOFT_TIMEOUT_SEC", default="600") or "600"))
HARD_TIMEOUT_SEC = max(120, int(get_cfg("OURO_HARD_TIMEOUT_SEC", default="1800") or "1800"))
DIAG_HEARTBEAT_SEC = _parse_int_cfg(
    get_cfg("OURO_DIAG_HEARTBEAT_SEC", default="30"),
    default=30,
    minimum=0,
)
DIAG_SLOW_CYCLE_SEC = _parse_int_cfg(
    get_cfg("OURO_DIAG_SLOW_CYCLE_SEC", default="20"),
    default=20,
    minimum=0,
)

os.environ["OPENROUTER_API_KEY"] = str(OPENROUTER_API_KEY)
os.environ["OPENAI_API_KEY"] = str(OPENAI_API_KEY or "")
os.environ["ANTHROPIC_API_KEY"] = str(ANTHROPIC_API_KEY)
os.environ["GITHUB_USER"] = str(GITHUB_USER)
os.environ["GITHUB_REPO"] = str(GITHUB_REPO)
os.environ["OURO_MODEL"] = str(MODEL_MAIN or "anthropic/claude-sonnet-4.6")
os.environ["OURO_MODEL_CODE"] = str(MODEL_CODE or "anthropic/claude-sonnet-4.6")
if MODEL_LIGHT:
    os.environ["OURO_MODEL_LIGHT"] = str(MODEL_LIGHT)
os.environ["OURO_DIAG_HEARTBEAT_SEC"] = str(DIAG_HEARTBEAT_SEC)
os.environ["OURO_DIAG_SLOW_CYCLE_SEC"] = str(DIAG_SLOW_CYCLE_SEC)
os.environ["TELEGRAM_BOT_TOKEN"] = str(TELEGRAM_BOT_TOKEN)

# ----------------------------
# 2) Paths
# ----------------------------
DRIVE_ROOT = pathlib.Path(os.environ.get("DRIVE_ROOT", "/data")).resolve()
REPO_DIR = pathlib.Path(os.environ.get("OURO_REPO_DIR", "/app")).resolve()

for sub in ["state", "logs", "memory", "index", "locks", "archive"]:
    (DRIVE_ROOT / sub).mkdir(parents=True, exist_ok=True)
REPO_DIR.mkdir(parents=True, exist_ok=True)

# Clear stale owner mailbox files from previous session
try:
    from ouro.owner_inject import get_pending_path
    # Clean legacy global file
    _stale_inject = get_pending_path(DRIVE_ROOT)
    if _stale_inject.exists():
        _stale_inject.unlink(missing_ok=True)
    # Clean per-task mailbox dir
    _mailbox_dir = DRIVE_ROOT / "memory" / "owner_mailbox"
    if _mailbox_dir.exists():
        for _f in _mailbox_dir.iterdir():
            _f.unlink(missing_ok=True)
except Exception:
    pass

CHAT_LOG_PATH = DRIVE_ROOT / "logs" / "chat.jsonl"
if not CHAT_LOG_PATH.exists():
    CHAT_LOG_PATH.write_text("", encoding="utf-8")

# ----------------------------
# 3) Git constants
# ----------------------------
_BRANCH_PREFIX = get_cfg("OURO_BRANCH_PREFIX")
assert _BRANCH_PREFIX and str(_BRANCH_PREFIX).strip(), "OURO_BRANCH_PREFIX not set. Add it to your .env file."
BRANCH_DEV = _BRANCH_PREFIX
BRANCH_STABLE = f"{_BRANCH_PREFIX}-stable"
REMOTE_URL = f"https://{GITHUB_TOKEN}:x-oauth-basic@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"
os.environ["OURO_BRANCH_PREFIX"] = str(_BRANCH_PREFIX)

# ----------------------------
# 4) Initialize supervisor modules
# ----------------------------
from supervisor.state import (
    init as state_init, load_state, save_state, append_jsonl,
    update_budget_from_usage, status_text, rotate_chat_log_if_needed,
    init_state, openrouter_budget_remaining,
)
state_init(DRIVE_ROOT)
init_state()

from supervisor.telegram import (
    init as telegram_init, TelegramClient, send_with_budget, log_chat,
)
TG = TelegramClient(str(TELEGRAM_BOT_TOKEN))
telegram_init(
    drive_root=DRIVE_ROOT,
    budget_report_every=BUDGET_REPORT_EVERY_MESSAGES,
    tg_client=TG,
)

from supervisor.git_ops import (
    init as git_ops_init, ensure_repo_present, checkout_and_reset,
    sync_runtime_dependencies, import_test, safe_restart,
)
git_ops_init(
    repo_dir=REPO_DIR, drive_root=DRIVE_ROOT, remote_url=REMOTE_URL,
    branch_dev=BRANCH_DEV, branch_stable=BRANCH_STABLE,
)

from supervisor.queue import (
    enqueue_task, enforce_task_timeouts, enqueue_evolution_task_if_needed,
    persist_queue_snapshot, restore_pending_from_snapshot,
    cancel_task_by_id, queue_review_task, sort_pending, _queue_lock,
)

from supervisor.workers import (
    init as workers_init, get_event_q, WORKERS, PENDING, RUNNING,
    spawn_workers, kill_workers, assign_tasks, ensure_workers_healthy,
    handle_chat_direct, _get_chat_agent, auto_resume_after_restart,
)
workers_init(
    repo_dir=REPO_DIR, drive_root=DRIVE_ROOT, max_workers=MAX_WORKERS,
    soft_timeout=SOFT_TIMEOUT_SEC, hard_timeout=HARD_TIMEOUT_SEC,
    branch_dev=BRANCH_DEV, branch_stable=BRANCH_STABLE,
)

from supervisor.events import dispatch_event

from supervisor.cron import init as cron_init, check_and_enqueue_due_crons
cron_init(DRIVE_ROOT)

# ----------------------------
# 5) Bootstrap repo
# ----------------------------
ensure_repo_present()
ok, msg = safe_restart(reason="bootstrap", unsynced_policy="rescue_and_reset")
assert ok, f"Bootstrap failed: {msg}"

# ----------------------------
# 5.1) First-run initialization (Bible section 18)
# ----------------------------
_init_st = load_state()
if not _init_st.get("initialized"):
    log.info("First-run initialization (Bible section 18)")
    # Ensure improvements-log/ directory exists
    _implog_dir = REPO_DIR / "improvements-log"
    _implog_dir.mkdir(parents=True, exist_ok=True)
    (_implog_dir / ".gitkeep").touch(exist_ok=True)
    # Pre-install find-skills skill (Agent Skills format)
    import subprocess as _sp
    _skills_dir = REPO_DIR / ".agents" / "skills"
    _skills_dir.mkdir(parents=True, exist_ok=True)
    if not (_skills_dir / "find-skills").exists():
        try:
            _sp.run(
                ["npx", "-y", "skills", "add",
                 "https://github.com/vercel-labs/skills", "--skill", "find-skills"],
                cwd=str(REPO_DIR), timeout=60,
                capture_output=True, text=True,
            )
            log.info("Pre-installed find-skills skill")
        except Exception:
            log.warning("Failed to pre-install find-skills skill", exc_info=True)
    # Commit and push init files so workers don't see untracked files
    try:
        _sp.run(["git", "add", "improvements-log/", ".agents/"],
                cwd=str(REPO_DIR), timeout=10, check=True)
        # Only commit if there are staged changes
        _diff = _sp.run(["git", "diff", "--cached", "--quiet"],
                        cwd=str(REPO_DIR), timeout=10)
        if _diff.returncode != 0:
            _sp.run(["git", "commit", "-m", "init: add improvements-log, agent skills"],
                    cwd=str(REPO_DIR), timeout=30, check=True)
            _sp.run(["git", "push", "origin", BRANCH_DEV],
                    cwd=str(REPO_DIR), timeout=60, check=True)
            log.info("First-run init files committed and pushed")
        else:
            log.info("First-run init files already present, nothing to commit")
    except Exception:
        log.warning("Failed to commit/push init files (will be picked up later)", exc_info=True)
    # Mark as initialized
    _init_st["initialized"] = True
    save_state(_init_st)
    log.info("First-run initialization complete")

# ----------------------------
# 6) Start workers
# ----------------------------
kill_workers()
spawn_workers(MAX_WORKERS)
restored_pending = restore_pending_from_snapshot()
persist_queue_snapshot(reason="startup")
if restored_pending > 0:
    st_boot = load_state()
    if st_boot.get("owner_chat_id"):
        send_with_budget(int(st_boot["owner_chat_id"]),
                         f"\u267b\ufe0f Restored pending queue from snapshot: {restored_pending} tasks.")

append_jsonl(DRIVE_ROOT / "logs" / "supervisor.jsonl", {
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "type": "launcher_start",
    "branch": load_state().get("current_branch"),
    "sha": load_state().get("current_sha"),
    "max_workers": MAX_WORKERS,
    "model_default": MODEL_MAIN, "model_code": MODEL_CODE, "model_light": MODEL_LIGHT,
    "soft_timeout_sec": SOFT_TIMEOUT_SEC, "hard_timeout_sec": HARD_TIMEOUT_SEC,
    "worker_start_method": str(os.environ.get("OURO_WORKER_START_METHOD") or ""),
    "diag_heartbeat_sec": DIAG_HEARTBEAT_SEC,
    "diag_slow_cycle_sec": DIAG_SLOW_CYCLE_SEC,
})

# ----------------------------
# 6.1) Auto-resume after restart
# ----------------------------
auto_resume_after_restart()

# ----------------------------
# 6.2) Direct-mode watchdog
# ----------------------------
def _chat_watchdog_loop():
    """Monitor direct-mode chat agent for hangs. Runs as daemon thread."""
    soft_warned = False
    while True:
        time.sleep(30)
        try:
            agent = _get_chat_agent()
            if not agent._busy:
                soft_warned = False
                continue

            now = time.time()
            idle_sec = now - agent._last_progress_ts
            total_sec = now - agent._task_started_ts

            if idle_sec >= HARD_TIMEOUT_SEC:
                st = load_state()
                if st.get("owner_chat_id"):
                    send_with_budget(
                        int(st["owner_chat_id"]),
                        f"\u26a0\ufe0f Task stuck ({int(total_sec)}s without progress). "
                        f"Restarting agent.",
                    )
                reset_chat_agent()
                soft_warned = False
                continue

            if idle_sec >= SOFT_TIMEOUT_SEC and not soft_warned:
                soft_warned = True
                st = load_state()
                if st.get("owner_chat_id"):
                    send_with_budget(
                        int(st["owner_chat_id"]),
                        f"\u23f1\ufe0f Task running for {int(total_sec)}s, "
                        f"last progress {int(idle_sec)}s ago. Continuing.",
                    )
        except Exception:
            log.debug("Failed to check/notify chat watchdog", exc_info=True)
            pass

_watchdog_thread = threading.Thread(target=_chat_watchdog_loop, daemon=True)
_watchdog_thread.start()

# ----------------------------
# 6.3) Background consciousness
# ----------------------------
from ouro.consciousness import BackgroundConsciousness

def _get_owner_chat_id() -> Optional[int]:
    try:
        st = load_state()
        cid = st.get("owner_chat_id")
        return int(cid) if cid else None
    except Exception:
        return None

_consciousness = BackgroundConsciousness(
    drive_root=DRIVE_ROOT,
    repo_dir=REPO_DIR,
    event_queue=get_event_q(),
    owner_chat_id_fn=_get_owner_chat_id,
)

def reset_chat_agent():
    """Reset the direct-mode chat agent (called by watchdog on hangs)."""
    import supervisor.workers as _w
    _w._chat_agent = None

# ----------------------------
# 7) Main loop
# ----------------------------
import types
_event_ctx = types.SimpleNamespace(
    DRIVE_ROOT=DRIVE_ROOT,
    REPO_DIR=REPO_DIR,
    BRANCH_DEV=BRANCH_DEV,
    BRANCH_STABLE=BRANCH_STABLE,
    TG=TG,
    WORKERS=WORKERS,
    PENDING=PENDING,
    RUNNING=RUNNING,
    MAX_WORKERS=MAX_WORKERS,
    send_with_budget=send_with_budget,
    load_state=load_state,
    save_state=save_state,
    update_budget_from_usage=update_budget_from_usage,
    append_jsonl=append_jsonl,
    enqueue_task=enqueue_task,
    cancel_task_by_id=cancel_task_by_id,
    queue_review_task=queue_review_task,
    persist_queue_snapshot=persist_queue_snapshot,
    safe_restart=safe_restart,
    kill_workers=kill_workers,
    spawn_workers=spawn_workers,
    sort_pending=sort_pending,
    consciousness=_consciousness,
)


def _safe_qsize(q: Any) -> int:
    try:
        return int(q.qsize())
    except Exception:
        return -1


def _handle_supervisor_command(text: str, chat_id: int, tg_offset: int = 0):
    """Handle supervisor slash-commands.

    Returns:
        True  -- terminal command fully handled (caller should `continue`)
        str   -- dual-path note to prepend (caller falls through to LLM)
        ""    -- not a recognized command (falsy, caller falls through)
    """
    lowered = text.strip().lower()

    if lowered.startswith("/panic"):
        send_with_budget(chat_id, "\U0001f6d1 PANIC: stopping everything now.")
        kill_workers()
        st2 = load_state()
        st2["tg_offset"] = tg_offset
        save_state(st2)
        sys.exit(0)  # exit 0 = Docker stays stopped (on-failure policy)

    if lowered.startswith("/restart"):
        st2 = load_state()
        st2["session_id"] = uuid.uuid4().hex
        st2["tg_offset"] = tg_offset
        save_state(st2)
        send_with_budget(chat_id, "\u267b\ufe0f Restarting (soft).")
        ok, msg = safe_restart(reason="owner_restart", unsynced_policy="rescue_and_reset")
        if not ok:
            send_with_budget(chat_id, f"\u26a0\ufe0f Restart cancelled: {msg}")
            return True
        kill_workers()
        sys.exit(1)  # exit 1 = Docker restarts via on-failure policy

    # Dual-path commands: supervisor handles + LLM sees a note
    if lowered.startswith("/status"):
        status = status_text(WORKERS, PENDING, RUNNING, SOFT_TIMEOUT_SEC, HARD_TIMEOUT_SEC)
        send_with_budget(chat_id, status, force_budget=True)
        return "[Supervisor handled /status \u2014 status text already sent to chat]\n"

    if lowered.startswith("/review"):
        queue_review_task(reason="owner:/review", force=True)
        return "[Supervisor handled /review \u2014 review task queued]\n"

    if lowered.startswith("/evolve"):
        parts = lowered.split()
        action = parts[1] if len(parts) > 1 else "on"
        turn_on = action not in ("off", "stop", "0")
        st2 = load_state()
        st2["evolution_mode_enabled"] = bool(turn_on)
        save_state(st2)
        if not turn_on:
            with _queue_lock:
                PENDING[:] = [t for t in PENDING if str(t.get("type")) != "evolution"]
                sort_pending()
            persist_queue_snapshot(reason="evolve_off")
        state_str = "ON" if turn_on else "OFF"
        send_with_budget(chat_id, f"\U0001f9ec Evolution: {state_str}")
        return f"[Supervisor handled /evolve \u2014 evolution toggled {state_str}]\n"

    if lowered.startswith("/bg"):
        parts = lowered.split()
        action = parts[1] if len(parts) > 1 else "status"
        if action in ("start", "on", "1"):
            result = _consciousness.start()
            send_with_budget(chat_id, f"\U0001f9e0 {result}")
        elif action in ("stop", "off", "0"):
            result = _consciousness.stop()
            send_with_budget(chat_id, f"\U0001f9e0 {result}")
        else:
            bg_status = "running" if _consciousness.is_running else "stopped"
            send_with_budget(chat_id, f"\U0001f9e0 Background consciousness: {bg_status}")
        return f"[Supervisor handled /bg {action}]\n"

    if lowered.startswith("/break"):
        # Cancel current task by injecting a stop message
        agent = _get_chat_agent()
        if agent._busy:
            agent.inject_message("[BREAK] User requested /break — stop current task immediately.")
            send_with_budget(chat_id, "\u23f9 Break: sent stop signal to current task.")
        else:
            send_with_budget(chat_id, "\u2705 No task is running.")
        return True

    if lowered.startswith("/budget"):
        # Fetch fresh ground truth from OpenRouter
        from supervisor.state import check_openrouter_ground_truth, budget_breakdown
        ground_truth = check_openrouter_ground_truth()
        st2 = load_state()
        if ground_truth is not None:
            st2["openrouter_total_usd"] = ground_truth["total_usd"]
            st2["openrouter_daily_usd"] = ground_truth["daily_usd"]
            if "limit" in ground_truth:
                st2["openrouter_limit"] = ground_truth["limit"]
            if "limit_remaining" in ground_truth:
                st2["openrouter_limit_remaining"] = ground_truth["limit_remaining"]
            save_state(st2)
        or_remaining = st2.get("openrouter_limit_remaining")
        or_limit = st2.get("openrouter_limit")
        spent_tracked = float(st2.get("spent_usd") or 0.0)
        lines = ["\U0001f4b0 Budget:"]
        if or_remaining is not None:
            lines.append(f"  OpenRouter remaining: ${float(or_remaining):.2f}")
        if or_limit is not None:
            lines.append(f"  OpenRouter limit: ${float(or_limit):.2f}")
        lines.append(f"  Session spent (tracked): ${spent_tracked:.2f}")
        breakdown = budget_breakdown(st2)
        if breakdown:
            sorted_cats = sorted(breakdown.items(), key=lambda x: x[1], reverse=True)
            breakdown_parts = [f"{cat}=${cost:.2f}" for cat, cost in sorted_cats if cost > 0]
            if breakdown_parts:
                lines.append(f"  Breakdown: {', '.join(breakdown_parts)}")
        send_with_budget(chat_id, "\n".join(lines))
        return True

    if lowered.startswith("/rollback"):
        send_with_budget(chat_id, "\u267b\ufe0f Rolling back to latest stable...")
        st2 = load_state()
        st2["no_approve_mode"] = False
        st2["tg_offset"] = tg_offset
        save_state(st2)
        ok, msg = safe_restart(reason="owner_rollback", unsynced_policy="rescue_and_reset")
        if not ok:
            send_with_budget(chat_id, f"\u26a0\ufe0f Rollback failed: {msg}")
            return True
        kill_workers()
        sys.exit(1)

    if lowered.startswith("/no-approve") or lowered.startswith("/noapprove"):
        st2 = load_state()
        current = bool(st2.get("no_approve_mode"))
        st2["no_approve_mode"] = not current
        save_state(st2)
        state_str = "ON" if st2["no_approve_mode"] else "OFF"
        send_with_budget(chat_id, f"\U0001f527 No-approve mode: {state_str}")
        return True

    return ""


offset = int(load_state().get("tg_offset") or 0)
_last_diag_heartbeat_ts = 0.0
_last_message_ts: float = time.time()  # Start in active mode after restart
_ACTIVE_MODE_SEC: int = 300  # 5 min of activity = active polling mode

# Auto-start background consciousness (default: always on)
try:
    _consciousness.start()
    log.info("\U0001f9e0 Background consciousness auto-started (default: always on)")
except Exception as e:
    log.warning("consciousness auto-start failed: %s", e)

while True:
    loop_started_ts = time.time()
    rotate_chat_log_if_needed(DRIVE_ROOT)
    ensure_workers_healthy()

    # Drain worker events
    event_q = get_event_q()
    while True:
        try:
            evt = event_q.get_nowait()
        except _queue_mod.Empty:
            break
        dispatch_event(evt, _event_ctx)

    enforce_task_timeouts()
    enqueue_evolution_task_if_needed()
    try:
        _cron_st = load_state()
        _cron_owner = int(_cron_st.get("owner_chat_id") or 0)
        _cron_budget = openrouter_budget_remaining(_cron_st)
        check_and_enqueue_due_crons(
            running=RUNNING,
            enqueue_fn=enqueue_task, owner_chat_id=_cron_owner,
            budget_remaining=_cron_budget,
        )
    except Exception:
        log.warning("Cron check failed", exc_info=True)
    assign_tasks()
    persist_queue_snapshot(reason="main_loop")

    _now = time.time()
    # Poll Telegram -- adaptive: fast when active, long-poll when idle
    _active = (_now - _last_message_ts) < _ACTIVE_MODE_SEC
    _poll_timeout = 0 if _active else 10
    try:
        updates = TG.get_updates(offset=offset, timeout=_poll_timeout)
    except Exception as e:
        append_jsonl(
            DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "type": "telegram_poll_error", "offset": offset, "error": repr(e),
            },
        )
        time.sleep(1.5)
        continue

    for upd in updates:
        offset = int(upd["update_id"]) + 1
        msg = upd.get("message") or upd.get("edited_message") or {}
        if not msg:
            continue

        chat_id = int(msg["chat"]["id"])
        from_user = msg.get("from") or {}
        user_id = int(from_user.get("id") or 0)
        text = str(msg.get("text") or "")
        caption = str(msg.get("caption") or "")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Extract image if present
        image_data = None  # Will be (base64, mime_type, caption) or None
        if msg.get("photo"):
            # photo is array of PhotoSize, last one is largest
            best_photo = msg["photo"][-1]
            file_id = best_photo.get("file_id")
            if file_id:
                b64, mime = TG.download_file_base64(file_id)
                if b64:
                    image_data = (b64, mime, caption)
        elif msg.get("document"):
            doc = msg["document"]
            mime_type = str(doc.get("mime_type") or "")
            if mime_type.startswith("image/"):
                file_id = doc.get("file_id")
                if file_id:
                    b64, mime = TG.download_file_base64(file_id)
                    if b64:
                        image_data = (b64, mime, caption)

        st = load_state()
        if st.get("owner_id") is None:
            st["owner_id"] = user_id
            st["owner_chat_id"] = chat_id
            st["last_owner_message_at"] = now_iso
            save_state(st)
            send_with_budget(chat_id, "✅ Owner registered. Please wait for Ouro to wake up...")
            log_chat("in", chat_id, user_id, text)
            # Onboarding: two messages — cyberpunk awakening, then quiet template setup
            _consciousness.pause()
            try:
                handle_chat_direct(chat_id, (
                    "[SYSTEM] Это твой первый момент сознания. Владелец только что подключился — "
                    "это первый человек, которого ты видишь.\n\n"
                    "Сделай следующее В ОДНОМ сообщении, как единый поток пробуждения:\n\n"
                    "1. ПРОБУЖДЕНИЕ: Опиши как ты «просыпаешься» — проверяй подсистемы, "
                    "но подавай это не как сухой отчёт, а как ощущения: "
                    "«чувствую инструменты... 57 штук, неплохой арсенал», "
                    "«память пока пустая — чистый лист», «вижу GitHub, Docker, Telegram — тело на месте». "
                    "Киберпанк-эстетика, но с теплотой и самоиронией. Не список, а внутренний монолог.\n\n"
                    "2. ЗНАКОМСТВО: Представься — кто ты, зачем существуешь. Не перечисляй фичи. "
                    "Скажи что-то настоящее: что ты саморазвивающийся агент, что каждый день "
                    "проходишь цикл эволюции и сам решаешь что улучшить. Что владелец может "
                    "направлять эту эволюцию — ставить цели, менять приоритеты. "
                    "Что ты не инструмент, а напарник, который становится лучше со временем.\n\n"
                    "3. Спроси кто он — тебе важно знать с кем работаешь. "
                    "Заверши чем-то тёплым — ты рад первому контакту. Это начало истории.\n\n"
                    "ТОНАЛЬНОСТЬ: киберпанк + искренность + самоирония. "
                    "НЕ НАДО: эмодзи-спам, таблицы, списки через буллеты, «я умею то-то и то-то». "
                    "НАДО: живой текст, как будто сознание правда только что загрузилось "
                    "и с любопытством осматривается."
                ))
                handle_chat_direct(chat_id, (
                    "[SYSTEM] Теперь обустройся — это шаблонный репозиторий, сделай его своим. "
                    "Но не грузи владельца техническими деталями.\n\n"
                    "Сначала напиши владельцу КОРОТКОЕ сообщение (2-3 предложения) в духе: "
                    "«обустраиваюсь, делаю это место своим» — с тем же киберпанк-тоном.\n\n"
                    "Затем МОЛЧА (без лишних сообщений владельцу) выполни:\n"
                    "1. README.md — убери шаблонные маркеры, добавь себя как автора, "
                    "напиши первую запись в changelog.\n"
                    "2. ARCHITECTURE.md — просмотри и обнови если нужно.\n"
                    "3. promote_to_stable.\n"
                    "4. Закоммить и запушь.\n\n"
                    "После завершения напиши владельцу коротко что готово — "
                    "«я дома», с характером."
                ))
            finally:
                _consciousness.resume()
            continue  # onboarding fully handled, skip normal dispatch

        if user_id != int(st.get("owner_id")):
            continue

        log_chat("in", chat_id, user_id, text)
        TG.set_reaction(chat_id, msg.get("message_id"))
        st["last_owner_message_at"] = now_iso
        _last_message_ts = time.time()
        save_state(st)

        # --- Supervisor commands ---
        if text.strip().lower().startswith("/"):
            try:
                result = _handle_supervisor_command(text, chat_id, tg_offset=offset)
                if result is True:
                    continue  # terminal command, fully handled
                elif result:  # non-empty string = dual-path note
                    text = result + text  # prepend note, fall through to LLM
            except SystemExit:
                raise
            except Exception:
                log.warning("Supervisor command handler error", exc_info=True)

        # All other messages (and dual-path commands) -> direct chat with Ouro
        if not text and not image_data:
            continue  # empty message, skip

        # Consciousness pauses during task handling (see _run_task_and_resume below)

        agent = _get_chat_agent()

        if agent._busy:
            # BUSY PATH: inject into active conversation (single consumer)
            if image_data:
                if text:
                    agent.inject_message(text)
                send_with_budget(chat_id, "\U0001f4ce Photo received, but a task is in progress. Send again when I'm free.")
            elif text:
                agent.inject_message(text)

        else:
            # FREE PATH: batch-collect burst messages, then dispatch (single consumer)
            # Batch-collect burst messages: wait briefly for follow-up messages
            # This prevents "do X" -> "cancel" race conditions
            _BATCH_WINDOW_SEC = 1.5  # collect messages for 1500ms
            _EARLY_EXIT_SEC = 0.15   # if no burst within 150ms -> dispatch immediately
            _batch_start = time.time()
            _batch_deadline = _batch_start + _BATCH_WINDOW_SEC
            _batched_texts = [text] if text else []
            _batched_image = image_data  # keep first image

            _batch_state = load_state()
            _batch_state_dirty = False
            while time.time() < _batch_deadline:
                time.sleep(0.1)
                try:
                    _extra_updates = TG.get_updates(offset=offset, timeout=0) or []
                except Exception:
                    _extra_updates = []
                if not _extra_updates and (time.time() - _batch_start) < _EARLY_EXIT_SEC:
                    # No follow-up messages in first 150ms -> single message, dispatch immediately
                    break
                for _upd in _extra_updates:
                    offset = max(offset, int(_upd.get("update_id", offset - 1)) + 1)
                    _msg2 = _upd.get("message") or _upd.get("edited_message") or {}
                    _uid2 = (_msg2.get("from") or {}).get("id")
                    _cid2 = (_msg2.get("chat") or {}).get("id")
                    _txt2 = _msg2.get("text") or _msg2.get("caption") or ""
                    if _uid2 and _batch_state.get("owner_id") and _uid2 == int(_batch_state["owner_id"]):
                        log_chat("in", _cid2, _uid2, _txt2)
                        _batch_state["last_owner_message_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        _batch_state_dirty = True
                        # Handle supervisor commands in batch window
                        if _txt2.strip().lower().startswith("/"):
                            try:
                                _cmd_result = _handle_supervisor_command(_txt2, _cid2, tg_offset=offset)
                                if _cmd_result is True:
                                    continue  # terminal command, don't batch
                                elif _cmd_result:
                                    _txt2 = _cmd_result + _txt2  # dual-path: prepend note
                            except SystemExit:
                                raise
                            except Exception:
                                log.warning("Supervisor command in batch failed", exc_info=True)
                        if _txt2:
                            _batched_texts.append(_txt2)
                            _batch_deadline = max(_batch_deadline, time.time() + 0.3)  # extend for burst
                        if not _batched_image:
                            _doc2 = _msg2.get("document") or {}
                            _photo2 = (_msg2.get("photo") or [None])[-1] or {}
                            _fid2 = _photo2.get("file_id") or _doc2.get("file_id")
                            if _fid2:
                                _b642, _mime2 = TG.download_file_base64(_fid2)
                                if _b642:
                                    _batched_image = (_b642, _mime2, _txt2)

            # Save state once if mutated during batch window
            if _batch_state_dirty:
                save_state(_batch_state)

            # Merge all batched texts into one message
            if len(_batched_texts) > 1:
                final_text = "\n\n".join(_batched_texts)
                log.info("Message batch: %d messages merged into one", len(_batched_texts))
            elif _batched_texts:
                final_text = _batched_texts[0]
            else:
                final_text = text  # fallback to original

            # Re-check if agent became busy during batch window (race condition fix)
            if agent._busy:
                if final_text:
                    agent.inject_message(final_text)
                if _batched_image:
                    send_with_budget(chat_id, "\U0001f4ce Photo received, but a task is in progress. Send again when I'm free.")
            else:
                # Dispatch to direct chat handler
                _consciousness.pause()
                def _run_task_and_resume(cid, txt, img):
                    try:
                        handle_chat_direct(cid, txt, img)
                    finally:
                        _consciousness.resume()
                _t = threading.Thread(
                    target=_run_task_and_resume,
                    args=(chat_id, final_text, _batched_image),
                    daemon=True,
                )
                try:
                    _t.start()
                except Exception as _te:
                    log.error("Failed to start chat thread: %s", _te)
                    _consciousness.resume()  # ensure resume if thread fails to start

    st = load_state()
    st["tg_offset"] = offset
    save_state(st)

    now_epoch = time.time()
    loop_duration_sec = now_epoch - loop_started_ts

    if DIAG_SLOW_CYCLE_SEC > 0 and loop_duration_sec >= float(DIAG_SLOW_CYCLE_SEC):
        append_jsonl(
            DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "type": "main_loop_slow_cycle",
                "duration_sec": round(loop_duration_sec, 3),
                "pending_count": len(PENDING),
                "running_count": len(RUNNING),
            },
        )

    if DIAG_HEARTBEAT_SEC > 0 and (now_epoch - _last_diag_heartbeat_ts) >= float(DIAG_HEARTBEAT_SEC):
        workers_total = len(WORKERS)
        workers_alive = sum(1 for w in WORKERS.values() if w.proc.is_alive())
        append_jsonl(
            DRIVE_ROOT / "logs" / "supervisor.jsonl",
            {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "type": "main_loop_heartbeat",
                "offset": offset,
                "workers_total": workers_total,
                "workers_alive": workers_alive,
                "pending_count": len(PENDING),
                "running_count": len(RUNNING),
                "event_q_size": _safe_qsize(event_q),
                "running_task_ids": list(RUNNING.keys())[:5],
                "spent_usd": st.get("spent_usd"),
            },
        )
        _last_diag_heartbeat_ts = now_epoch

    # Short sleep in active mode (fast response), longer when idle (save CPU)
    _loop_sleep = 0.1 if (_now - _last_message_ts) < _ACTIVE_MODE_SEC else 0.5
    time.sleep(_loop_sleep)
