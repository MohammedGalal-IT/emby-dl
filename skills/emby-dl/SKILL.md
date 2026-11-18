---
name: emby-dl
description: Comprehensive operational guide and complete institutional knowledge base for installing, maintaining, and executing emby-dl, the resilient Emby CLI downloader and dataset manager. Built for autonomous AI coding agents and terminal power users.
triggers:
  - emby
  - emby-dl
  - emby_dl
  - download series
  - queue
  - طابور
  - تحميل مسلسل
  - emby media
  - emby server
  - الملك2
---

# `emby-dl` Skill — Autonomous Agent Operational Manual & Knowledge Base

This skill is a self-contained, definitive operational manual and institutional knowledge base for any AI coding assistant (OpenCode, Claude Code, Cursor, Windsurf, Roo, etc.). It enables any agent to install, verify, execute, automate, and maintain `emby-dl` to interact reliably with Emby media servers (specifically the home server **الملك2** at `http://10.10.10.9:8096` and standard Emby 4.x servers).

---

## 1. Tool Identity & GitHub Repository

- **Project Name:** `emby-dl` (CLI commands: `emby-dl` and `emby_dl`)
- **Current Version:** `1.3.0` (2026-11-13; the v1.3.1 change set ships under this version string)
- **Official GitHub Repository:** **[https://github.com/MohammedGalal-IT/emby-dl](https://github.com/MohammedGalal-IT/emby-dl)**
- **License:** MIT
- **Architecture:** Pure Python 3.9+ standard library + `curl` + `ffmpeg` (zero third-party Python dependencies required).
- **Supported Platforms:** Linux, Windows 10/11, macOS.

---

## 2. Installation, Updates & Environment Verification

### How to Install

Depending on your environment, choose the best method:

#### Method A: Isolated Install via `pipx` (Recommended)
```bash
pipx install git+https://github.com/MohammedGalal-IT/emby-dl.git
```

#### Method B: Clone & Editable Install
```bash
git clone https://github.com/MohammedGalal-IT/emby-dl.git
cd emby-dl
pip install -e .
```

#### Method C: Standalone Single-File Execution (Zero Installation)
If `git` or `pip` is restricted, download the canonical module directly from GitHub raw:
```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/MohammedGalal-IT/emby-dl/main/emby_dl.py -o emby_dl.py
python3 emby_dl.py --version

# Windows (PowerShell)
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/MohammedGalal-IT/emby-dl/main/emby_dl.py" -OutFile "emby_dl.py"
python emby_dl.py --version
```

### How to Check for Updates
- **If installed with `pipx`:**
  ```bash
  pipx upgrade emby-dl
  ```
- **If cloned with `git`:**
  ```bash
  git pull && pip install -e .
  ```
- **Agent Version Check:**
  Run `emby-dl --version` to inspect the installed version. You can query the latest version from GitHub via:
  ```bash
  curl -fsSL https://raw.githubusercontent.com/MohammedGalal-IT/emby-dl/main/pyproject.toml | grep version
  ```

### Pre-Flight System Diagnostic (`doctor`)
Always run the built-in diagnostic doctor before starting automated download tasks:
```bash
emby-dl doctor --json
```
If `ok: true`, all pre-flight diagnostic systems are healthy:
1. `curl`: Executable found in `$PATH`.
2. `server_url`: Server endpoint configured.
3. `access_token`: Authentication token present.
4. `env_overrides`: Active `EMBY_*` environment variables.
5. `config_permissions`: Mode `0600` on POSIX systems (or Windows ACL).
6. `downloads_dir`: Target folder exists and is writable.
7. `disk_space`: Free disk exceeds required reserve (default 2 GiB).
8. `dataset`: Consistency check passes (`dataset --check`).
9. `dataset_schema`: Schema version is current (Schema v3).
10. `server_reachable` & `token_valid`: Real-time ping and authentication to Emby server.
11. `ffmpeg`: Auto-detected (`ffmpeg_path` → `$PATH` → known Windows locations). Required and enforced in `hls` mode; reports `not required` in `direct` mode.
12. `telegram_bot` & `telegram_chat`: Token authentication via Telegram `getMe` and chat ID verification (if configured).

---

## 3. Zero-Configuration Defaults

When running on the local home network, **`emby-dl` requires zero configuration**. It has pre-configured hardcoded defaults for the home server:
- **Default Server URL:** `http://10.10.10.9:8096`
- **Default Username:** `دخول الاستراحة 2`
- **Default Server ID:** `335a39030db14fc0919f6da76570e4db`
- **Default User ID:** `f5d170effc9b40d4a6183b9288c1d055`
- **Default Downloads Directory:** `./downloads`, resolved against the directory containing the config file (or the current directory when no config exists). Override with `downloads_path` or `--downloads-dir`.

**Automatic Passwordless First-Run Login:**
The default lounge user (`دخول الاستراحة 2`) has no password. If `access_token` is missing from the local config, the tool automatically contacts `http://10.10.10.9:8096`, obtains a fresh access token, saves it locally, and executes the command seamlessly.

### Custom Configuration (`init` & Environment Overrides)
To configure a different server, port, or user:
```bash
# Interactive setup
emby-dl init

# Non-interactive / Agent setup
emby-dl init --server-url "http://YOUR_SERVER:8096" --username "user" --password "secret"
```

**Environment Variables Precedence:**
Environment variables take precedence over configuration files (`CLI flags > ENV > config.json > defaults`):
- `EMBY_URL`: Override server base URL.
- `EMBY_TOKEN`: Override API token.
- `EMBY_USER_ID`: Override user GUID.
- `EMBY_USERNAME`: Override username.
- `EMBY_DOWNLOADS_DIR`: Override destination folder.

---

## 4. Golden Operational Rules for AI Agents

1. **NEVER Run Download Jobs in Parallel (Single-Episode Guarantee):**
   - Emby servers struggle with simultaneous transcode/stream downloads. `emby-dl` enforces an exclusive machine-wide lock file (`.emby_dl.lock`).
   - HLS mode may fetch several segments of the *same* episode concurrently (`hls_workers`), but two episodes are never downloaded at once.
   - If another download is active, the tool immediately exits with **code 5**. Do not hammer the command; inspect live progress with `emby-dl status`.

2. **ALWAYS Run `plan --json` Before Initiating Downloads:**
   - Never run `download` blind. Always execute `plan` first:
     ```bash
     emby-dl plan "<series>" <spec> --json
     ```
   - Verify `summary.todo_bytes` (network transfer required) against disk space.
   - Verify `summary.unknown_sizes`: if > 0, server size could not be determined. The tool will fail rather than guess sizes.
   - Inspect `items[]`: items with `status: "skip"` are already complete and verified on disk.

3. **ALWAYS Use `--json` for Agent Automation:**
   - In automated sessions, passing `--json` emits structured JSON Lines events to stdout:
     - `start`: Batch initialization, planned items count.
     - `progress`: Real-time transfer updates (downloaded bytes, total bytes, speed bps, ETA seconds).
     - `verdict`: Per-file result (`downloaded`, `skipped`, `self_healed`, `failed`) and reason.
     - `summary`: Overall batch summary.

4. **ALWAYS Disambiguate with `--series-id` When Ambiguous (Universal Support):**
   - If an Emby server has multiple series with the same name, `emby-dl` strictly refuses to guess and exits with **code 4**, listing candidates with their IDs and release years.
   - Always supply `--series-id <id>` (e.g. `emby-dl plan "Black Clover" --series-id 276491 S2`).
   - `--series-id` is supported universally across all input modes: single downloads, planning, queue text files (`Show S1 --series-id 1234`), `queue add`, `watch`, and `verify`.
   - **Smart Series Resolution Cache:** The tool automatically caches resolved series IDs locally in `.emby_series_cache.json` and seeds from `downloaded_dataset.json`. Subsequent commands resolve in **0.02 seconds** directly via `/Items/{id}`, completely eliminating server-side full-library search timeouts.

5. **NEVER Issue Manual `curl` or `Range:` Requests Directly:**
   - Emby servers (specifically 4.9.x on Windows) hang indefinitely on HTTP `Range: bytes=...` headers. `emby-dl` contains specialized probe-and-restart logic designed specifically for this bug. Let the tool manage curl.

6. **Token Secrecy via `curl -K`:**
   - The tool passes authentication headers to curl via a private temporary configuration file (`.emby_dl_headers`, mode `0600`) with `-K`.
   - The token never appears in process arguments, `/proc/<pid>/cmdline`, or shell histories.

7. **Multi-Level Automatic Failure Rescheduling (Autonomous Resilience):**
   - Do NOT abort a batch if an individual episode encounters an error. `emby-dl` automatically reschedules failed episodes to the end of the series queue (`pending_queue.append(failed_item)`).
   - The downloader proceeds with remaining episodes and re-attempts failed ones at the end of the series.
   - At the queue level, `emby-dl queue` automatically runs an auto-retry sweep pass over incomplete series.

---

## 5. Complete CLI Command Reference

### Commands
```bash
# Initialize / configure
emby-dl init [--server-url URL] [--username USER] [--password PWD]

# Dry run / pre-flight plan
emby-dl plan "Series Name" [spec] [--series-id ID] [--source 1|N|max] [--json]
emby-dl plan -f queue.txt [--json]

# Download episodes (subcommand optional: bare series name defaults to download)
emby-dl download "Series Name" [spec] [--series-id ID] [--source 1|N|max] [--json]
emby-dl download -f queue.txt [--notify] [--json]
emby-dl "Series Name" [spec] [--series-id ID] [--source 1|N|max] [--json]

# Multi-series queue execution and management
emby-dl queue "Show A" "Show B" "Show C" [--json]
emby-dl queue "Show A" S1 ';' "Show B" all [--json]
emby-dl queue -f queue.txt [--notify] [--json]
emby-dl queue add "Show A" S1 [--series-id ID] [--source 1|N|max]
emby-dl queue list [--json]
emby-dl queue run [--notify] [--progress auto|plain|json|none]
emby-dl queue plan [-f queue.txt] [--json]
emby-dl queue remove <index>
emby-dl queue clear

# Live status of active background download
emby-dl status [--json]

# Verify disk files against server exact sizes
emby-dl verify ["Series Name"] [--series-id ID] [--fix] [--json]

# Poll for new / missing episodes periodically
emby-dl watch "Series Name" [spec] [--interval 600] [--once] [--json]

# Dataset maintenance (Schema v3)
emby-dl dataset --stats               # Show per-series byte counts & episodes
emby-dl dataset --check               # Verify disk vs dataset integrity (exit 1 on issues)
emby-dl dataset --sync                # Regenerate all per-series datasets from central
emby-dl dataset --dedupe              # Clean duplicate entries, keeping best verified record
emby-dl dataset --rebuild             # Scan disk files from scratch and rebuild v3 dataset
emby-dl dataset --prune               # Drop records whose files no longer exist on disk

# System health diagnostics
emby-dl doctor [--offline] [--json]

# Authentication & capabilities
emby-dl auth [--reset-caps] [--json]
emby-dl login --username "user" [--password "pwd"]

# Search & discovery
emby-dl search "query" [--limit 30] [--json]
emby-dl list "Series Name" [--series-id ID] [--json]
emby-dl config [--json]
```

---

## 6. Spec Grammar & Source Selection

### Selection Specs
| Spec Syntax | Behavior |
|---|---|
| `<series>` | Default: First episode of the first real season (Season ≥ 1). |
| `<series> all` | All episodes across all real seasons (add `--include-specials` for S0). |
| `<series> last` | The newest episode of the latest real season. |
| `<series> missing` | All episodes from real seasons not yet downloaded on disk. |
| `<series> new` | All episodes numbered higher than your current highest recorded episode. |
| `<series> S2` | Entire season 2. |
| `<series> S2 E5` | Season 2, episode 5. |
| `<series> S2 E1-10` | Season 2, episodes 1 through 10 (inclusive). |
| `<series> S2 E5-last`| Season 2, episode 5 through the end of the season. |
| `<series> S0` | Specials season (only included when explicitly asked for). |

### Multi-Version Source Selection (`--source`)
When episodes have multiple formats or qualities:
- `--source 1`: Primary / default source listed by the server (default).
- `--source <N>`: 1-based index of available media source version (e.g. `2`, `3`).
- `--source max`: Highest bitrate / largest file size version.
- If a different source is requested for an already-downloaded episode, `plan` marks it `source_changed` (`SRC`) and retrieves the new version.

---

## 7. Multi-Series Queue Automation (`queue`)

AI agents should use the `queue` command when tasks require downloading multiple series:

```bash
# 1. Direct multi-series execution with per-series specs
emby-dl queue "Show A" S1 ';' "Show B" all ';' "Show C" S2 E1-5 --json

# 2. File-based queue execution (supports inline --series-id on any line)
emby-dl queue -f queue.txt --json

# 3. Persistent queue automation
emby-dl queue add "Show A" S1 --series-id 1234
emby-dl queue add "Show B" all --source max
emby-dl queue list --json
emby-dl queue run --json

# 4. Queue dry-run planning & total disk calculation
emby-dl queue plan -f queue.txt --json
```

**Queue File Syntax (`queue.txt`):**
```text
# Comments and blank lines are ignored
فنون سارا The Art of Sarah
The Flower of Evil - زهرة الشر S1 E1-10
انت من احب S1 E21-23 --source max
المدينة البعيدة S2 E27-35 --series-id 145070
المدينة البعيدة S3 all --series-id 145070
```

**Default spec:** a queue-file line with no spec (and a direct `queue "Show"` entry) defaults to `all` real seasons, unlike a bare single-series CLI call, which means “first episode”.

**Queue Rescheduling Architecture:**
- If an individual episode fails, `emby-dl` automatically reschedules it to the end of the series queue (`pending_queue.append(failed_item)`).
- The agent's overall queue never halts due to a single transient glitch.
- On multi-series runs, `Total Queue` and live `ETA` are streamed in real time.
- All resolved series IDs are cached in `.emby_series_cache.json` for instant 0.02s zero-search future lookups.

---

## 8. HLS Streaming Mode & Lossless Remux Architecture

**`"download_mode": "hls"` is the DEFAULT** (matched to the home-server profile). For servers running Emby 4.x that hang on HTTP `Range:` headers or throttle single direct streams, no configuration is needed. To switch to the single-stream fallback, set `"download_mode": "direct"` in `config.json` — completed episodes are never re-fetched when switching.

### ffmpeg Auto-Detection & Missing-Tool Behaviour:
`emby-dl` locates ffmpeg automatically in this order: (1) `download_settings.ffmpeg_path` if set (default empty), (2) `ffmpeg` on `PATH`, (3) known Windows install locations. `ffprobe` is resolved next to the found binary.
- **Missing ffmpeg in `hls` mode:** the tool fails fast BEFORE downloading with `ConfigError: HLS mode needs ffmpeg. Install it (winget install Gyan.FFmpeg) or set download_settings.ffmpeg_path.` — fix by installing ffmpeg (`winget install Gyan.FFmpeg`) or pointing `ffmpeg_path` at an existing binary, or falling back to `"download_mode": "direct"`.
- **`doctor` check:** reports `ffmpeg: found/missing` in `hls` mode and `not required` in `direct` mode.

### Two-Stage Lossless Remux Pipeline:
Emby's live HLS transcoder emits MPEG-TS segments where video packets frequently lack timestamps. Direct Matroska muxing fails with `Can't write packet with unknown timestamp`. `emby-dl` solves this natively:
1. **Raw Segment Concatenation:** Concatenates downloaded `.ts` segments into a single transport stream (preserves exact duration; does not drift like concat demuxer).
2. **Stage 1 (MP4 Intermediate):** Invokes `ffmpeg -nostdin -hide_banner -loglevel error -y -fflags +genpts -i input.ts -map 0:v:0 -map 0:a:0? -c copy -f mp4 temp.mp4`. MP4 muxer repairs and normalizes presentation timestamps with zero re-encoding.
3. **Stage 2 (Final Container):** Remuxes `temp.mp4` to final MKV or MP4, losslessly muxing SubRip subtitles into MKV.
4. **Stdin Hang Protection:** All ffmpeg/ffprobe subprocesses enforce `-nostdin` to eliminate 30-minute hangs in background agent shells.
5. **Duration Tolerance:** Enforces `tolerance = max(90.0, expected_duration * 0.03)` to tolerate transcode keyframe padding while catching truncated files.

---

## 9. Interactive Telegram Bot Integration

When `notifications.telegram_bot_token` and `telegram_chat_id` are configured in `config.json`, `emby-dl` spawns an interactive daemon bot thread:
- **Automatic Provisioning & Bot Switching Safeguards:**
  - `deleteWebhook` Execution: Automatically purges dangling webhooks on startup to prevent silent `HTTP 409 Conflict` errors when switching bot tokens.
  - Automatic Description & Commands: Sets `setMyCommands`, `setMyDescription`, and `setMyShortDescription` automatically on Telegram servers.
  - Dynamic Hot-Reloading: Inspects `config.json` every 30 seconds; changing tokens during active downloads migrates polling to the new bot in-memory without restarting.
- **Interactive Commands:**
  - `/status`: Displays real-time download status, active episode, transfer speed, and instantaneous `ETA`.
  - `/queue` (or `/q`): Detailed queue breakdown showing active series (`⏳`), completed series (`✅`), waiting series (`🕒`), per-series counts, estimated sizes, and remaining queue total.
  - `/help`: Lists available bot commands.
  - `/ping`: Pong response.
- **Accurate Queue Progress:** `Total Queue` strictly filters against `target_series_ids`, ensuring previous downloads from past jobs never skew queue progress counters.

---

## 10. Exit Codes & Error Recovery Playbook

| Exit Code | Category | Meaning | Autonomous Agent Recovery Action |
|:---:|---|---|---|
| **0** | Success | All operations succeeded. | Proceed to next task. |
| **1** | Failed | Download failed, verification mismatch, or `doctor` error. | Parse JSON Lines events for failure reason (e.g., `size_mismatch`, `http_500`). Run `emby-dl verify --fix` to re-download bad files or `emby-dl doctor` to check issues. |
| **2** | Not Found | Series name does not match any items on server. | Run `emby-dl search "<query>"` to locate the exact series title or ID. |
| **3** | Server / Auth | Cannot connect, HTTP 401/403, or invalid token. | Check network reachability to server URL. Run `emby-dl auth` or `emby-dl login`. |
| **4** | Usage Error | Ambiguous series name, bad spec range, or invalid `--source`. | Check candidates list printed on stderr. Re-run providing `--series-id <id>` or valid `--source`. |
| **5** | Lock Busy | Another download is actively in progress. | Check `emby-dl status`. Wait for the transfer to finish. Never delete the lock file unless the PID is dead. |
| **6** | Disk Full | Available free disk is lower than required transfer + reserve. | Clear temporary files, reduce batch selection, or change `--downloads-dir`. |

---

## 11. Institutional Memory — Emby Server Quirks

These facts are verified against Emby Server 4.9.x on Windows (host: **الملك2** `http://10.10.10.9:8096`):

1. **Broken Range Requests (HTTP 206 / Resume Hang):**
   - The server advertises `Accept-Ranges: bytes`, but requests containing `Range: bytes=...` stall indefinitely with zero bytes sent.
   - **Workaround:** `emby-dl` performs one short resume probe (10s timeout). If the probe stalls, it purges `.part` and downloads fresh. In HLS mode, Range requests are avoided entirely.

2. **HTTP 500 Error Bodies on Broken Streams:**
   - When a video stream fails internally on the server, Emby responds with HTTP 500 and a short plain text body (`text/plain`).
   - `curl -f` is mandatory; without `-f`, curl exits 0 and records the error message as a valid media file. `emby-dl` enforces `-f` and rejects non-video content types.

3. **Size Resolution Order:**
   - Server file size is resolved in strict priority:
     1. `MediaSources[i].Size` (authoritative, exact).
     2. `/Items?Ids=...` detail retry.
     3. `HEAD /Videos/{id}/stream` (short timeout; server sometimes hangs on HEAD).
     4. If unknown: fails with `size_unknown`. An unknown size is **never trusted**.

4. **The `/Users/Me` Endpoint Bug:**
   - Emby 4.9.x returns `HTTP 500 Unrecognized Guid format` for `/Users/Me`.
   - Always query `/Users/{user_id}` with the concrete user ID.

5. **Title Search Latency Under Transcode Load:**
   - Full-text search (`GET /Items?SearchTerm=...`) on large libraries can take >20s when transcoding is active, causing timeouts.
   - Always supply `--series-id <id>` or allow `SeriesCache` to resolve via cached IDs in 0.02s.

6. **Dataset Schema v3 Architecture:**
   - `<downloads>/downloaded_dataset.json` is the single authoritative commit point.
   - Per-series `<downloads>/<Series>/downloaded_dataset.json` are derived views.
   - If per-series datasets ever drift, run:
     ```bash
     emby-dl dataset --sync
     ```
   - To audit disk vs dataset integrity, run:
     ```bash
     emby-dl dataset --check
     ```

---

## 12. Configuration Reference

`config.json` is merged over `DEFAULT_CONFIG`; environment variables override the file, and CLI flags override both. `emby-dl config --json` prints the effective values with secrets masked.

| Key | Default | Purpose |
|---|---:|---|
| `server_url` | `http://10.10.10.9:8096` | Emby base URL |
| `server_id` / `user_id` | home values | informational; used by `auth`/`login` |
| `access_token` | `""` | optional when `username` is set (auto-auth) |
| `username` | `دخول الاستراحة 2` | login name |
| `downloads_path` | `./downloads` | resolved against the config file's directory |
| `download_settings.download_mode` | `"hls"` | `hls` or `direct` |
| `download_settings.hls_workers` | `4` | concurrent segment workers (1–8) |
| `download_settings.hls_segment_retries` | `4` | retries per failed segment |
| `download_settings.ffmpeg_path` | `""` | explicit ffmpeg; empty = auto-detect |
| `download_settings.speed_limit_bytes` / `speed_time_seconds` | `1000` / `1800` | curl stall thresholds, **not** a bandwidth cap |
| `download_settings.max_retries` | `5` | per-file attempts |
| `download_settings.retry_backoff_initial_seconds` / `retry_backoff_max_seconds` | `15` / `600` | health-gated backoff |
| `download_settings.resume_enabled` / `resume_stall_seconds` | `false` / `10` | direct-mode Range probe |
| `download_settings.queue_item_reschedule_rounds` | `2` | intra-series reschedule rounds |
| `download_settings.disk_reserve_bytes` | `2147483648` | free space kept untouched (2 GiB) |
| `download_settings.api_timeout_seconds` / `api_retries` | `60` / `3` | API timeout and retries |
| `download_settings.head_timeout_seconds` | `6` | last-resort HEAD size probe |
| `download_settings.completion_grace_seconds` | `15` | HLS straggler wait |
| `download_settings.idle_timeout_seconds` | `3600` | abort a transfer with zero progress |
| `notifications.desktop` | `true` | desktop notifications |
| `notifications.webhook_url` | `""` | completion webhook |
| `notifications.telegram_bot_token` / `telegram_chat_id` | `""` | enables the two-way bot |
| `notifications.notify_on_start` / `notify_on_complete` / `notify_on_failure` | `false` / `true` / `true` | alert triggers |
| `notifications.stall_alert_seconds` | `300` | stall alert threshold |

**Config file permissions:** `config.json` should be mode `600` on POSIX. A fresh git checkout can arrive as `644`, which makes `doctor` fail its `config_permissions` check and exit 1; run `chmod 600 config.json`.

---

## 13. Troubleshooting Playbook

| Symptom | Cause / fix |
|---|---|
| `Ambiguous series name ... Candidates` | duplicate titles → rerun with `--series-id <id>` |
| `Authentication failed (HTTP 401)` | token invalid → `emby-dl login --username ...` |
| `Cannot connect to server` | server restart/outage → rerun; retries are health-gated |
| exit `5` | another download holds `.emby_dl.lock` → wait or inspect `status` |
| exit `6` | free space minus `disk_reserve_bytes` is too small → free space or reduce the batch |
| `size_unknown` | server did not report a size; the tool refuses to record an unverified file |
| `bad_content_type` | server returned an error page (HTTP 500 `text/plain`) → check auth/server health |
| `verify` reports `size_mismatch` for HLS files | `verify` compares against the source size; HLS output is a remux. Use `dataset --check` for HLS entries; `verify --fix` accepts the recorded size and does not re-download. |
| `doctor` exits 1 with `config_permissions 0o644` | `chmod 600 config.json` (fresh checkouts arrive group/world-readable) |
| a `D:\...` folder appears on Linux/macOS | `downloads_path` is a Windows path; set a POSIX path or pass `--downloads-dir` |
| `HLS mode needs ffmpeg` | install ffmpeg (`winget install Gyan.FFmpeg`) or set `download_settings.ffmpeg_path` |
| `hls_error: timed out` | transcode spin-up delay → automatic retry with the 60 s API timeout |
| `hls_error: duration mismatch` | keyframe padding → tolerance `max(90 s, 3%)`; investigate only if persistent |
| `Can't write packet with unknown timestamp` | automatic MP4-intermediate repair; if it persists, check the ffmpeg build |
| `queue ... ; ...` runs only the first series | the shell consumed `;` → quote `';'` or the whole list |
| Telegram polling frozen / `409 Conflict` | dangling webhook → restart; the bot calls `deleteWebhook` on startup |
| `/queue` says `Unknown command` | long-running process has old bytecode → restart the process |
| full-library search times out | pass `--series-id`; `SeriesCache` resolves cached IDs directly |

---

*Last updated: Fri 2026-11-13 | Tool version: emby_dl 1.3.0 (v1.3.1 feature set)*
