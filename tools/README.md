# emby_dl — Episode Downloader for the Home Emby Server

A robust, single-stream command-line tool that downloads episodes from the home Emby server
(`http://10.10.10.9:8096`, server "الملك2") through its REST API.

It is built for **humans in a terminal** (live progress, readable messages) and for
**AI agents** (`--json` JSON Lines events, stable exit codes, no log scraping).

Core guarantees:

- **One file at a time, machine-wide.** A lock file prevents two downloads from
  running at once. There is never a parallel transfer.
- **Exact-size verification.** A file is recorded only when its byte count equals
  the size the server reports. A size the server cannot report is never trusted.
- **Atomic files.** Data is written to `<name>.part` and renamed only after
  verification, so a killed process can never leave a half-file with a final name.
- **Single atomic dataset commit.** The central dataset (schema v3) is the single
  commit point. Per-series datasets are derived views, checked with `dataset --check`
  and restored with `dataset --sync`.
- **Multi-version media source selection.** Choose version via `--source 1|N|max`.
- **Zero token exposure.** The authentication token is never passed on curl's
  command line; it is read by curl via a mode `0600` config file (`-K`), so `ps aux`
  never reveals credentials.
- **Resumable in practice.** One short resume probe is attempted on a partial;
  if the server hangs on Range requests (it does), the tool deletes the partial
  and starts a fresh transfer with stall detection.
- **Safe failure.** Bad specs, invalid sources, and ambiguous series names stop *before*
  any download. Unknown sizes, HTTP errors, HTML error pages, truncation and disk
  exhaustion are refused rather than recorded.

For the server story, API quirks and historical pitfalls, see
[`../docs/EMBY_FULL_GUIDE.md`](../docs/EMBY_FULL_GUIDE.md). This file is the tool
reference only.

---

## 1. Requirements and setup

- Python 3.9+ (tested on 3.12)
- `curl` (tested on 8.5) — performs the transfer
- Run via direct script: `chmod +x tools/emby_dl` → `tools/emby_dl`
- Or install via pipx / pip: `pipx install .` → provides `emby-dl` and `emby_dl`
- Shell completions available in `completions/` for Bash, Zsh, and Fish.
- Optional: `notify-send` for desktop notifications

Run the tool from anywhere; relative paths anchor to the config file, not the CWD.

### 1.1 Configuration search order & environment overrides

Precedence order: **CLI options > Environment variables > Config file > Defaults**.

Supported environment overrides:

| Variable | Config Key | Purpose |
|---|---|---|
| `EMBY_URL` | `server_url` | Base URL of the Emby server |
| `EMBY_TOKEN` | `access_token` | Authentication token |
| `EMBY_USER_ID` | `user_id` | User ID for user-scoped queries |
| `EMBY_USERNAME` | `username` | Display username |
| `EMBY_DOWNLOADS_DIR` | `downloads_path` | Target downloads directory |

Config file search order (first match wins):

1. `--config <path>` / `-c <path>`
2. `./config.json`
3. `<project root>/config.json` (one level above `tools/`)
4. `<script directory>/config.json`
5. `~/.config/emby_dl/config.json`
6. `./downloads/.emby_config` (legacy bash config, read-only fallback)

The real config is `config.json` at the project root and must be mode `600`.

### 1.2 Complete config reference

```json
{
  "server_url": "http://10.10.10.9:8096",
  "server_id": "335a39030db14fc0919f6da76570e4db",
  "user_id": "f5d170effc9b40d4a6183b9288c1d055",
  "access_token": "EMBY_ACCESS_TOKEN",
  "username": "دخول الاستراحة 2",
  "downloads_path": "./downloads",
  "download_settings": {
    "download_mode": "hls",
    "speed_limit_bytes": 1000,
    "speed_time_seconds": 1800,
    "max_retries": 5,
    "retry_delay_seconds": 5,
    "retry_backoff_initial_seconds": 15,
    "retry_backoff_max_seconds": 600,
    "resume_enabled": false,
    "resume_stall_seconds": 10,
    "hls_workers": 4,
    "hls_segment_retries": 4,
    "ffmpeg_path": "",
    "queue_item_reschedule_rounds": 2,
    "disk_reserve_bytes": 2147483648,
    "api_timeout_seconds": 60,
    "api_retries": 3,
    "head_timeout_seconds": 6,
    "completion_grace_seconds": 15,
    "idle_timeout_seconds": 3600
  },
  "notifications": {
    "desktop": true,
    "webhook_url": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "notify_on_start": false,
    "notify_on_complete": true,
    "notify_on_failure": true,
    "stall_alert_seconds": 300
  }
}
```

`server_url` and `access_token` are required. `server_id`, `user_id` and
`username` are informational but used by `auth` and `login`.

These defaults match the home-server profile exactly: the tool is tuned for the
`http://10.10.10.9:8096` Emby 4.9 server out of the box (HLS mode, long stall
windows, 60s API timeout). Installing on another machine reproduces the same
behaviour without editing anything.

`downloads_path` may be relative; it always resolves against the directory that
contains the config file. `--downloads-dir` overrides it for one run (relative to
the CWD in that case).

#### Download settings

> **Note:** `speed_limit_bytes` and `speed_time_seconds` are **stall-detection
> thresholds, not a bandwidth cap**. They map directly to curl's
> `--speed-limit` / `--speed-time`: a download is aborted as "stalled" only when
> its transfer speed stays below `speed_limit_bytes` bytes/sec for
> `speed_time_seconds` continuously. The default (1000 B/s for 1800 s) tolerates
> a very slow, throttling home server — it does **not** limit your speed.

| Key | Default | Meaning |
|---|---:|---|
| `download_mode` | `"hls"` | `"hls"` (resilient segment download + lossless remux) or `"direct"` (single HTTP stream) — see [Switching download modes](#switching-download-modes-direct--hls) |
| `speed_limit_bytes` | `1000` | stall threshold (min transfer speed), **not** a bandwidth cap |
| `speed_time_seconds` | `1800` | how long a transfer may stay under the threshold before curl aborts |
| `max_retries` | `5` | fresh-download attempts per file |
| `retry_delay_seconds` | `5` | legacy pause value (kept for compatibility) |
| `retry_backoff_initial_seconds` | `15` | first health-gated backoff: 15 → 30 → 60 → 120 … |
| `retry_backoff_max_seconds` | `600` | backoff cap; the tool waits for `/System/Info/Public` before retrying |
| `resume_enabled` | `false` | attempt one short Range probe on an existing `.part` in direct mode (unnecessary in HLS mode, which resumes natively) |
| `resume_stall_seconds` | `10` | short stall window for that probe |
| `hls_workers` | `4` | concurrent curl worker threads downloading independent HLS segments (1–8) |
| `hls_segment_retries` | `4` | retry attempts per failed HLS segment before refreshing session |
| `ffmpeg_path` | `""` | explicit path to ffmpeg. Empty = **auto-detect** (see below) |
| `queue_item_reschedule_rounds` | `2` | automatic failure rescheduling rounds before marking an episode failed |
| `disk_reserve_bytes` | `2147483648` | free space kept untouched (2 GiB) |
| `api_timeout_seconds` | `60` | JSON API request timeout |
| `api_retries` | `3` | API retries after an HTTP 5xx or connection error |
| `head_timeout_seconds` | `6` | last-resort HEAD size probe timeout |
| `completion_grace_seconds` | `15` | extra wait for stragglers before declaring an HLS episode complete |
| `idle_timeout_seconds` | `3600` | abort a transfer with zero progress for this long |

#### Notification settings

| Key | Default | Meaning |
|---|---|---|
| `desktop` | `true` | desktop notifications (PowerShell balloon tips on Windows, `notify-send` on Linux) |
| `webhook_url` | `""` | POST a JSON body when a batch finishes |
| `telegram_bot_token` | `""` | Telegram bot token (enables alerts and interactive two-way bot commands) |
| `telegram_chat_id` | `""` | target chat ID for Telegram messages |
| `notify_on_start` | `false` | notify on queue/batch start |
| `notify_on_complete` | `true` | notify when a batch has no failures |
| `notify_on_failure` | `true` | notify when at least one item fails |

Telegram is supported both via webhook and via native bot integration (`telegram_bot_token` + `telegram_chat_id`). When `telegram_bot_token` is configured, a background bot thread automatically responds to `/status`, `/help`, and `/ping` commands in Telegram with live progress and ETA.

`--notify` enables desktop notifications for one run. `--webhook URL` overrides the
webhook for one run. Neither changes the config file.

### 1.2a Switching download modes (`direct` ↔ `hls`)

`download_mode` in `config.json` selects the transfer engine. **The default is
`"hls"`.**

| | `hls` *(default)* | `direct` |
|---|---|---|
| How it works | Many small 6-second MPEG-TS segments via the live transcode endpoint, concatenated raw and remuxed losslessly (`TS → MP4 → MKV/MP4`) | One single HTTP stream request (`/Videos/{id}/stream?static=true`) |
| Requires | `ffmpeg` (+ `ffprobe`) | `curl` only |
| Survives server resets | ✅ each segment is an independent request | ❌ a reset kills the transfer |
| Resumes across runs | ✅ natively (completed segments are kept in `<dest>.hls`) | ⚠️ only with `resume_enabled: true` (Range probe) |
| Timestamp/audio-sync handling | ✅ repaired automatically by the remux pipeline | n/a (server-made file) |
| Best for | Emby 4.9 servers that hang on `Range:` headers or throttle long streams | servers with healthy direct-stream support, or when ffmpeg cannot be installed |

**How to switch** — edit `config.json` (no restart flags needed; applies to the
next run):

```json
"download_settings": {
  "download_mode": "hls"      // or "direct"
}
```

```bash
# Verify which mode will be used before downloading:
tools/emby_dl plan "Series Name" S1 --json      # reports the plan, no side effects
tools/emby_dl doctor                            # ffmpeg check follows the mode (see below)
```

Notes:
- Switching modes does **not** re-download finished episodes: completed entries
  recorded in `downloaded_dataset.json` are always skipped.
- A partially-downloaded `.part` file from `direct` mode is ignored by `hls`
  mode (it rebuilds from its own `<dest>.hls` segment folder) and vice versa;
  delete the stale artifact if you switch modes mid-episode.
- If ffmpeg is unavailable and you cannot install it, set `"download_mode":
  "direct"` — HLS remuxing is the only thing that needs ffmpeg.

### 1.2b ffmpeg: auto-detection & missing-tool behaviour

`emby-dl` never requires you to hand-edit an ffmpeg path. It locates ffmpeg in
this order on every HLS remux:

1. **`download_settings.ffmpeg_path`** — explicit path, if set (default: empty).
2. **`ffmpeg` on `PATH`** — standard lookup (`shutil.which`).
3. **Windows known locations** — e.g. `C:\Program Files\DownloadHelper CoApp\ffmpeg.exe`.

`ffprobe` is resolved next to the found ffmpeg binary, falling back to `PATH`.

**If ffmpeg cannot be found** (and mode is `hls`), the tool fails fast with a
clear, actionable error **before** downloading anything:

```text
ConfigError: HLS mode needs ffmpeg. Install it (winget install Gyan.FFmpeg)
or set download_settings.ffmpeg_path.
```

Fix options:
```bash
winget install Gyan.FFmpeg          # Windows: install ffmpeg into PATH
# or point the config at an existing binary:
#   "ffmpeg_path": "C:\\path\\to\\ffmpeg.exe"
```

`emby-dl doctor` reports this explicitly: in `hls` mode the `ffmpeg` check is
**FAIL/missing** when the binary cannot be located, and in `direct` mode it
reports `not required` (so doctor stays green on ffmpeg-less machines).

### 1.3 Logging in / refreshing a token

```bash
tools/emby_dl login --username "دخول الاستراحة 2"
# Password prompt. On success the token is saved to config.json (mode 600).

tools/emby_dl login --username me --password 'secret'   # non-interactive
EMBY_PASSWORD='secret' tools/emby_dl login --username me
```

`login` calls `POST /Users/AuthenticateByName`, stores `AccessToken`, `user_id`,
`server_id` and the canonical username. It never prints the token.

Check the token without changing anything:

```bash
tools/emby_dl auth          # human
tools/emby_dl auth --json   # machine
```

Manual fallback: log into the Emby web UI → DevTools → Application → Local Storage
→ `servercredentials3` → copy `Servers[].Users[].AccessToken` into `access_token`.

---

## 2. Command overview

| Command | Purpose |
|---|---|
| `download` | download a selection (supports single series, queue file, or comma-separated list) |
| `queue` | manage and execute multi-series download queues (`add`, `list`, `run`, `remove`, `clear`, `plan`) |
| `plan` | resolve the selection, show sizes/skips/sources, download nothing (supports `-f <queue_file>`) |
| `status` | live progress + PID of the running download |
| `verify [series]` | compare every recorded file with the server (`--fix` repairs) |
| `watch` | poll a series and download new/missing episodes (`--once` for one pass) |
| `dataset` | list entries, `--stats`, `--check`, `--sync`, `--dedupe`, `--rebuild`, `--prune` |
| `doctor` | verify system dependencies, configs, permissions, dataset and server reachability |
| `search` | find series by name |
| `list` | seasons and episode counts |
| `config` | show active config with secrets masked |
| `auth` | check server and token health (`--reset-caps` clears cached capabilities) |
| `login` | authenticate and store a token |

Global options (`--config`, `--downloads-dir`, `--json`) are accepted before or
after the subcommand and are hoisted to the front. Any subcommand option may also
appear before, between or after positionals: `plan "Show" --series-id 1 S2` and
`plan --series-id 1 "Show" S2` are equivalent. `--series-id` belongs to the
selection commands (`download`, `plan`, `queue`, `list`, `verify`, `watch`).
`--source` belongs to `download`, `plan`, `queue`, and `watch`.
`--include-specials` belongs to `download`, `plan`, `queue`, and `watch` when using `all`.
`-f` / `--file` belongs to `download`, `plan`, and `queue`.

---

## 3. Downloading

### 3.1 Two equivalent forms

```bash
tools/emby_dl download "المنظمة" S7 E3    # explicit
tools/emby_dl "المنظمة" S7 E3             # bare form
```

If the first non-option token is not a known command, `download` is inserted.
A typo of a command can therefore be treated as a series name — use `plan` or
`search` when unsure.

### 3.2 Complete spec grammar

Case-insensitive. Supported forms:

| Form | Meaning |
|---|---|
| `<series>` | first episode of the first real season (index ≥ 1) |
| `<series> all` | every episode of every real season |
| `<series> last` | last episode of the last real season |
| `<series> missing` | every episode not recorded *and* present on disk |
| `<series> new` | every episode after the highest recorded (season, episode) |
| `<series> S2` | all episodes of season 2 |
| `<series> S2 all` | same as above |
| `<series> S2 last` | last episode of season 2 |
| `<series> S2 E3` or `S2E3` | exactly S02E03 |
| `<series> S2 E1-4` or `S2E1-4` | S02E01 through S02E04 |
| `<series> S2 E3-last` | S02E03 through the end of season 2 |
| `<series> S0` | specials, only when asked for explicitly |

`all`, `missing` and `new` apply to **real seasons only**. `S0` is never included
unless requested. Ranges are inclusive. `E3-999` also works and means "to the end"
because no season has 999 episodes.

Examples:

```bash
tools/emby_dl "في السابعة عشر" S1 E19-23
tools/emby_dl "في السابعة عشر" last
tools/emby_dl "المنظمة" S7 E3-last
tools/emby_dl "اسطنبول راساً على عقب" missing
tools/emby_dl "Black Clover" --series-id 276491 S2
```

### 3.3 Multi-version media source selection (`--source`)

When episodes have multiple versions (e.g., 480p, 1080p, 4K Remux), the `--source` option
controls which version is targeted:

- `--source 1`: default source (first listed by the server).
- `--source N`: 1-based index corresponding to the source position (e.g. `2`, `3`).
- `--source max`: dynamically selects the source with the largest `Size`.

If an invalid source is specified, the tool stops immediately with exit code `4` and lists
all available candidates with their indices, names, byte sizes, and IDs.

If an episode is already downloaded on disk but you request a different source, `plan` marks
it as `SRC` (`source_changed`) and `download` retrieves the new source version.

```bash
tools/emby_dl "Alpha Show" S1 E1 --source 2
tools/emby_dl plan "Alpha Show" all --source max
```

### 3.4 Direct Series ID Support (`--series-id`) & Smart Resolution Cache

Some series names exist more than once on the server (e.g., reboots, alternate releases).
For example, the home server returns:

```text
114002  Black Clover
276491  Black Clover
```

The tool **refuses to guess** and exits `4`, listing all candidates. When you provide `--series-id`,
it bypasses full-library search entirely, querying `/Items/{id}` directly in **0.03 seconds**.

#### 3.4.1 Universal `--series-id` Support Across All Input Patterns

`--series-id` is a first-class citizen supported in every input mode:

```bash
# 1. Single Download / Bare Form
emby-dl "Black Clover" S2 E3 --series-id 276491
emby-dl download "Black Clover" all --series-id 276491

# 2. Dry-Run Planning
emby-dl plan "Black Clover" all --series-id 276491

# 3. Persistent Queue Management
emby-dl queue add "Black Clover" S1 --series-id 276491
emby-dl queue add "Attack on Titan" S4 --series-id 12345 --source max

# 4. Direct Multi-Series CLI Queue
emby-dl queue "Show A" S1 --series-id 111 ; "Show B" all --series-id 222

# 5. Queue Text Files (-f / --file)
# Inline --series-id can be specified directly on any line:
#   Black Clover S1 --series-id 276491
#   One Piece S2 all --series-id 99881 --source max

# 6. Maintenance & Inspection Commands
emby-dl list "Black Clover" --series-id 276491
emby-dl verify "Black Clover" --series-id 276491 --fix
emby-dl watch "Black Clover" --series-id 276491 --interval 600
```

#### 3.4.2 Automatic Smart Series Resolution Cache (`SeriesCache`)

To ensure maximum performance without requiring users to manually look up and specify
`--series-id` every time, `emby-dl` includes an automatic, persistent **Series Resolution Cache**
(`<downloads>/.emby_series_cache.json`):

1. **Automatic Seeding from Dataset:**
   The cache automatically seeds itself from `downloaded_dataset.json`. Any series you have
   ever downloaded is instantly known to the cache.
2. **Instant Verification (0.02s Lookup):**
   When you request a series by title (e.g., `emby-dl queue "الطبيب المعجزة"`), the tool checks
   the local cache first. If found, it verifies the cached ID with a lightweight `/Items/{id}`
   call in **0.02 seconds**, completely skipping the heavy, database-wide `/Items?SearchTerm=...`
   scan on the server.
3. **Continuous Self-Learning:**
   Whenever any series is resolved (via search or explicit `--series-id`), the mapping
   `normalized_name -> series_id` is automatically committed to `.emby_series_cache.json`.
   Subsequent commands resolve instantly with zero search overhead.
4. **Search Timeout Immunity:**
   Because Emby 4.x servers under transcode load can stall or timeout on full-text search queries,
   the Smart Cache eliminates the root cause of search timeouts for all known series.

Resolution Rules:

- `--series-id` specified → verify item is Series and use directly (0.03s)
- Name cached locally → verify cached ID and use directly (0.02s)
- Exactly one exact name match on server → use it and cache for future runs
- Exactly one search result on server → use it and cache for future runs
- Multiple matches / ambiguous results → exit `4` with candidates list
- No results found → exit `2`
- `--series-id` pointing at a non-Series item → exit `4`

### 3.5 `plan` — dry run before spending hours

```bash
tools/emby_dl plan "في السابعة عشر" S1 E19-23
```

```text
Series: في السابعة عشر (id=267519)

  SKIP S01E19    558.92 MiB  19
  SKIP S01E20      2.99 GiB  20
  ...
Summary: 0 to download, 5 skipped, 0 unknown size, 0 B to transfer
```

Labels: `GET ` (`todo`), `SKIP` (verified complete), `HEAL` (complete on disk but
missing from the dataset), `MISS` (recorded but the file is gone), `?` (server did
not report a size; the download will fail rather than trust it).

`plan --json` returns one object:

```json
{
  "series": {"id": "267519", "name": "في السابعة عشر"},
  "selection": {"mode": "episodes", "season": 1, "low": 19, "high": "23"},
  "items": [
    {"season": 1, "episode": 19, "episode_id": "276735", "title": "19",
     "container": "mkv", "relative_path": "...", "size_bytes": 586065115,
     "status": "skip", "reason": "ok", "on_disk": true, "disk_bytes": 586065115}
  ],
  "summary": {"total": 5, "todo": 0, "skip": 5, "todo_bytes": 0,
              "unknown_sizes": 0}
}
```

`plan` never creates a file and never takes the download lock.

---

### 3.6 Multi-Series Queue System (`queue` and `-f / --file`)

`emby-dl` provides official, hardened multi-series queue management. You can download or plan
dozens of series in a single command, from a text file, or by building a persistent queue.

#### 3.6.1 Direct Command-Line Queues

Multiple series can be specified directly on the command line:

```bash
# Download multiple series in one command (downloads 'all' real seasons by default)
emby-dl queue "فنون سارا The Art of Sarah" "The Flower of Evil - زهرة الشر" "لعبة الهرم Pyramid Game"

# Download with per-series episode ranges using ';' as delimiter
emby-dl queue "Show A" S1 ; "Show B" all ; "Show C" S2 E1-5

# Double-dash '--' can also be used as a delimiter between series
emby-dl queue "Show A" S1 E1-5 -- "Show B" all -- "Show C" S2

# Comma-separated list with 'download' command
emby-dl download "Show A, Show B, Show C"
```

#### 3.6.2 File-Based Queues (`-f / --file`)

For long queues or automated tasks, pass a text file containing series names and specs:

```bash
# Execute queue from a text file
emby-dl queue -f queue.txt
emby-dl download -f queue.txt

# Preview / dry-run plan for an entire queue file
emby-dl queue plan -f queue.txt
emby-dl plan -f queue.txt
```

**Queue File Syntax Rules (`queue.txt`):**
- One series per line.
- Blank lines and comment lines starting with `#` are automatically ignored.
- UTF-8 BOM (`\ufeff`) is automatically stripped (PowerShell / Windows safe).
- Any trailing spec tokens (`S1`, `all`, `E1-10`, `S2 E5-last`) are parsed as the episode selection.
- Supports inline `--series-id <id>` and `--source <source>` options per line.

*Example `queue.txt`:*
```text
# Morning batch
فنون سارا The Art of Sarah
The Flower of Evil - زهرة الشر S1 E1-10
انت من احب S1 E21-23 --source max
المدينة البعيدة S2 E27-35 --series-id 145070
المدينة البعيدة S3 all --series-id 145070
```

#### 3.6.3 Persistent Queue Management

Build a queue incrementally and run it whenever ready. The queue is stored atomically
in `<downloads>/.download_queue.json`.

| Action | Command | Purpose |
|---|---|---|
| `add` | `emby-dl queue add <series> [spec...]` | Add a series and spec to the queue |
| `list` | `emby-dl queue list [--json]` | View waiting items with 1-based indices |
| `run` | `emby-dl queue run [options]` | Execute the queue sequentially |
| `plan` | `emby-dl queue plan` | Dry-run plan for all queued items |
| `remove` | `emby-dl queue remove <index>` | Remove a series by its 1-based index |
| `clear` | `emby-dl queue clear` | Empty the entire persistent queue |

*Lifecycle Example:*
```bash
# 1. Add series over time
emby-dl queue add "Black Clover" S1
emby-dl queue add "One Piece" S2 all
emby-dl queue add "Attack on Titan" S4 --series-id 12345 --source max

# 2. Inspect waiting items
emby-dl queue list
# === Persistent Queue (3 items) ===
#   [1] Black Clover S1
#   [2] One Piece S2 all
#   [3] Attack on Titan [id=12345] S4

# 3. Dry-run plan and preview required storage
emby-dl queue plan

# 4. Remove an unwanted item
emby-dl queue remove 2

# 5. Start downloading the queue
emby-dl queue run --notify

# 6. Clear queue manually if needed
emby-dl queue clear
```

#### 3.6.4 Detailed Subcommand Help

Every queue action provides its own dedicated, rich `--help` manual:

```bash
emby-dl queue --help          # Main queue overview & syntax guide
emby-dl queue add --help      # Detailed guide & examples for adding series
emby-dl queue list --help     # Guide for listing and JSON exports
emby-dl queue run --help      # Guide for running and notification options
emby-dl queue plan --help     # Guide for dry-run previewing
emby-dl queue remove --help   # Guide for index-based removal
emby-dl queue clear --help    # Guide for clearing the queue
```

---

### 3.7 Multi-Level Automatic Failure Rescheduling

To ensure maximum download completion without human babysitting, `emby-dl` implements
a **Three-Level Failure Rescheduling Architecture**:

1. **Level 1 — Micro-Retry with Backoff:**
   When an episode encounters an error (transient network glitch, transcode startup delay, or socket timeout),
   it is retried up to 3 times with exponential backoff (e.g. 5s, 10s) and an extended 60s API timeout.
2. **Level 2 — Intra-Series Queue Rescheduling:**
   If an episode still fails after Level 1 retries, it is **not abandoned**. It is automatically
   moved to the **end of the current series queue** (`pending_queue.append(failed_item)`).
   Downloading continues immediately with the subsequent episodes so the queue is never halted.
   Once the other episodes complete, the failed episode is attempted again fresh. Because previously
   downloaded HLS segments are preserved in `<dest>.hls`, the retry resumes instantly.
3. **Level 3 — Queue-Level Sweep Pass:**
   At the end of a multi-series queue, the queue engine audits `downloaded_dataset.json`. If any
   series has missing episodes, it runs an automatic sweep pass. Because completed episodes are
   already recorded, they are skipped in milliseconds and only missing episodes are retrieved.

---

### 3.8 HLS Streaming Mode & Lossless Remuxing Pipeline

HLS is the **default** download mode (`"download_mode": "hls"`), ideal for servers running
Emby 4.x that hang on HTTP `Range:` headers or throttle single direct streams:

- **Configuration:** `"download_mode": "hls"` (default — no change needed). Set `"direct"` to fall back to a single HTTP stream (see section 1.2a for the full comparison and switching guide).
- **Parallel Segment Fetching:** Downloads small `.ts` segments concurrently using worker threads
  (`hls_workers`, default 4) with per-segment auto-retry (`hls_segment_retries`).
- **Lossless Remux Pipeline:** Segments are losslessly concatenated and remuxed into MKV or MP4.
  To eliminate Matroska timestamp rejections (`Can't write packet with unknown timestamp`), the
  engine remuxes via an MP4 intermediate (`TS -> MP4 intermediate -> MKV / MP4`), regenerating
  presentation timestamps (`+genpts`) while preserving 100% original video/audio quality.
- **Terminal Hang Protection:** ffmpeg is invoked with `-nostdin` to prevent 30-minute hangs on
  closed terminal standard input.
- **Embedded Subtitles:** Subtitle tracks are retrieved from Emby and muxed losslessly into the final MKV.

---

### 3.9 Telegram Two-Way Bot Integration & Real-Time Status

When `telegram_bot_token` and `telegram_chat_id` are configured in `config.json`, `emby-dl`
spawns an interactive daemon bot thread during downloads:

- **Automatic Bot Provisioning & Switching Safeguards:**
  - `deleteWebhook` Execution: Whenever the bot thread initializes, `deleteWebhook` is automatically
    invoked to clear any dangling webhooks from previous integrations. This completely eliminates
    silent `HTTP 409 Conflict` errors and ensures long-polling (`getUpdates`) always succeeds.
  - Automatic Description & Commands Registration: On startup, `setMyCommands`, `setMyDescription`,
    and `setMyShortDescription` configure the bot's commands and profile description on Telegram's servers.
  - Dynamic Hot-Reloading: If you edit `config.json` and change `telegram_bot_token` or `telegram_chat_id`
    while a multi-hour download is running, the running bot thread automatically detects the change
    within 30 seconds, provisions the new bot, and migrates polling seamlessly without interrupting the transfer.
- **Interactive Commands:**
  - `/status`: Displays real-time download status for the currently downloading episode, speed, remaining size, and estimated time of arrival (`ETA`).
  - `/queue`: Displays a comprehensive, detailed breakdown of the entire download queue, including overall queue progress, active series indicator (`⏳`), completed series (`✅`), waiting series (`🕒`), per-series episode counts (`x/y`), estimated sizes, and remaining queue total.
  - `/help`: Lists available bot commands.
  - `/ping`: Responds with `pong` to verify bot responsiveness.
- **Live ETA Calculation:** Real-time remaining time is computed from instantaneous transfer speeds
  and displayed in `/status` and in periodic 10-minute notifications.
- **Accurate Queue Progress:** `Total Queue` strictly filters against the active queue's target
  series IDs, ensuring historical downloads never distort queue progress counters.

#### Sample Bot Outputs

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

**`/queue` — Comprehensive Queue Breakdown:**
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

#### Switching to a New Bot (Step-by-Step)
```text
1. Create a new bot in @BotFather on Telegram and copy the API token.
2. Update "telegram_bot_token" in config.json.
3. Open your new bot in Telegram and tap "Start" (or send /start).
```
The tool will automatically delete any legacy webhooks, register all 5 commands in Telegram's menu, and provision the bot's description and profile summary. You can verify the bot connection at any time by running `emby-dl doctor`.

### 3.10 What happens during a download

For every item:

1. **Re-resolve size** from `MediaSources[0].Size`; if missing, fetch item detail;
   if still missing, one short HEAD; if still unknown → fail, record nothing.
2. **Check disk** (`free - disk_reserve_bytes >= file size`) → exit `6` if not.
3. **If the final file exists and is byte-exact** → skip (or self-heal the dataset).
4. **If a `.part` file exists** → one short Range probe. No progress or a bad
   response → delete it and download fresh.
5. **Download** with `curl -f -L --speed-limit … --speed-time …` into `.part` (or HLS segment fetcher).
6. **Validate** HTTP status (200/206), content type (reject `text/`, JSON, XML,
   HTML), curl exit code, and exact byte count.
7. **Rename** `.part` → final name atomically and write the dataset entry.

Any HTTP 401/403 exits `3` (auth). HTTP 5xx and connection failures retry with
health-gated backoff (15 → 30 → 60 → 120 … max 600 s). Other failures retry up to
`max_retries`; if an item still fails, it is rescheduled to the end of the series queue.

### 3.11 Progress modes

`--progress=auto|plain|json|none` (download/watch; `verify --fix` also accepts it):

| Mode | Behavior |
|---|---|
| `auto` (default) | plain if stdout is a TTY, otherwise none |
| `plain` | one `\r`-updated line: percent, bytes, speed, ETA |
| `json` | JSON Lines `progress` events (throttled to ~1/s) |
| `none` | silent |

`--json` forces progress mode `json` so stdout stays machine-readable.

A running download also writes `downloads/.emby_dl_state.json` roughly once per
second. `status` reads it:

```bash
tools/emby_dl status
# RUNNING pid 4242 | في السابعة عشر S01E22 | 45.2% | 1.47 GiB/3.25 GiB | 34.1 MiB/s | ETA 00:01:03 | updated 1s ago
tools/emby_dl status --json
```

### 3.12 `verify` — audit what is on disk

```bash
tools/emby_dl verify                       # every recorded file
tools/emby_dl verify "Black Clover" --series-id 276491
tools/emby_dl verify "Black Clover" --series-id 276491 --fix
```

For each dataset entry it checks: file exists, file size equals the server size,
and the dataset byte count matches the file. Issues:

| Issue | Meaning |
|---|---|
| `missing_file` | recorded but not on disk |
| `size_mismatch` | truncated or otherwise wrong |
| `unverified_size` | server did not report a size (cannot confirm) |
| `dataset_size_stale` | file is fine; dataset byte count is old |

`--fix` re-downloads `missing_file` and `size_mismatch` items and updates stale
dataset sizes. Exit `0` when no issues remain, `1` otherwise.

### 3.13 `watch` — poll for new episodes

```bash
tools/emby_dl watch "في السابعة عشر" --interval 600 --notify
tools/emby_dl watch "في السابعة عشر" missing --interval 600 --webhook "$URL"
tools/emby_dl watch "في السابعة عشر" --once --json
```

- Default selection is `missing`; any spec from §3.2 is accepted.
- Holds no lock while sleeping; takes the normal lock per download batch.
- If another download is running, the tick is skipped and retried next interval.
- Server/auth errors are reported, then retried next interval.
- `--once` runs a single tick and exits with that tick's code.
- `Ctrl-C` stops cleanly.
- Notifications are sent only when a batch actually downloads or fails.

### 3.14 Lock and disk behavior

- Lock file: `downloads/.emby_dl.lock` (flock). A second concurrent download
  exits **5**. The lock is held only while a batch is downloading.
- Disk preflight runs before a batch using the sum of known todo sizes, and again
  before each file. Insufficient space exits **6**; nothing is recorded.
- Only one curl connection exists at any moment in direct mode.

---

## 4. Inspecting and maintaining datasets

### 4.1 Where things live

| File | Role |
|---|---|
| `<downloads>/downloaded_dataset.json` | central index, schema v3 (authoritative commit point) |
| `<downloads>/<Series>/downloaded_dataset.json` | per-series derived copy |
| `<downloads>/.emby_dl.lock` | machine-wide download lock |
| `<downloads>/.emby_dl_state.json` | live progress state for single downloads & `status` |
| `<downloads>/.queue_state.json` | live queue progress state for multi-series runs & bot |
| `<downloads>/.download_queue.json` | persistent queue storage for `queue add/list/run` |
| `<downloads>/.emby_series_cache.json` | persistent series resolution cache (0.02s zero-search lookups) |
| `<downloads>/.emby_dl_caps.json` | cached server capabilities probe |
| `<downloads>/.emby_dl_headers` | mode `0600` private header file for curl (`-K`) |

Schema v3 keys by `series_id` and records exact byte size, season/episode IDs, source metadata, attempts, and duration:

```json
{
  "version": 3,
  "updated_at": "2026-11-07T12:00:00",
  "total_episodes": 23,
  "downloads": [
    {
      "series_name": "في السابعة عشر",
      "series_id": "267519",
      "season_id": "267520",
      "episode_id": "276735",
      "media_source_id": "ms1",
      "source_index": 1,
      "source_name": "1080p",
      "season_number": 1,
      "episode_number": 19,
      "original_name": "19.mkv",
      "file_name": "في السابعة عشر - S01E19.mkv",
      "relative_path": "في السابعة عشر/Season 1/في السابعة عشر - S01E19.mkv",
      "size_bytes": 586065115,
      "size_mb": 559,
      "container": "mkv",
      "attempts": 1,
      "tool_version": "1.3.0",
      "duration_minutes": 0.42,
      "downloaded_at": "2026-11-06 10:13"
    }
  ]
}
```

Writes are atomic (temp file + rename). Central dataset is the single atomic commit point;
legacy v1 and v2 datasets are upgraded on read.

### 4.2 Dataset commands

```bash
tools/emby_dl dataset                       # human table, marks missing files
tools/emby_dl dataset "المنظمة"               # exact series name filter
tools/emby_dl dataset --stats               # per-series counts + byte totals
tools/emby_dl dataset --stats --json
tools/emby_dl dataset --check               # audit dataset vs disk & derived files (exit 1 on issues)
tools/emby_dl dataset --sync                # regenerate all per-series datasets from central
tools/emby_dl dataset --dedupe              # remove duplicate entries, keeping best on-disk record
tools/emby_dl dataset --rebuild             # scan disk, rebuild v3 with exact sizes
tools/emby_dl dataset --prune               # drop records whose files are gone
```

`--rebuild` scans `<downloads>/<Series>/Season N/<Series> - SxxExx.ext`, preserves
known `episode_id`/`series_id` by (series, season, episode), and reports files it
cannot recognize. `--prune` never deletes media files; it only removes records.

Do **not** delete the datasets: they prevent re-downloading files that were moved.
To deliberately re-download, use `verify --fix` (for bad files) or remove the
specific dataset entry.

Dataset-vs-disk rules:

- complete file + missing record → record it (`HEAL`, no transfer)
- record + missing file → download again (`MISS`); v1 skipped forever, v1.1 fixes this
- partial `.part` → probe once, then fresh; never recorded until byte-exact

---

## 5. System diagnostics & other commands

### 5.1 `doctor` — check system and environment health

`doctor` runs pre-flight health checks covering tools, permissions, configs, dataset consistency, and server connectivity:

```bash
tools/emby_dl doctor
tools/emby_dl doctor --offline   # skip live server checks
tools/emby_dl doctor --json
```

Output checks:
1. `curl`: binary found in `$PATH`.
2. `ffmpeg`: required for `download_mode: "hls"`; auto-detected via `ffmpeg_path` → `$PATH` → known Windows locations. Reports `not required` in `direct` mode.
3. `server_url` & `access_token`: configured.
4. `env_overrides`: active `EMBY_*` environment variables.
5. `config_permissions`: config file is mode `0600`.
6. `downloads_dir`: directory exists and is writable.
7. `disk_space`: available free disk vs reserve.
8. `dataset`: runs `dataset --check`.
9. `dataset_schema`: matches v3.
10. `server_reachable` & `token_valid`: live server health check.
11. `telegram_bot`: verifies `telegram_bot_token` validity via Telegram `getMe` API and checks bot username.
12. `telegram_chat`: verifies `telegram_chat_id` configuration.

Exits `0` if all checks pass, `1` if any check fails.

### 5.2 Other commands

```bash
tools/emby_dl search "clover" --json
tools/emby_dl list "Black Clover" --series-id 276491 --json
tools/emby_dl config
tools/emby_dl config --json
tools/emby_dl auth
tools/emby_dl auth --reset-caps  # clear capabilities cache
tools/emby_dl auth --json
tools/emby_dl --version
```

`list --json` returns `series_id`, `series_name`, and one object per season with
`season_number`, `season_id`, `season_name`, `episode_count`. `search --json`
returns `[{"id", "name", "year"}]`. `config --json` hides `access_token` and any
webhook URL.

---

## 6. Machine-readable output (`--json`)

Info commands print one JSON object (or array). Health, verify, plan, dataset,
search, list and config always produce valid JSON on stdout.

`download` and `watch` print **JSON Lines** — one event per line:

```json
{"event":"start","ts":"...","series":{"id":"267519","name":"..."},"todo":2}
{"event":"progress","ts":"...","label":"... S01E19","downloaded":12345678,"total":586065115,"percent":2.1,"speed_bps":4200000,"eta_seconds":136}
{"event":"attempt","ts":"...","message":"... attempt 1/5"}
{"event":"verdict","ts":"...","status":"downloaded","reason":"ok","series":"...","season":1,"episode":19,"episode_id":"276735","path":"...","size_bytes":586065115}
{"event":"summary","ts":"...","series":{"id":"...","name":"..."},"total":2,"downloaded":1,"skipped":1,"failed":0,"bytes_downloaded":586065115,"failures":[]}
```

Verdict statuses: `downloaded`, `skipped`, `self_healed`, `failed`.
Failure reasons include `size_unknown`, `http_500`, `http_404`, `size_mismatch`,
`bad_content_type`, `server_unreachable`, and `curl_rc_<n>`.

`watch` additionally emits `watch_tick`, `error`, and the per-batch `summary`.

Errors are a single object: `{"error": "...", "code": N}`.

### Agent recipe

```bash
# 1. Resolve the exact series (refuse ambiguity yourself if exit 4)
tools/emby_dl search "المنظمة" --json

# 2. See what would happen; no side effects
tools/emby_dl plan "المنظمة" S7 E1-10 --json

# 3. Download, consuming JSONL
tools/emby_dl --json "المنظمة" S7 E1-10 | while IFS= read -r line; do
  printf '%s\n' "$line"
done

# 4. Check the final summary/exit code
code=${PIPESTATUS[0]}
```

---

## 7. Exit codes

| Code | Meaning |
|---:|---|
| `0` | success — downloaded, already present, or query answered |
| `1` | one or more downloads/verifications failed |
| `2` | not found — series, season or episode |
| `3` | configuration, authentication, or server unreachable |
| `4` | invalid arguments **or ambiguous series name** |
| `5` | another download is already running (lock busy) |
| `6` | insufficient disk space |

Unknown flags or invalid choices make `argparse` exit `2` (Python default, shared
with "not found"), so treat `2` carefully in agents and read the message. A known
option with a missing value is caught before `argparse` and exits `4`. The tool's
own "not found" messages are explicit.

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Ambiguous series name ... Candidates` | duplicate names on the server → add `--series-id <id>` |
| `Authentication failed (HTTP 401)` | token invalid → `login --username ...` |
| `Cannot connect to server` | server restart/outage → rerun; retries are health-gated |
| exit `5` | another `emby_dl` download is running → wait or stop it |
| exit `6` | not enough free space after `disk_reserve_bytes` → free space or lower the reserve deliberately |
| `resume unusable ... restarting fresh` | expected: this server hangs on Range requests; the fresh transfer follows |
| `size_unknown` | server did not report a size; the tool refuses to record an unverified file |
| `bad_content_type` | server returned an error page instead of video (check auth/server health) |
| `Episode N not found` | wrong season index/series → `list --series-id ...` |
| `Season N not found` | the series has different season indexes → `list` |
| `verify` reports `dataset_size_stale` | file is fine; rerun `verify --fix` or `dataset --rebuild` |
| `hls_error: timed out` | server transcode spin-up delay → automatically retried with 60s timeout, or supply `--series-id` to bypass database search |
| `hls_error: duration mismatch` | Emby boundary frame padding → automatically handled by `max(90.0, expected * 0.03)` tolerance |
| `Can't write packet with unknown timestamp` | Emby HLS video packets lack timestamps → automatically repaired by lossless MP4 intermediate (`TS -> MP4 -> MKV/MP4`) |
| `/queue command says Unknown command` | running bot process loaded older code in memory → restart the session to reload bytecode; `/setMyCommands` registers it automatically |

---

## 9. Tests

The repository includes a stdlib `unittest` suite (no pytest needed) that starts a
fake Emby server and exercises the real CLI + curl end to end:

```bash
python3 -m unittest discover -s tests -v
```

Covered: fresh download, skip, HTTP 500, truncated 200, unknown size, stall and
retry, Range hang, dataset-vs-disk repair, lock exit 5, ambiguity exit 4, invalid
specs, option/positional reordering (including a missing option value), plan/dry-run,
verify/--fix, rebuild/stats/prune, webhook, login/auth, search/list, and
`watch --once`.

---

## 10. File layout

```text
tools/
├── emby_dl                    # the tool (self-contained Python 3, executable)
└── README.md                  # this reference

docs/
└── EMBY_FULL_GUIDE.md         # server/API quirks, history, deep agent guide

tests/
└── test_emby_dl.py            # fake-server end-to-end tests

config.json                    # token + settings (chmod 600)
downloads/
├── downloaded_dataset.json    # central dataset (v2)
├── .emby_dl.lock              # machine-wide lock
├── .emby_dl_state.json        # live progress for `status`
└── <Series>/
    ├── downloaded_dataset.json
    └── Season N/
        └── <Series> - SxxExx.ext
```
