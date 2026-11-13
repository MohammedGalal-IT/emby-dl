# emby-dl

[![Tests](https://github.com/MohammedGalal-IT/emby-dl/actions/workflows/test.yml/badge.svg)](https://github.com/MohammedGalal-IT/emby-dl/actions/workflows/test.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**`emby-dl`** is a resilient, single-stream command-line downloader and dataset manager for [Emby media servers](https://emby.media). Built for both humans in the terminal and autonomous AI coding agents, it prioritizes reliability, byte-exact verification, and zero unverified downloads.

---

## Documentation & Guides

- **[Tool Reference (`tools/README.md`)](tools/README.md):** In-depth CLI manual, spec rules, progress modes, and dataset management commands.
- **[Full Emby Guide (`docs/EMBY_FULL_GUIDE.md`)](docs/EMBY_FULL_GUIDE.md):** Exhaustive institutional memory, server quirks (Range hang, HTTP 500 stream errors, `/Users/Me` bug), live series IDs, and architectural history.
- **[Agent Skill (`SKILL.md`)](SKILL.md):** The portable operational skill for AI coding assistants (OpenCode, Claude Code, Cursor, Windsurf).

---

## Key Features

- **Zero-Config Default:** Pre-configured with the default home server URL (`http://10.10.10.9:8096`) and auto-authentication for `دخول الاستراحة 2` — works immediately after installation on the local network!
- **Cross-Platform:** Works on Linux, Windows 10/11, and macOS out of the box with zero third-party dependencies (standard library + `curl`).
- **Machine-Wide Single Stream:** Exclusive file locking guarantees transfers never saturate your server or local disk with parallel jobs.
- **Exact-Size Verification:** Every downloaded episode is matched byte-for-byte against the server's reported file size before being confirmed.
- **Atomic Operations:** Downloads to `.part` files and renames only after verification — never leaving corrupt half-files.
- **Range-Hang Resilient:** Emby 4.x servers often hang indefinitely on HTTP `Range:` headers. `emby-dl` uses smart probe-and-restart logic rather than hanging.
- **Smart Series Resolution Cache:** Automatically caches series IDs locally from previous lookups and downloads, enabling instant 0.02-second lookups that bypass slow full-library searches and eliminate server search timeouts.
- **Multi-Series Queue & Failure Rescheduling:** Download multiple series in one command, from a queue file (`-f`), or via persistent queues (`add/list/run`). Failed episodes are automatically rescheduled to the end of the queue.
- **Universal `--series-id` Support:** Supported everywhere (single downloads, planning, queue files, persistent queue, watch, verify, list) to bypass name ambiguity and search latency.
- **Interactive Telegram Bot:** Two-way bot commands (`/status`, `/queue`, `/help`, `/ping`), live remaining time (`ETA`), and accurate queue progress counters.
- **Token Secrecy:** Secrets are passed to curl via owner-only configuration files (`-K`), keeping tokens out of `ps aux`, process tables, and command line histories.
- **System Doctor:** Built-in `doctor` diagnostic command checks curl, permissions, disk reserves, dataset consistency, and server connectivity.
- **Dataset Schema v3:** Atomic central commit point with automatic per-series derived views, integrity checks (`--check`), sync recovery (`--sync`), and deduplication (`--dedupe`).
- **Agent-Ready & Skill Included:** Clean JSON Lines streaming (`--json`), stable exit codes (0–6), and an included AI Agent Skill (`SKILL.md`) ready for OpenCode, Claude Code, Cursor, and Windsurf.

---

## Installation

### Linux / macOS

```bash
# Recommended: Isolated install with pipx
pipx install .

# Or via standard pip
pip install .

# Or run directly without installation
chmod +x tools/emby_dl
./tools/emby_dl --version
```

### Windows (PowerShell or Command Prompt)

```powershell
# Install into your Python environment
pip install .

# Or run directly
python emby_dl.py --version
```

### Shell Completions

Static completions for Bash, Zsh, and Fish are provided in [`completions/`](completions/):

```bash
# Bash
source completions/emby_dl.bash

# Zsh
fpath=(/path/to/emby-dl/completions $fpath)
autoload -Uz compinit && compinit

# Fish
cp completions/emby_dl.fish ~/.config/fish/completions/
```

---

## Quickstart

### 1. Ready Out of the Box (Zero-Config)

If you are on the home network, **`emby-dl` works right away** without any setup. It connects to `http://10.10.10.9:8096` using `دخول الاستراحة 2` and downloads to `~/Downloads/Emby` (or `%USERPROFILE%\Downloads\Emby` on Windows):

```bash
# Verify connection to the home server
emby-dl doctor

# Plan and download immediately
emby-dl plan "Black Clover" S1 E1
emby-dl "Black Clover" S1 E1
```

### 2. Custom Server Setup (`init`)

To connect to a different Emby server or account, run `init`:

```bash
# Interactive setup
emby-dl init

# Non-interactive / Agent setup
emby-dl init --server-url "http://10.10.10.9:8096" --username "myuser" --password "mypassword"
```

- **Default Config Location:**
  - Linux/macOS: `~/.config/emby_dl/config.json`
  - Windows: `%APPDATA%\emby_dl\config.json`
- **Default Downloads Folder:**
  - Linux/macOS: `~/Downloads/Emby`
  - Windows: `%USERPROFILE%\Downloads\Emby`

### 3. Verify System Health (`doctor`)

Run the diagnostic doctor to confirm all systems are ready:

```bash
emby-dl doctor
```

### 4. Dry-Run Planning (`plan`)

Always check what will be downloaded before initiating transfers:

```bash
emby-dl plan "Black Clover" S1 E1-5
```

### 5. Download Episodes

```bash
# Download by series name and spec
emby-dl "Black Clover" S1 E1

# Explicit subcommand
emby-dl download "Black Clover" S2 all

# Select specific media version (e.g. 4K / highest bitrate)
emby-dl "Black Clover" S2 E1 --source max
```

### 6. Multi-Series Queue Support (`queue` and `-f / --file`)

Download, plan, and automate multiple series in a single command, from a queue file, or using a persistent queue. Failed episodes are automatically rescheduled to the end of the queue so transfers never halt.

```bash
# 1. Direct Multi-Series Execution (one command)
emby-dl queue "Black Clover" "One Piece" "Bleach"
emby-dl queue "Black Clover" S1 E1-10 ; "One Piece" S2 all ; "Bleach" S1
emby-dl queue "Show A" S1 -- "Show B" all               # Double-dash delimiter
emby-dl download "Show A, Show B, Show C"               # Comma-separated with download

# 2. File-Based Batch Queues (-f / --file)
# Reads lines from text file (supports comments '#', blank lines, and per-line specs)
emby-dl queue -f my_shows.txt
emby-dl download -f my_shows.txt

# 3. Dry-Run Planning & Storage Estimation
emby-dl queue plan "Black Clover" "One Piece"
emby-dl queue plan -f my_shows.txt
emby-dl plan -f my_shows.txt

# 4. Persistent Queue Lifecycle (stored in .download_queue.json)
emby-dl queue add "Black Clover" S1                     # Add series to queue
emby-dl queue add "One Piece" S2 all --source max       # Add with max quality
emby-dl queue add "Attack on Titan" S4 --series-id 123  # Add with explicit ID
emby-dl queue list                                      # View waiting items
emby-dl queue plan                                      # Preview plan for queued items
emby-dl queue run --notify                              # Execute entire queue
emby-dl queue remove 2                                  # Remove item #2
emby-dl queue clear                                     # Empty the queue

# 5. Dedicated Subcommand Help
emby-dl queue --help                                    # Full queue overview
emby-dl queue add --help                                # Guide for adding items
emby-dl queue run --help                                # Guide for running queue
emby-dl queue plan --help                               # Guide for dry-run previewing
```

### 7. Telegram Bot Notifications & Two-Way Commands

`emby-dl` includes a built-in Telegram bot daemon that sends automatic completion alerts, 10-minute progress updates, and responds interactively to commands in real time:

#### Setup
Add your bot token and chat ID to `config.json`:
```json
"notifications": {
  "desktop": true,
  "telegram_bot_token": "YOUR_BOT_TOKEN_FROM_BOTFATHER",
  "telegram_chat_id": "YOUR_CHAT_ID",
  "notify_on_complete": true,
  "notify_on_failure": true
}
```

*Tip: If you don't know your `telegram_chat_id`, leave it empty, start any download, open your bot in Telegram and send `/start` — the tool will automatically detect and save your chat ID!*

#### Available Bot Commands
| Command | Response & Output |
|---|---|
| `/status` | Real-time speed, current episode %, transferred bytes, and instantaneous **ETA**. |
| `/queue` (or `/q`) | Complete queue breakdown with status icons (`✅` done, `⏳` active, `🕒` waiting), per-series episode counts, and remaining queue total. |
| `/help` | Lists all available bot commands. |
| `/ping` | Replies with `pong` to verify bot responsiveness. |
| `/start` | Registers/verifies your chat for alerts. |

#### Sample Telegram Responses

**`/status` — Live Transfer Status & ETA:**
```text
Status: running
Completed: 4/16 (remaining: 12)
Current: The Flower of Evil - زهرة الشر S01E05 (64.2%)
Queue: [2/5] The Flower of Evil - زهرة الشر
Total Queue: 10/74 episodes (13.5%)
Downloaded: 163.50 MiB / 254.76 MiB
Speed: 1.85 MiB/s
ETA: 00:49
```

**`/queue` — Detailed Queue Breakdown:**
```text
📋 تفاصيل طابور التحميل (Queue Details)
الحالة: قيد التشغيل 🟢
المسلسل الحالي: [2/5] The Flower of Evil - زهرة الشر
إجمالي تقدم الطابور: 10/74 حلقة (13.5%)

قائمة المسلسلات في الطابور:
1. ✅ الخليفة [S2 all] — (6/6 حلقات) (~3.83 GiB)
2. ⏳ The Flower of Evil [all] — (4/16 حلقات) (~4.54 GiB)
3. 🕒 حياة الاخرين [all] — (0/6 حلقات) (~5.08 GiB)
4. 🕒 الطبيب المعجزة [S1 E21-64] — (0/44 حلقات) (~36.29 GiB)
5. 🕒 اخواتي [all] — (0/15 حلقات) (~2.70 GiB)

المتبقي في الطابور: 64 حلقة
```

#### Bot Switching & Self-Provisioning Safeguards
- **Zero BotFather Setup:** The tool automatically configures the bot menu (`setMyCommands`), chat intro description (`setMyDescription`), and short profile summary (`setMyShortDescription`) on Telegram servers.
- **Webhook Conflict Immunity:** Purges dangling webhooks (`deleteWebhook`) on startup to prevent silent `HTTP 409 Conflict` polling freezes.
- **Dynamic Hot-Reloading:** If you update `telegram_bot_token` in `config.json` while a download is running, the bot thread detects the new token within 30 seconds and migrates polling in-memory without restarting.
- **Diagnostic Audit:** Run `emby-dl doctor` to verify your bot token with Telegram's servers and confirm your `@bot_username`.

---

## Command Reference

| Command | Description |
|---|---|
| `init` | Create default configuration and downloads folder (interactive or scripted). |
| `download` | Download selected episodes (supports single series, `-f <queue_file>`, or comma-separated). |
| `queue` | Official multi-series queue: download multiple series in one command, from file, or manage persistent queue. |
| `plan` | Dry-run resolution of episodes, sizes, skips, and source versions (supports `-f <queue_file>`). |
| `status` | Display real-time speed, progress, and PID of active background transfers. |
| `verify` | Audit downloaded files against server sizes (`--fix` to re-download bad files). |
| `watch` | Periodically poll for new/missing episodes (`--once` for one check). |
| `dataset` | Dataset inspection (`--stats`), integrity auditing (`--check`), and synchronization (`--sync`). |
| `doctor` | Diagnose dependencies, disk space, config permissions, and server health. |
| `search` | Search for series by title. |
| `list` | List seasons and episode counts for a series. |
| `auth` | Check server and token health (`--reset-caps` clears cached capabilities). |
| `login` | Authenticate with username and password to store a fresh token. |
| `config` | Display active configuration with secrets masked. |

---

## Spec Grammar

| Syntax | Target Episodes |
|---|---|
| `"Show"` | First episode of first real season (Season ≥ 1). |
| `"Show" all` | All episodes across all real seasons (use `--include-specials` for S0). |
| `"Show" last` | The newest episode of the latest season. |
| `"Show" missing` | All episodes not yet downloaded on disk. |
| `"Show" new` | All episodes numbered higher than your current highest recorded episode. |
| `"Show" S2` | Entire season 2. |
| `"Show" S2 E5` | Season 2, episode 5. |
| `"Show" S2 E1-10` | Season 2, episodes 1 through 10. |
| `"Show" S2 E5-last` | Season 2, episode 5 through the end of the season. |

---

## Environment Overrides

Environment variables override configuration file settings (`CLI flags > ENV > config.json > defaults`):

- `EMBY_URL`: Server URL.
- `EMBY_TOKEN`: Access token.
- `EMBY_USER_ID`: Emby user GUID.
- `EMBY_USERNAME`: Username.
- `EMBY_DOWNLOADS_DIR`: Target downloads folder.

---

## AI Agent Integration (Skills)

This repository includes a ready-to-use skill definition in [`SKILL.md`](SKILL.md) (and [`.opencode/skills/emby-dl/SKILL.md`](.opencode/skills/emby-dl/SKILL.md)).

Any autonomous agent reading this skill will automatically understand:
1. Operational invariants (single stream, mandatory `plan --json`, `--json` event parsing).
2. Emby 4.x server quirks (Range-header hang, broken `/Users/Me` endpoint, HTTP 500 error body handling).
3. Ambiguity handling (`--series-id` enforcement for duplicate series names).
4. Stable exit codes:
   - `0`: Success
   - `1`: Failure / error
   - `2`: Series not found
   - `3`: Auth/server error
   - `4`: Invalid spec or ambiguous series
   - `5`: Lock busy (another download running)
   - `6`: Insufficient disk space

---

## License

This project is licensed under the [MIT License](LICENSE).
