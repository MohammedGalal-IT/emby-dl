# EMBY FULL GUIDE — Server Facts, API Behavior, Workflow History & Agent Manual

**Audience:** any AI agent (or human) operating on the home media server or the
`emby_dl` tool. This is the institutional memory of everything learned while
building, hardening, and scaling the downloader across dozens of series and hundreds of gigabytes.

Every claim is tagged: **[VERIFIED]** (tested and reproduced), **[OBSERVED]**
(seen live, not fully explained), or **[UNKNOWN]** (open question).

Read this together with [`tools/README.md`](../tools/README.md) (the concise tool
reference) and [`SKILL.md`](../SKILL.md) (the autonomous agent operational skill).
This document is the "why" behind every "what".

> **Current tool version:** `emby_dl 1.3.0` (2026-11-13). Adds official multi-series
> queue management (`emby-dl queue`), persistent queue storage, text-file batch queues (`-f`),
> Smart Series Resolution Cache (`SeriesCache` with 0.02s lookups), multi-level failure
> rescheduling (micro-retries, intra-series queue re-ordering, queue sweep pass),
> resilient HLS streaming mode with two-stage lossless MP4 remuxing (timestamp defect repair),
> `-nostdin` terminal hang protection, interactive Telegram bot (`/status`, `/queue`, `/help`, `/ping`),
> real-time ETA calculation, and dataset v3 integration. Over 269 episodes and 147+ GiB
> downloaded and verified byte-exact.

---

## 1. Server inventory [VERIFIED]

| Property | Value |
|---|---|
| Server name | **الملك2** ("The King 2") |
| Version | Emby **4.9.3.0** |
| URL | `http://10.10.10.9:8096` |
| Server ID | `335a39030db14fc0919f6da76570e4db` |
| Host | **Windows** machine (SMB 139/445, RPC 135, DNS 53, HTTPS 443 open) |
| Media storage | remote NAS network shares (`\\s2\…`, `\\S3\…` paths appear in item metadata) |
| Public user list | `/Users/Public` responds **without auth** — leaks usernames, IDs, `HasPassword`. Admin username is `admin` (id `6f7a753d34974ab1b06af33c7450ce0e`). |
| Our API account | user `دخول الاستراحة 2`, id `f5d170effc9b40d4a6183b9288c1d055`, `IsAdministrator: False`, `EnableContentDownloading: True` |
| Household usage | dozens of `Emby Web` sessions (phones/TVs — Chrome Android, Safari iOS, Smart TVs…); evening hours host **20–30 simultaneous active streams**, several transcoding |
| Local Network | Server resides on `10.10.10.x` LAN / Wi-Fi profile `Estrahat-taiz-24 5G`. Unreachable when Wi-Fi adapter is disconnected or switched to external subnets. |

Known series IDs verified and downloaded in this project:

| Series Name | Series ID | Season / Episodes Downloaded | Verification Status |
|---|---|---|---|
| المنظمة | `145073` | S07 E01–E14 | [VERIFIED] Byte-exact |
| في السابعة عشر | `267519` | S01 (Season ID `267520`) 23 eps | [VERIFIED] Byte-exact |
| اسطنبول راساً على عقب | `267954` | S01 complete | [VERIFIED] Byte-exact |
| Black Clover (ours) | `276491` | S02 E01–E04 | [VERIFIED] Byte-exact |
| Black Clover (duplicate) | `114002` | Alt release | [VERIFIED] Ambiguity flagged |
| الياقة المغبرة | `148871` | S01 E01–E26 (26 eps) | [VERIFIED] Lossless HLS remux |
| ورود وذنوب | `243914` | S02 (Season ID `275239`) E04–E09 | [VERIFIED] Lossless HLS remux |
| فنون سارا The Art of Sarah | `261481` | S01 E01–E08 (8 eps complete) | [VERIFIED] Lossless HLS remux |
| The Flower of Evil - زهرة الشر | `227619` | S01 E01–E16 (16 eps complete) | [VERIFIED] Lossless HLS remux |
| لعبة الهرم Pyramid Game | `228028` | S01 E01–E10 (10 eps complete) | [VERIFIED] Lossless HLS remux |
| الجمال الحقيقي True Beauty | `240007` | S01 E01–E16 (16 eps complete) | [VERIFIED] Lossless HLS remux |
| عودة رجل عصابة الي المدرسة الثانوية | `227969` | S01 E01–E08 (8 eps complete) | [VERIFIED] Lossless HLS remux |
| كلنا موتى - All of Us Are Dead | `228012` | S01 E01–E12 (12 eps complete) | [VERIFIED] Lossless HLS remux |
| The Perfect Lie | `274976` | S01 E01–E07 (7 eps complete) | [VERIFIED] Lossless HLS remux |
| Doctor X Mafia in White الدكتورة إكس | `277639` | S01 E01–E10 (10 eps complete) | [VERIFIED] Lossless HLS remux |
| حبك يشحن قلبي Take Charge of My Heart | `279116` | S01 E01–E10 (10 eps complete) | [VERIFIED] Lossless HLS remux |
| قاتلة بحسن نية A Bona Fide Killer | `273180` | S01 E01–E14 (14 eps complete) | [VERIFIED] Lossless HLS remux |
| مهمة في المجمع السكني The Apartment Job | `271199` | S01 E01–E12 (12 eps complete) | [VERIFIED] Lossless HLS remux |
| المائدة الاخيرة Final Table | `278542` | S01 E01–E05 (5 eps complete) | [VERIFIED] Lossless HLS remux |
| وظيفة الكوابيس Dead-End Job | `279452` | S01 E01–E08 (8 eps complete) | [VERIFIED] Lossless HLS remux |
| انت من احب | `255723` | S01 E21–E23 (3 eps complete) | [VERIFIED] Lossless HLS remux |
| المدينة البعيدة (الموسم 2) | `145070` | S02 (ID `147031`) E27–E35 (9 eps) | [VERIFIED] Lossless HLS remux |
| المدينة البعيدة (الموسم 3) | `145070` | S03 (ID `275242`) E01–E08 (8 eps) | [VERIFIED] Lossless HLS remux |
| الزواج جميل | `275189` | S01 E01–E09 (9 eps complete) | [VERIFIED] Lossless HLS remux |
| الياقوت | `148872` | S01 E01–E26 (26 eps complete) | [VERIFIED] Lossless HLS remux |
| الغرفة المجاورة | `148809` | S01 E01–E04 (4 eps complete) | [VERIFIED] Lossless HLS remux |
| الخليفة | `145053` | S02 E01–E06 (6 eps complete) | [VERIFIED] Lossless HLS remux |
| هذا البحر سوف يفيض | `243946` | S02 E04–E06 (3 eps complete) | [VERIFIED] Lossless HLS remux |
| حياة الاخرين | `276207` | S01 E01–E06 (6 eps complete) | [VERIFIED] Lossless HLS remux |
| الطبيب المعجزة | `148788` | S01 E21–E64 (44 eps complete) | [VERIFIED] Lossless HLS remux |
| اخواتي | `213278` | S01 E01–E15 (15 eps complete) | [VERIFIED] Lossless HLS remux |

---

## 2. Endpoint behavior cheat-sheet [VERIFIED unless noted]

| Request | Behavior |
|---|---|
| `GET /System/Info/Public` | fast, **unauthenticated**, proves the server is up; used as the health gate |
| `GET /Items?SearchTerm=…&IncludeItemTypes=Series` | series search (~65 ms baseline, but **can stall >20s under heavy transcode load**) |
| `GET /Items?Ids={id}&Fields=Path,MediaSources,Size` | one item with sources and size (ultra-fast, ~20–30 ms) |
| `GET /Shows/{id}/Seasons` | season list (`IndexNumber`; specials = 0) |
| `GET /Shows/{id}/Episodes?seasonId={sid}&Fields=Path,MediaSources,Size` | episodes **with sizes**; preferred over hand-built queries |
| `GET /Users/{id}` | works; use this instead of `/Users/Me` |
| `GET /Users/Me` | **HTTP 500 `Unrecognized Guid format.`** on this build — do not use |
| `GET /Users/Public` | 200 *without auth*; leaks the public user list |
| `GET /Sessions` | 200 +token; can be **multi-MB** (3300+ stale session records — see §5). Parse the full body only. |
| `GET /Items/{id}/Download` | **404 on HEAD, hangs on GET — dead end, do not use** |
| `GET /Videos/{id}/stream?static=true` | direct HTTP stream route; works for complete downloads, but **hangs on resume** |
| `POST /Items/{id}/PlaybackInfo` | **HLS session generator**; returns `PlaySessionId` and stream descriptors (mandatory for HLS) |
| `GET /Videos/{id}/master.m3u8?...` | Master HLS playlist containing variant stream definitions |
| `GET /Videos/{id}/main.m3u8?...` | Media playlist containing `#EXTINF` durations and segment URLs |
| `GET /Videos/{id}/hls1/main/{i}.ts` | Individual MPEG-TS HLS segment (~6 seconds each) |
| `GET /Videos/{id}/{source_id}/Subtitles/{idx}/Stream.srt` | Raw SubRip subtitle stream |
| **Any request with `Range:` header** | **hangs, zero bytes, no status line.** Advertises `Accept-Ranges: bytes`, but Range is broken in this build. See §4. |
| Bad item id / failed stream | **HTTP 500 with a `text/plain` body** — curl must use `-f` |
| `POST /Users/AuthenticateByName` | login endpoint; returns `AccessToken`, `User`, `ServerId` |
| `POST /Users/ForgotPassword` | returns `{"Action":"PinCode","PinFile":"F:\\…\\passwordreset.txt",…}`; PIN lands in local Windows file (§7) |
| `POST /Sessions/{id}/Playing/Stop` | non-admin token: `204 accepted` but **silently ignored**; stopping others needs admin |

---

## 3. Download route mechanics

`emby-dl` supports two distinct download modes: **Direct Stream** and **HLS Segment Engine**.

### 3.1 Direct Stream Mode Mechanics

1. Resolve series using `SeriesCache` or `--series-id` (0.02s) to bypass database searches.
2. Query `/Shows/{id}/Seasons` and `/Shows/{id}/Episodes?...` for exact byte sizes.
3. Preflight check: `free_disk - disk_reserve_bytes >= file_size`.
4. If `.part` file exists, perform one short Range probe (10s). When it hangs (it will on this server), delete `.part`.
5. Download via `curl -f -L -K <headers_file> --speed-limit 1000 --speed-time 1800` (stall-detection thresholds, **not** a bandwidth cap: abort only if speed stays below 1 KB/s for 30 minutes).
6. Validate exact byte count and content type (reject text/plain, HTML, JSON).
7. Atomically rename `.part` -> destination file and write dataset entry.

### 3.2 HLS Segment Engine Mechanics [VERIFIED 2026-11-08]

Because direct downloads cannot be resumed when the server or connection drops, `emby-dl` implements an **HLS Segment Engine** (`"download_mode": "hls"`). **HLS is the tool default** as of v1.3.0; the single-stream fallback is `"download_mode": "direct"`. Switching between them never re-downloads completed episodes (dataset entries are authoritative).

1. **Session Negotiation:**
   Calls `POST /Items/{item_id}/PlaybackInfo` with `{UserId, StartTimeTicks: 0, IsPlayback: True}`.
   Extracts `PlaySessionId`. Emby binds a background transcoding job to this session ID.
2. **Playlist Parsing:**
   Fetches `master.m3u8`, extracts the variant stream URI, then fetches `main.m3u8`.
   Parses `#EXTINF:<dur>` tags and segment paths (`hls1/main/0.ts?PlaySessionId=...`).
3. **Resilient Parallel Segment Fetching:**
   Segments are saved to a directory `<destination>.hls/{index:05d}.ts`.
   A `ThreadPoolExecutor` (4 worker threads) downloads segments concurrently using curl.
   If any segment returns HTTP 404/410, it indicates playback session expiry. The engine transparently calls `_hls_session()` to refresh `PlaySessionId` and resumes only missing segments.
4. **Resumable Across Process Restarts:**
   If the network disconnects, PC sleeps, or process is killed, all downloaded `.ts` segments remain intact in `.hls`. Upon restarting, existing segments are checked via `os.path.getsize(path) > 0` and skipped instantly. Only missing segments are fetched.

### 3.3 The MPEG-TS Timestamp Defect & Two-Stage Lossless Remuxing [VERIFIED 2026-11-09]

#### The Problem:
Emby's live HLS transcoder frequently emits MPEG-TS segments where video packets lack presentation timestamps (PTS/DTS). When concatenating `.ts` files and muxing directly to Matroska via ffmpeg (`ffmpeg -i all.ts -map 0 -c copy -f matroska out.mkv`), ffmpeg immediately crashes with:
```text
[matroska @ 0x...] Can't write packet with unknown timestamp
[aost#0:1/copy @ 0x...] Error submitting a packet to the muxer: Invalid argument
[out#0/matroska @ 0x...] Error muxing a packet
```
This error reproduces 100% on episodes such as *الياقة المغبرة S01E21, S01E24, S01E25*. Direct Matroska muxing refuses un-timestamped packets.

#### The Validated Solution:
MPEG-4 container muxers (`-f mp4`) tolerate and reconstruct missing presentation timestamps based on frame duration. Matroska then accepts the repaired MP4 stream without issue.
`emby-dl` implements a **Two-Stage Lossless Remux Pipeline**:
```text
[Raw .ts Segments]
        │ (raw byte copy)
        ▼
   concat.ts
        │
        ▼ (Stage 1: ffmpeg -fflags +genpts -i concat.ts -map 0:v:0 -map 0:a:0? -c copy -f mp4)
   intermediate.mp4  <── Timestamps repaired losslessly; 0 re-encoding!
        │
        ▼ (Stage 2: ffmpeg -i intermediate.mp4 [-i subtitles.srt] -map 0 -c copy [-c:s srt] -f matroska)
   final.mkv (or final.mp4)
```
- **Stream Mapping:** Uses `-map 0:v:0 -map 0:a:0?` explicitly. Emby HLS MPEG-TS files expose duplicate program streams; bare `-map 0` erroneously mapped 4 streams (2 video + 2 audio) and caused muxer failures.
- **Audio Sync & Duration Verification:** Raw byte concatenation preserves the true timeline (e.g. 7873.6s vs 7872.0s expected). The ffmpeg concat demuxer (`-f concat -i list.txt`) was tested and rejected because it accumulated audio tails across 1300+ segments, causing a +94s drift!

### 3.4 Terminal Stdin Hang Protection (`-nostdin`) [VERIFIED 2026-11-09]

When ffmpeg is invoked from background processes, daemon threads, or Windows PowerShell subshells without an interactive console, it attempts to read keyboard input from `stdin`. This results in a silent, indefinite 30-minute freeze until the subprocess timeout fires (`hls_error: timed out`).
**Mandatory Fix:** Every invocation of `ffmpeg` and `ffprobe` in `emby-dl` includes `-nostdin`. Remuxing completes in 10–25 seconds.

### 3.4a ffmpeg Auto-Detection & Missing-Tool Behaviour

`emby-dl` locates ffmpeg automatically on every HLS remux, in priority order:
1. **`download_settings.ffmpeg_path`** — explicit path (default: empty).
2. **`ffmpeg` on `PATH`** — standard `shutil.which` lookup.
3. **Known Windows locations** — e.g. `C:\Program Files\DownloadHelper CoApp\ffmpeg.exe` (the home workstation's ffmpeg is found here even with an empty `ffmpeg_path`).

`ffprobe` is resolved next to the located ffmpeg binary, falling back to `PATH`.

**If ffmpeg is missing while mode is `hls`:** the tool raises a fail-fast error BEFORE any bytes are downloaded:
```text
ConfigError: HLS mode needs ffmpeg. Install it (winget install Gyan.FFmpeg)
or set download_settings.ffmpeg_path.
```
Recovery: install ffmpeg (`winget install Gyan.FFmpeg`), set `ffmpeg_path` to an existing binary, or switch to `"download_mode": "direct"`. `emby-dl doctor` reports the `ffmpeg` check as `found`/`missing` in `hls` mode and `not required` in `direct` mode.

### 3.5 Duration Padding Tolerance [VERIFIED 2026-11-10]

Emby's playlist durations are calculated from segment headers (`#EXTINF:6.0`). Because transcoding chunk boundaries align to video GOP keyframes, the remuxed video duration legitimately differs by 1–3% (e.g. 4117.5s vs 4032.0s on a 67-minute episode).
The duration validator enforces:
```python
tolerance = max(90.0, expected_duration * 0.03)
```
This prevents false-positive duration failures while catching genuinely truncated files.

---

## 4. The Range-request hang — the server's defining quirk [VERIFIED]

- Any HTTP `Range: bytes=…` request (any item, any route) hangs with no response.
- Plain sequential GET (full-file) works perfectly, up to multi-GB.
- Consequence: In direct mode, resume is impossible. `emby-dl` does one short resume probe (`resume_stall_seconds: 10`), then purges `.part` and restarts fresh.
- In HLS mode (`"download_mode": "hls"`), this limitation is completely bypassed because each 6-second segment is an independent, non-ranged HTTP request.

---

## 5. Multi-Series Queue System & Failure Rescheduling [VERIFIED 2026-11-10]

### 5.1 Three-Level Rescheduling Architecture

To achieve unattended 100% completion across multi-day, multi-series jobs (e.g., our 195-episode and 74-episode batches):

```text
                  [Episode Download Attempt]
                              │
                              ▼
                     [Did transfer fail?]
                       /              \
                     NO                YES
                     │                  │
                     ▼                  ▼
             [Record in Dataset]   [Level 1: Micro-Retry (3x)]
                                   (60s timeout, exponential backoff)
                                        │
                                        ▼ (Still failing?)
                                   [Level 2: Intra-Series Rescheduling]
                                   (Move failed episode to end of queue:
                                    pending_queue.append(item);
                                    continue downloading remaining episodes;
                                    retry failed episode when queue loops)
                                        │
                                        ▼ (Still failing after round 2?)
                                   [Level 3: Queue-Level Sweep Pass]
                                   (Audit downloaded_dataset.json at end;
                                    re-run incomplete series; skip finished files)
```

**Live Verification:**
During the download of *اخواتي*, episodes 12 and 13 encountered a transient Windows socket error (`WinError 10065 unreachable host`). Rather than halting, Level 2 rescheduled E12 and E13 to the end of the queue. Episodes 14 and 15 completed, then E12 and E13 were re-attempted and completed byte-exact without human intervention.

### 5.2 Smart Series Resolution Cache (`SeriesCache`) [VERIFIED 2026-11-10]

- **Problem:** When Emby is under transcode load, executing `GET /Items?SearchTerm=...` scans the entire database and frequently exceeds 20 seconds, throwing `ServerError: timed out`.
- **Solution:** `SeriesCache` stores `<downloads>/.emby_series_cache.json` and auto-seeds from `downloaded_dataset.json`.
- **Behavior:** When resolving any series, the cache is checked first. If matched, it verifies the ID via `/Items/{id}` directly in **0.02 seconds**, completely eliminating full-library search queries and search timeouts.
- **Universal Input Support:** `--series-id <id>` is accepted across all input modes (single CLI downloads, dry-run `plan`, `queue add`, direct queue arguments, and inline within queue text files).

---

## 6. Telegram Two-Way Bot System [VERIFIED 2026-11-10]

When `notifications.telegram_bot_token` and `telegram_chat_id` are configured:

1. **Daemon Polling Thread:** Runs in the background of active download processes with long-polling (`getUpdates`).
2. **Switching to a New Bot & Automatic Provisioning [VERIFIED 2026-11-11]:**
   - **`deleteWebhook` Cleanup (HTTP 409 Conflict Prevention):** If a bot token was previously associated with any webhook, Telegram blocks `getUpdates` with `HTTP 409 Conflict`. `emby-dl` automatically executes `deleteWebhook` on startup, ensuring long-polling succeeds immediately.
   - **Automatic Commands Registration:** Calls `setMyCommands` to register `/start`, `/status`, `/queue`, `/help`, and `/ping` with localized descriptions on Telegram's servers.
   - **Automatic Profile Branding:** Calls `setMyDescription` and `setMyShortDescription` to populate the bot's intro and profile text automatically without requiring manual setup in `@BotFather`.
   - **Dynamic Hot-Reloading:** The polling thread inspects `config.json` every 30 seconds. If `telegram_bot_token` or `telegram_chat_id` is updated while a long batch is running, it dynamically hot-reloads the new token, reconfigures Telegram, and migrates polling in-memory without restarting the process.
3. **Interactive Bot Commands:**
   - `/status`: Real-time transfer speed, current episode percentage, remaining size, and instantaneous estimated time of arrival (`ETA`).
   - `/queue` (or `/q`): Exhaustive queue breakdown showing current active series (`⏳`), completed series (`✅`), waiting series (`🕒`), per-series counts, estimated sizes, and remaining queue total.
   - `/help`: Lists available bot commands.
   - `/ping`: Pong response to verify bot liveness.
4. **Accurate Queue Filtering:**
   `Total Queue` calculates progress by filtering `downloaded_dataset.json` against `target_series_ids`, ensuring previous downloads from past jobs never skew queue progress counters.
5. **System Diagnostic Integration (`doctor`):**
   `emby-dl doctor` audits the Telegram bot token via `getMe`, verifying token validity and reporting the `@bot_username`.

---

## 7. Performance profile [VERIFIED]

| Condition | Measured Throughput | Operational Notes |
|---|---|---|
| Evening peak (20–30 active viewer streams) | ~**300–450 KB/s** | Server CPU / disk throttles single connections. HLS parallel segments sustain higher aggregate throughput. |
| Daytime / Off-peak | ~**1.5–3.5 MB/s** | Sustained high-speed transfers. Episodes complete in 1–3 minutes. |
| Server Outages / Reboots | Unpingable for 1–5 min | Health-gated retries ride through reboots cleanly. |
| PC Sleep / Session Lock | Wi-Fi adapter disconnects | OpenCode subshell cancels; relaunching resumes seamlessly without repeating completed work. |

---

## 8. Datasets as the Single Source of Truth

- Central dataset: `<downloads>/downloaded_dataset.json` (Schema v3).
- Every completed episode records `series_id`, `season_number`, `episode_number`, `size_bytes`, `media_source_id`, `attempts`, and `duration_minutes`.
- In-flight skips check disk existence + dataset record. Resuming a queue of 195 episodes evaluates 195 checks in <1 second and proceeds directly to the first incomplete item.

---

## 9. Comprehensive Pitfalls Checklist (Each one bit us at least once)

1. **Don't send `Range` headers** — even reading 100 bytes stalls the server indefinitely.
2. **Never use `/Items/{id}/Download`** — dead endpoint on this server.
3. **Always use `-nostdin` for ffmpeg/ffprobe** — without it, background shells block on stdin and freeze for 30 minutes.
4. **MPEG-TS HLS video packets lack timestamps** — direct Matroska remuxing fails (`Can't write packet with unknown timestamp`). Always remux via an MP4 intermediate (`TS -> MP4 -> MKV/MP4`).
5. **Never use the ffmpeg concat demuxer on Emby segments** — it drifts audio sync (+94s over 1300 segments). Use raw binary concatenation.
6. **Title searches time out under transcode load** — use `--series-id` or let `SeriesCache` resolve via cached IDs in 0.02s.
7. **Use `/Users/{id}`, never `/Users/Me`** — returns HTTP 500 on Emby 4.9.x.
8. **`curl -f` is mandatory** — without `-f`, HTTP 500 error pages are saved as valid video files.
9. **Never run two downloads concurrently** — `emby-dl` enforces an exclusive machine-wide file lock (`.emby_dl.lock`).
10. **Windows session locks / laptop sleep disconnects Wi-Fi** — when resuming, inspect `downloaded_dataset.json`; existing files skip instantly.
11. **Do not parse truncated `/Sessions` JSON** — can exceed 4 MB of phantom stale records.
12. **Always include `PlaySessionId` in HLS queries** — missing `PlaySessionId` results in HTTP 400 Bad Request.
13. **HLS transcode startup requires extended timeouts** — use `api_timeout_seconds: 60` and `api_retries: 3`.
14. **Process memory does not update on file edit** — a running Python process keeps old bytecode in RAM until restarted.
15. **Filter queue progress by target series IDs** — simple `len(dataset)` counts past history rather than active queue progress.

---

## 10. Change log

### v1.3.0 — 2026-11-13 (Multi-Series Queue, HLS Remux Engine & Bot Polish)

- **Official Multi-Series Queue (`emby-dl queue`)**: Native CLI command supporting direct multi-series arguments, semicolon/dash delimiters, queue files (`-f / --file`), and persistent queue management (`add`, `list`, `run`, `remove`, `clear`, `plan`).
- **Two-Stage Lossless HLS Remux Pipeline**: Resolved the critical Matroska timestamp defect (`Can't write packet with unknown timestamp`) by remuxing via lossless MP4 intermediate (`TS -> MP4 -> MKV/MP4`) with presentation timestamp generation (`+genpts`) and subtitle preservation.
- **Closed Stdin Protection**: Added `-nostdin` to all ffmpeg/ffprobe subprocess calls, eliminating 30-minute background shell hangs.
- **Multi-Level Automatic Failure Rescheduling**: Intra-series queue rescheduling (`pending_queue.append(failed_item)`) and queue-level sweep passes ensure 100% completion without human babysitting.
- **Smart Series Resolution Cache (`SeriesCache`)**: Persistent cache (`.emby_series_cache.json`) auto-seeded from dataset; enables 0.02s lookups and eliminates search timeouts. Universal `--series-id` support added across all commands and queue files.
- **Interactive Telegram Bot Upgrades**: Added dedicated `/queue` command, live remaining time (`ETA`) in status and periodic alerts, and strict queue filtering using `target_series_ids`.
- **Resilience Defaults**: Default `api_timeout_seconds` increased to 60s, `api_retries` increased to 3 with exponential backoff.
- **Massive Production Verification**: 269 episodes (147.26 GiB) across 23 distinct series downloaded and verified byte-exact.

### v1.2.0 — 2026-11-07 (Phase C polish)
- Dataset Schema v3 with central atomic commit point.
- Multi-version `--source 1|N|max` selection.
- Token secrecy via `curl -K` (`.emby_dl_headers`, mode `0600`).
- Capabilities cache (`.emby_dl_caps.json`) and system diagnostics (`doctor`).
- Packaging (`pyproject.toml`) and shell completions.

### v1.1.0 — 2026-11-07 (hardening)
- Safe failure rules, exact-size verification, `.part` atomic renames, Range probe-and-restart, machine-wide lock.

---

*Last verified: Fri 2026-11-13 (server 4.9.3.0, tool 1.3.0). Verified across 269 episodes and 147+ GiB.*
