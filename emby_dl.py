#!/usr/bin/env python3
"""
emby_dl - Download episodes from an Emby media server via the REST API.

Human usage:
    emby_dl <series>                    # First episode of first real season
    emby_dl <series> all                # All episodes of all real seasons
    emby_dl <series> last               # Last episode of last real season
    emby_dl <series> missing            # Everything not yet recorded/on disk
    emby_dl <series> new                # Episodes after the highest recorded one
    emby_dl <series> S2                 # All episodes of season 2
    emby_dl <series> S2 E3              # Season 2, episode 3
    emby_dl <series> S2 E1-4            # Season 2, episodes 1-4
    emby_dl <series> S2 E3-last         # Season 2, episode 3 to the end
    emby_dl <series> S2E3-7             # one-token form
    emby_dl queue <series...>           # Download multiple series in one command
    emby_dl queue -f queue.txt          # Download batch from a queue file
    emby_dl queue add <series> [spec]   # Add to persistent download queue
    emby_dl queue list | run | clear    # Manage and execute persistent queue
    emby_dl plan <series> S2 E1-4       # resolve + sizes + skips, download nothing
    emby_dl status                      # live progress of a running download
    emby_dl verify [series] [--fix]     # re-check every recorded file vs server
    emby_dl watch <series> [spec] --interval 600 --notify
    emby_dl dataset [--stats|--rebuild|--prune]
    emby_dl search <query> | list <series> | config
    emby_dl login --username me         # store a fresh token (password prompt)
    emby_dl auth                        # check server + token health
    emby_dl init                        # interactive or scripted initial setup

AI-agent mode:
    Add --json for machine-readable output. Downloads emit JSON Lines events
    (progress/retry/verdict/summary) so no log scraping is needed.
    --progress=auto|plain|json|none controls progress rendering.

Exit codes:
    0 - success (everything downloaded or already present)
    1 - one or more downloads/verifications failed
    2 - not found (series/season/episode)
    3 - configuration/auth/server-unreachable error
    4 - invalid arguments or ambiguous series name
    5 - another download is already running (lock held)
    6 - insufficient disk space
"""
from __future__ import annotations

import argparse
import collections
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import getpass
import json
import os
import platform
import re
import shutil
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None

VERSION = "1.3.0"
PROG = "emby_dl"
SCHEMA_VERSION = 3

# =============================================================================
# CONFIGURATION
# =============================================================================

DEFAULT_CONFIG: Dict[str, Any] = {
    "server_url": "http://10.10.10.9:8096",
    "server_id": "335a39030db14fc0919f6da76570e4db",
    "user_id": "f5d170effc9b40d4a6183b9288c1d055",
    "access_token": "",
    "username": "دخول الاستراحة 2",
    "downloads_path": "./downloads",
    "download_settings": {
        "speed_limit_bytes": 1000,
        "speed_time_seconds": 1800,
        "max_retries": 5,
        "retry_delay_seconds": 5,
        "retry_backoff_initial_seconds": 15,
        "retry_backoff_max_seconds": 600,
        "resume_enabled": False,
        "resume_stall_seconds": 10,
        "download_mode": "hls",
        "hls_workers": 4,
        "hls_segment_retries": 4,
        "ffmpeg_path": "",
        "disk_reserve_bytes": 2 * 1024 ** 3,
        "api_timeout_seconds": 60,
        "api_retries": 3,
        "head_timeout_seconds": 6,
        "completion_grace_seconds": 15,
        "idle_timeout_seconds": 3600,
    },
    "notifications": {
        "desktop": True,
        "webhook_url": "",
        "telegram_chat_id": "",
        "telegram_bot_token": "",
        "notify_on_start": False,
        "notify_on_complete": True,
        "notify_on_failure": True,
        "stall_alert_seconds": 300,
    },
}

LOCAL_STATE_NAME = ".emby_dl_state.json"
LOCK_NAME = ".emby_dl.lock"
CAPS_NAME = ".emby_dl_caps.json"
CURL_PID_NAME = ".emby_dl_curl.json"


def get_user_config_path() -> str:
    """OS-native user configuration file location."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "emby_dl", "config.json")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "emby_dl", "config.json")


def get_default_downloads_dir() -> str:
    """OS-native default downloads directory (e.g. ~/Downloads/Emby)."""
    home = Path.home()
    return str(home / "Downloads" / "Emby")


def find_config_file(config_arg: Optional[str] = None) -> Optional[str]:
    """Find config file in priority order."""
    if config_arg:
        if os.path.exists(config_arg):
            return os.path.abspath(config_arg)
        return None

    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        "./config.json",
        os.path.join(script_dir, os.pardir, "config.json"),
        os.path.join(script_dir, "config.json"),
        get_user_config_path(),
        os.path.expanduser("~/.config/emby_dl/config.json"),
        "./downloads/.emby_config",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
    return None


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_bash_config(path: str) -> Dict[str, Any]:
    """Load an old bash-style .emby_config file."""
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    mapping = {
        "SERVER_URL": "server_url",
        "SERVER_ID": "server_id",
        "USER_ID": "user_id",
        "ACCESS_TOKEN": "access_token",
        "USERNAME": "username",
    }
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key in mapping:
                config[mapping[key]] = value
    config["_config_dir"] = os.path.dirname(path)
    config["_config_path"] = path
    return config


ENV_OVERRIDES = {
    "EMBY_URL": "server_url",
    "EMBY_TOKEN": "access_token",
    "EMBY_USER_ID": "user_id",
    "EMBY_USERNAME": "username",
    "EMBY_DOWNLOADS_DIR": "downloads_path",
}


def apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    """Apply EMBY_* environment overrides. Precedence: CLI > env > config > defaults."""
    applied = []
    for env_name, key in ENV_OVERRIDES.items():
        value = os.environ.get(env_name)
        if value:
            config[key] = value
            applied.append(env_name)
    config["_env_applied"] = applied
    return config


def load_config(config_arg: Optional[str] = None) -> Dict[str, Any]:
    """Load configuration from file, merge with defaults, then apply env overrides."""
    if config_arg and not os.path.exists(config_arg):
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["_config_dir"] = os.path.dirname(os.path.abspath(config_arg))
        config["_config_path"] = os.path.abspath(config_arg)
        return apply_env_overrides(config)

    config_path = find_config_file(config_arg)
    if not config_path:
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["_config_dir"] = os.getcwd()
        config["_config_path"] = None
        return apply_env_overrides(config)

    if config_path.endswith(".emby_config"):
        config = load_bash_config(config_path)
        return apply_env_overrides(config)

    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        config = _deep_merge(DEFAULT_CONFIG, raw)
        config["_config_dir"] = os.path.dirname(config_path)
        config["_config_path"] = config_path
        return apply_env_overrides(config)
    except (json.JSONDecodeError, OSError) as exc:
        raise ConfigError(f"Error loading config from {config_path}: {exc}")


def get_downloads_path(config: Dict[str, Any]) -> str:
    """Absolute downloads path. Relative paths anchor to the config file."""
    path = config.get("downloads_path")
    if not path:
        return get_default_downloads_dir()
    if not os.path.isabs(path):
        base = config.get("_config_dir") or os.getcwd()
        path = os.path.join(base, path)
    return os.path.abspath(path)


def save_config(config: Dict[str, Any], path: Optional[str] = None) -> str:
    """Persist config atomically with owner-only permissions."""
    target = path or config.get("_config_path")
    if not target:
        target = get_user_config_path()
    payload = {k: v for k, v in config.items() if not k.startswith("_")}
    atomic_write_json(target, payload, mode=0o600)
    config["_config_path"] = target
    config["_config_dir"] = os.path.dirname(target)
    return target


# =============================================================================
# ERRORS, OUTPUT AND SMALL UTILITIES
# =============================================================================

class ToolError(Exception):
    exit_code = 1


class NotFoundError(ToolError):
    exit_code = 2


class ConfigError(ToolError):
    exit_code = 3


class ServerError(ToolError):
    exit_code = 3


class AuthError(ConfigError):
    exit_code = 3


class HlsExpired(ToolError):
    """The server dropped the HLS playback session; refresh and retry."""


class UsageError(ToolError):
    exit_code = 4


class LockBusy(ToolError):
    exit_code = 5


class DiskFull(ToolError):
    exit_code = 6


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def human_bytes(value: Optional[float]) -> str:
    if value is None:
        return "?"
    value = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} TiB"


def human_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0 or seconds != seconds:
        return "--:--"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip() or "Unknown"


def normalize_name(name: str) -> str:
    name = unicodedata.normalize("NFC", name or "")
    return " ".join(name.strip().casefold().split())


def atomic_write_json(path: str, data: Dict[str, Any], mode: Optional[int] = None) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        if mode is not None:
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def emit_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def emit_json_event(kind: str, **fields: Any) -> None:
    event = {"event": kind, "ts": now_iso()}
    event.update(fields)
    emit_json(event)


def info(message: str) -> None:
    print(message, flush=True)


def warn(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def parse_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


# =============================================================================
# NOTIFICATIONS (desktop + generic/Telegram webhook)
# =============================================================================

def send_webhook(url: str, title: str, message: str,
                 telegram_chat_id: str = "", timeout: int = 10) -> bool:
    if not url:
        return False
    try:
        if "api.telegram.org" in url:
            payload = {"chat_id": telegram_chat_id or "", "text": f"{title}\n{message}"}
        else:
            payload = {"title": title, "text": message, "message": f"{title}\n{message}"}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(1024)
        return True
    except Exception:
        return False


# =============================================================================
# TELEGRAM BOT (notifications + two-way commands)
# =============================================================================

def telegram_api(token: str, method: str, params: Optional[Dict[str, Any]] = None,
                 timeout: int = 30) -> Dict[str, Any]:
    """Call a Telegram Bot API method. Returns parsed JSON or {'ok': False}."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    if params:
        data = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(url, data=data,
                                    method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        parsed = json.loads(raw.decode("utf-8", "replace"))
        return parsed if isinstance(parsed, dict) else {"ok": False}
    except Exception:
        return {"ok": False}


def telegram_send_message(token: str, chat_id: Any, text: str) -> bool:
    if not token or not chat_id or not text:
        return False
    try:
        result = telegram_api(token, "sendMessage",
                              {"chat_id": chat_id, "text": text}, timeout=15)
        return bool(result.get("ok"))
    except Exception:
        return False


def configure_telegram_bot(token: str) -> None:
    """Ensure bot commands, descriptions, and polling readiness are configured on Telegram."""
    try:
        telegram_api(token, "deleteWebhook", {"drop_pending_updates": "false"})
    except Exception:
        pass
    try:
        telegram_api(token, "setMyCommands", {
            "commands": json.dumps([
                {"command": "start", "description": "Register this chat for notifications"},
                {"command": "status", "description": "Show current download progress & speed"},
                {"command": "queue", "description": "Show detailed queue information"},
                {"command": "help", "description": "List available commands"},
                {"command": "ping", "description": "Check bot responsiveness"},
            ]),
        })
    except Exception:
        pass
    try:
        telegram_api(token, "setMyDescription", {
            "description": "بوت إدارة ومتابعة تنزيل المسلسلات عبر أداة emby-dl.\nالأوامر المتاحة: /status, /queue, /help, /ping"
        })
        telegram_api(token, "setMyShortDescription", {
            "short_description": "بوت إشعارات وأوامر تحميل Emby (emby-dl)"
        })
    except Exception:
        pass


def run_telegram_bot(config: Dict[str, Any],
                     progress_state: Dict[str, Any],
                     stop_event: threading.Event) -> None:
    """Long-poll Telegram for commands and reply. Runs in a daemon thread."""
    settings = config.get("notifications", {})
    if not isinstance(settings, dict):
        settings = {}
        config["notifications"] = settings
    token = settings.get("telegram_bot_token") or ""
    if not token:
        return
    configure_telegram_bot(token)
    authorized_chat = str(settings.get("telegram_chat_id") or "")
    offset = 0
    last_config_check = time.monotonic()

    while not stop_event.is_set():
        now = time.monotonic()
        if now - last_config_check >= 30:
            last_config_check = now
            try:
                cfg_path = config.get("_config_path")
                if cfg_path and os.path.exists(cfg_path):
                    fresh_cfg = load_config(cfg_path)
                    new_token = (fresh_cfg.get("notifications") or {}).get("telegram_bot_token") or ""
                    new_chat = (fresh_cfg.get("notifications") or {}).get("telegram_chat_id") or ""
                    if new_token and new_token != token:
                        token = new_token
                        settings["telegram_bot_token"] = token
                        configure_telegram_bot(token)
                        offset = 0
                    if new_chat:
                        authorized_chat = str(new_chat)
                        settings["telegram_chat_id"] = authorized_chat
            except Exception:
                pass

        try:
            upd = telegram_api(token, "getUpdates",
                               {"offset": offset, "timeout": 20})
        except Exception:
            upd = {"ok": False}
        if not upd.get("ok"):
            stop_event.wait(5)
            continue
        for update in (upd.get("result") or []):
            try:
                uid = int(update.get("update_id", 0))
                offset = uid + 1
            except (TypeError, ValueError):
                pass
            try:
                msg = update.get("message") or update.get("edited_message") or {}
                chat = msg.get("chat") or {}
                chat_id = str(chat.get("id", ""))
                text = (msg.get("text") or "").strip()
                if not chat_id or not text:
                    continue
                if authorized_chat and chat_id != authorized_chat:
                    continue
                if text.startswith("/start"):
                    if not authorized_chat:
                        authorized_chat = chat_id
                        settings["telegram_chat_id"] = authorized_chat
                        try:
                            save_config(config)
                        except Exception:
                            pass
                        telegram_send_message(
                            token, chat_id,
                            "Registered. You will receive download notifications here.\n"
                            "Commands: /status, /queue, /help, /ping")
                    else:
                        telegram_send_message(
                            token, chat_id,
                            "Already registered.\n"
                            "Commands: /status, /queue, /help, /ping")
                    continue
                if not authorized_chat:
                    telegram_send_message(token, chat_id,
                                         "Send /start to register this chat.")
                    continue
                if text.startswith("/status"):
                    st = read_state(get_downloads_path(config))
                    st = st if isinstance(st, dict) else {}
                    cur_label = st.get("label") or "\u2014"
                    cur_pct = st.get("percent")
                    pct_str = (f"{cur_pct:.1f}%" if isinstance(cur_pct, (int, float))
                               else "\u2014")
                    running = bool(st.get("running"))
                    stalled_for = int(progress_state.get("stalled_for") or 0)
                    if running and stalled_for >= 120:
                        state_word = "stalled"
                    else:
                        state_word = "running" if running else "idle"
                    completed = int(progress_state.get("completed", 0) or 0)
                    total = int(progress_state.get("total", 0) or 0)
                    lines = [
                        f"Status: {state_word}",
                        f"Completed: {completed}/{total} "
                        f"(remaining: {max(0, total - completed)})",
                        f"Current: {cur_label} ({pct_str})",
                    ]
                    qpath = os.path.join(get_downloads_path(config), ".queue_state.json")
                    if os.path.exists(qpath):
                        try:
                            with open(qpath, "r", encoding="utf-8") as qf:
                                qst = json.load(qf)
                            if isinstance(qst, dict) and qst.get("running"):
                                cur_idx = qst.get("current_index", 1)
                                tot_series = qst.get("total_series", 1)
                                q_tot = qst.get("total_eps", 0)
                                target_ids = set(str(x) for x in qst.get("target_series_ids", []))
                                d_path = os.path.join(get_downloads_path(config), "downloaded_dataset.json")
                                live_done = 0
                                if os.path.exists(d_path) and target_ids:
                                    try:
                                        with open(d_path, "r", encoding="utf-8") as dh:
                                            d_data = json.load(dh)
                                        live_done = sum(1 for e in d_data.get("downloads", []) if str(e.get("series_id")) in target_ids)
                                    except Exception:
                                        live_done = 0
                                if not live_done:
                                    base_done = qst.get("queue_completed_eps", 0)
                                    live_done = base_done + completed
                                pct_q = f" ({round(live_done * 100.0 / q_tot, 1)}%)" if q_tot else ""
                                lines.insert(1, f"Queue: [{cur_idx}/{tot_series}] {qst.get('current_name', '')}")
                                if q_tot:
                                    lines.insert(2, f"Total Queue: {live_done}/{q_tot} episodes{pct_q}")
                        except Exception:
                            pass
                    downloaded = st.get("downloaded")
                    expected = st.get("total")
                    if isinstance(downloaded, int) and isinstance(expected, int):
                        lines.append(
                            f"Downloaded: {human_bytes(downloaded)} / "
                            f"{human_bytes(expected)}")
                    speed = st.get("speed_bps")
                    if isinstance(speed, (int, float)):
                        lines.append(f"Speed: {human_bytes(speed)}/s")
                    eta = st.get("eta_seconds")
                    if (eta is None or eta == 0) and isinstance(speed, (int, float)) and speed > 0:
                        if isinstance(downloaded, int) and isinstance(expected, int) and expected > downloaded:
                            eta = int((expected - downloaded) / speed)
                    if eta is not None and running:
                        lines.append(f"ETA: {human_duration(eta)}")
                    if running and stalled_for >= 120:
                        lines.append(f"No progress for {stalled_for // 60} min")
                    telegram_send_message(token, chat_id, "\n".join(lines))
                elif text.startswith("/queue") or text.startswith("/q"):
                    qpath = os.path.join(get_downloads_path(config), ".queue_state.json")
                    d_path = os.path.join(get_downloads_path(config), "downloaded_dataset.json")
                    d_data = {}
                    if os.path.exists(d_path):
                        try:
                            with open(d_path, "r", encoding="utf-8") as dh:
                                d_data = json.load(dh)
                        except Exception:
                            d_data = {}
                    all_downloads = d_data.get("downloads", [])

                    qst = None
                    if os.path.exists(qpath):
                        try:
                            with open(qpath, "r", encoding="utf-8") as qf:
                                qst = json.load(qf)
                        except Exception:
                            qst = None

                    q_lines = []
                    if qst and isinstance(qst, dict) and qst.get("running"):
                        cur_idx = qst.get("current_index", 1)
                        tot_series = qst.get("total_series", 1)
                        cur_name = qst.get("current_name", "—")
                        q_tot = qst.get("total_eps", 0)
                        target_ids = set(str(x) for x in qst.get("target_series_ids", []))

                        live_done = sum(1 for e in all_downloads if str(e.get("series_id")) in target_ids)
                        if not live_done:
                            base_done = qst.get("queue_completed_eps", 0)
                            live_done = base_done + int(progress_state.get("completed", 0) or 0)

                        pct_q = round(live_done * 100.0 / q_tot, 1) if q_tot else 0

                        q_lines.append("📋 تفاصيل طابور التحميل (Queue Details)")
                        q_lines.append("الحالة: قيد التشغيل 🟢")
                        q_lines.append(f"المسلسل الحالي: [{cur_idx}/{tot_series}] {cur_name}")
                        q_lines.append(f"إجمالي تقدم الطابور: {live_done}/{q_tot} حلقة ({pct_q}%)")
                        q_lines.append("")

                        series_list = qst.get("series_list", [])
                        if series_list:
                            q_lines.append("قائمة المسلسلات في الطابور:")
                            for idx, s_item in enumerate(series_list, start=1):
                                s_id = str(s_item.get("id") or "")
                                s_name = s_item.get("name") or "—"
                                s_spec = s_item.get("spec") or "all"
                                s_total = s_item.get("total", 0)
                                s_size = s_item.get("size_bytes")
                                size_str = f" (~{human_bytes(s_size)})" if s_size else ""

                                s_done = sum(1 for e in all_downloads if str(e.get("series_id")) == s_id)
                                if s_total and s_done >= s_total:
                                    icon = "✅"
                                elif idx == cur_idx:
                                    icon = "⏳"
                                elif s_done > 0:
                                    icon = "🔄"
                                else:
                                    icon = "🕒"

                                q_lines.append(f"{idx}. {icon} {s_name} [{s_spec}] — ({s_done}/{s_total} حلقات){size_str}")

                            remaining_eps = max(0, q_tot - live_done)
                            q_lines.append("")
                            q_lines.append(f"المتبقي في الطابور: {remaining_eps} حلقة")
                        else:
                            q_lines.append(f"• المسلسل الحالي: {cur_name}")
                            q_lines.append(f"• المتبقي في الطابور: {max(0, q_tot - live_done)} حلقة")
                    else:
                        p_items = load_persistent_queue(get_downloads_path(config))
                        if p_items:
                            q_lines.append("📋 الطابور الدائم (في الانتظار):")
                            for idx, it in enumerate(p_items, start=1):
                                q_lines.append(f"{idx}. 🕒 {it.series} [{' '.join(it.spec)}]")
                            q_lines.append("")
                            q_lines.append(f"إجمالي المسلسلات المنتظرة: {len(p_items)}")
                            q_lines.append("لتشغيل الطابور: emby-dl queue run")
                        elif qst and isinstance(qst, dict) and qst.get("status") == "completed":
                            tot = qst.get("total_eps", 0)
                            done = qst.get("queue_completed_eps", 0)
                            ended = qst.get("ended_at", "—")
                            q_lines.append("🎉 اكتمل تنفيذ آخر طابور بنجاح!")
                            q_lines.append(f"• الحلقات المكتملة: {done}/{tot} حلقة (100%)")
                            q_lines.append(f"• انتهى في: {ended}")
                        else:
                            q_lines.append("لا يوجد طابور تحميل نشط حالياً.")
                            q_lines.append("لإضافة مسلسلات للطابور: emby-dl queue add <series> [spec...]")

                    telegram_send_message(token, chat_id, "\n".join(q_lines))
                elif text.startswith("/help"):
                    telegram_send_message(
                        token, chat_id,
                        "Commands:\n"
                        "/status - Current download progress, speed & ETA\n"
                        "/queue - Detailed queue breakdown & series list\n"
                        "/ping - Pong (bot responsiveness)\n"
                        "/help - This message"
                    )
                elif text.startswith("/ping"):
                    telegram_send_message(token, chat_id, "pong")
                else:
                    telegram_send_message(token, chat_id, "Unknown command. /help")
            except Exception:
                continue



def notify(config: Dict[str, Any], title: str, message: str,
           urgency: str = "normal", force: bool = False) -> None:
    settings = config.get("notifications", {})
    if not force and not settings.get("desktop") and not settings.get("webhook_url") and not settings.get("telegram_bot_token"):
        return
    if settings.get("desktop"):
        if os.name == "nt" or sys.platform == "win32":
            sender = shutil.which("powershell") or shutil.which("powershell.exe")
            if sender:
                def _ps_escape(value: str) -> str:
                    return str(value).replace("'", "''")
                ps_script = (
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "$n = New-Object System.Windows.Forms.NotifyIcon; "
                    "$n.Icon = [System.Drawing.SystemIcons]::Information; "
                    "$n.BalloonTipIcon = 'Info'; "
                    "$n.BalloonTipTitle = '%s'; " % _ps_escape(title)
                    + "$n.BalloonTipText = '%s'; " % _ps_escape(message)
                    + "$n.Visible = $true; "
                    "$n.ShowBalloonTip(5000); "
                    "Start-Sleep -Seconds 6; "
                    "$n.Dispose()"
                )
                try:
                    subprocess.Popen(
                        [sender, "-WindowStyle", "Hidden", "-Command", ps_script],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    pass
        else:
            sender = shutil.which("notify-send")
            if sender:
                env = dict(os.environ)
                env.setdefault("DISPLAY", ":0")
                try:
                    subprocess.run(
                        [sender, "-a", PROG, "-u", urgency, title, message],
                        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=10, check=False,
                    )
                except Exception:
                    pass
    bot_token = settings.get("telegram_bot_token") or ""
    chat_id = settings.get("telegram_chat_id") or ""
    if bot_token and chat_id:
        telegram_send_message(bot_token, chat_id, f"{title}\n{message}")
    url = settings.get("webhook_url") or ""
    if url:
        send_webhook(url, title, message, settings.get("telegram_chat_id", ""))


# =============================================================================
# SERIES RESOLUTION CACHE
# =============================================================================

SERIES_CACHE_NAME = ".emby_series_cache.json"


class SeriesCache:
    """Persistent mapping of normalized series names to Emby series IDs.

    Prevents expensive full-library text searches and timeouts by caching
    known series IDs locally. Automatically seeds from downloaded_dataset.json.
    """

    def __init__(self, downloads_path: str):
        self.downloads_path = downloads_path
        self.path = os.path.join(downloads_path, SERIES_CACHE_NAME)
        self._cache: Optional[Dict[str, str]] = None

    def _load(self) -> Dict[str, str]:
        if self._cache is not None:
            return self._cache
        cache: Dict[str, str] = {}
        d_path = os.path.join(self.downloads_path, "downloaded_dataset.json")
        if os.path.exists(d_path):
            try:
                with open(d_path, "r", encoding="utf-8") as dh:
                    d_data = json.load(dh)
                for entry in d_data.get("downloads", []):
                    s_name = entry.get("series_name")
                    s_id = entry.get("series_id")
                    if s_name and s_id:
                        cache[normalize_name(s_name)] = str(s_id)
            except Exception:
                pass
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    for k, v in data.items():
                        cache[normalize_name(k)] = str(v)
            except Exception:
                pass
        self._cache = cache
        return self._cache

    def get(self, query: str) -> Optional[str]:
        cache = self._load()
        return cache.get(normalize_name(query))

    def set(self, query: str, series_id: str) -> None:
        cache = self._load()
        norm = normalize_name(query)
        cache[norm] = str(series_id)
        try:
            atomic_write_json(self.path, cache, mode=0o600)
        except Exception:
            pass


# =============================================================================
# EMBY API CLIENT
# =============================================================================

class EmbyAPI:
    """Small Emby REST client with health-gated retries."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.server_url = (config.get("server_url") or "").rstrip("/")
        self.token = config.get("access_token") or ""
        self.user_id = config.get("user_id") or ""
        self.settings = config.get("download_settings", DEFAULT_CONFIG["download_settings"])
        self.timeout = int(self.settings.get("api_timeout_seconds", 60))
        self.api_retries = int(self.settings.get("api_retries", 3))
        try:
            dl_path = get_downloads_path(config)
            self._series_cache = SeriesCache(dl_path)
        except Exception:
            self._series_cache = None
        if not self.server_url:
            raise ConfigError("Missing server_url in configuration")
        if not self.token and self.server_url and self.config.get("username"):
            try:
                auth_data = authenticate(self.server_url, self.config["username"], "")
                self.token = auth_data.get("AccessToken") or ""
                user = auth_data.get("User") or {}
                if not self.user_id:
                    self.user_id = user.get("Id") or ""
                self.config["access_token"] = self.token
                self.config["user_id"] = self.user_id
            except Exception:
                pass
        if not self.token:
            raise ConfigError("Missing access_token in configuration (run: emby_dl login --username ...)")

    # -- low-level ---------------------------------------------------------

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.server_url}{path}"

    def _backoff(self, attempt: int) -> None:
        initial = float(self.settings.get("retry_backoff_initial_seconds", 15))
        maximum = float(self.settings.get("retry_backoff_max_seconds", 600))
        delay = min(initial * (2 ** max(0, attempt)), maximum)
        if delay <= 0:
            return
        time.sleep(delay)
        # Do not hammer a server that just restarted: wait until it answers,
        # but never longer than the configured cap in one gate.
        deadline = time.monotonic() + maximum
        while time.monotonic() < deadline and not self.check_health(timeout=5):
            time.sleep(min(15.0, max(1.0, deadline - time.monotonic())))

    def _request_json(self, path: str, timeout: Optional[int] = None,
                      retries: Optional[int] = None) -> Any:
        url = self._url(path)
        attempts = self.api_retries if retries is None else retries
        last_error: Optional[Exception] = None
        for attempt in range(attempts + 1):
            request = urllib.request.Request(
                url,
                headers={
                    "X-Emby-Token": self.token,
                    "Accept": "application/json",
                    "User-Agent": f"{PROG}/{VERSION}",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                    raw = response.read()
                return json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise AuthError(
                        f"Authentication failed (HTTP {exc.code}). Run: emby_dl login --username ..."
                    )
                if exc.code >= 500:
                    last_error = ServerError(f"Server error HTTP {exc.code} for {path}")
                    if attempt < attempts:
                        self._backoff(attempt)
                        continue
                    raise last_error
                raise ServerError(f"Server returned HTTP {exc.code} for {path}")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                reason = getattr(exc, "reason", str(exc))
                last_error = ServerError(f"Cannot connect to server: {reason}")
                if attempt < attempts:
                    self._backoff(attempt)
                    continue
                raise last_error
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ServerError(f"Invalid JSON from server for {path}: {exc}")
        raise last_error or ServerError(f"Request failed: {path}")

    def check_health(self, timeout: int = 5) -> bool:
        try:
            request = urllib.request.Request(
                self._url("/System/Info/Public"),
                headers={"User-Agent": f"{PROG}/{VERSION}"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response.read(1024)
            return True
        except Exception:
            return False

    # -- read endpoints ----------------------------------------------------

    def search_series(self, query: str, limit: int = 30) -> List[Dict[str, Any]]:
        quoted = urllib.parse.quote(query)
        data = self._request_json(
            f"/Items?SearchTerm={quoted}&Recursive=true&IncludeItemTypes=Series&Limit={limit}"
            "&Fields=ProductionYear,ChildCount",
        )
        return data.get("Items", []) if isinstance(data, dict) else []

    def get_series_seasons(self, series_id: str) -> List[Dict[str, Any]]:
        data = self._request_json(f"/Shows/{series_id}/Seasons?Fields=Path")
        return data.get("Items", []) if isinstance(data, dict) else []

    def get_season_episodes(self, series_id: str, season_id: str) -> List[Dict[str, Any]]:
        data = self._request_json(
            f"/Shows/{series_id}/Episodes?seasonId={season_id}"
            "&Fields=Path,MediaSources,Size"
        )
        return data.get("Items", []) if isinstance(data, dict) else []

    def get_item(self, item_id: str) -> Dict[str, Any]:
        data = self._request_json(f"/Items?Ids={urllib.parse.quote(item_id)}&Fields=Path,MediaSources,Size")
        items = data.get("Items", []) if isinstance(data, dict) else []
        return items[0] if items else {}

    def get_user(self) -> Dict[str, Any]:
        # This Emby build returns HTTP 500 "Unrecognized Guid format" for
        # /Users/Me, so prefer the concrete user id when we have one.
        path = f"/Users/{self.user_id}" if self.user_id else "/Users/Me"
        data = self._request_json(path)
        return data if isinstance(data, dict) else {}

    def get_public_info(self) -> Dict[str, Any]:
        data = self._request_json("/System/Info/Public", retries=0)
        return data if isinstance(data, dict) else {}

    # -- sizes and URLs ----------------------------------------------------

    def get_download_url(self, item_id: str, media_source_id: str = "") -> str:
        url = f"{self.server_url}/Videos/{item_id}/stream?static=true"
        if media_source_id:
            url += f"&MediaSourceId={urllib.parse.quote(str(media_source_id))}"
        return url

    @staticmethod
    def media_sources(item: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [source for source in (item.get("MediaSources") or [])
                if isinstance(source, dict)]

    @classmethod
    def select_media_source(cls, item: Dict[str, Any], source_spec: str = "1"
                            ) -> Tuple[Optional[Dict[str, Any]], int]:
        """Return (source, 1-based index) for --source; None means server default."""
        sources = cls.media_sources(item)
        if not sources:
            if str(source_spec) == "1":
                return None, 0
            raise UsageError(
                f"Source {source_spec} is not available: the server lists no media "
                "sources for this episode"
            )
        if str(source_spec) == "max":
            index = max(
                range(len(sources)),
                key=lambda i: parse_int(sources[i].get("Size")) or -1,
            )
        else:
            index = int(source_spec) - 1
            if index < 0 or index >= len(sources):
                lines = []
                for position, source in enumerate(sources, 1):
                    name = source.get("Name") or os.path.basename(source.get("Path") or "")
                    size = human_bytes(parse_int(source.get("Size")))
                    source_id = source.get("Id") or "?"
                    lines.append(f"  {position}. {name or '(unnamed)'} "
                                  f"[{size}] id={source_id}")
                raise UsageError(
                    f"Source {source_spec} does not exist; this episode has "
                    f"{len(sources)} source(s). Candidates:\n" + "\n".join(lines)
                )
        return sources[index], index + 1

    @staticmethod
    def source_size(source: Optional[Dict[str, Any]]) -> Optional[int]:
        if not source:
            return None
        size = parse_int(source.get("Size"))
        return size if size and size > 0 else None

    @classmethod
    def item_size(cls, item: Dict[str, Any], source_spec: str = "1") -> Optional[int]:
        source, _ = cls.select_media_source(item, source_spec)
        if source is None:
            for candidate in item.get("MediaSources") or []:
                size = parse_int(candidate.get("Size"))
                if size:
                    return size
            return parse_int(item.get("Size"))
        return cls.source_size(source)

    def head_size(self, item_id: str, media_source_id: str = "") -> Optional[int]:
        """Last-resort size probe. The server can advertise HEAD but hang on it."""
        url = self.get_download_url(item_id, media_source_id)
        request = urllib.request.Request(
            url, method="HEAD",
            headers={"X-Emby-Token": self.token, "User-Agent": f"{PROG}/{VERSION}"},
        )
        try:
            with urllib.request.urlopen(
                request, timeout=int(self.settings.get("head_timeout_seconds", 6))
            ) as response:
                length = response.headers.get("Content-Length")
                return int(length) if length and length.isdigit() else None
        except Exception:
            return None

    def resolve_size(self, item: Dict[str, Any], allow_head: bool = True,
                     source_spec: str = "1") -> Optional[int]:
        """Resolve exact byte size from the selected source, item detail, then HEAD."""
        source, _ = self.select_media_source(item, source_spec)
        size = self.source_size(source)
        if size:
            return size
        item_id = item.get("Id")
        if item_id:
            detailed = self.get_item(item_id)
            detailed_source, _ = self.select_media_source(detailed, source_spec)
            size = self.source_size(detailed_source)
            if size:
                return size
            if allow_head:
                media_source_id = (source or {}).get("Id", "")
                return self.head_size(item_id, str(media_source_id or ""))
        return None

    def resolve_series(self, query: str, series_id: Optional[str] = None) -> Dict[str, Any]:
        """Resolve a series name, refusing ambiguous matches (exit 4).

        Checks local series cache first to bypass expensive server-side searches.
        """
        # 1. Explicit series_id provided by user
        if series_id:
            item = self.get_item(series_id)
            if not item:
                raise NotFoundError(f"Series id not found: {series_id}")
            if item.get("Type") and item.get("Type") != "Series":
                raise UsageError(f"Item {series_id} is not a Series (type={item.get('Type')})")
            if self._series_cache and item.get("Name"):
                self._series_cache.set(item["Name"], str(series_id))
                self._series_cache.set(query, str(series_id))
            return item

        # 2. Check local series cache & dataset (instant 0.02s lookup)
        if self._series_cache:
            cached_id = self._series_cache.get(query)
            if cached_id:
                try:
                    item = self.get_item(cached_id)
                    if item and item.get("Type") == "Series" and normalize_name(item.get("Name", "")) == normalize_name(query):
                        return item
                except Exception:
                    pass

        # 3. Fall back to search
        results = self.search_series(query)
        if not results:
            raise NotFoundError(f"Series not found: {query}")
        wanted = normalize_name(query)
        exact = [r for r in results if normalize_name(r.get("Name", "")) == wanted]

        resolved_item = None
        if len(exact) == 1:
            resolved_item = exact[0]
        elif len(exact) > 1:
            candidates = exact
        elif len(results) == 1:
            resolved_item = results[0]
        else:
            candidates = results

        if resolved_item:
            if self._series_cache and resolved_item.get("Id"):
                self._series_cache.set(query, str(resolved_item["Id"]))
                if resolved_item.get("Name"):
                    self._series_cache.set(resolved_item["Name"], str(resolved_item["Id"]))
            return resolved_item

        lines = []
        for item in candidates[:15]:
            year = f" ({item.get('ProductionYear')})" if item.get("ProductionYear") else ""
            lines.append(f"  {item.get('Id')}  {item.get('Name')}{year}")
        listing = "\n".join(lines)
        raise UsageError(
            f"Ambiguous series name: {query!r}. Refusing to guess. "
            f"Re-run with --series-id <id>. Candidates:\n{listing}"
        )


def authenticate(server_url: str, username: str, password: str) -> Dict[str, Any]:
    """Authenticate with Emby and return the raw response payload."""
    server_url = server_url.rstrip("/")
    node_name = platform.node() or "device"
    auth_header = (
        f'MediaBrowser Client="{PROG}", Device="CLI", '
        f'DeviceId="{PROG}-{node_name}", Version="{VERSION}"'
    )
    body = json.dumps({"Username": username, "Pw": password}).encode("utf-8")
    request = urllib.request.Request(
        f"{server_url}/Users/AuthenticateByName", data=body,
        headers={
            "Content-Type": "application/json",
            "X-Emby-Authorization": auth_header,
            "User-Agent": f"{PROG}/{VERSION}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AuthError("Login rejected: wrong username or password")
        raise ServerError(f"Login failed with HTTP {exc.code}")
    except urllib.error.URLError as exc:
        raise ServerError(f"Cannot connect to server: {exc.reason}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServerError(f"Login returned invalid JSON: {exc}")


# =============================================================================
# DATASET MANAGEMENT (JSON, version 2)
# =============================================================================

class DatasetManager:
    """Central + per-series datasets with exact byte sizes (schema version 3).

    The central file is the single commit point. Per-series files are derived
    views; if one goes stale, ``dataset --check`` reports it and
    ``dataset --sync`` regenerates it from the central dataset.
    """

    FILENAME = "downloaded_dataset.json"

    def __init__(self, downloads_path: str):
        self.downloads_path = os.path.abspath(downloads_path)
        self.central_path = os.path.join(self.downloads_path, self.FILENAME)
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_signature: Optional[Tuple[int, int]] = None

    # -- paths -------------------------------------------------------------

    def series_dir(self, series_name: str) -> str:
        return os.path.join(self.downloads_path, sanitize_filename(series_name))

    def series_path(self, series_name: str) -> str:
        return os.path.join(self.series_dir(series_name), self.FILENAME)

    def disk_path(self, entry: Dict[str, Any]) -> str:
        relative = entry.get("relative_path") or ""
        if relative:
            return os.path.join(self.downloads_path, relative)
        series = entry.get("series_name", "Unknown")
        season = int(entry.get("season_number") or 0)
        file_name = entry.get("file_name") or ""
        return os.path.join(self.series_dir(series), f"Season {season}", file_name)

    # -- read/write --------------------------------------------------------

    def _normalize_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        entry.setdefault("series_name", "Unknown")
        entry.setdefault("series_id", "")
        entry.setdefault("season_id", "")
        entry.setdefault("episode_id", "")
        entry.setdefault("media_source_id", "")
        entry.setdefault("source_index", None)
        entry.setdefault("source_name", "")
        entry.setdefault("season_number", 0)
        entry.setdefault("episode_number", 0)
        entry.setdefault("attempts", 0)
        entry.setdefault("tool_version", "")
        entry.setdefault("container", "")
        size_bytes = parse_int(entry.get("size_bytes"))
        if not size_bytes:
            path = self.disk_path(entry)
            if os.path.exists(path):
                size_bytes = os.path.getsize(path)
            else:
                size_mb = parse_int(entry.get("size_mb"))
                size_bytes = int(round(size_mb * 1048576)) if size_mb else None
            if size_bytes:
                entry["size_bytes"] = size_bytes
        if size_bytes:
            entry["size_mb"] = int(round(size_bytes / 1048576))
        return entry

    def _normalize(self, data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            data = {}
        data.setdefault("downloads", [])
        if not isinstance(data["downloads"], list):
            data["downloads"] = []
        data["downloads"] = [
            self._normalize_entry(entry)
            for entry in data["downloads"] if isinstance(entry, dict)
        ]
        data["version"] = SCHEMA_VERSION
        data["total_episodes"] = len(data["downloads"])
        return data

    def _read_json(self, path: str) -> Dict[str, Any]:
        if not os.path.exists(path):
            return self._normalize({"downloads": []})
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return self._normalize(json.load(handle))
        except (json.JSONDecodeError, OSError):
            return self._normalize({"downloads": []})

    def _signature(self) -> Tuple[int, int]:
        try:
            stat = os.stat(self.central_path)
            return (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return (0, 0)

    def _raw_central(self) -> Dict[str, Any]:
        signature = self._signature()
        if self._cache is not None and self._cache_signature == signature:
            return self._cache
        data = self._read_json(self.central_path)
        # Re-read the signature: the file may have appeared between calls.
        self._cache = data
        self._cache_signature = self._signature()
        return data

    def central(self) -> Dict[str, Any]:
        # Callers mutate the result before writing, so never hand out the cache.
        return copy.deepcopy(self._raw_central())

    def write_central(self, data: Dict[str, Any]) -> None:
        data = self._normalize(data)
        data["updated_at"] = now_iso()
        atomic_write_json(self.central_path, data)
        self._cache = None
        self._cache_signature = None

    def _write_series(self, series_name: str, entries: List[Dict[str, Any]],
                      series_id: str = "") -> None:
        data = self._normalize({"downloads": entries})
        data["series_name"] = series_name
        data["series_id"] = series_id
        data["updated_at"] = now_iso()
        atomic_write_json(self.series_path(series_name), data)

    @staticmethod
    def _group_entries(downloads: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for entry in downloads:
            groups.setdefault(entry.get("series_name", "Unknown"), []).append(entry)
        return groups

    def _write_series_groups(self, downloads: Iterable[Dict[str, Any]]) -> None:
        for series_name, entries in self._group_entries(downloads).items():
            series_id = next((e.get("series_id") for e in entries
                              if e.get("series_id")), "")
            try:
                self._write_series(series_name, entries, series_id)
            except OSError as exc:
                # Central is already committed; a stale derived file is
                # recoverable with `dataset --sync`.
                warn(f"Warning: could not update per-series dataset for "
                     f"{series_name}: {exc} (run: dataset --sync)")

    # -- lookups -----------------------------------------------------------

    @staticmethod
    def key(series_name: str, season: int, episode: int) -> Tuple[str, int, int]:
        """Legacy name key, retained for external callers."""
        return (normalize_name(series_name), int(season), int(episode))

    @staticmethod
    def _matches(entry: Dict[str, Any], series_id: str, series_name: str,
                 season: int, episode: int) -> bool:
        if int(entry.get("season_number") or 0) != int(season):
            return False
        if int(entry.get("episode_number") or 0) != int(episode):
            return False
        entry_id = str(entry.get("series_id") or "")
        wanted_id = str(series_id or "")
        if wanted_id and entry_id:
            return entry_id == wanted_id
        return normalize_name(entry.get("series_name", "")) == normalize_name(series_name)

    def find_entry(self, series_name: str, season: int, episode: int,
                   series_id: str = "") -> Optional[Dict[str, Any]]:
        for entry in self._raw_central().get("downloads", []):
            if self._matches(entry, series_id, series_name, season, episode):
                return copy.deepcopy(entry)
        return None

    def get_entry(self, series_name: str, season: int, episode: int,
                  series_id: str = "") -> Optional[Dict[str, Any]]:
        return self.find_entry(series_name, season, episode, series_id)

    def has(self, series_name: str, season: int, episode: int,
            series_id: str = "") -> bool:
        entry = self.get_entry(series_name, season, episode, series_id)
        if not entry:
            return False
        return os.path.exists(self.disk_path(entry))

    def entries(self, series_name: Optional[str] = None) -> List[Dict[str, Any]]:
        downloads = copy.deepcopy(self._raw_central().get("downloads", []))
        if series_name:
            wanted = normalize_name(series_name)
            downloads = [e for e in downloads
                         if normalize_name(e.get("series_name", "")) == wanted]
        return sorted(
            downloads,
            key=lambda e: (
                e.get("series_name", ""),
                int(e.get("season_number") or 0),
                int(e.get("episode_number") or 0),
            ),
        )

    # -- mutations ---------------------------------------------------------

    def add_entry(self, entry: Dict[str, Any]) -> None:
        entry = self._normalize_entry(dict(entry))
        series_name = entry.get("series_name", "Unknown")
        series_id = entry.get("series_id", "")
        season = int(entry.get("season_number") or 0)
        episode = int(entry.get("episode_number") or 0)

        central = self.central()
        central["downloads"] = [
            existing for existing in central.get("downloads", [])
            if not self._matches(existing, series_id, series_name, season, episode)
        ]
        central["downloads"].append(entry)
        central["total_episodes"] = len(central["downloads"])
        self.write_central(central)
        self._write_series_groups(central["downloads"])

    def remove_entry(self, series_name: str, season: int, episode: int,
                     series_id: str = "") -> bool:
        central = self.central()
        before = len(central.get("downloads", []))
        central["downloads"] = [
            existing for existing in central.get("downloads", [])
            if not self._matches(existing, series_id, series_name, season, episode)
        ]
        if len(central["downloads"]) == before:
            return False
        self.write_central(central)
        self._write_series_groups(central["downloads"])
        return True


    # -- maintenance -------------------------------------------------------

    @staticmethod
    def _identity(entry: Dict[str, Any]) -> Tuple[str, int, int]:
        """Stable identity: series id when known, normalized name otherwise."""
        series_id = str(entry.get("series_id") or "")
        name_key = normalize_name(entry.get("series_name", ""))
        return (
            series_id or name_key,
            int(entry.get("season_number") or 0),
            int(entry.get("episode_number") or 0),
        )

    def rebuild(self) -> Dict[str, Any]:
        """Scan disk and rebuild central + per-series datasets with exact bytes."""
        old = self.central()
        old_by_key = {
            self.key(e.get("series_name", ""), e.get("season_number", 0),
                     e.get("episode_number", 0)): e
            for e in old.get("downloads", [])
        }

        file_pattern = re.compile(r"^(.+) - S(\d+)E(\d+)\.([A-Za-z0-9]+)$")
        entries: Dict[Tuple[str, int, int], Dict[str, Any]] = {}
        skipped: List[str] = []

        if not os.path.isdir(self.downloads_path):
            raise ConfigError(f"Downloads directory does not exist: {self.downloads_path}")

        for series_dir in sorted(os.listdir(self.downloads_path)):
            if series_dir.startswith("."):
                continue
            series_path = os.path.join(self.downloads_path, series_dir)
            if not os.path.isdir(series_path):
                continue
            for root, dirs, files in os.walk(series_path):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                season_match = re.match(r"^Season\s+(\d+)$", os.path.basename(root))
                if not season_match:
                    continue
                for file_name in files:
                    if file_name.startswith(".") or file_name.endswith(".part"):
                        continue
                    match = file_pattern.match(file_name)
                    if not match:
                        skipped.append(os.path.join(root, file_name))
                        continue
                    series_name, season_text, episode_text, container = match.groups()
                    # Filename is authoritative; directory name can be sanitized.
                    if sanitize_filename(series_name) != series_dir and series_dir != series_name:
                        series_name = series_dir
                    season = int(season_text)
                    episode = int(episode_text)
                    path = os.path.join(root, file_name)
                    size_bytes = os.path.getsize(path)
                    wanted = self.key(series_name, season, episode)
                    previous = old_by_key.get(wanted, {})
                    if wanted in entries and entries[wanted]["size_bytes"] >= size_bytes:
                        continue
                    entries[wanted] = {
                        "series_name": series_name,
                        "series_id": previous.get("series_id", ""),
                        "season_id": previous.get("season_id", ""),
                        "episode_id": previous.get("episode_id", ""),
                        "media_source_id": previous.get("media_source_id", ""),
                        "source_index": previous.get("source_index"),
                        "source_name": previous.get("source_name", ""),
                        "season_number": season,
                        "episode_number": episode,
                        "original_name": previous.get("original_name", ""),
                        "file_name": file_name,
                        "relative_path": os.path.relpath(path, self.downloads_path).replace("\\", "/"),
                        "size_bytes": size_bytes,
                        "size_mb": int(round(size_bytes / 1048576)),
                        "container": container.lower(),
                        "attempts": previous.get("attempts", 0),
                        "tool_version": previous.get("tool_version", ""),
                        "duration_minutes": previous.get("duration_minutes"),
                        "downloaded_at": previous.get(
                            "downloaded_at",
                            datetime.datetime.fromtimestamp(os.path.getmtime(path))
                            .strftime("%Y-%m-%d %H:%M"),
                        ),
                    }

        all_entries = sorted(
            entries.values(),
            key=lambda e: (e["series_name"], e["season_number"], e["episode_number"]),
        )
        central = {"downloads": all_entries, "total_episodes": len(all_entries)}
        self.write_central(central)
        self._write_series_groups(all_entries)

        return {
            "entries": len(all_entries),
            "series": len({e["series_name"] for e in all_entries}),
            "skipped_files": skipped,
            "total_bytes": sum(e["size_bytes"] for e in all_entries),
        }

    def prune(self) -> List[Dict[str, Any]]:
        """Drop dataset entries whose files no longer exist on disk."""
        central = self.central()
        removed = [e for e in central.get("downloads", [])
                   if not os.path.exists(self.disk_path(e))]
        if removed:
            removed_ids = {id(e) for e in removed}
            central["downloads"] = [
                e for e in central.get("downloads", [])
                if id(e) not in removed_ids
            ]
            self.write_central(central)
            self._write_series_groups(central["downloads"])
        return removed

    def dedupe(self) -> List[Dict[str, Any]]:
        """Remove duplicate entries, preferring on-disk files then newest data."""
        central = self.central()
        best: Dict[Tuple[str, int, int], Dict[str, Any]] = {}
        removed: List[Dict[str, Any]] = []
        for entry in central.get("downloads", []):
            identity = self._identity(entry)
            current = best.get(identity)
            if current is None:
                best[identity] = entry
                continue
            current_on_disk = os.path.exists(self.disk_path(current))
            entry_on_disk = os.path.exists(self.disk_path(entry))
            entry_rank = (
                entry_on_disk,
                str(entry.get("downloaded_at") or ""),
                int(entry.get("size_bytes") or 0),
            )
            current_rank = (
                current_on_disk,
                str(current.get("downloaded_at") or ""),
                int(current.get("size_bytes") or 0),
            )
            if entry_rank > current_rank:
                best[identity] = entry
                removed.append(current)
            else:
                removed.append(entry)
        if removed:
            kept_ids = {id(e) for e in best.values()}
            central["downloads"] = [
                e for e in central.get("downloads", []) if id(e) in kept_ids
            ]
            self.write_central(central)
            self._write_series_groups(central["downloads"])
        return removed

    def stats(self) -> Dict[str, Any]:
        entries = self.entries()
        by_series: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            name = entry.get("series_name", "Unknown")
            bucket = by_series.setdefault(name, {
                "series_name": name, "recorded": 0, "on_disk": 0, "missing": 0,
                "total_bytes": 0, "disk_bytes": 0,
            })
            bucket["recorded"] += 1
            bucket["total_bytes"] += int(entry.get("size_bytes") or 0)
            path = self.disk_path(entry)
            if os.path.exists(path):
                bucket["on_disk"] += 1
                bucket["disk_bytes"] += os.path.getsize(path)
            else:
                bucket["missing"] += 1
        return {
            "version": SCHEMA_VERSION,
            "series": sorted(by_series.values(), key=lambda b: b["series_name"]),
            "total_episodes": len(entries),
            "total_bytes": sum(b["total_bytes"] for b in by_series.values()),
            "disk_bytes": sum(b["disk_bytes"] for b in by_series.values()),
            "missing": sum(b["missing"] for b in by_series.values()),
        }

    def check(self) -> Dict[str, Any]:
        """Report dataset/disk/derived-file inconsistencies without changing anything."""
        central = self.central()
        entries = central.get("downloads", [])
        issues: List[Dict[str, Any]] = []
        duplicates: List[Dict[str, Any]] = []

        seen: Dict[Tuple[str, int, int], Dict[str, Any]] = {}
        for entry in entries:
            identity = self._identity(entry)
            if identity in seen:
                duplicates.append({
                    "identity": list(identity),
                    "series_name": entry.get("series_name", ""),
                    "season": entry.get("season_number"),
                    "episode": entry.get("episode_number"),
                })
            else:
                seen[identity] = entry

        missing_files: List[Dict[str, Any]] = []
        size_mismatches: List[Dict[str, Any]] = []
        for entry in entries:
            path = self.disk_path(entry)
            expected = parse_int(entry.get("size_bytes"))
            if not os.path.exists(path):
                missing_files.append({
                    "series_name": entry.get("series_name", ""),
                    "season": entry.get("season_number"),
                    "episode": entry.get("episode_number"),
                    "path": path,
                })
                continue
            actual = os.path.getsize(path)
            if expected is not None and actual != expected:
                size_mismatches.append({
                    "series_name": entry.get("series_name", ""),
                    "season": entry.get("season_number"),
                    "episode": entry.get("episode_number"),
                    "expected_bytes": expected,
                    "actual_bytes": actual,
                    "path": path,
                })

        stale_series: List[Dict[str, Any]] = []
        groups = self._group_entries(entries)
        for series_name, group in groups.items():
            path = self.series_path(series_name)
            if not os.path.exists(path):
                stale_series.append({"series_name": series_name, "reason": "missing"})
                continue
            derived = self._read_json(path)
            derived_ids = {self._identity(e) for e in derived.get("downloads", [])}
            central_ids = {self._identity(e) for e in group}
            if derived_ids != central_ids:
                stale_series.append({"series_name": series_name, "reason": "out_of_date"})

        for item in duplicates:
            issues.append({"kind": "duplicate", **item})
        for item in missing_files:
            issues.append({"kind": "missing_file", **item})
        for item in size_mismatches:
            issues.append({"kind": "size_mismatch", **item})
        for item in stale_series:
            issues.append({"kind": "stale_series_dataset", **item})

        return {
            "ok": not issues,
            "version": SCHEMA_VERSION,
            "entries": len(entries),
            "series": len(groups),
            "duplicates": duplicates,
            "missing_files": missing_files,
            "size_mismatches": size_mismatches,
            "stale_series": stale_series,
            "issues": issues,
        }

    def sync(self) -> Dict[str, Any]:
        """Regenerate every per-series dataset from the central dataset."""
        central = self.central()
        groups = self._group_entries(central.get("downloads", []))
        written = 0
        for series_name, group in groups.items():
            series_id = next((e.get("series_id") for e in group if e.get("series_id")), "")
            self._write_series(series_name, group, series_id)
            written += 1
        return {"series": written, "entries": len(central.get("downloads", []))}


# =============================================================================
# SERVER CAPABILITIES CACHE
# =============================================================================

class CapsCache:
    """Cache of server capabilities so repeated commands skip live probing."""

    def __init__(self, downloads_path: str):
        self.path = os.path.join(downloads_path, CAPS_NAME)

    def read(self) -> Dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def write(self, caps: Dict[str, Any]) -> None:
        caps = dict(caps)
        caps["updated_at"] = now_iso()
        caps["tool_version"] = VERSION
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        atomic_write_json(self.path, caps, mode=0o600)

    def reset(self) -> bool:
        try:
            os.unlink(self.path)
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise ConfigError(f"Could not remove caps cache: {exc}")


def probe_caps(api: "EmbyAPI", config: Dict[str, Any]) -> Dict[str, Any]:
    """Probe server capabilities, using the cache when it is fresh."""
    cache = CapsCache(get_downloads_path(config))
    cached = cache.read()
    if cached and not os.environ.get("EMBY_NO_CAPS_CACHE"):
        return cached

    caps: Dict[str, Any] = {}
    public = api.get_public_info()
    caps["server_name"] = public.get("ServerName", "")
    caps["server_version"] = public.get("Version", "")
    user = {}
    try:
        user = api.get_user()
    except ToolError:
        pass
    caps["user_id"] = user.get("Id", config.get("user_id", ""))
    caps["is_admin"] = bool((user.get("Policy") or {}).get("IsAdministrator"))
    cache.write(caps)
    return caps


# =============================================================================
# LOCK, STATE AND PROGRESS
# =============================================================================

def _try_lock_handle(handle) -> bool:
    """Non-blocking exclusive lock of an open file handle."""
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, PermissionError, OSError):
            return False
    elif msvcrt is not None:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except (BlockingIOError, PermissionError, OSError):
            return False
    return True


def _unlock_handle(handle) -> None:
    """Unlock an open file handle."""
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
    elif msvcrt is not None:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


def _pid_is_alive(pid: int) -> bool:
    """Best-effort liveness check that never signals the target process."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _pid_name(pid: int) -> str:
    """Return the executable name for a PID, or an empty string if unknown."""
    if pid <= 0:
        return ""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return ""
            try:
                size = wintypes.DWORD(32768)
                buffer = ctypes.create_unicode_buffer(size.value)
                if not kernel32.QueryFullProcessImageNameW(
                        handle, 0, buffer, ctypes.byref(size)):
                    return ""
                return os.path.basename(buffer.value).lower()
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return ""
    try:
        with open(f"/proc/{int(pid)}/comm", "r", encoding="utf-8") as handle:
            return handle.read().strip().lower()
    except OSError:
        return ""


def _terminate_pid(pid: int) -> None:
    """Terminate a process by PID, ignoring already-dead targets."""
    if pid <= 0 or pid == os.getpid():
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15, check=False,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        pass


def _write_curl_pid(downloads_path: str, pid: int, part_path: str) -> None:
    """Remember the active curl child so a later run can clean it up."""
    payload = {
        "pid": int(pid),
        "python_pid": os.getpid(),
        "part": os.path.basename(part_path),
        "started_at": now_iso(),
    }
    try:
        atomic_write_json(os.path.join(downloads_path, CURL_PID_NAME), payload)
    except OSError:
        pass


def _clear_curl_pid(downloads_path: str, pid: int) -> None:
    path = os.path.join(downloads_path, CURL_PID_NAME)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            tracked = json.load(handle)
        if int(tracked.get("pid") or 0) != int(pid):
            return
        os.unlink(path)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass


def _kill_stale_curl(downloads_path: str) -> None:
    """Kill a curl left behind by a previous run that no longer holds the lock."""
    path = os.path.join(downloads_path, CURL_PID_NAME)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            tracked = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return
    pid = parse_int(tracked.get("pid"))
    owner = parse_int(tracked.get("python_pid"))
    if pid:
        name = _pid_name(pid)
        if name and "curl" not in name:
            pid = None  # PID was reused by an unrelated process.
    if pid and _pid_is_alive(pid) and not (owner and _pid_is_alive(owner)):
        _terminate_pid(pid)
    try:
        os.unlink(path)
    except OSError:
        pass


class DownloadLock:
    """Exclusive machine-wide lock for actual downloads (not for planning)."""

    def __init__(self, downloads_path: str):
        self.path = os.path.join(downloads_path, LOCK_NAME)
        self.handle = None

    def acquire(self) -> "DownloadLock":
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")
        if not _try_lock_handle(handle):
            try:
                handle.seek(0)
                holder = handle.read().strip()
            except OSError:
                holder = ""
            handle.close()
            raise LockBusy(
                "Another emby_dl download is already running"
                + (f" (pid {holder})" if holder else "")
                + f"; lock: {self.path}"
            )
        # A previous process may have been killed while curl kept writing.
        _kill_stale_curl(os.path.dirname(self.path))
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
        except OSError:
            pass
        self.handle = handle
        return self

    def release(self) -> None:
        if not self.handle:
            return
        _unlock_handle(self.handle)
        try:
            self.handle.close()
        finally:
            self.handle = None

    def __enter__(self) -> "DownloadLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def lock_is_held(downloads_path: str) -> bool:
    path = os.path.join(downloads_path, LOCK_NAME)
    if not os.path.exists(path):
        return False
    try:
        handle = open(path, "a+", encoding="utf-8")
    except (PermissionError, OSError):
        return True
    try:
        if not _try_lock_handle(handle):
            return True
        _unlock_handle(handle)
        return False
    finally:
        handle.close()


def state_file(downloads_path: str) -> str:
    return os.path.join(downloads_path, LOCAL_STATE_NAME)


def read_state(downloads_path: str) -> Optional[Dict[str, Any]]:
    path = state_file(downloads_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


class ProgressReporter:
    """Renders progress, writes a status file, and emits JSONL events."""

    def __init__(self, mode: str, downloads_path: str, total_bytes: Optional[int],
                 label: str):
        if mode == "auto":
            mode = "plain" if sys.stdout.isatty() else "none"
        if mode not in ("plain", "json", "none"):
            mode = "none"
        self.mode = mode
        self.state_path = state_file(downloads_path)
        self.total = total_bytes
        self.label = label
        self.start_time = time.monotonic()
        self.started_at = now_iso()
        self.samples: collections.deque = collections.deque(maxlen=60)
        self.last_render = 0.0
        self.last_state = 0.0
        self.last_json = 0.0
        self.rendered = False
        self.downloaded = 0

    @property
    def is_json(self) -> bool:
        return self.mode == "json"

    def event(self, kind: str, **fields: Any) -> None:
        if self.is_json:
            emit_json_event(kind, **fields)
        elif self.mode == "plain":
            message = fields.get("message")
            if message:
                if kind in ("retry", "error", "warning"):
                    warn(f">> {message}")
                else:
                    info(f">> {message}")

    def _speed(self) -> Optional[float]:
        if len(self.samples) < 2:
            return None
        cutoff = time.monotonic() - 10.0
        recent = [sample for sample in self.samples if sample[0] >= cutoff]
        if len(recent) < 2:
            return None
        (first_t, first_b), (last_t, last_b) = recent[0], recent[-1]
        if last_t <= first_t:
            return None
        return max(0.0, (last_b - first_b) / (last_t - first_t))

    def update(self, downloaded: int, total: Optional[int] = None,
               status: str = "running") -> None:
        now = time.monotonic()
        if total is not None:
            self.total = total
        self.downloaded = downloaded
        self.samples.append((now, downloaded))
        speed = self._speed()
        percent = None
        eta = None
        if self.total and self.total > 0:
            percent = min(100.0, downloaded * 100.0 / self.total)
            if speed and speed > 0:
                eta = max(0.0, (self.total - downloaded) / speed)

        if now - self.last_render >= 0.5:
            self.last_render = now
            if self.mode == "plain":
                line = (
                    f"\r>> {percent:5.1f}% " if percent is not None
                    else f"\r>> {human_bytes(downloaded)} "
                )
                if percent is not None:
                    line += f"{human_bytes(downloaded)}/{human_bytes(self.total)} "
                line += f"{human_bytes(speed)}/s ETA {human_duration(eta)}"
                sys.stdout.write(line)
                sys.stdout.flush()
                self.rendered = True
            elif self.mode == "json" and now - self.last_json >= 1.0:
                self.last_json = now
                emit_json_event(
                    "progress", label=self.label, downloaded=downloaded,
                    total=self.total, percent=round(percent, 2) if percent is not None else None,
                    speed_bps=int(speed) if speed is not None else None,
                    eta_seconds=int(eta) if eta is not None else None,
                )

        if now - self.last_state >= 1.0 or status != "running":
            self.last_state = now
            state = {
                "running": status == "running",
                "status": status,
                "pid": os.getpid(),
                "label": self.label,
                "downloaded": downloaded,
                "total": self.total,
                "percent": round(percent, 2) if percent is not None else None,
                "speed_bps": int(speed) if speed is not None else None,
                "eta_seconds": int(eta) if eta is not None else None,
                "started_at": self.started_at,
                "updated_at": now_iso(),
            }
            try:
                atomic_write_json(self.state_path, state)
            except OSError:
                pass

    def finish(self, status: str = "done", **fields: Any) -> None:
        self.update(self.downloaded, self.total, status=status)
        if self.mode == "plain" and self.rendered:
            sys.stdout.write("\n")
            sys.stdout.flush()
        if self.is_json:
            emit_json_event("progress_done", label=self.label, status=status, **fields)


def ensure_disk_space(downloads_path: str, needed: Optional[int],
                      config: Dict[str, Any], what: str = "download") -> None:
    os.makedirs(downloads_path, exist_ok=True)
    usage = shutil.disk_usage(downloads_path)
    reserve = int(config.get("download_settings", {}).get("disk_reserve_bytes", 2 * 1024 ** 3))
    if needed is None:
        if usage.free <= 0:
            raise DiskFull(f"No free disk space for {what} at {downloads_path}")
        return
    if usage.free - reserve < needed:
        raise DiskFull(
            f"Insufficient disk space for {what}: need {human_bytes(needed)}, "
            f"free {human_bytes(usage.free)}, reserve {human_bytes(reserve)} "
            f"({downloads_path})"
        )


# =============================================================================
# SELECTION PARSING AND PLANNING
# =============================================================================

_TOKEN_SE = re.compile(r"^S(\d+)E(\d+)(?:-(\d+|last))?$", re.IGNORECASE)
_TOKEN_S = re.compile(r"^S(\d+)$", re.IGNORECASE)
_TOKEN_E = re.compile(r"^E(\d+)(?:-(\d+|last))?$", re.IGNORECASE)


def parse_selection(tokens: List[str]) -> Dict[str, Any]:
    """Parse CLI tokens into mode/season/episode range. Raises UsageError."""
    mode: Optional[str] = None
    season: Optional[int] = None
    low: Any = None
    high: Any = None

    def fail(token: str) -> None:
        raise UsageError(
            f"Invalid spec token: {token!r}. Expected all, last, missing, new, "
            "S<N>, E<M>, E<M>-<K>, S<N>E<M>, or S<N>E<M>-<K>; nothing was downloaded."
        )

    if not tokens:
        return {"mode": "first", "season": None, "low": None, "high": None}

    for token in tokens:
        lowered = token.strip().lower()
        if not lowered:
            continue
        if lowered in ("all", "missing", "new"):
            if lowered == "all" and season is not None and low is None and mode is None:
                continue  # "S2 all" == all episodes of season 2
            if mode is not None or low is not None or season is not None:
                fail(token)
            mode = lowered
            continue
        if lowered == "last":
            if season is None:
                if mode is not None or low is not None:
                    fail(token)
                mode = "last"
            else:
                if low is not None:
                    fail(token)
                low = "last"
            continue
        match = _TOKEN_SE.match(token)
        if match:
            if season is not None or low is not None:
                fail(token)
            season = int(match.group(1))
            low = int(match.group(2))
            high = match.group(3)
            continue
        match = _TOKEN_S.match(token)
        if match:
            if season is not None or low is not None:
                fail(token)
            season = int(match.group(1))
            continue
        match = _TOKEN_E.match(token)
        if match:
            if season is None or low is not None:
                fail(token)
            low = int(match.group(1))
            high = match.group(2)
            continue
        fail(token)

    if mode in ("all", "missing", "new"):
        if season is not None or low is not None:
            fail(" ".join(tokens))
        return {"mode": mode, "season": None, "low": None, "high": None}
    if mode == "last":
        return {"mode": "last", "season": None, "low": None, "high": None}
    if low is not None and season is None:
        raise UsageError("E<M> requires an explicit season (e.g. S2 E3).")
    if high is not None and low is not None and low != "last" and high != "last":
        if int(high) < int(low):
            raise UsageError(f"Invalid episode range: {low}-{high} (end before start).")
    if season is None:
        return {"mode": "first", "season": None, "low": None, "high": None}
    if low is None:
        return {"mode": "season", "season": season, "low": None, "high": None}
    return {"mode": "episodes", "season": season, "low": low, "high": high}


def parse_source_spec(value: Any) -> str:
    """Validate --source: 1-based index or 'max'."""
    text = str(value or "1").strip().lower()
    if text == "max":
        return "max"
    if re.fullmatch(r"[1-9]\d*", text):
        return text
    raise UsageError(
        f"Invalid --source value: {value!r} (use 1, N, or max)"
    )


def episode_container(episode: Dict[str, Any], source: Optional[Dict[str, Any]] = None) -> str:
    candidates = [source] if source else []
    candidates.extend(episode.get("MediaSources") or [])
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        container = (candidate.get("Container") or "").split(",")[0].strip().lower()
        if container:
            return container
    path = episode.get("Path") or ""
    name = os.path.basename(path)
    if "." in name:
        return name.rsplit(".", 1)[-1].lower()
    return "mkv"


def episode_destination(downloads_path: str, series_name: str, season: int,
                        episode: int, container: str) -> Tuple[str, str, str]:
    safe_series = sanitize_filename(series_name)
    file_name = f"{safe_series} - S{int(season):02d}E{int(episode):02d}.{container}"
    relative = f"{safe_series}/Season {int(season)}/{file_name}"
    dest = os.path.join(downloads_path, safe_series, f"Season {int(season)}", file_name)
    return dest, relative, file_name


def _episode_status(dataset: DatasetManager, downloads_path: str, series_name: str,
                    series_id: str, season: int, episode: Dict[str, Any],
                    expected: Optional[int], container: str,
                    source_spec: str = "1") -> Dict[str, Any]:
    number = int(episode.get("IndexNumber") or 0)
    source, source_index = EmbyAPI.select_media_source(episode, source_spec)
    source_id = str((source or {}).get("Id") or "")
    source_name = str((source or {}).get("Name") or "")
    if source is not None and expected is None:
        expected = EmbyAPI.source_size(source)
    dest, relative, file_name = episode_destination(
        downloads_path, series_name, season, number, container
    )
    recorded = dataset.get_entry(series_name, season, number, series_id)
    on_disk = os.path.exists(dest)
    disk_size = os.path.getsize(dest) if on_disk else None
    recorded_size = parse_int((recorded or {}).get("size_bytes"))
    recorded_source = str((recorded or {}).get("media_source_id") or "")
    source_changed = bool(recorded and source_id and recorded_source
                          and recorded_source != source_id)

    # A remuxed file (e.g. HLS -> MKV) legitimately differs in size from the
    # server's source. Trust the dataset when it recorded that exact size.
    matches_recorded = bool(recorded_size and disk_size == recorded_size)

    reason = "not_recorded"
    status = "todo"
    if expected is None:
        reason = "size_unknown"
    elif on_disk and not source_changed and (disk_size == expected or matches_recorded):
        status = "skip"
        reason = "ok" if recorded else "self_heal"
    elif on_disk:
        reason = "source_changed" if source_changed else (
            "partial" if disk_size < expected else "size_mismatch")
    elif recorded:
        reason = "recorded_missing"

    return {
        "season": season,
        "episode": number,
        "series_id": series_id,
        "episode_id": episode.get("Id", ""),
        "season_id": episode.get("SeasonId", ""),
        "media_source_id": source_id,
        "source_index": source_index,
        "source_name": source_name,
        "source_changed": source_changed,
        "title": episode.get("Name", ""),
        "container": container,
        "file_name": file_name,
        "relative_path": relative,
        "path": dest,
        "size_bytes": expected,
        "on_disk": on_disk,
        "disk_bytes": disk_size,
        "recorded": bool(recorded),
        "status": status,
        "reason": reason,
    }


def build_plan(api: EmbyAPI, dataset: DatasetManager, config: Dict[str, Any],
               series: Dict[str, Any], selection: Dict[str, Any],
               resolve_unknown_sizes: bool = False,
               source_spec: str = "1",
               include_specials: bool = False) -> Dict[str, Any]:
    """Resolve the selection into a list of items with status/size, no download."""
    downloads_path = get_downloads_path(config)
    series_id = series.get("Id", "")
    series_name = series.get("Name", "")
    seasons = sorted(
        api.get_series_seasons(series_id),
        key=lambda s: parse_int(s.get("IndexNumber")) or 0,
    )
    real_seasons = [s for s in seasons if (parse_int(s.get("IndexNumber")) or 0) >= 1]
    mode = selection["mode"]

    if mode in ("first", "last") and not real_seasons:
        real_seasons = seasons
    if not seasons:
        raise NotFoundError(f"No seasons found for {series_name}")

    if mode in ("all", "missing", "new"):
        target_seasons = real_seasons or seasons
        if include_specials and mode == "all":
            specials = [s for s in seasons
                        if (parse_int(s.get("IndexNumber")) or 0) == 0]
            target_seasons = sorted(
                specials + target_seasons,
                key=lambda s: parse_int(s.get("IndexNumber")) or 0,
            )
        wanted_range: Optional[Tuple[Any, Any]] = None
        take_first = take_last = False
    elif mode == "first":
        target_seasons = [real_seasons[0]]
        wanted_range = None
        take_first = True
        take_last = False
    elif mode == "last":
        target_seasons = [real_seasons[-1]]
        wanted_range = None
        take_first = False
        take_last = True
    else:
        wanted_season = int(selection["season"])
        target_seasons = [
            season for season in seasons
            if (parse_int(season.get("IndexNumber")) or 0) == wanted_season
        ]
        if not target_seasons:
            available = ", ".join(
                str(parse_int(s.get("IndexNumber")) or 0) for s in seasons
            )
            raise NotFoundError(
                f"Season {wanted_season} not found for {series_name} "
                f"(available: {available})"
            )
        if mode == "season":
            wanted_range = None
            take_first = take_last = False
        else:
            wanted_range = (selection["low"], selection["high"])
            take_first = take_last = False

    items: List[Dict[str, Any]] = []
    for season in target_seasons:
        season_number = parse_int(season.get("IndexNumber")) or 0
        episodes = sorted(
            api.get_season_episodes(series_id, season["Id"]),
            key=lambda e: parse_int(e.get("IndexNumber")) or 0,
        )
        if not episodes:
            continue

        selected: List[Dict[str, Any]]
        if take_first:
            selected = episodes[:1]
        elif take_last:
            selected = episodes[-1:]
        elif wanted_range is not None:
            low, high = wanted_range
            if low == "last":
                selected = episodes[-1:]
            else:
                low_number = int(low)
                if high == "last":
                    selected = [e for e in episodes
                                if (parse_int(e.get("IndexNumber")) or 0) >= low_number]
                elif high is None:
                    selected = [e for e in episodes
                                if (parse_int(e.get("IndexNumber")) or 0) == low_number]
                else:
                    high_number = int(high)
                    selected = [
                        e for e in episodes
                        if low_number <= (parse_int(e.get("IndexNumber")) or 0) <= high_number
                    ]
        else:
            selected = list(episodes)

        for episode in selected:
            number = parse_int(episode.get("IndexNumber")) or 0
            source, _ = EmbyAPI.select_media_source(episode, source_spec)
            container = episode_container(episode, source)
            expected = EmbyAPI.source_size(source)
            if expected is None and resolve_unknown_sizes:
                expected = api.resolve_size(
                    episode, allow_head=True, source_spec=source_spec
                )
            items.append(_episode_status(
                dataset, downloads_path, series_name, series_id, season_number,
                episode, expected, container, source_spec,
            ))

    # De-duplicate in case of odd season data (same S/E in two season ids).
    unique: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for item in items:
        unique.setdefault((item["season"], item["episode"]), item)
    items = sorted(unique.values(), key=lambda i: (i["season"], i["episode"]))

    if mode == "missing":
        items = [item for item in items if item["status"] != "skip"]
    elif mode == "new":
        recorded = dataset.entries(series_name)
        highest: Optional[Tuple[int, int]] = None
        for entry in recorded:
            key = (int(entry.get("season_number") or 0),
                   int(entry.get("episode_number") or 0))
            if highest is None or key > highest:
                highest = key
        if highest is not None:
            items = [item for item in items
                     if (item["season"], item["episode"]) > highest]

    todo = [item for item in items if item["status"] != "skip"]
    return {
        "series": {"id": series_id, "name": series_name},
        "selection": selection,
        "source": source_spec,
        "items": items,
        "summary": {
            "total": len(items),
            "todo": len(todo),
            "skip": len(items) - len(todo),
            "todo_bytes": sum(int(item["size_bytes"] or 0) for item in todo),
            "unknown_sizes": sum(1 for item in todo if item["size_bytes"] is None),
            "source_changed": sum(1 for item in items if item.get("source_changed")),
        },
    }


# =============================================================================
# DOWNLOAD ENGINE
# =============================================================================

class DownloadEngine:
    """Download one episode at a time with verification and resume probing."""

    def __init__(self, config: Dict[str, Any], api: EmbyAPI, dataset: DatasetManager):
        self.config = config
        self.api = api
        self.dataset = dataset
        self.settings = config.get("download_settings", DEFAULT_CONFIG["download_settings"])
        self.downloads_path = get_downloads_path(config)

    @staticmethod
    def _content_type_ok(content_type: str) -> bool:
        if not content_type:
            return True
        lowered = content_type.lower()
        return not any(token in lowered for token in
                       ("text/", "json", "xml", "html"))

    def _header_file(self) -> str:
        """Write the auth header to a 0600 curl config file so the token never
        appears in `ps aux` or in the process environment."""
        directory = self.downloads_path
        try:
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, ".emby_dl_headers")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f'header = "X-Emby-Token: {self.api.token}"\n')
            return os.path.abspath(path).replace("\\", "/")
        except OSError as exc:
            raise ConfigError(f"Could not create private curl header file: {exc}")

    def _run_curl(self, url: str, part_path: str, resume: bool,
                  stall_seconds: int, reporter: ProgressReporter) -> Dict[str, Any]:
        curl = shutil.which("curl")
        if not curl:
            raise ConfigError("curl is required but was not found in PATH")
        speed_limit = int(self.settings.get("speed_limit_bytes", 5000))
        command = [
            curl, "-f", "-L", "-sS",
            "--speed-limit", str(speed_limit),
            "--speed-time", str(max(1, int(stall_seconds))),
            "-K", self._header_file(),
            "-o", part_path,
            "--write-out", "\n%{http_code}\t%{size_download}\t%{content_type}",
        ]
        if resume:
            command[2:2] = ["-C", "-"]
        command.append(url)

        try:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
        except OSError as exc:
            raise ConfigError(f"Could not start curl: {exc}")

        _write_curl_pid(self.downloads_path, process.pid, part_path)

        completion_grace = max(1, int(self.settings.get(
            "completion_grace_seconds", 15)))
        # This Emby server cannot serve HTTP Range requests, so an aborted
        # stream cannot be resumed. Prefer waiting out a long stall over
        # throwing away a nearly complete transfer.
        idle_timeout = max(600, int(self.settings.get(
            "idle_timeout_seconds", max(3600, int(stall_seconds) * 2))))
        forced_complete = False
        last_size = -1
        last_change = time.monotonic()
        try:
            while process.poll() is None:
                size = os.path.getsize(part_path) if os.path.exists(part_path) else 0
                reporter.update(size)
                now = time.monotonic()
                if size != last_size:
                    last_size = size
                    last_change = now
                if (reporter.total and size >= reporter.total
                        and now - last_change >= completion_grace):
                    # The server can keep the connection open after the final
                    # byte; once the expected size is stable, stop waiting.
                    forced_complete = True
                    process.terminate()
                    break
                if now - last_change >= idle_timeout:
                    # curl's own --speed-* watchdog can miss half-open sockets.
                    process.terminate()
                    break
                time.sleep(0.25)
            try:
                stdout, stderr = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            reporter.finish("interrupted")
            raise
        finally:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            _clear_curl_pid(self.downloads_path, process.pid)

        size = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        reporter.update(size)
        if forced_complete and size == reporter.total:
            reporter.event(
                "warning",
                message=(f"{reporter.label}: connection stayed open at 100%; "
                         f"stopped curl after {completion_grace}s"),
            )
        http_code = 0
        content_type = ""
        for line in reversed((stdout or "").strip().splitlines()):
            parts = line.strip().split("\t")
            if len(parts) >= 3 and parts[0].isdigit():
                http_code = int(parts[0])
                content_type = parts[2].strip()
                break
        return {
            "rc": process.returncode,
            "http": http_code,
            "size": size,
            "content_type": content_type,
            "stderr": (stderr or "").strip(),
            "forced_complete": forced_complete and size == reporter.total,
        }

    # -- HLS fallback ------------------------------------------------------
    #
    # Some Emby servers reset long HTTP transfers and do not honour Range
    # requests, so a partial direct download can never be resumed. HLS splits
    # the same stream into short, independently fetchable segments; existing
    # segments are kept between runs, which makes the download resumable.

    @staticmethod
    def _device_id() -> str:
        return "emby_dl_hls"

    def _ffmpeg_tools(self) -> Tuple[str, str]:
        configured = str(self.settings.get("ffmpeg_path") or "").strip()
        candidates: List[str] = []
        if configured:
            candidates.append(configured)
        found = shutil.which("ffmpeg")
        if found:
            candidates.append(found)
        if os.name == "nt":
            candidates.append(r"C:\Program Files\DownloadHelper CoApp\ffmpeg.exe")
        ffmpeg = next((path for path in candidates if path and os.path.exists(path)), "")
        if not ffmpeg:
            raise ConfigError(
                "HLS mode needs ffmpeg. Install it (winget install Gyan.FFmpeg) "
                "or set download_settings.ffmpeg_path."
            )
        probe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        ffprobe = os.path.join(os.path.dirname(ffmpeg), probe_name)
        if not os.path.exists(ffprobe):
            ffprobe = shutil.which("ffprobe") or ""
        return ffmpeg, ffprobe

    def _fetch_text(self, url: str, timeout: Optional[int] = None, retries: int = 3) -> str:
        req_timeout = timeout if timeout is not None else max(60, self.api.timeout)
        last_exc: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            request = urllib.request.Request(
                url,
                headers={
                    "X-Emby-Token": self.api.token,
                    "Accept": "*/*",
                    "User-Agent": f"{PROG}/{VERSION}",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=req_timeout) as response:
                    return response.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                if exc.code in (404, 410):
                    raise HlsExpired(f"HLS playlist expired (HTTP {exc.code})")
                raise ServerError(f"HLS request failed (HTTP {exc.code})")
            except (TimeoutError, OSError, urllib.error.URLError) as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(2 * attempt)
                    continue
        reason = getattr(last_exc, "reason", str(last_exc))
        raise ServerError(f"HLS request failed: {reason}")

    def _hls_session(self, item_id: str, media_source_id: str,
                     play_session_id: str = "") -> Dict[str, Any]:
        session_id = play_session_id
        if not session_id:
            body = json.dumps({
                "UserId": self.api.user_id,
                "StartTimeTicks": 0,
                "IsPlayback": True,
                "AutoOpenLiveStream": False,
            }).encode("utf-8")
            request = urllib.request.Request(
                self.api._url(f"/Items/{item_id}/PlaybackInfo"),
                data=body,
                headers={
                    "X-Emby-Token": self.api.token,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": f"{PROG}/{VERSION}",
                },
                method="POST",
            )
            info = None
            last_err: Optional[Exception] = None
            pb_timeout = max(60, self.api.timeout)
            for attempt in range(1, 4):
                try:
                    with urllib.request.urlopen(request, timeout=pb_timeout) as response:
                        info = json.loads(response.read().decode("utf-8"))
                        break
                except urllib.error.HTTPError as exc:
                    if exc.code in (401, 403):
                        raise AuthError(f"PlaybackInfo authentication failed (HTTP {exc.code})")
                    last_err = exc
                    if attempt < 3:
                        time.sleep(2 * attempt)
                except (TimeoutError, OSError, urllib.error.URLError) as exc:
                    last_err = exc
                    if attempt < 3:
                        time.sleep(2 * attempt)
            if not info:
                reason = getattr(last_err, "reason", str(last_err))
                raise ServerError(f"Cannot reach server for PlaybackInfo: {reason}")
            session_id = str(info.get("PlaySessionId") or "")
            if not session_id:
                raise ServerError(f"Server returned no PlaySessionId for item {item_id}")

        query = urllib.parse.urlencode({
            "MediaSourceId": media_source_id,
            "PlaySessionId": session_id,
            "DeviceId": self._device_id(),
            "api_key": self.api.token,
            "VideoCodec": "h264",
            "AudioCodec": "aac",
            "TranscodingContainer": "ts",
            "TranscodingProtocol": "hls",
            "SegmentContainer": "ts",
            "MinSegments": "1",
            "BreakOnNonKeyFrames": "True",
        })
        master_url = self.api._url(f"/Videos/{item_id}/master.m3u8?{query}")
        master = self._fetch_text(master_url)
        variant = ""
        lines = master.splitlines()
        for index, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                for follow in lines[index + 1:]:
                    if follow and not follow.startswith("#"):
                        variant = follow.strip()
                        break
                break
        if not variant:
            raise ServerError(f"HLS master playlist has no variant for item {item_id}")

        variant_url = urllib.parse.urljoin(master_url, variant)
        playlist = self._fetch_text(variant_url)
        segments: List[Tuple[float, str]] = []
        pending = 0.0
        for line in playlist.splitlines():
            if line.startswith("#EXTINF:"):
                try:
                    pending = float(line.split(":", 1)[1].split(",", 1)[0])
                except ValueError:
                    pending = 0.0
            elif line and not line.startswith("#"):
                segments.append((pending, urllib.parse.urljoin(variant_url, line.strip())))
                pending = 0.0
        if not segments:
            raise ServerError(f"HLS playlist has no segments for item {item_id}")
        return {
            "play_session_id": session_id,
            "segments": segments,
            "duration": sum(duration for duration, _ in segments),
        }

    def _download_segment(self, curl: str, header_file: str, url: str,
                          path: str, retries: int) -> None:
        tmp = path + ".tmp"
        last_error = ""
        for attempt in range(1, retries + 1):
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
            try:
                result = subprocess.run(
                    [curl, "-f", "-L", "-sS",
                     "--max-time", "120",
                     "--speed-limit", "500", "--speed-time", "60",
                     "-K", header_file, "-o", tmp,
                     "-w", "%{http_code}", url],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=180,
                )
            except subprocess.TimeoutExpired:
                result = None

            http_code = ""
            if result is not None and result.stdout:
                stripped = result.stdout.strip()
                if stripped[-3:].isdigit():
                    http_code = stripped[-3:]

            # The server dropped the playback session: refresh it upstream
            # instead of retrying the same dead segment URL.
            if http_code in ("404", "410"):
                try:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                except OSError:
                    pass
                raise HlsExpired(
                    f"segment {os.path.basename(path)} expired (HTTP {http_code})"
                )
            if http_code in ("401", "403"):
                try:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                except OSError:
                    pass
                raise AuthError(
                    f"segment {os.path.basename(path)} rejected (HTTP {http_code})"
                )

            ok = bool(result is not None and result.returncode == 0
                      and os.path.exists(tmp) and os.path.getsize(tmp) > 0)
            if ok:
                with open(tmp, "rb") as handle:
                    first = handle.read(1)
                if first == b"\x47":  # MPEG-TS sync byte
                    os.replace(tmp, path)
                    return
                last_error = "response is not an MPEG-TS segment"
            elif result is not None:
                last_error = (result.stderr or f"curl rc={result.returncode}").strip()
            else:
                last_error = "segment timed out"
            if attempt < retries:
                time.sleep(min(30, 2 ** attempt))
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        raise ServerError(f"segment {os.path.basename(path)} failed: {last_error}")

    def _subtitle_text(self, item_id: str, media_source_id: str,
                       source: Optional[Dict[str, Any]]) -> Tuple[str, str]:
        """Return (subtitle text, language) for the first text subtitle track."""
        for stream in (source or {}).get("MediaStreams") or []:
            if stream.get("Type") != "Subtitle":
                continue
            index = stream.get("Index")
            if index is None:
                continue
            url = self.api._url(
                f"/Videos/{item_id}/{media_source_id}/Subtitles/{index}/Stream.srt"
                f"?api_key={urllib.parse.quote(self.api.token)}"
            )
            request = urllib.request.Request(
                url,
                headers={
                    "X-Emby-Token": self.api.token,
                    "Accept": "*/*",
                    "User-Agent": f"{PROG}/{VERSION}",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    raw = response.read()
                text = raw.decode("utf-8", errors="replace")
                if text.strip() and "-->" in text:
                    return text, str(stream.get("Language") or "ara")
            except (urllib.error.HTTPError, urllib.error.URLError, OSError):
                continue
        return "", ""

    def _hls_download(self, item_id: str, media_source_id: str, dest: str,
                      expected: int, reporter: ProgressReporter, label: str,
                      source: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ffmpeg, ffprobe = self._ffmpeg_tools()
        curl = shutil.which("curl")
        if not curl:
            raise ConfigError("curl is required but was not found in PATH")
        header_file = self._header_file()
        segment_retries = max(1, int(self.settings.get("hls_segment_retries", 4)))
        workers = max(1, min(8, int(self.settings.get("hls_workers", 4))))
        seg_dir = dest + ".hls"
        os.makedirs(seg_dir, exist_ok=True)

        session = self._hls_session(item_id, media_source_id)
        segments = session["segments"]
        total = len(segments)
        reporter.event("hls", message=f"{label}: HLS playlist with {total} segments")

        def seg_path(index: int) -> str:
            return os.path.join(seg_dir, f"{index:05d}.ts")

        def segment_ready(index: int) -> bool:
            path = seg_path(index)
            return os.path.exists(path) and os.path.getsize(path) > 0

        completed = sum(1 for i in range(total) if segment_ready(i))

        def report() -> None:
            synthetic = int(expected * completed / total) if total else 0
            reporter.update(min(synthetic, max(0, expected - 1)), expected)

        report()

        def worker(index: int) -> None:
            self._download_segment(
                curl, header_file, segments[index][1], seg_path(index),
                segment_retries,
            )

        # Emby expires the playback session after roughly a minute, so keep
        # refreshing it and resume only the segments that are still missing.
        stalled_rounds = 0
        while True:
            missing = [index for index in range(total) if not segment_ready(index)]
            if not missing:
                break
            before = completed
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(worker, index): index for index in missing}
                try:
                    for future in as_completed(futures):
                        index = futures[future]
                        try:
                            future.result()
                            completed += 1
                            if completed % 10 == 0 or completed == total:
                                report()
                        except HlsExpired as exc:
                            reporter.event(
                                "warning", message=f"{label}: {exc}"
                            )
                        except AuthError:
                            for pending in futures:
                                pending.cancel()
                            raise
                        except Exception as exc:  # noqa: BLE001 - per-segment isolation
                            reporter.event(
                                "warning",
                                message=f"{label}: segment {index} failed ({exc})",
                            )
                finally:
                    for pending in futures:
                        pending.cancel()

            if completed > before:
                stalled_rounds = 0
            else:
                stalled_rounds += 1
                if stalled_rounds >= 3:
                    remaining = sum(
                        1 for index in range(total) if not segment_ready(index)
                    )
                    raise ServerError(
                        f"{label}: {remaining} HLS segments failed"
                    )

            if all(segment_ready(index) for index in range(total)):
                break

            reporter.event(
                "retry",
                message=(f"{label}: refreshing HLS session "
                         f"({completed}/{total} segments)"),
            )
            try:
                refreshed = self._hls_session(item_id, media_source_id)
                if len(refreshed["segments"]) == total:
                    segments = refreshed["segments"]
            except ToolError:
                pass
            report()

        ts_path = dest + ".ts"
        mp4_path = dest + ".remux.mp4"
        remux_path = dest + ".remux.final"
        srt_path = dest + ".srt.tmp"
        matroska = not dest.lower().endswith(".mp4")
        container_name = "MKV" if matroska else "MP4"
        success = False

        def run_ffmpeg(args: List[str]) -> "subprocess.CompletedProcess[str]":
            # -nostdin prevents ffmpeg from blocking on a closed terminal, which
            # previously surfaced as a 30 minute "timed out" remux.
            return subprocess.run(
                [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-y"] + args,
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=1800,
            )

        def finalize(input_path: str,
                     include_subtitle: bool) -> "subprocess.CompletedProcess[str]":
            args = ["-i", input_path]
            mapping = ["-map", "0:v:0"]
            if include_subtitle:
                args += ["-i", srt_path]
                mapping += ["-map", "1:0"]
            mapping += ["-map", "0:a:0?", "-c", "copy"]
            if include_subtitle:
                mapping += ["-c:s", "srt",
                            "-metadata:s:s:0", f"language={language}"]
            if matroska:
                mapping += ["-f", "matroska"]
            else:
                mapping += ["-movflags", "+faststart", "-f", "mp4"]
            return run_ffmpeg(args + mapping + [remux_path])

        try:
            with open(ts_path, "wb") as output:
                for index in range(total):
                    with open(seg_path(index), "rb") as chunk:
                        shutil.copyfileobj(chunk, output, 1024 * 1024)
            reporter.event(
                "hls", message=f"{label}: remuxing to {container_name}"
            )

            subtitle, language = self._subtitle_text(item_id, media_source_id, source)
            if subtitle and matroska:
                with open(srt_path, "w", encoding="utf-8") as handle:
                    handle.write(subtitle)
            else:
                subtitle = ""

            # Emby HLS video packets often carry no timestamp. Matroska refuses
            # them ("Can't write packet with unknown timestamp") while MP4
            # accepts and repairs them, so always go through an MP4
            # intermediate before writing the final container.
            result = run_ffmpeg(
                ["-fflags", "+genpts", "-i", ts_path,
                 "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                 "-f", "mp4", mp4_path]
            )
            mp4_ready = result.returncode == 0 and os.path.exists(mp4_path)
            if mp4_ready:
                result = finalize(mp4_path, bool(subtitle))
            else:
                # Fall back to a direct remux for sources that already carry
                # valid timestamps.
                result = finalize(ts_path, bool(subtitle))
            if result.returncode != 0 and subtitle:
                # Never lose the episode over a subtitle muxing problem.
                reporter.event(
                    "warning",
                    message=(f"{label}: subtitle mux failed, remuxing without it"),
                )
                result = finalize(mp4_path if mp4_ready else ts_path, False)
            if result.returncode != 0 or not os.path.exists(remux_path):
                raise ConfigError(
                    f"ffmpeg remux failed: {(result.stderr or '').strip()}"
                )

            actual_duration = 0.0
            if ffprobe:
                probe = subprocess.run(
                    [ffprobe, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", remux_path],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=180,
                )
                try:
                    actual_duration = float((probe.stdout or "").strip())
                except ValueError:
                    actual_duration = 0.0
            expected_duration = float(session.get("duration") or 0)
            if actual_duration and expected_duration:
                tolerance = max(90.0, expected_duration * 0.03)
                if abs(actual_duration - expected_duration) > tolerance:
                    raise ConfigError(
                        f"duration mismatch: {actual_duration:.1f}s vs "
                        f"{expected_duration:.1f}s"
                    )

            os.replace(remux_path, dest)
            success = True
            return {
                "size": os.path.getsize(dest),
                "duration": actual_duration,
                "segments": total,
            }
        finally:
            for path in (ts_path, mp4_path, remux_path, srt_path):
                try:
                    if os.path.exists(path):
                        os.unlink(path)
                except OSError:
                    pass
            if success:
                shutil.rmtree(seg_dir, ignore_errors=True)

    def _entry(self, series_name: str, series_id: str, season: int,
               episode: int, episode_id: str, original_name: str,
               file_name: str, relative: str, size: int, container: str,
               source: Optional[Dict[str, Any]] = None,
               source_index: int = 0, season_id: str = "",
               attempts: int = 1,
               duration_minutes: Optional[float] = None) -> Dict[str, Any]:
        source = source or {}
        return {
            "series_name": series_name,
            "series_id": series_id,
            "season_id": season_id,
            "episode_id": episode_id,
            "season_number": int(season),
            "episode_number": int(episode),
            "media_source_id": str(source.get("Id") or ""),
            "source_index": int(source_index) if source_index else None,
            "source_name": str(source.get("Name") or ""),
            "original_name": original_name,
            "file_name": file_name,
            "relative_path": relative,
            "size_bytes": int(size),
            "size_mb": int(round(size / 1048576)),
            "container": container,
            "attempts": int(attempts),
            "tool_version": VERSION,
            "duration_minutes": duration_minutes,
            "downloaded_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

    def download_item(self, series_name: str, series_id: str, item: Dict[str, Any],
                      progress_mode: str = "auto",
                      source_spec: str = "1") -> Dict[str, Any]:
        """Download one planned item. Returns status/path/size/series/episode."""
        season = int(item["season"])
        episode_number = int(item["episode"])
        episode_id = item.get("episode_id", "")
        label = f"{series_name} S{season:02d}E{episode_number:02d}"
        started = time.monotonic()

        # Refresh metadata + size from the server when possible; never trust a
        # stale or absent size at record time.
        detail: Dict[str, Any] = {}
        source: Optional[Dict[str, Any]] = None
        source_index = int(item.get("source_index") or 0)
        season_id = str(item.get("season_id") or "")
        expected = parse_int(item.get("size_bytes"))
        try:
            if episode_id:
                detail = self.api.get_item(episode_id)
            if detail:
                source, source_index = self.api.select_media_source(detail, source_spec)
                season_id = str(detail.get("SeasonId") or season_id)
                detail_size = self.api.source_size(source)
                if detail_size is None:
                    detail_size = self.api.resolve_size(
                        detail, allow_head=True, source_spec=source_spec
                    )
                if detail_size:
                    expected = detail_size
        except ToolError:
            detail = {}
            source = None
        if source is None and item.get("media_source_id"):
            source = {
                "Id": item.get("media_source_id"),
                "Name": item.get("source_name", ""),
                "Container": item.get("container", ""),
            }
            source_index = int(item.get("source_index") or 0)
        media_source_id = str((source or {}).get("Id") or "")
        container = episode_container(detail, source) if (detail or source) \
            else item.get("container") or "mkv"
        dest, relative, file_name = episode_destination(
            self.downloads_path, series_name, season, episode_number, container
        )
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        part = dest + ".part"
        original_name = os.path.basename(detail.get("Path") or "") if detail else ""

        def duration() -> float:
            return round((time.monotonic() - started) / 60.0, 2)

        def record(size: int, attempts: int) -> None:
            self.dataset.add_entry(self._entry(
                series_name, series_id, season, episode_number, episode_id,
                original_name, file_name, relative, size, container,
                source=source, source_index=source_index, season_id=season_id,
                attempts=attempts, duration_minutes=duration(),
            ))

        def outcome(status: str, size: Optional[int] = None,
                    reason: str = "") -> Dict[str, Any]:
            return {
                "status": status,
                "reason": reason,
                "series_name": series_name,
                "season": season,
                "episode": episode_number,
                "episode_id": episode_id,
                "media_source_id": media_source_id,
                "source_index": source_index,
                "source_name": str((source or {}).get("Name") or ""),
                "relative_path": relative,
                "size_bytes": size,
            }

        if expected is None:
            return outcome("failed", None, "size_unknown")

        recorded = self.dataset.get_entry(series_name, season, episode_number, series_id)
        recorded_size = parse_int((recorded or {}).get("size_bytes"))

        # Already complete on disk. A remuxed HLS file legitimately differs
        # from the server's source size, so trust the recorded size too.
        if os.path.exists(dest):
            disk_size = os.path.getsize(dest)
            if disk_size == expected or (recorded_size and disk_size == recorded_size):
                if not recorded:
                    record(disk_size, 1)
                    return outcome("self_healed", disk_size, "recorded")
                return outcome("skipped", disk_size, "ok")
            if disk_size > expected:
                os.unlink(dest)
            elif not os.path.exists(part):
                # Legacy partial final file: use it for one resume probe.
                os.replace(dest, part)

        if os.path.exists(part):
            part_size = os.path.getsize(part)
            if part_size == expected:
                os.replace(part, dest)
                record(part_size, 1)
                return outcome("downloaded", part_size, "completed_part")
            if part_size > expected:
                os.unlink(part)

        notify_message = f"{label} -> {human_bytes(expected)}"
        reporter = ProgressReporter(progress_mode, self.downloads_path, expected, label)
        reporter.update(0, expected)

        # HLS mode: short independent segments survive server resets and
        # resume across runs. The stream is remuxed losslessly to MKV or MP4.
        if str(self.settings.get("download_mode", "hls")).lower() == "hls":
            try:
                os.unlink(part)
            except OSError:
                pass
            hls_retries = max(1, int(self.settings.get("max_retries", 3)))
            last_hls_error = "unknown"
            for attempt in range(1, hls_retries + 1):
                try:
                    hls_result = self._hls_download(
                        episode_id, media_source_id, dest, expected, reporter, label,
                        source=source,
                    )
                    actual = int(hls_result.get("size") or os.path.getsize(dest))
                    reporter.finish("done")
                    record(actual, attempt)
                    return outcome("downloaded", actual, "hls")
                except AuthError:
                    raise
                except ToolError as exc:
                    last_hls_error = str(exc)
                    if attempt < hls_retries:
                        reporter.event(
                            "retry",
                            message=f"{label}: attempt {attempt}/{hls_retries} failed ({exc}); retrying in 5s...",
                        )
                        time.sleep(5)
                except Exception as exc:  # noqa: BLE001 - keep the run alive
                    last_hls_error = str(exc)
                    if attempt < hls_retries:
                        reporter.event(
                            "retry",
                            message=f"{label}: attempt {attempt}/{hls_retries} failed ({exc}); retrying in 5s...",
                        )
                        time.sleep(5)
            reporter.finish("failed")
            return outcome("failed", None, f"hls_error:{last_hls_error}")

        max_retries = max(1, int(self.settings.get("max_retries", 5)))
        stall_seconds = int(self.settings.get("speed_time_seconds", 60))
        resume_stall = int(self.settings.get("resume_stall_seconds", 10))
        resume_enabled = bool(self.settings.get("resume_enabled", True))
        last_reason = "download_failed"

        # One short resume probe on an existing partial. Range requests can
        # hang on some servers, so never let the probe consume a full retry
        # budget; a partial that cannot be resumed is restarted fresh.
        if (resume_enabled and os.path.exists(part)
                and os.path.getsize(part) > 0):
            before = os.path.getsize(part)
            reporter.event(
                "resume",
                message=f"{label}: probing resume at {human_bytes(before)}",
            )
            probe = self._run_curl(
                self.api.get_download_url(episode_id, media_source_id), part,
                resume=True, stall_seconds=resume_stall, reporter=reporter,
            )
            after = os.path.getsize(part) if os.path.exists(part) else 0
            if (probe["rc"] == 0 and after == expected
                    and self._content_type_ok(probe["content_type"])):
                os.replace(part, dest)
                reporter.finish("done")
                record(after, 1)
                return outcome("downloaded", after, "resumed")
            reporter.event(
                "retry",
                message=(f"{label}: resume unusable (rc={probe['rc']}, "
                         f"{human_bytes(before)}->{human_bytes(after)}); "
                         f"restarting fresh"),
            )
            try:
                os.unlink(part)
            except OSError:
                pass

        for attempt in range(1, max_retries + 1):
            ensure_disk_space(self.downloads_path, expected, self.config, label)
            reporter.event("attempt", message=f"{label}: attempt {attempt}/{max_retries}")
            try:
                os.unlink(part)
            except OSError:
                pass
            reporter.update(0, expected)
            result = self._run_curl(
                self.api.get_download_url(episode_id, media_source_id), part,
                resume=False, stall_seconds=stall_seconds, reporter=reporter,
            )
            actual = result["size"]
            http_code = result["http"]

            if http_code in (401, 403):
                reporter.finish("failed")
                raise AuthError(
                    f"Authentication failed while downloading {label} (HTTP {http_code})"
                )

            # Size is authoritative: this server drops the socket after the
            # final byte, so a non-zero curl rc must not discard a full file.
            if actual == expected:
                os.replace(part, dest)
                reporter.finish("done")
                record(actual, attempt)
                return outcome("downloaded", actual, "ok")

            if result["rc"] == 0 and not self._content_type_ok(result["content_type"]):
                last_reason = f"bad_content_type:{result['content_type']}"
            elif result["rc"] == 0 and actual != expected:
                last_reason = f"size_mismatch:{actual}!={expected}"
            elif http_code >= 500:
                last_reason = f"http_{http_code}"
            elif result["rc"] == 22 and http_code == 404:
                last_reason = "http_404"
            elif result["rc"] in (6, 7):
                last_reason = "server_unreachable"
            else:
                last_reason = f"curl_rc_{result['rc']}"

            reporter.event(
                "retry",
                message=(f"{label}: attempt {attempt} failed ({last_reason}, "
                         f"{human_bytes(actual)}/{human_bytes(expected)})"),
            )
            try:
                if os.path.exists(part):
                    os.unlink(part)
            except OSError:
                pass

            if last_reason == "http_404":
                break
            if attempt < max_retries:
                self.api._backoff(attempt - 1)
                reporter.update(0, expected)

        reporter.finish("failed")
        return outcome("failed", None, last_reason)


def describe_item(item: Dict[str, Any]) -> str:
    size = human_bytes(item.get("size_bytes"))
    source = ""
    if item.get("source_index") and item.get("media_source_id"):
        name = item.get("source_name") or ""
        source = f" [source {item['source_index']}"
        if name:
            source += f": {name}"
        source += "]"
    return (
        f"S{int(item['season']):02d}E{int(item['episode']):02d} "
        f"{item.get('title') or ''} [{size}]{source}"
    ).strip()


def execute_plan(plan: Dict[str, Any], api: EmbyAPI, dataset: DatasetManager,
                 config: Dict[str, Any], progress_mode: str,
                 json_output: bool, notify_summary: bool = False) -> int:
    """Download every todo item in a plan. Returns 0 ok, 1 failures."""
    series = plan["series"]
    items = plan["items"]
    todo = [item for item in items if item["status"] != "skip"]
    if not todo:
        for item in items:
            if json_output:
                emit_json_event("verdict", status="skipped",
                                series=series["name"], season=item["season"],
                                episode=item["episode"], path=item["relative_path"],
                                size_bytes=item.get("size_bytes"))
            else:
                info(f">> SKIP (already downloaded): {item['relative_path']}")
        if json_output:
            emit_json_event("summary", series=series, total=len(items),
                            downloaded=0, skipped=len(items), failed=0,
                            bytes_downloaded=0)
        return 0

    ensure_disk_space(get_downloads_path(config), plan["summary"].get("todo_bytes"),
                      config, f"{series['name']} ({len(todo)} episodes)")

    engine = DownloadEngine(config, api, dataset)
    downloaded = skipped = failed = 0
    bytes_downloaded = 0
    failures: List[Dict[str, Any]] = []

    progress_state = {"completed": 0, "total": len(items), "stalled_for": 0}
    stop_event = threading.Event()

    def _periodic_notify():
        interval = 600
        settings = config.get("notifications") or {}
        stall_after = max(120, int(settings.get("stall_alert_seconds", 300)))
        last_notice = time.monotonic()
        last_signature = None
        last_change = time.monotonic()
        stall_reported = None
        while not stop_event.wait(30):
            try:
                st = read_state(get_downloads_path(config))
                st = st if isinstance(st, dict) else {}
                now = time.monotonic()
                signature = (st.get("label"), st.get("downloaded"), st.get("status"))
                if signature != last_signature:
                    last_signature = signature
                    last_change = now
                    stall_reported = None
                stalled_for = int(now - last_change)
                progress_state["stalled_for"] = stalled_for
                cur_label = st.get("label") or ""
                cur_pct = st.get("percent")
                pct_str = (f"{cur_pct:.1f}%" if isinstance(cur_pct, (int, float))
                           else "—")
                completed = progress_state["completed"]
                total = progress_state["total"]
                title = f"{PROG}: {series['name']}"
                if stalled_for >= stall_after and signature != stall_reported:
                    stall_reported = signature
                    notify(
                        config, title,
                        f"No progress for {stalled_for // 60} min | "
                        f"Completed: {completed}/{total} | "
                        f"Current: {cur_label or '—'} ({pct_str})",
                        urgency="critical",
                    )
                if now - last_notice >= interval:
                    last_notice = now
                    speed = st.get("speed_bps")
                    speed_str = (f"{human_bytes(speed)}/s"
                                 if isinstance(speed, (int, float)) else "—")
                    eta = st.get("eta_seconds")
                    if (eta is None or eta == 0) and isinstance(speed, (int, float)) and speed > 0:
                        dl = st.get("downloaded")
                        tot = st.get("total")
                        if isinstance(dl, int) and isinstance(tot, int) and tot > dl:
                            eta = int((tot - dl) / speed)
                    eta_str = f" | ETA: {human_duration(eta)}" if eta is not None else ""
                    message = (
                        f"Completed: {completed}/{total} "
                        f"(remaining: {max(0, total - completed)}) | "
                        f"Current: {cur_label or '—'} ({pct_str}) | "
                        f"Speed: {speed_str}{eta_str}"
                    )
                    notify(config, title, message)
            except Exception:
                pass

    periodic_thread = None
    if notify_summary:
        periodic_thread = threading.Thread(target=_periodic_notify, daemon=True)
        periodic_thread.start()

    bot_thread = None
    _tg_token = (config.get("notifications", {}) or {}).get("telegram_bot_token")
    if _tg_token:
        bot_thread = threading.Thread(
            target=run_telegram_bot,
            args=(config, progress_state, stop_event),
            daemon=True,
        )
        bot_thread.start()

    try:
        with DownloadLock(get_downloads_path(config)):
            pending_queue: collections.deque[Tuple[Dict[str, Any], int]] = collections.deque(
                (item, 1) for item in items
            )
            attempted_counter = 0
            max_reschedule_rounds = max(1, int(config.get("download_settings", {}).get("queue_item_reschedule_rounds", 2)))

            while pending_queue:
                item, attempt_round = pending_queue.popleft()
                attempted_counter += 1
                if not json_output:
                    round_str = f" [retry round {attempt_round}]" if attempt_round > 1 else ""
                    info(f"--- [{attempted_counter}/{len(items)}] {describe_item(item)}{round_str} ---")
                result = engine.download_item(
                    series["name"], series["id"], item, progress_mode=progress_mode,
                    source_spec=plan.get("source", "1"),
                )
                status = result["status"]
                if status in ("downloaded", "self_healed"):
                    downloaded += 1
                    bytes_downloaded += int(result.get("size_bytes") or 0)
                elif status == "skipped":
                    skipped += 1
                else:
                    if attempt_round < max_reschedule_rounds:
                        pending_queue.append((item, attempt_round + 1))
                        if not json_output:
                            warn(f">> Rescheduled {result['relative_path']} to end of queue for retry ({result.get('reason') or 'failed'})")
                        if notify_summary:
                            notify(
                                config,
                                f"{PROG}: {series['name']}",
                                f"S{result['season']:02d}E{result['episode']:02d} failed ({result.get('reason') or 'download_failed'}). Rescheduled to end of queue.",
                            )
                        continue
                    else:
                        failed += 1
                        failures.append(result)
                        if not json_output:
                            warn(f">> FAILED: {result['relative_path']} ({result['reason']})")
                progress_state["completed"] = downloaded + skipped
                if notify_summary and status != "skipped":
                    title = f"{PROG}: {series['name']}"
                    completed = progress_state["completed"]
                    total = progress_state["total"]
                    if status in ("downloaded", "self_healed"):
                        detail = ("already on disk" if status == "self_healed"
                                  else human_bytes(result.get("size_bytes")))
                        notify(
                            config, title,
                            f"Finished: S{result['season']:02d}E"
                            f"{result['episode']:02d} ({detail}) | "
                            f"Completed: {completed}/{total}",
                        )
                    else:
                        notify(
                            config, title,
                            f"FAILED: S{result['season']:02d}E"
                            f"{result['episode']:02d} "
                            f"({result.get('reason') or 'download_failed'}) | "
                            f"Completed: {completed}/{total}",
                            urgency="critical",
                        )
                if json_output:
                    emit_json_event(
                        "verdict", status=status, reason=result.get("reason"),
                        series=series["name"], season=result["season"],
                        episode=result["episode"], episode_id=result.get("episode_id"),
                        path=result["relative_path"], size_bytes=result.get("size_bytes"),
                    )
                elif status == "skipped":
                    info(f">> SKIP (already downloaded): {result['relative_path']}")
                elif status == "self_healed":
                    info(f">> File existed and matched; recorded in dataset: {result['relative_path']}")
    finally:
        stop_event.set()
        if periodic_thread is not None:
            periodic_thread.join(timeout=2)
        if bot_thread is not None:
            bot_thread.join(timeout=2)

    if json_output:
        emit_json_event(
            "summary", series=series, total=len(items), downloaded=downloaded,
            skipped=skipped, failed=failed, bytes_downloaded=bytes_downloaded,
            failures=[{"path": f["relative_path"], "reason": f["reason"]} for f in failures],
        )
    else:
        info(
            f"=== Done: {downloaded} downloaded, {skipped} skipped, "
            f"{failed} failed ({human_bytes(bytes_downloaded)}) ==="
        )

    if notify_summary and len(items) > 1:
        settings = config.get("notifications", {})
        title = f"{PROG}: {series['name']}"
        message = (f"{downloaded} downloaded, {skipped} skipped, {failed} failed "
                   f"({human_bytes(bytes_downloaded)})")
        if failed and settings.get("notify_on_failure", True):
            notify(config, title, message, urgency="critical")
        elif not failed and settings.get("notify_on_complete", True):
            notify(config, title, message)

    return 0 if failed == 0 else 1


# =============================================================================
# QUEUE ENGINE
# =============================================================================

QUEUE_FILE_NAME = ".download_queue.json"
QUEUE_STATE_NAME = ".queue_state.json"


class QueueItem:
    """A series item waiting in the download queue."""

    def __init__(self, series: str, spec: Optional[List[str]] = None,
                 series_id: Optional[str] = None, source: str = "1",
                 include_specials: bool = False):
        self.series = series.strip()
        self.spec = list(spec) if spec else ["all"]
        self.series_id = str(series_id) if series_id else None
        self.source = str(source) if source else "1"
        self.include_specials = bool(include_specials)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "series": self.series,
            "spec": self.spec,
            "series_id": self.series_id,
            "source": self.source,
            "include_specials": self.include_specials,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QueueItem":
        return cls(
            series=data.get("series", ""),
            spec=data.get("spec", ["all"]),
            series_id=data.get("series_id"),
            source=data.get("source", "1"),
            include_specials=bool(data.get("include_specials", False)),
        )

    def __repr__(self) -> str:
        spec_str = " ".join(self.spec) if self.spec else "all"
        id_str = f" [id={self.series_id}]" if self.series_id else ""
        return f"{self.series}{id_str} {spec_str}"


def is_spec_token(token: str) -> bool:
    t = token.strip().lower()
    if t in ("all", "last", "missing", "new"):
        return True
    if _TOKEN_SE.match(token) or _TOKEN_S.match(token) or _TOKEN_E.match(token):
        return True
    return False


def parse_series_and_spec_line(line: str) -> Optional[QueueItem]:
    """Parse one line of text into a QueueItem."""
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.split()

    series_id = None
    source = "1"
    clean_tokens: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--series-id" and i + 1 < len(tokens):
            series_id = tokens[i + 1]
            i += 2
            continue
        if tok.startswith("--series-id="):
            series_id = tok.split("=", 1)[1]
            i += 1
            continue
        if tok == "--source" and i + 1 < len(tokens):
            source = tokens[i + 1]
            i += 2
            continue
        if tok.startswith("--source="):
            source = tok.split("=", 1)[1]
            i += 1
            continue
        clean_tokens.append(tok)
        i += 1

    if not clean_tokens:
        return None

    spec_tokens: List[str] = []
    while clean_tokens and is_spec_token(clean_tokens[-1]):
        spec_tokens.insert(0, clean_tokens.pop())

    series_name = " ".join(clean_tokens).strip()
    if (series_name.startswith('"') and series_name.endswith('"')) or \
       (series_name.startswith("'") and series_name.endswith("'")):
        series_name = series_name[1:-1].strip()

    if not series_name:
        return None

    return QueueItem(
        series=series_name,
        spec=spec_tokens if spec_tokens else ["all"],
        series_id=series_id,
        source=source,
    )


def parse_queue_args(tokens: List[str]) -> List[QueueItem]:
    """Parse a list of CLI tokens into a list of QueueItems."""
    if not tokens:
        return []

    # Single argument containing comma or semicolon
    if len(tokens) == 1 and (";" in tokens[0] or "," in tokens[0]):
        delim = ";" if ";" in tokens[0] else ","
        parts = [p.strip() for p in tokens[0].split(delim) if p.strip()]
        items: List[QueueItem] = []
        for part in parts:
            qi = parse_series_and_spec_line(part)
            if qi:
                items.append(qi)
        return items

    # Group tokens by explicit delimiters (;, --, +)
    groups: List[List[str]] = []
    current_group: List[str] = []
    for tok in tokens:
        if tok in (";", "--", "+"):
            if current_group:
                groups.append(current_group)
                current_group = []
        elif tok.endswith(";") and len(tok) > 1:
            current_group.append(tok[:-1])
            groups.append(current_group)
            current_group = []
        elif tok.endswith(",") and len(tok) > 1:
            current_group.append(tok[:-1])
            groups.append(current_group)
            current_group = []
        else:
            current_group.append(tok)
    if current_group:
        groups.append(current_group)

    if len(groups) > 1:
        items = []
        for g in groups:
            qi = parse_series_and_spec_line(" ".join(g))
            if qi:
                items.append(qi)
        return items

    # No delimiters: check if tokens contain multiple series
    items = []
    cur_series_words: List[str] = []
    cur_specs: List[str] = []

    def flush_item() -> None:
        nonlocal cur_series_words, cur_specs
        if cur_series_words:
            s_name = " ".join(cur_series_words).strip()
            if (s_name.startswith('"') and s_name.endswith('"')) or \
               (s_name.startswith("'") and s_name.endswith("'")):
                s_name = s_name[1:-1].strip()
            if s_name:
                items.append(QueueItem(
                    series=s_name,
                    spec=cur_specs if cur_specs else ["all"],
                ))
            cur_series_words = []
            cur_specs = []

    for tok in tokens:
        if is_spec_token(tok):
            cur_specs.append(tok)
        else:
            # If we already have specs, or if the current token has whitespace (was a quoted string)
            # and we already have series words, flush the previous item!
            if cur_specs:
                flush_item()
                cur_series_words.append(tok)
            elif cur_series_words and (" " in tok or tok.startswith('"') or tok.startswith("'")):
                flush_item()
                cur_series_words.append(tok)
            else:
                cur_series_words.append(tok)
    flush_item()
    return items


def load_queue_from_file(file_path: str) -> List[QueueItem]:
    if not os.path.exists(file_path):
        raise UsageError(f"Queue file not found: {file_path}")
    items: List[QueueItem] = []
    with open(file_path, "r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            qi = parse_series_and_spec_line(line)
            if qi:
                items.append(qi)
    return items


def persistent_queue_file(downloads_path: str) -> str:
    return os.path.join(downloads_path, QUEUE_FILE_NAME)


def load_persistent_queue(downloads_path: str) -> List[QueueItem]:
    path = persistent_queue_file(downloads_path)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, list):
            return [QueueItem.from_dict(d) for d in data if isinstance(d, dict)]
        return []
    except (json.JSONDecodeError, OSError):
        return []


def save_persistent_queue(downloads_path: str, items: List[QueueItem]) -> None:
    path = persistent_queue_file(downloads_path)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([item.to_dict() for item in items], handle,
                      ensure_ascii=False, indent=2)
    except OSError as exc:
        raise ConfigError(f"Failed to save queue to {path}: {exc}")


def update_queue_state(downloads_path: str, data: Dict[str, Any]) -> None:
    path = os.path.join(downloads_path, QUEUE_STATE_NAME)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    except OSError:
        pass


def execute_queue(api: EmbyAPI, dataset: DatasetManager, config: Dict[str, Any],
                  queue: List[QueueItem], progress_mode: str = "auto",
                  json_output: bool = False, notify_summary: bool = True) -> int:
    """Execute a list of QueueItems sequentially with queue-level state and error isolation."""
    if not queue:
        if not json_output:
            info("Queue is empty.")
        return 0

    downloads_path = get_downloads_path(config)
    total_series = len(queue)
    start_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not json_output:
        info(f"=== Queue: {total_series} series to process ===")

    total_episodes_planned = 0
    total_bytes_planned = 0
    plans: List[Tuple[QueueItem, Optional[Dict[str, Any]]]] = []

    for item in queue:
        try:
            series = api.resolve_series(item.series, item.series_id)
            selection = parse_selection(item.spec)
            source_spec = parse_source_spec(item.source)
            plan = build_plan(api, dataset, config, series, selection,
                              resolve_unknown_sizes=False, source_spec=source_spec,
                              include_specials=item.include_specials)
            plans.append((item, plan))
            total_episodes_planned += plan["summary"]["total"]
            total_bytes_planned += (plan["summary"].get("todo_bytes") or 0)
        except Exception as exc:
            plans.append((item, None))
            warn(f"Warning: could not resolve {item.series}: {exc}")

    if notify_summary:
        notify(
            config,
            f"{PROG}: Queue Started",
            f"Processing {total_series} series (~{human_bytes(total_bytes_planned)}, "
            f"{total_episodes_planned} episodes).",
        )

    queue_completed_eps = 0
    successful_series = 0
    failed_series = 0

    series_info_list = []
    for it, p in plans:
        summary = p.get("summary", {}) if p else {}
        size_b = summary.get("total_bytes") or summary.get("todo_bytes")
        series_info_list.append({
            "name": it.series,
            "spec": " ".join(it.spec),
            "id": it.series_id or (p.get("series", {}).get("id") if p else ""),
            "total": summary.get("total", 0) if p else len(it.spec),
            "size_bytes": size_b if size_b else None,
        })

    for idx, (item, pre_plan) in enumerate(plans, start=1):
        target_sids = [str(it.series_id) for it, _ in plans if it.series_id]
        if not target_sids and pre_plan:
            target_sids = [str(p.get("series", {}).get("id")) for _, p in plans if p]
        update_queue_state(downloads_path, {
            "running": True,
            "current_index": idx,
            "total_series": total_series,
            "current_name": item.series,
            "target_series_ids": target_sids,
            "series_list": series_info_list,
            "queue_completed_eps": queue_completed_eps,
            "total_eps": total_episodes_planned,
            "started_at": start_time,
            "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

        if not json_output:
            info(f"\n>>> [{idx}/{total_series}] Queue: {item.series} {' '.join(item.spec)}")

        try:
            series = api.resolve_series(item.series, item.series_id)
            selection = parse_selection(item.spec)
            source_spec = parse_source_spec(item.source)
            plan = build_plan(api, dataset, config, series, selection,
                              resolve_unknown_sizes=False, source_spec=source_spec,
                              include_specials=item.include_specials)
            rc = execute_plan(plan, api, dataset, config, progress_mode,
                              json_output, notify_summary=notify_summary)
            if rc == 0:
                successful_series += 1
            else:
                failed_series += 1
            queue_completed_eps += len(plan["items"])
        except Exception as exc:
            failed_series += 1
            warn(f">> FAILED batch {item.series}: {exc}")
            if notify_summary:
                notify(
                    config,
                    f"{PROG}: Batch Alert",
                    f"Series {item.series} encountered error: {exc}. Moving to next...",
                    urgency="critical",
                )

    # Final sweep pass for any incomplete series in the queue
    if failed_series > 0:
        if not json_output:
            info(f"\n{'='*70}\nRunning auto-retry sweep pass for {failed_series} incomplete series...\n{'='*70}")
        for item, pre_plan in plans:
            try:
                series = api.resolve_series(item.series, item.series_id)
                selection = parse_selection(item.spec)
                source_spec = parse_source_spec(item.source)
                plan = build_plan(api, dataset, config, series, selection,
                                  resolve_unknown_sizes=False, source_spec=source_spec,
                                  include_specials=item.include_specials)
                if plan["summary"]["todo"] > 0:
                    rc = execute_plan(plan, api, dataset, config, progress_mode,
                                      json_output, notify_summary=notify_summary)
                    if rc == 0:
                        failed_series = max(0, failed_series - 1)
                        successful_series += 1
            except Exception:
                pass

    end_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    update_queue_state(downloads_path, {
        "running": False,
        "status": "completed",
        "current_index": total_series,
        "total_series": total_series,
        "current_name": "Completed",
        "queue_completed_eps": queue_completed_eps,
        "total_eps": total_episodes_planned,
        "started_at": start_time,
        "ended_at": end_time,
    })

    if notify_summary:
        summary_msg = (
            f"Queue finished: {successful_series}/{total_series} series successful "
            f"({failed_series} failed)."
        )
        notify(config, f"{PROG}: Queue Finished", summary_msg)

    if not json_output:
        info(f"\n=== Queue Finished: {successful_series}/{total_series} series OK, "
             f"{failed_series} failed ===")

    return 0 if failed_series == 0 else 1


def plan_queue(api: EmbyAPI, dataset: DatasetManager, config: Dict[str, Any],
               queue: List[QueueItem], json_output: bool = False) -> int:
    """Show plan and total sizes for a queue of series."""
    if not queue:
        info("Queue is empty.")
        return 0

    grand_total_eps = 0
    grand_todo_eps = 0
    grand_total_bytes = 0
    grand_todo_bytes = 0
    series_plans = []

    for it in queue:
        try:
            s = api.resolve_series(it.series, it.series_id)
            plan = build_plan(api, dataset, config, s, parse_selection(it.spec),
                              resolve_unknown_sizes=True, source_spec=it.source,
                              include_specials=it.include_specials)
            sm = plan["summary"]
            grand_total_eps += sm["total"]
            grand_todo_eps += sm["todo"]
            grand_total_bytes += (sm.get("total_bytes") or 0)
            grand_todo_bytes += (sm.get("todo_bytes") or 0)
            series_plans.append(plan)
            if not json_output:
                info(f"  {s['Name']} [{' '.join(it.spec)}]: {sm['todo']}/{sm['total']} eps, {human_bytes(sm['todo_bytes'])}")
        except Exception as exc:
            warn(f"  {it.series}: error resolving plan ({exc})")

    if json_output:
        emit_json({
            "queue_plans": series_plans,
            "summary": {
                "total_series": len(queue),
                "total_episodes": grand_total_eps,
                "todo_episodes": grand_todo_eps,
                "total_bytes": grand_total_bytes,
                "todo_bytes": grand_todo_bytes,
            }
        })
        return 0

    info(f"\nGrand Total: {grand_todo_eps}/{grand_total_eps} episodes to download ({human_bytes(grand_todo_bytes)})")
    return 0


# =============================================================================
# COMMANDS
# =============================================================================

def make_api(config: Dict[str, Any]) -> EmbyAPI:
    return EmbyAPI(config)


def resolution_for(args: argparse.Namespace) -> List[str]:
    return getattr(args, "spec", []) or []


def cmd_download(args: argparse.Namespace, config: Dict[str, Any],
                 json_output: bool) -> int:
    queue_file = getattr(args, "file", None)
    series_list = getattr(args, "series_list", None) or []
    series_arg = getattr(args, "series", None) or ""
    has_delimiter = bool(series_arg and ("," in series_arg or ";" in series_arg))

    if queue_file or series_list or has_delimiter or (not series_arg and not queue_file):
        if not series_arg and not queue_file and not series_list:
            raise UsageError("Missing series name (e.g. emby-dl download <series> [spec...]) or pass -f <queue_file>")
        api = make_api(config)
        dataset = DatasetManager(get_downloads_path(config))
        queue: List[QueueItem] = []
        if queue_file:
            queue.extend(load_queue_from_file(queue_file))
        if series_list:
            for s in series_list:
                qi = parse_series_and_spec_line(s)
                if qi:
                    queue.append(qi)
        if has_delimiter:
            delim = ";" if ";" in series_arg else ","
            for part in series_arg.split(delim):
                qi = parse_series_and_spec_line(part)
                if qi:
                    queue.append(qi)
        if queue:
            progress_mode = "json" if json_output else (getattr(args, "progress", None) or "auto")
            if getattr(args, "notify", False):
                config.setdefault("notifications", {})["desktop"] = True
            webhook = getattr(args, "webhook", None)
            if webhook:
                config.setdefault("notifications", {})["webhook_url"] = webhook
            notify_summary = bool(
                getattr(args, "notify", False) or webhook
                or config.get("notifications", {}).get("desktop")
                or config.get("notifications", {}).get("webhook_url")
                or config.get("notifications", {}).get("telegram_bot_token")
            )
            return execute_queue(api, dataset, config, queue, progress_mode,
                                 json_output, notify_summary=notify_summary)

    api = make_api(config)
    series = api.resolve_series(args.series, getattr(args, "series_id", None))
    selection = parse_selection(resolution_for(args))
    source_spec = parse_source_spec(getattr(args, "source", "1"))
    dataset = DatasetManager(get_downloads_path(config))
    plan = build_plan(api, dataset, config, series, selection,
                      resolve_unknown_sizes=False, source_spec=source_spec,
                      include_specials=bool(getattr(args, "include_specials", False)))

    if json_output:
        emit_json_event(
            "start", series=plan["series"], selection=selection,
            source=source_spec,
            total=plan["summary"]["total"], todo=plan["summary"]["todo"],
        )
    else:
        info(f"=== Series: {series.get('Name')} (id={series.get('Id')}) ===")
        if source_spec != "1":
            info(f"=== Source: {source_spec} ===")
        if plan["summary"]["total"] == 0:
            info("Nothing matches this selection.")
            return 0

    progress_mode = "json" if json_output else (getattr(args, "progress", None) or "auto")
    if getattr(args, "notify", False):
        config.setdefault("notifications", {})["desktop"] = True
    webhook = getattr(args, "webhook", None)
    if webhook:
        config.setdefault("notifications", {})["webhook_url"] = webhook

    notify_summary = bool(
        getattr(args, "notify", False) or webhook
        or config.get("notifications", {}).get("desktop")
        or config.get("notifications", {}).get("webhook_url")
        or config.get("notifications", {}).get("telegram_bot_token")
    )
    return execute_plan(plan, api, dataset, config, progress_mode,
                        json_output, notify_summary=notify_summary)


def cmd_plan(args: argparse.Namespace, config: Dict[str, Any],
             json_output: bool) -> int:
    queue_file = getattr(args, "file", None)
    series_list = getattr(args, "series_list", None) or []
    series_arg = getattr(args, "series", None) or ""
    has_delimiter = bool(series_arg and ("," in series_arg or ";" in series_arg))

    if queue_file or series_list or has_delimiter or (not series_arg and not queue_file):
        if not series_arg and not queue_file and not series_list:
            raise UsageError("Missing series name (e.g. emby-dl plan <series> [spec...]) or pass -f <queue_file>")
        api = make_api(config)
        dataset = DatasetManager(get_downloads_path(config))
        queue: List[QueueItem] = []
        if queue_file:
            queue.extend(load_queue_from_file(queue_file))
        if series_list:
            for s in series_list:
                qi = parse_series_and_spec_line(s)
                if qi:
                    queue.append(qi)
        if has_delimiter:
            delim = ";" if ";" in series_arg else ","
            for part in series_arg.split(delim):
                qi = parse_series_and_spec_line(part)
                if qi:
                    queue.append(qi)
        return plan_queue(api, dataset, config, queue, json_output)

    api = make_api(config)
    series = api.resolve_series(args.series, getattr(args, "series_id", None))
    selection = parse_selection(resolution_for(args))
    source_spec = parse_source_spec(getattr(args, "source", "1"))
    dataset = DatasetManager(get_downloads_path(config))
    plan = build_plan(api, dataset, config, series, selection,
                      resolve_unknown_sizes=True, source_spec=source_spec,
                      include_specials=bool(getattr(args, "include_specials", False)))

    if json_output:
        emit_json(plan)
        return 0

    info(f"Series: {plan['series']['name']} (id={plan['series']['id']})")
    if source_spec != "1":
        info(f"Source: {source_spec} (plan shows the selected source per episode)")
    info("")
    for item in plan["items"]:
        label = "SKIP" if item["status"] == "skip" else "GET "
        if item["reason"] == "self_heal":
            label = "HEAL"
        elif item["reason"] == "recorded_missing":
            label = "MISS"
        elif item["reason"] == "size_unknown":
            label = "?   "
        elif item["reason"] == "source_changed":
            label = "SRC "
        detail = human_bytes(item.get("size_bytes"))
        if item["on_disk"] and item.get("disk_bytes") is not None \
                and item.get("disk_bytes") != item.get("size_bytes"):
            detail += f" (on disk {human_bytes(item['disk_bytes'])})"
        if item.get("source_index") and item.get("media_source_id"):
            name = item.get("source_name") or ""
            detail += f" [src {item['source_index']}"
            if name:
                detail += f": {name}"
            detail += "]"
        info(
            f"  {label} S{int(item['season']):02d}E{int(item['episode']):02d}  "
            f"{detail:>12}  {item.get('title') or ''}"
        )
    summary = plan["summary"]
    info("")
    info(
        f"Summary: {summary['todo']} to download, {summary['skip']} skipped, "
        f"{summary['unknown_sizes']} unknown size, "
        f"{summary['source_changed']} source changed, "
        f"{human_bytes(summary['todo_bytes'])} to transfer"
    )
    return 0


def cmd_queue(args: argparse.Namespace, config: Dict[str, Any],
              json_output: bool) -> int:
    api = make_api(config)
    dataset = DatasetManager(get_downloads_path(config))
    downloads_path = get_downloads_path(config)

    action = getattr(args, "action", None) or "run"
    tokens = getattr(args, "items", []) or []
    queue_file = getattr(args, "file", None)

    # 1. Action: add
    if action == "add":
        if not tokens:
            raise UsageError("queue add requires a series name (e.g. emby-dl queue add 'Show' S1)")
        items = parse_queue_args(tokens)
        if not items:
            raise UsageError("Could not parse series name from arguments.")
        curr = load_persistent_queue(downloads_path)
        for it in items:
            if getattr(args, "series_id", None):
                it.series_id = args.series_id
            if getattr(args, "source", None):
                it.source = args.source
            curr.append(it)
        save_persistent_queue(downloads_path, curr)
        if not json_output:
            for it in items:
                info(f"Added to queue: {it}")
            info(f"Persistent queue now contains {len(curr)} item(s).")
        return 0

    # 2. Action: list
    if action == "list":
        curr = load_persistent_queue(downloads_path)
        if json_output:
            emit_json([it.to_dict() for it in curr])
            return 0
        if not curr:
            info("Queue is empty. Add series with: emby-dl queue add <series> [spec...]")
            return 0
        info(f"=== Persistent Queue ({len(curr)} items) ===")
        for idx, it in enumerate(curr, start=1):
            id_str = f" (id={it.series_id})" if it.series_id else ""
            info(f"  [{idx}] {it.series}{id_str} {' '.join(it.spec)}")
        info("\nRun queue with: emby-dl queue run")
        return 0

    # 3. Action: clear
    if action == "clear":
        save_persistent_queue(downloads_path, [])
        if not json_output:
            info("Persistent queue cleared.")
        return 0

    # 4. Action: remove
    if action == "remove":
        if not tokens:
            raise UsageError("queue remove requires an index (e.g. emby-dl queue remove 1)")
        try:
            remove_idx = int(tokens[0]) - 1
        except ValueError:
            raise UsageError(f"Invalid index: {tokens[0]!r}")
        curr = load_persistent_queue(downloads_path)
        if 0 <= remove_idx < len(curr):
            removed = curr.pop(remove_idx)
            save_persistent_queue(downloads_path, curr)
            if not json_output:
                info(f"Removed [{remove_idx + 1}] {removed.series} from queue.")
        else:
            raise UsageError(f"Index {remove_idx + 1} out of range (1..{len(curr)})")
        return 0

    # 5. Action: plan
    if action == "plan":
        queue: List[QueueItem] = []
        if queue_file:
            queue.extend(load_queue_from_file(queue_file))
        if tokens:
            queue.extend(parse_queue_args(tokens))
        if not queue:
            queue = load_persistent_queue(downloads_path)
        if not queue:
            info("Queue is empty. Specify series or a file to plan.")
            return 0
        return plan_queue(api, dataset, config, queue, json_output)

    # 6. Action: run or direct execution
    queue = []
    if queue_file:
        queue.extend(load_queue_from_file(queue_file))
    if action not in ("run", "add", "list", "clear", "remove", "plan"):
        all_tokens = [action] + tokens
        queue.extend(parse_queue_args(all_tokens))
    elif tokens:
        queue.extend(parse_queue_args(tokens))

    is_persistent = False
    if not queue:
        queue = load_persistent_queue(downloads_path)
        is_persistent = True

    if not queue:
        info("Queue is empty. Pass series names, use -f <file>, or add with 'emby-dl queue add'.")
        return 0

    progress_mode = "json" if json_output else (getattr(args, "progress", None) or "auto")
    if getattr(args, "notify", False):
        config.setdefault("notifications", {})["desktop"] = True
    webhook = getattr(args, "webhook", None)
    if webhook:
        config.setdefault("notifications", {})["webhook_url"] = webhook

    notify_summary = bool(
        getattr(args, "notify", False) or webhook
        or config.get("notifications", {}).get("desktop")
        or config.get("notifications", {}).get("webhook_url")
        or config.get("notifications", {}).get("telegram_bot_token")
    )

    rc = execute_queue(api, dataset, config, queue, progress_mode,
                       json_output, notify_summary=notify_summary)
    if is_persistent and rc == 0:
        save_persistent_queue(downloads_path, [])
    return rc


def _parse_iso(value: Optional[str]) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        return None


def cmd_status(args: argparse.Namespace, config: Dict[str, Any],
               json_output: bool) -> int:
    downloads_path = get_downloads_path(config)
    state = read_state(downloads_path)
    held = lock_is_held(downloads_path)
    running = bool(held and state and state.get("running"))
    updated_ago = None
    updated = _parse_iso((state or {}).get("updated_at"))
    if updated:
        updated_ago = max(0, int((datetime.datetime.now() - updated).total_seconds()))

    if json_output:
        emit_json({
            "running": running,
            "lock_held": held,
            "downloads_path": downloads_path,
            "updated_ago_seconds": updated_ago,
            "state": state,
        })
        return 0

    if not state:
        info("No download running (no state recorded).")
        return 0
    if not held:
        info(
            f"No download running. Last state: {state.get('status', 'unknown')} — "
            f"{state.get('label') or 'n/a'} ({state.get('percent') or 0:.1f}%)."
        )
        return 0
    if not state.get("running"):
        info(f"Lock is held (pid {state.get('pid')}) but state is not running.")
        return 0

    percent = state.get("percent")
    line = (
        f"RUNNING pid {state.get('pid')} | {state.get('label')} | "
        f"{percent:.1f}%" if percent is not None
        else f"RUNNING pid {state.get('pid')} | {state.get('label')} |"
    )
    line += (
        f" | {human_bytes(state.get('downloaded'))}/{human_bytes(state.get('total'))}"
        f" | {human_bytes(state.get('speed_bps'))}/s"
        f" | ETA {human_duration(state.get('eta_seconds'))}"
    )
    if updated_ago is not None:
        line += f" | updated {updated_ago}s ago"
    info(line)
    if updated_ago is not None and updated_ago > 60:
        warn("Warning: progress state is stale (no update in over a minute).")
    return 0


def cmd_verify(args: argparse.Namespace, config: Dict[str, Any],
               json_output: bool) -> int:
    api = make_api(config)
    downloads_path = get_downloads_path(config)
    dataset = DatasetManager(downloads_path)

    series_name = None
    if getattr(args, "series", None):
        series = api.resolve_series(args.series, getattr(args, "series_id", None))
        series_name = series.get("Name")
    entries = dataset.entries(series_name)

    results: List[Dict[str, Any]] = []
    fix_items: List[Dict[str, Any]] = []
    fix_entries: List[Dict[str, Any]] = []

    for entry in entries:
        path = dataset.disk_path(entry)
        on_disk = os.path.exists(path)
        disk_bytes = os.path.getsize(path) if on_disk else None
        detail: Dict[str, Any] = {}
        expected = None
        episode_id = entry.get("episode_id", "")
        source_spec = str(entry.get("source_index") or "1")
        source_missing = False
        if episode_id:
            try:
                detail = api.get_item(episode_id)
                sources = api.media_sources(detail)
                recorded_source = str(entry.get("media_source_id") or "")
                if recorded_source and sources:
                    source_missing = not any(
                        str(source.get("Id") or "") == recorded_source
                        for source in sources
                    )
                if detail and not source_missing:
                    expected = api.resolve_size(
                        detail, allow_head=True, source_spec=source_spec
                    )
            except ToolError:
                detail = {}
        if expected is None:
            expected = parse_int(entry.get("size_bytes"))

        issue = None
        if not on_disk:
            issue = "missing_file"
        elif source_missing:
            issue = "source_missing"
        elif expected is None:
            issue = "unverified_size"
        elif disk_bytes != expected:
            issue = "size_mismatch"
        elif parse_int(entry.get("size_bytes")) != disk_bytes:
            issue = "dataset_size_stale"

        result = {
            "series_name": entry.get("series_name"),
            "series_id": entry.get("series_id"),
            "season": entry.get("season_number"),
            "episode": entry.get("episode_number"),
            "episode_id": episode_id,
            "source_index": entry.get("source_index"),
            "source_name": entry.get("source_name"),
            "media_source_id": entry.get("media_source_id"),
            "path": os.path.relpath(path, downloads_path).replace("\\", "/"),
            "expected_bytes": expected,
            "disk_bytes": disk_bytes,
            "status": "ok" if issue is None else "issue",
            "issue": issue,
        }
        results.append(result)

        if issue in ("missing_file", "size_mismatch", "source_missing"):
            fix_items.append({
                "series_name": entry.get("series_name", ""),
                "series_id": entry.get("series_id", ""),
                "season": int(entry.get("season_number") or 0),
                "episode": int(entry.get("episode_number") or 0),
                "episode_id": episode_id,
                "season_id": entry.get("season_id", ""),
                "title": entry.get("file_name", ""),
                "container": entry.get("container", "mkv"),
                "relative_path": os.path.relpath(path, downloads_path).replace("\\", "/"),
                "path": path,
                "size_bytes": expected,
                "source_spec": source_spec,
                "source_index": entry.get("source_index"),
                "media_source_id": entry.get("media_source_id"),
                "source_name": entry.get("source_name"),
            })
            fix_entries.append(entry)
        elif issue == "dataset_size_stale":
            fix_entries.append(entry)

    fixed = 0
    remaining = 0
    if getattr(args, "fix", False) and results:
        engine = DownloadEngine(config, api, dataset)
        progress_mode = "json" if json_output else (getattr(args, "progress", None) or "auto")
        needs_download = [item for item in fix_items if item.get("size_bytes") is not None]
        if needs_download:
            ensure_disk_space(
                downloads_path,
                sum(int(item.get("size_bytes") or 0) for item in needs_download),
                config, "verify --fix",
            )
            with DownloadLock(downloads_path):
                for item in needs_download:
                    series_for_item = item.get("series_name") or series_name or ""
                    result = engine.download_item(
                        series_for_item, item.get("series_id") or args.series_id or "", item,
                        progress_mode=progress_mode,
                        source_spec=item.get("source_spec", "1"),
                    )
                    if json_output:
                        emit_json_event("verdict", status=result["status"],
                                        reason=result.get("reason"),
                                        series=series_for_item,
                                        season=item["season"], episode=item["episode"],
                                        path=result["relative_path"],
                                        size_bytes=result.get("size_bytes"))
                    if result["status"] in ("downloaded", "self_healed", "skipped"):
                        fixed += 1
                        for entry in results:
                            if entry["series_name"] == series_for_item \
                                    and entry["season"] == item["season"] \
                                    and entry["episode"] == item["episode"]:
                                entry["status"] = "fixed"
                                entry["issue"] = None
                                if result.get("size_bytes"):
                                    entry["disk_bytes"] = result["size_bytes"]
                                    entry["expected_bytes"] = result["size_bytes"]
                    else:
                        remaining += 1
        # Dataset-only drift: update byte counts from disk without downloading.
        for entry in fix_entries:
            path = dataset.disk_path(entry)
            if os.path.exists(path) and not any(
                item["season"] == int(entry.get("season_number") or 0)
                and item["episode"] == int(entry.get("episode_number") or 0)
                and item.get("series_name") == entry.get("series_name")
                for item in needs_download
            ):
                size = os.path.getsize(path)
                if parse_int(entry.get("size_bytes")) != size:
                    entry = dict(entry)
                    entry["size_bytes"] = size
                    entry["size_mb"] = int(round(size / 1048576))
                    dataset.add_entry(entry)

        remaining = sum(1 for result in results if result["status"] == "issue")

    if json_output:
        summary = {
            "checked": len(results),
            "ok": sum(1 for r in results if r["status"] == "ok"),
            "issues": sum(1 for r in results if r["status"] == "issue"),
            "fixed": fixed,
            "remaining": remaining,
        }
        emit_json({"items": results, "summary": summary})
    else:
        for result in results:
            marker = "OK  " if result["status"] == "ok" else (
                "FIX " if result["status"] == "fixed" else "FAIL"
            )
            detail = (f"S{int(result['season']):02d}E{int(result['episode']):02d} "
                      f"{marker} {human_bytes(result['disk_bytes'])}")
            if result["issue"]:
                detail += f"  ({result['issue']})"
            info(f"{result['series_name']}: {detail}  {result['path']}")
        issues = sum(1 for r in results if r["status"] == "issue")
        info(f"\nChecked {len(results)}: {len(results) - issues} ok, "
             f"{issues} issues, {fixed} fixed, {remaining} remaining.")

    issues_left = any(result["status"] == "issue" for result in results)
    return 1 if issues_left else 0


def cmd_watch(args: argparse.Namespace, config: Dict[str, Any],
              json_output: bool) -> int:
    tokens = resolution_for(args)
    if not tokens:
        tokens = ["missing"]
    selection = parse_selection(tokens)
    source_spec = parse_source_spec(getattr(args, "source", "1"))
    interval = max(10, int(getattr(args, "interval", 600)))
    progress_mode = "json" if json_output else (getattr(args, "progress", None) or "auto")
    if getattr(args, "notify", False):
        config.setdefault("notifications", {})["desktop"] = True
    webhook = getattr(args, "webhook", None)
    if webhook:
        config.setdefault("notifications", {})["webhook_url"] = webhook
    notify_summary = bool(
        getattr(args, "notify", False) or webhook
        or config.get("notifications", {}).get("desktop")
        or config.get("notifications", {}).get("webhook_url")
        or config.get("notifications", {}).get("telegram_bot_token")
    )

    if not json_output:
        info(f"Watching {args.series} ({selection['mode']}); interval {interval}s. "
             "Ctrl-C to stop.")

    while True:
        code = 0
        try:
            api = make_api(config)
            series = api.resolve_series(args.series, getattr(args, "series_id", None))
            dataset = DatasetManager(get_downloads_path(config))
            plan = build_plan(api, dataset, config, series, selection,
                              resolve_unknown_sizes=False, source_spec=source_spec,
                              include_specials=bool(getattr(args, "include_specials", False)))
            todo = plan["summary"]["todo"]
            if json_output:
                emit_json_event(
                    "watch_tick", series=plan["series"], todo=todo,
                    total=plan["summary"]["total"],
                )
            elif todo:
                info(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] "
                     f"{todo} new episode(s) to download.")
            else:
                info(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] No new episodes.")

            if todo:
                code = execute_plan(plan, api, dataset, config, progress_mode,
                                    json_output, notify_summary=notify_summary)
            elif json_output:
                emit_json_event(
                    "summary", series=plan["series"], total=plan["summary"]["total"],
                    downloaded=0, skipped=plan["summary"]["total"], failed=0,
                    bytes_downloaded=0,
                )
        except LockBusy as exc:
            code = 5
            if json_output:
                emit_json_event("error", code=5, message=str(exc))
            else:
                warn(f"Another download is running; will retry next tick. ({exc})")
        except ToolError as exc:
            code = exc.exit_code
            if json_output:
                emit_json_event("error", code=exc.exit_code, message=str(exc))
            else:
                warn(f"Watch error: {exc}")

        if getattr(args, "once", False):
            return code
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            if not json_output:
                info("Watch stopped.")
            return 0


def cmd_dataset(args: argparse.Namespace, config: Dict[str, Any],
                json_output: bool) -> int:
    downloads_path = get_downloads_path(config)
    dataset = DatasetManager(downloads_path)

    if getattr(args, "check", False):
        report = dataset.check()
        if json_output:
            emit_json({"action": "check", **report})
        else:
            info(f"Dataset v{report['version']}: {report['entries']} entries "
                 f"across {report['series']} series.")
            if report["ok"]:
                info("Dataset check: OK (central, per-series and disk agree).")
            else:
                warn(f"Dataset check: {len(report['issues'])} issue(s) found.")
                for issue in report["issues"]:
                    if issue["kind"] == "duplicate":
                        warn(f"  duplicate: {issue['series_name']} "
                             f"S{int(issue['season'] or 0):02d}E{int(issue['episode'] or 0):02d}")
                    elif issue["kind"] == "missing_file":
                        warn(f"  missing file: {issue['path']}")
                    elif issue["kind"] == "size_mismatch":
                        warn(f"  size mismatch: {issue['path']} "
                             f"(expected {human_bytes(issue['expected_bytes'])}, "
                             f"on disk {human_bytes(issue['actual_bytes'])})")
                    elif issue["kind"] == "stale_series_dataset":
                        warn(f"  stale per-series dataset: {issue['series_name']} "
                             f"({issue['reason']})")
        return 0 if report["ok"] else 1

    if getattr(args, "sync", False):
        summary = dataset.sync()
        if json_output:
            emit_json({"action": "sync", **summary})
        else:
            info(f"Synced {summary['series']} per-series dataset(s) from the "
                 f"central dataset ({summary['entries']} entries).")
        return 0

    if getattr(args, "dedupe", False):
        removed = dataset.dedupe()
        if json_output:
            emit_json({"action": "dedupe", "removed": removed})
        else:
            info(f"Removed {len(removed)} duplicate dataset entr"
                 f"{'y' if len(removed) == 1 else 'ies'}.")
            for entry in removed:
                info(f"  removed {entry.get('relative_path')}")
        return 0

    if getattr(args, "rebuild", False):
        summary = dataset.rebuild()
        if json_output:
            emit_json({"action": "rebuild", **summary})
        else:
            info(f"Rebuilt dataset: {summary['entries']} entries across "
                 f"{summary['series']} series ({human_bytes(summary['total_bytes'])}).")
            for skipped in summary["skipped_files"]:
                warn(f"Skipped unrecognized file: {skipped}")
        return 0

    if getattr(args, "prune", False):
        removed = dataset.prune()
        if json_output:
            emit_json({"action": "prune", "removed": removed})
        else:
            info(f"Pruned {len(removed)} dataset entries whose files were missing.")
            for entry in removed:
                info(f"  removed {entry.get('relative_path')}")
        return 0

    if getattr(args, "stats", False):
        stats = dataset.stats()
        if json_output:
            emit_json(stats)
        else:
            info(f"Dataset v{stats['version']}: {stats['total_episodes']} entries, "
                 f"{human_bytes(stats['total_bytes'])} recorded, "
                 f"{human_bytes(stats['disk_bytes'])} on disk, "
                 f"{stats['missing']} missing.")
            for bucket in stats["series"]:
                info(f"  {bucket['series_name']}: {bucket['recorded']} recorded, "
                     f"{bucket['on_disk']} on disk, {bucket['missing']} missing, "
                     f"{human_bytes(bucket['total_bytes'])}")
        return 0

    entries = dataset.entries(getattr(args, "series", None))
    output = []
    for entry in entries:
        path = dataset.disk_path(entry)
        output.append({
            **entry,
            "on_disk": os.path.exists(path),
            "disk_bytes": os.path.getsize(path) if os.path.exists(path) else None,
        })

    if json_output:
        emit_json(output)
        return 0

    series_filter = getattr(args, "series", None)
    if series_filter:
        info(f"Downloaded episodes for: {series_filter}")
    else:
        info("All downloaded episodes:")
    if not entries:
        info("  (none)")
        return 0

    current = None
    for entry in output:
        name = entry.get("series_name", "Unknown")
        if name != current:
            current = name
            info(f"\n[{name}]")
        marker = "" if entry["on_disk"] else "  MISSING ON DISK"
        info(
            f"  S{int(entry.get('season_number') or 0):02d}"
            f"E{int(entry.get('episode_number') or 0):02d} | "
            f"{entry.get('file_name', '')} | "
            f"{human_bytes(entry.get('size_bytes'))}{marker}"
        )
    return 0


def cmd_search(args: argparse.Namespace, config: Dict[str, Any],
               json_output: bool) -> int:
    api = make_api(config)
    results = api.search_series(args.query, getattr(args, "limit", 30))
    if json_output:
        emit_json([
            {"id": item.get("Id"), "name": item.get("Name"),
             "year": item.get("ProductionYear")}
            for item in results
        ])
        return 0
    if not results:
        info(f"No results for: {args.query}")
        return 0
    info(f"Found {len(results)} series:")
    for item in results:
        year = f" ({item.get('ProductionYear')})" if item.get("ProductionYear") else ""
        info(f"  {item.get('Id')}\t{item.get('Name')}{year}")
    return 0


def cmd_list(args: argparse.Namespace, config: Dict[str, Any],
             json_output: bool) -> int:
    api = make_api(config)
    series = api.resolve_series(args.series, getattr(args, "series_id", None))
    seasons = sorted(
        api.get_series_seasons(series["Id"]),
        key=lambda season: parse_int(season.get("IndexNumber")) or 0,
    )
    output = []
    for season in seasons:
        episodes = api.get_season_episodes(series["Id"], season["Id"])
        output.append({
            "season_number": parse_int(season.get("IndexNumber")) or 0,
            "season_id": season.get("Id"),
            "season_name": season.get("Name"),
            "episode_count": len(episodes),
        })

    if json_output:
        emit_json({
            "series_id": series.get("Id"),
            "series_name": series.get("Name"),
            "seasons": output,
        })
        return 0

    info(f"Series: {series.get('Name')} (id={series.get('Id')})")
    info("")
    for season in output:
        info(f"  Season {season['season_number']}: {season['season_name']} "
             f"({season['episode_count']} episodes)")
    return 0


def cmd_config(args: argparse.Namespace, config: Dict[str, Any],
               json_output: bool) -> int:
    safe = {key: value for key, value in config.items() if not key.startswith("_")}
    if safe.get("access_token"):
        safe["access_token"] = "***hidden***"
    notifications = safe.get("notifications")
    if isinstance(notifications, dict):
        masked = dict(notifications)
        if masked.get("webhook_url"):
            masked["webhook_url"] = "***hidden***"
        if masked.get("telegram_bot_token"):
            masked["telegram_bot_token"] = "***hidden***"
        if masked != notifications:
            safe["notifications"] = masked
    if json_output:
        emit_json({"config_path": config.get("_config_path"), "config": safe})
        return 0
    info(f"Config file: {config.get('_config_path') or '(defaults; no file found)'}")
    info("")
    for key, value in safe.items():
        if isinstance(value, dict):
            info(f"{key}:")
            for sub_key, sub_value in value.items():
                info(f"  {sub_key}: {sub_value}")
        else:
            info(f"{key}: {value}")
    return 0


def cmd_auth(args: argparse.Namespace, config: Dict[str, Any],
             json_output: bool) -> int:
    api = make_api(config)
    if getattr(args, "reset_caps", False):
        CapsCache(get_downloads_path(config)).reset()
    public = api.get_public_info()
    me = api.get_user()
    # Cache the successful probe so repeated commands can skip live checks.
    CapsCache(get_downloads_path(config)).write({
        "server_name": public.get("ServerName", ""),
        "server_version": public.get("Version", ""),
        "user_id": me.get("Id", ""),
        "is_admin": bool((me.get("Policy") or {}).get("IsAdministrator")),
    })
    payload = {
        "status": "ok",
        "caps_cache_reset": bool(getattr(args, "reset_caps", False)),
        "server_url": config.get("server_url"),
        "server_name": public.get("ServerName"),
        "server_version": public.get("Version"),
        "user_name": me.get("Name"),
        "user_id": me.get("Id"),
        "is_admin": (me.get("Policy") or {}).get("IsAdministrator"),
    }
    if json_output:
        emit_json(payload)
    else:
        if getattr(args, "reset_caps", False):
            info("Capabilities cache cleared.")
        info(f"OK: {payload['server_name']} {payload['server_version']} at "
             f"{payload['server_url']}")
        info(f"User: {payload['user_name']} (id={payload['user_id']})")
    return 0


def cmd_login(args: argparse.Namespace, config: Dict[str, Any],
              json_output: bool) -> int:
    server_url = config.get("server_url") or ""
    if not server_url:
        raise ConfigError("server_url is missing from config; add it before logging in")
    username = args.username or config.get("username")
    if not username:
        raise UsageError("Username required: emby_dl login --username <name>")
    password = args.password
    if password is None:
        password = os.environ.get("EMBY_PASSWORD")
    if password is None:
        if not sys.stdin.isatty():
            raise UsageError(
                "No password provided. Use --password or set EMBY_PASSWORD "
                "(prompting is only available on a TTY)."
            )
        password = getpass.getpass(f"Password for {username}: ")

    payload = authenticate(server_url, username, password)
    token = payload.get("AccessToken") or ""
    user = payload.get("User") or {}
    server_id = payload.get("ServerId") or user.get("ServerId") or config.get("server_id", "")
    if not token:
        raise AuthError("Login succeeded but the server returned no access token")

    config["access_token"] = token
    config["user_id"] = user.get("Id") or config.get("user_id", "")
    config["username"] = user.get("Name") or username
    config["server_id"] = server_id
    saved = save_config(config)

    result = {
        "status": "ok",
        "config_path": saved,
        "user": config["username"],
        "user_id": config["user_id"],
        "server_id": server_id,
    }
    if json_output:
        emit_json(result)
    else:
        info(f"Logged in as {result['user']} (id={result['user_id']}).")
        info(f"Token saved to {saved} (mode 600).")
    return 0


def cmd_init(args: argparse.Namespace, config: Dict[str, Any],
             json_output: bool) -> int:
    config_path = getattr(args, "config", None) or config.get("_config_path") or get_user_config_path()
    downloads_path = getattr(args, "downloads_dir", None) or config.get("downloads_path") or get_default_downloads_dir()
    os.makedirs(downloads_path, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(config_path)), exist_ok=True)

    server_url = getattr(args, "server_url", None) or config.get("server_url") or ""
    username = getattr(args, "username", None) or config.get("username") or ""
    password = getattr(args, "password", None) or os.environ.get("EMBY_PASSWORD")

    is_interactive = sys.stdin.isatty() and not json_output

    if is_interactive:
        if not server_url:
            default_url = "http://localhost:8096"
            entered_url = input(f"Emby Server URL [{default_url}]: ").strip()
            server_url = entered_url or default_url
        if not username:
            username = input("Username: ").strip()
        if not password and username:
            password = getpass.getpass(f"Password for {username}: ")
        chosen_dir = input(f"Downloads directory [{downloads_path}]: ").strip()
        if chosen_dir:
            downloads_path = os.path.abspath(os.path.expanduser(chosen_dir))
            os.makedirs(downloads_path, exist_ok=True)

    token = config.get("access_token") or ""
    user_id = config.get("user_id") or ""
    server_id = config.get("server_id") or ""
    authenticated = False

    if server_url and username and password:
        try:
            auth_data = authenticate(server_url, username, password)
            token = auth_data.get("AccessToken") or ""
            user = auth_data.get("User") or {}
            user_id = user.get("Id") or ""
            server_id = auth_data.get("ServerId") or user.get("ServerId") or ""
            authenticated = bool(token)
        except Exception as exc:
            if not json_output:
                warn(f"Warning: Authentication attempt failed: {exc}")

    new_config = json.loads(json.dumps(DEFAULT_CONFIG))
    new_config["server_url"] = server_url
    new_config["server_id"] = server_id
    new_config["user_id"] = user_id
    new_config["access_token"] = token
    new_config["username"] = username
    new_config["downloads_path"] = downloads_path

    saved = save_config(new_config, path=config_path)

    result = {
        "status": "ok",
        "config_path": saved,
        "downloads_path": downloads_path,
        "server_url": server_url,
        "username": username,
        "authenticated": authenticated,
    }
    if json_output:
        emit_json(result)
    else:
        info(f"Initialized configuration at {saved}")
        info(f"Downloads directory set to: {downloads_path}")
        if authenticated:
            info(f"Successfully authenticated as {username} (id={user_id}).")
        elif not token:
            info("Note: Run 'emby_dl login --username <name>' to store an access token.")
    return 0


# =============================================================================
# DOCTOR
# =============================================================================

def run_doctor(config: Dict[str, Any], json_output: bool,
               network: bool = True) -> Tuple[int, Dict[str, Any]]:
    """Check config, environment, server, tools, dataset and disk health."""
    checks: List[Dict[str, Any]] = []
    downloads_path = get_downloads_path(config)

    def record(name: str, ok: bool, status: str, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok),
                       "status": status, "detail": detail})

    # 1. curl availability
    curl = shutil.which("curl")
    record("curl", bool(curl), "found" if curl else "missing",
           curl or "curl is required for downloads")

    # 2. ffmpeg availability (required only for HLS remux mode)
    dl_mode = str((config.get("download_settings") or {})
                  .get("download_mode", "hls")).lower()
    ffmpeg_candidates = [str((config.get("download_settings") or {})
                             .get("ffmpeg_path") or "").strip()]
    found_which = shutil.which("ffmpeg")
    if found_which:
        ffmpeg_candidates.append(found_which)
    if os.name == "nt":
        ffmpeg_candidates.append(r"C:\Program Files\DownloadHelper CoApp\ffmpeg.exe")
    ffmpeg = next((p for p in ffmpeg_candidates if p and os.path.exists(p)), "")
    if dl_mode == "hls":
        record("ffmpeg", bool(ffmpeg), "found" if ffmpeg else "missing",
               ffmpeg or ("required for download_mode=hls; install with "
                          "winget install Gyan.FFmpeg or set download_settings.ffmpeg_path"))
    else:
        record("ffmpeg", True, "not required", f"download_mode={dl_mode}")

    # 3. server URL + token configured
    server_url = config.get("server_url") or ""
    token = config.get("access_token") or ""
    record("server_url", bool(server_url),
           "configured" if server_url else "missing",
           server_url)
    record("access_token", bool(token),
           "configured" if token else "missing",
           "hidden" if token else "run: emby_dl login --username <name>")

    # 4. env overrides in effect
    env_applied = config.get("_env_applied", [])
    record("env_overrides", True,
           ", ".join(env_applied) if env_applied else "none",
           "EMBY_URL/EMBY_TOKEN/EMBY_USER_ID/EMBY_USERNAME/EMBY_DOWNLOADS_DIR")

    # 5. config file permissions (token secrecy on disk)
    config_path = config.get("_config_path")
    if config_path:
        try:
            mode = os.stat(config_path).st_mode & 0o777
            if os.name == "nt":
                record("config_permissions", True, "windows_acl", "ok")
            else:
                record("config_permissions", mode == 0o600,
                       oct(mode), "expected 600" if mode != 0o600 else "ok")
        except OSError as exc:
            record("config_permissions", False, "unreadable", str(exc))
    else:
        record("config_permissions", False, "no config file",
               "run: emby_dl init or emby_dl login to create one")

    # 6. downloads dir writable (created on demand like the rest of the tool)
    try:
        os.makedirs(downloads_path, exist_ok=True)
        writable = os.access(downloads_path, os.W_OK)
        record("downloads_dir", writable,
               "writable" if writable else "not writable", downloads_path)
    except OSError as exc:
        record("downloads_dir", False, "unwritable", str(exc))

    # 7. free disk space vs configured reserve
    free = shutil.disk_usage(os.path.dirname(downloads_path) or ".").free
    reserve = parse_int(config.get("download_settings", {})
                       .get("disk_reserve_bytes", 2 * 1024 ** 3))
    record("disk_space", free > (reserve or 0),
           f"{human_bytes(free)} free",
           f"reserve {human_bytes(reserve or 2 * 1024 ** 3)}")

    # 8. dataset consistency
    dataset = DatasetManager(downloads_path)
    report = dataset.check()
    record("dataset", report["ok"],
           f"ok ({report['entries']} entries)" if report["ok"]
           else f"{len(report['issues'])} issue(s)",
           "run: emby_dl dataset --check")

    # 9. dataset schema version
    record("dataset_schema", True, f"v{report['version']}",
           f"tool expects v{SCHEMA_VERSION}")

    # 10. live server (skipped with --offline)
    if network:
        caps = CapsCache(downloads_path).read()
        if caps and not os.environ.get("EMBY_NO_CAPS_CACHE"):
            record("server_reachable", True,
                   f"{caps.get('server_name', '?')} "
                   f"{caps.get('server_version', '?')} (cached)",
                   server_url)
            record("token_valid", True, "cached",
                   f"user {caps.get('user_id', '?')}")
        else:
            try:
                api = make_api(config)
                public = api.get_public_info()
                record("server_reachable", True,
                       f"{public.get('ServerName', '?')} {public.get('Version', '?')}",
                       server_url)
                me = {}
                try:
                    me = api.get_user()
                except ToolError:
                    pass
                record("token_valid", bool(me.get("Id")),
                       me.get("Name") or "unauthorized",
                       "run: emby_dl login" if not me.get("Id") else "ok")
            except ToolError as exc:
                record("server_reachable", False, "unreachable", str(exc))
                record("token_valid", False, "unknown", "server unreachable")

    # 11. Telegram Bot integration (if configured)
    bot_token = (config.get("notifications") or {}).get("telegram_bot_token") or ""
    chat_id = (config.get("notifications") or {}).get("telegram_chat_id") or ""
    if bot_token and network:
        me = telegram_api(bot_token, "getMe", timeout=5)
        bot_ok = bool(me.get("ok"))
        bot_user = me.get("result", {}).get("username")
        record("telegram_bot", bot_ok,
               f"@{bot_user}" if bot_ok else "unauthorized",
               "ok" if bot_ok else "invalid telegram_bot_token")
        if bot_ok and chat_id:
            record("telegram_chat", True, f"id={chat_id}", "ok")
        elif bot_ok:
            record("telegram_chat", True, "not registered", "send /start to bot to register")
    elif bot_token:
        record("telegram_bot", True, "configured", "offline check skipped")

    failed = [c for c in checks if not c["ok"]]
    payload = {
        "tool_version": VERSION,
        "schema_version": SCHEMA_VERSION,
        "ok": not failed,
        "checks": checks,
        "failed": len(failed),
    }
    return (0 if not failed else 1), payload


def cmd_doctor(args: argparse.Namespace, config: Dict[str, Any],
               json_output: bool) -> int:
    code, payload = run_doctor(config, json_output,
                               network=not getattr(args, "offline", False))
    if json_output:
        emit_json(payload)
        return code
    for check in payload["checks"]:
        marker = "OK  " if check["ok"] else "FAIL"
        info(f"[{marker}] {check['name']}: {check['status']}"
             + (f" ({check['detail']})" if check["detail"] else ""))
    info("")
    if payload["ok"]:
        info("All checks passed.")
    else:
        warn(f"{payload['failed']} check(s) failed.")
    return code


# =============================================================================
# CLI
# =============================================================================

KNOWN_COMMANDS = {
    "search", "list", "dataset", "config", "download", "plan", "status",
    "verify", "watch", "auth", "login", "doctor", "init", "queue",
}
GLOBAL_VALUE_FLAGS = {"--config", "-c", "--downloads-dir"}
GLOBAL_BOOL_FLAGS = {"--json"}
VALUE_FLAGS = {
    "--series-id", "--progress", "--interval", "--webhook",
    "--username", "--password", "--limit", "--source", "--server-url",
    "--file", "-f", "--series",
}
ALL_VALUE_FLAGS = GLOBAL_VALUE_FLAGS | VALUE_FLAGS
KNOWN_FLAGS = ALL_VALUE_FLAGS | {
    "--json", "--fix", "--once", "--notify", "--stats", "--rebuild",
    "--prune", "--check", "--sync", "--dedupe", "--reset-caps",
    "--include-specials", "--help", "-h", "--version", "-V",
}


def _missing_option_value(argv: List[str], index: int) -> bool:
    if index + 1 >= len(argv):
        return True
    following = argv[index + 1]
    return following in KNOWN_FLAGS


def normalize_argv(argv: List[str]) -> List[str]:
    """Canonicalise argv for argparse.

    - global options go before the subcommand;
    - subcommand options go right after the subcommand, before positionals;
    - a bare first token becomes the implicit ``download`` subcommand.

    argparse stops filling a ``nargs="*"`` positional once it sees an option,
    so ``plan "Show" --series-id 1 S2`` would otherwise lose ``S2``. Moving all
    options ahead of positionals removes that ambiguity everywhere.
    """
    globals_out: List[str] = []
    sub_options: List[str] = []
    positionals: List[str] = []
    command: Optional[str] = None
    index = 0

    while index < len(argv):
        token = argv[index]
        if token == "--":
            positionals.extend(argv[index + 1:])
            break
        if token.startswith("-") and token != "-":
            name = token.split("=", 1)[0]
            has_inline_value = "=" in token
            needs_value = name in ALL_VALUE_FLAGS and not has_inline_value
            if needs_value and _missing_option_value(argv, index):
                raise UsageError(f"Option {name} requires a value; nothing was done.")
            value: Optional[str] = None
            if needs_value:
                value = argv[index + 1]
            target = (globals_out if name in GLOBAL_VALUE_FLAGS
                      or name in GLOBAL_BOOL_FLAGS else sub_options)
            target.append(token)
            if needs_value and value is not None:
                target.append(value)
                index += 2
                continue
            index += 1
            continue
        if command is None:
            command = token
        else:
            positionals.append(token)
        index += 1

    if command is None:
        return globals_out + sub_options + positionals
    if command not in KNOWN_COMMANDS:
        positionals.insert(0, command)
        command = "download"
    return globals_out + [command] + sub_options + positionals


def print_queue_action_help(action: str) -> None:
    action = action.lower()
    if action == "add":
        print(f"""usage: {PROG} queue add <series> [spec...] [options]

Add a series and optional episode selection to the persistent download queue.

positional arguments:
  series                Series name or search query
  spec                  Episode spec: all, S<N>, E<M>, S<N> E<M>-<K>, etc. (default: all)

options:
  --series-id ID        Explicit Emby series ID (bypasses name ambiguity)
  --source SOURCE       Media source version: 1 (default), N, or max
  --include-specials    Include season 0 / specials when using 'all'

examples:
  {PROG} queue add "Black Clover"
  {PROG} queue add "Black Clover" S1
  {PROG} queue add "One Piece" S2 E1-10
  {PROG} queue add "Bleach" all --source max
  {PROG} queue add "Attack on Titan" S4 --series-id 12345
""")
    elif action == "list":
        print(f"""usage: {PROG} queue list [--json]

Display all series and episode ranges currently waiting in the persistent queue.

options:
  --json                Output queue entries as a machine-readable JSON array

examples:
  {PROG} queue list
  {PROG} queue list --json
""")
    elif action == "run":
        print(f"""usage: {PROG} queue run [options]

Execute all series in the persistent download queue sequentially.
Completed items are automatically removed from the queue on success.
Failed episodes are automatically rescheduled to the end of the queue.

options:
  --progress {{auto,plain,json,none}}  Progress rendering mode
  --notify                           Desktop and Telegram notification on batch completion
  --webhook URL                      POST a completion webhook (Telegram-ready)
  --json                             Machine-readable JSON Lines output

examples:
  {PROG} queue run
  {PROG} queue run --notify
  {PROG} queue run --progress plain
""")
    elif action == "plan":
        print(f"""usage: {PROG} queue plan [series...] [-f FILE] [--json]

Calculate and preview the download plan, episode counts, and total storage
required for a queue of series without downloading anything.

If no series or file is given, plans the items currently in the persistent queue.

options:
  -f, --file FILE       Read queue items from a text file
  --json                Output detailed per-series plans as machine-readable JSON

examples:
  {PROG} queue plan "Show A" "Show B"
  {PROG} queue plan "Show A" S1 ; "Show B" all
  {PROG} queue plan -f my_shows.txt
  {PROG} queue plan                     # Plans the persistent queue
""")
    elif action == "remove":
        print(f"""usage: {PROG} queue remove <index>

Remove a queued series by its 1-based index (as displayed in '{PROG} queue list').

positional arguments:
  index                 1-based item number to remove

examples:
  {PROG} queue remove 1                 # Remove the first series in queue
  {PROG} queue remove 3                 # Remove the third series in queue
""")
    elif action == "clear":
        print(f"""usage: {PROG} queue clear

Empty all series from the persistent download queue.

examples:
  {PROG} queue clear
""")


def handle_queue_subhelp(argv: List[str]) -> bool:
    """Print specialized help for queue subactions when --help or -h is passed."""
    if len(argv) >= 2 and argv[0] == "queue":
        action = argv[1].lower()
        if action in ("add", "list", "run", "remove", "clear", "plan") and any(h in argv[2:] for h in ("-h", "--help")):
            print_queue_action_help(action)
            return True
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Download episodes from an Emby media server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "Black Clover"                 # First episode of first real season
  %(prog)s "Black Clover" all             # All episodes of all real seasons
  %(prog)s "Black Clover" S2 E3           # Season 2, episode 3
  %(prog)s "Black Clover" S2 E1-4         # Season 2, episodes 1-4
  %(prog)s queue "Show A" "Show B"        # Download multiple series in one command
  %(prog)s queue "Show A" S1 ; "Show B"   # Download with per-series specs (using ';')
  %(prog)s queue -f shows.txt             # Download all series listed in a file
  %(prog)s queue add "Show" S1            # Add to persistent queue
  %(prog)s queue run                      # Run persistent queue
  %(prog)s plan "Black Clover" S2         # Show what would happen, download nothing
  %(prog)s status                         # Live progress of a running download
  %(prog)s verify "Black Clover" --fix    # Check files and repair problems
  %(prog)s watch "Black Clover" --interval 600 --notify
  %(prog)s dataset --stats                # Per-series counts and sizes
  %(prog)s search "clover" | list "Black Clover"
  %(prog)s auth                           # Check server + token
  %(prog)s login --username me            # Store a fresh token

Global options (before the subcommand): --config, --downloads-dir, --json.
Exit codes: 0 ok, 1 failed, 2 not found, 3 config/auth/server,
            4 invalid/ambiguous args, 5 lock busy, 6 insufficient disk.
        """,
    )
    parser.add_argument("--config", "-c", help="Path to config file")
    parser.add_argument("--downloads-dir", help="Override downloads directory")
    parser.add_argument("--json", action="store_true", dest="json_global",
                        help="Machine-readable output")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    sub = parser.add_subparsers(dest="command")

    def add_json(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--json", action="store_true", dest="json_sub",
                                    help="Machine-readable output")

    download = sub.add_parser(
        "download",
        help="Download episodes (single series, queue file, or list)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "Black Clover" S1 E1-5
  %(prog)s "Black Clover" all --source max
  %(prog)s -f shows.txt                              # Download multiple series from a file
  %(prog)s "Show A, Show B, Show C"                  # Download comma-separated series
  %(prog)s --series "Show A" --series "Show B"
        """,
    )
    download.add_argument("series", nargs="?")
    download.add_argument("spec", nargs="*")
    download.add_argument("-f", "--file", help="Download multiple series from a queue file")
    download.add_argument("--series", action="append", dest="series_list",
                          help="Additional series to queue for download")
    download.add_argument("--series-id")
    download.add_argument("--source", default="1",
                          help="Media source to download: 1 (default), N, or max")
    download.add_argument("--include-specials", action="store_true",
                          help="With 'all': also include season 0 / specials")
    download.add_argument("--progress", choices=["auto", "plain", "json", "none"])
    download.add_argument("--notify", action="store_true",
                          help="Desktop notification when the batch finishes")
    download.add_argument("--webhook", help="POST a completion webhook (Telegram-ready)")
    add_json(download)

    plan = sub.add_parser(
        "plan",
        help="Resolve selection and show what would download",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "Black Clover" S1 E1-5
  %(prog)s -f shows.txt                              # Plan multiple series from a file
  %(prog)s "Show A, Show B"
        """,
    )
    plan.add_argument("series", nargs="?")
    plan.add_argument("spec", nargs="*")
    plan.add_argument("-f", "--file", help="Plan multiple series from a queue file")
    plan.add_argument("--series", action="append", dest="series_list",
                      help="Additional series to queue for planning")
    plan.add_argument("--series-id")
    plan.add_argument("--source", default="1",
                      help="Media source to plan for: 1 (default), N, or max")
    plan.add_argument("--include-specials", action="store_true",
                      help="With 'all': also include season 0 / specials")
    add_json(plan)

    queue_cmd = sub.add_parser(
        "queue",
        help="Manage and run multi-series download queues",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Manage and execute multi-series download queues.

Supports direct multi-series execution, text-file batch queues,
and a persistent queue with add / list / run / remove / clear actions.
Failed episodes are automatically rescheduled to the end of the queue.
""",
        epilog="""
Actions:
  add <series> [spec...]       Add series and episode spec to persistent queue
  list                         View all series in the persistent queue
  run                          Download all series in the persistent queue
  remove <index>               Remove a series from queue by 1-based index
  clear                        Empty the persistent queue
  plan [items...] [-f file]    Calculate sizes and plan queue without downloading

Direct Queue Execution:
  %(prog)s "Show A" "Show B" "Show C"            # Download multiple series
  %(prog)s "Show A" S1 ; "Show B" all            # With per-series specs (using ';')
  %(prog)s "Show A" S1 -- "Show B" all           # Delimited by '--'
  %(prog)s -f shows.txt                          # Download from file

Persistent Queue Lifecycle:
  %(prog)s add "Black Clover" S1                 # Add Season 1
  %(prog)s add "One Piece" S2 all                # Add Season 2 All
  %(prog)s add "Bleach" all --source max         # Add with max quality
  %(prog)s list                                  # List queued items
  %(prog)s plan                                  # Preview queue plan
  %(prog)s run                                   # Start downloading
  %(prog)s remove 2                              # Remove item #2
  %(prog)s clear                                 # Clear queue

Subcommand Help:
  Run '%(prog)s <action> --help' for detailed help on any action
  (e.g. '%(prog)s add --help', '%(prog)s plan --help').
""",
    )
    queue_cmd.add_argument("action", nargs="?",
                           help="Action (add, list, run, clear, remove, plan) or series name")
    queue_cmd.add_argument("items", nargs="*",
                           help="Series names, episode specs, or action arguments")
    queue_cmd.add_argument("-f", "--file", help="Path to text file containing series list")
    queue_cmd.add_argument("--series-id", help="Explicit series ID")
    queue_cmd.add_argument("--source", default="1", help="Media source: 1, N, or max")
    queue_cmd.add_argument("--include-specials", action="store_true")
    queue_cmd.add_argument("--progress", choices=["auto", "plain", "json", "none"])
    queue_cmd.add_argument("--notify", action="store_true", help="Desktop/Telegram notification")
    queue_cmd.add_argument("--webhook", help="Webhook URL")
    add_json(queue_cmd)

    status = sub.add_parser("status", help="Show live download progress")
    add_json(status)

    verify = sub.add_parser("verify", help="Verify recorded files against the server")
    verify.add_argument("series", nargs="?")
    verify.add_argument("--series-id")
    verify.add_argument("--fix", action="store_true", help="Re-download bad/missing files")
    verify.add_argument("--progress", choices=["auto", "plain", "json", "none"])
    add_json(verify)

    watch = sub.add_parser("watch", help="Poll for new episodes and download them")
    watch.add_argument("series")
    watch.add_argument("spec", nargs="*")
    watch.add_argument("--series-id")
    watch.add_argument("--source", default="1",
                       help="Media source to download: 1 (default), N, or max")
    watch.add_argument("--interval", type=int, default=600,
                       help="Seconds between checks (default 600)")
    watch.add_argument("--once", action="store_true", help="Run one check then exit")
    watch.add_argument("--include-specials", action="store_true",
                       help="With 'all': also include season 0 / specials")
    watch.add_argument("--notify", action="store_true")
    watch.add_argument("--webhook")
    watch.add_argument("--progress", choices=["auto", "plain", "json", "none"])
    add_json(watch)

    dataset = sub.add_parser("dataset", help="Show or maintain the download dataset")
    dataset.add_argument("series", nargs="?")
    dataset.add_argument("--stats", action="store_true")
    dataset.add_argument("--rebuild", action="store_true",
                         help="Rebuild datasets from files on disk")
    dataset.add_argument("--prune", action="store_true",
                         help="Remove records whose files are missing")
    dataset.add_argument("--check", action="store_true",
                         help="Report inconsistencies; exits 1 if any are found")
    dataset.add_argument("--sync", action="store_true",
                         help="Regenerate per-series datasets from the central one")
    dataset.add_argument("--dedupe", action="store_true",
                         help="Remove duplicate entries, keeping the best record")
    add_json(dataset)

    search = sub.add_parser("search", help="Search for series")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=30)
    add_json(search)

    listing = sub.add_parser("list", help="List seasons and episode counts")
    listing.add_argument("series")
    listing.add_argument("--series-id")
    add_json(listing)

    config = sub.add_parser("config", help="Show configuration (secrets hidden)")
    add_json(config)

    auth = sub.add_parser("auth", help="Check server and token health")
    auth.add_argument("action", nargs="?", choices=["check"], default="check")
    auth.add_argument("--reset-caps", action="store_true",
                      help="Clear the cached server capabilities probe")
    add_json(auth)

    doctor = sub.add_parser("doctor", help="Diagnose config, server, tools and dataset")
    doctor.add_argument("--offline", action="store_true",
                        help="Skip live server checks")
    add_json(doctor)

    login = sub.add_parser("login", help="Authenticate and store a fresh token")
    login.add_argument("--username")
    login.add_argument("--password")
    add_json(login)

    init_cmd = sub.add_parser("init", help="Initialize configuration and default downloads directory")
    init_cmd.add_argument("--server-url", help="Emby server URL (e.g. http://10.10.10.9:8096)")
    init_cmd.add_argument("--username", help="Username")
    init_cmd.add_argument("--password", help="Password")
    add_json(init_cmd)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv or "-V" in argv:
        print(f"{PROG} {VERSION}")
        return 0

    if handle_queue_subhelp(argv):
        return 0

    json_hint = "--json" in argv
    try:
        argv = normalize_argv(argv)
    except ToolError as exc:
        if json_hint:
            emit_json({"error": str(exc), "code": exc.exit_code})
        else:
            warn(f"Error: {exc}")
        return exc.exit_code
    parser = build_parser()
    args = parser.parse_args(argv)

    json_output = bool(getattr(args, "json_global", False)
                       or getattr(args, "json_sub", False))

    if not getattr(args, "command", None):
        config_path = find_config_file(getattr(args, "config", None))
        if not config_path and not json_output:
            info(f"Welcome to {PROG} {VERSION}!")
            info("No configuration found. Get started by running:")
            info(f"  {PROG} init")
            info("")
            info(f"Or see available options with '{PROG} --help'.")
            return 0
        parser.print_help()
        return 0

    try:
        config = load_config(args.config)
        if getattr(args, "downloads_dir", None):
            config["downloads_path"] = args.downloads_dir
            config["_config_dir"] = None

        if args.command == "init":
            return cmd_init(args, config, json_output)
        if args.command == "download":
            return cmd_download(args, config, json_output)
        if args.command == "queue":
            return cmd_queue(args, config, json_output)
        if args.command == "plan":
            return cmd_plan(args, config, json_output)
        if args.command == "status":
            return cmd_status(args, config, json_output)
        if args.command == "verify":
            return cmd_verify(args, config, json_output)
        if args.command == "watch":
            return cmd_watch(args, config, json_output)
        if args.command == "dataset":
            return cmd_dataset(args, config, json_output)
        if args.command == "search":
            return cmd_search(args, config, json_output)
        if args.command == "list":
            return cmd_list(args, config, json_output)
        if args.command == "config":
            return cmd_config(args, config, json_output)
        if args.command == "auth":
            return cmd_auth(args, config, json_output)
        if args.command == "doctor":
            return cmd_doctor(args, config, json_output)
        if args.command == "login":
            return cmd_login(args, config, json_output)
        parser.print_help()
        return 0
    except ToolError as exc:
        if json_output:
            emit_json({"error": str(exc), "code": exc.exit_code})
        else:
            warn(f"Error: {exc}")
        return exc.exit_code
    except KeyboardInterrupt:
        warn("Interrupted.")
        return 130
    except OSError as exc:
        if json_output:
            emit_json({"error": str(exc), "code": 1})
        else:
            warn(f"Error: {exc}")
        return 1
    except BrokenPipeError:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
