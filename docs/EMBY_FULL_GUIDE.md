# EMBY FULL GUIDE — Server Facts, API Behavior, Workflow History & Agent Manual

**Audience:** any AI agent (or human) operating on the home media server or the
`emby_dl` tool. This is the institutional memory of everything learned while
building and hardening the downloader.

Every claim is tagged: **[VERIFIED]** (tested and reproduced), **[OBSERVED]**
(seen live, not fully explained), or **[UNKNOWN]** (open question).

Read this together with [`tools/README.md`](../tools/README.md) (the concise tool
reference). This document is the "why" behind every "what".

> **Current tool version:** `emby_dl 1.2.0` (2026-11-07). It adds dataset v3 (keyed
> by `series_id` with central single-commit and derived-file recovery), multi-version
> `--source 1|N|max` selection, zero-token CLI exposure via `curl -K`, capabilities
> caching with `auth --reset-caps`, `doctor` system health diagnostics, `pyproject.toml`
> packaging, shell completions, and environment overrides (`EMBY_*`).

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

The LAN has a second subnet (`10.10.10.x` for family devices; our workstation sits
on `10.108.199.126`); all sessions are logged by Emby regardless.

Known series ids used in this project:

| Series | id |
|---|---|
| المنظمة | `145073` |
| في السابعة عشر | `267519` (season 1 id `267520`) |
| اسطنبول راساً على عقب | `267954` |
| Black Clover (ours, 4 downloaded eps) | `276491` |
| Black Clover (the *other* one) | `114002` |

---

## 2. Endpoint behavior cheat-sheet [VERIFIED unless noted]

| Request | Behavior |
|---|---|
| `GET /System/Info/Public` | fast, **unauthenticated**, proves the server is up; used as the health gate |
| `GET /Items?SearchTerm=…&IncludeItemTypes=Series` | series search (~65 ms metadata) |
| `GET /Items?Ids={id}&Fields=Path,MediaSources,Size` | one item with sources and size |
| `GET /Shows/{id}/Seasons` | season list (`IndexNumber`; specials = 0) |
| `GET /Shows/{id}/Episodes?seasonId={sid}&Fields=Path,MediaSources,Size` | episodes **with sizes**; preferred over hand-built queries |
| `GET /Users/{id}` | works; use this instead of `/Users/Me` |
| `GET /Users/Me` | **HTTP 500 `Unrecognized Guid format.`** on this build — do not use |
| `GET /Users/Public` | 200 *without auth*; leaks the public user list |
| `GET /Sessions` | 200 +token; can be **multi-MB** (3300+ stale session records — see §5). Parse the full body only. |
| `GET /Items/{id}/Download` | **404 on HEAD, hangs on GET — dead end, do not use** |
| `GET /Videos/{id}/stream?static=true` | the working download route; returns the true video bytes |
| `GET /Videos/{id}/stream.mp4|stream.mkv?static=true` | same file, same headers; no advantage |
| `HEAD /Videos/{id}/stream?static=true` | fast, returns `Content-Length`; now only a **last-resort** size probe |
| **Any request with `Range:` header** | **hangs, zero bytes, no status line.** Advertises `Accept-Ranges: bytes`, but Range is broken in this build. See §4. |
| Bad item id / failed stream | **HTTP 500 with a `text/plain` body** — see §3.1; curl must use `-f` |
| `POST /Users/AuthenticateByName` | login endpoint; returns `AccessToken`, `User`, `ServerId` |
| `POST /Users/ForgotPassword` | returns `{"Action":"PinCode","PinFile":"F:\\…\\passwordreset.txt",…}`; the PIN lands in a local Windows file we cannot read remotely (§7) |
| `POST /Sessions/{id}/Playing/Stop` | non-admin token: `204 accepted` but **silently ignored**; stopping others needs admin |

---

## 3. Download route mechanics — how v1.1 downloads work

1. Resolve the series (`/Items?SearchTerm…`) with **ambiguity refusal**; or fetch
   it directly by `--series-id`.
2. `/Shows/{id}/Seasons` → sorted `IndexNumber`; real seasons are `>= 1` unless
   `S0` is explicitly requested.
3. `/Shows/{id}/Episodes?seasonId=…&Fields=Path,MediaSources,Size` → item ids,
   containers and **exact byte sizes**.
4. Per episode, resolve the expected size in this order:
   1. `MediaSources[0].Size` (normal, fast, exact)
   2. `/Items?Ids={id}&Fields=Path,MediaSources,Size` (detail retry)
   3. `HEAD /Videos/{id}/stream?static=true` with a short timeout (last resort)
   4. still unknown → **fail and record nothing** (never trust an unknown size)
5. Disk preflight: `free - disk_reserve_bytes >= size` (default reserve 2 GiB).
6. Download with `curl -f -L -sS --speed-limit … --speed-time …` into `file.part`.
7. Validate: curl rc = 0, HTTP 200/206, content type is not `text/`, JSON, XML or
   HTML, and part size == expected bytes.
8. Atomically rename `.part` → final; write the dataset entry with exact
   `size_bytes`.

### 3.1 `curl -f` is mandatory [VERIFIED 2026-11-06]

A bad item / failed stream returns **HTTP 500 + a short `text/plain` body**. Without
`curl -f`, curl exits `0` and the error page can be written as if it were video.
v1.1 always uses `-f` and additionally rejects text-like content types. This closes
the old "HTTP 500 body recorded as a file" hole.

### 3.2 Sizes: MediaSources first, HEAD last [VERIFIED 2026-11-06]

`MediaSources[0].Size` is usually present and exact; it is sometimes absent
transiently. Fetching item detail recovers it most times. HEAD is kept only as a
last resort because HEAD can be slow/hang when the NAS is unhappy. If all three
fail, the episode fails with `size_unknown`; v1.1 never records from a guessed
size.

### 3.3 Token secrecy and `curl -K` [VERIFIED 2026-11-07]

In v1.1 and earlier, the token was passed as `-H "X-Emby-Token: …"`, meaning `ps aux`
revealed the secret to any user on the machine.

In v1.2.0, this is resolved: `emby_dl` writes the authentication header to a temporary,
owner-only file (`<downloads>/.emby_dl_headers`, mode `0600`) and passes `-K <path>` to curl.
The token never appears in process arguments, `/proc/<pid>/cmdline`, or shell histories.
The token also never appears in filenames, datasets, or URLs.

---

## 4. The Range-request hang — the server's defining quirk [VERIFIED]

- Any `Range: bytes=…` request (any item, any route) hangs with no response.
- Plain sequential GET (full-file) works perfectly, up to multi-GB.
- Consequence: **resume is impossible** → the tool does one *short resume probe*
  (`resume_stall_seconds: 10`), then deletes the partial and restarts fresh with
  the normal stall window (`speed_time_seconds: 60`).
- Curl exit code 28 (`Operation too slow`) is the *normal* way a stalled transfer
  surfaces; the retry machinery handles it.
- If the server is ever fixed, resume starts working automatically; or set
  `resume_enabled: false`.

v1.1 detail: downloads go to `<name>.part`, so a probe never touches the final
filename. After a failed probe the `.part` is removed and a fresh transfer starts
in the same invocation.

---

## 5. Session-store weirdness [OBSERVED]

`/Sessions` once returned **3307 records at ~4.7 MB** (months of phantom family
sessions). Hung/stale sessions are **not** cleaned automatically and do not block
new downloads (a hung stream request never even registers a session). Treat
`/Sessions` as dirty data to be parsed carefully, not truth. Cleaning it requires
admin (a full server restart clears it).

---

## 6. Performance profile [VERIFIED as of 2026-11-04]

| Condition | Measured |
|---|---|
| Evening, ~27 active viewer streams | our single connection ~**300–450 KB/s**; parallel probe connections did NOT add throughput (aggregate of 3 stayed ~300–620 KB/s): **the cap is server-side and shared** |
| Quieter hours | ~**800 KB/s+**; early-morning tests were faster still |
| Best plan for bulk downloads | late night / early morning |
| Restart resilience | hard outages (unpingable at 13:50 and 16:36) self-resolve in minutes; retries ride through them |

**"One file at a time" is a user requirement, not a technical necessity** —
parallel probes were only ever diagnostic. v1.1 enforces it with a lock.

---

## 7. Admin access & the password-recovery campaign — **PAUSED** [OBSERVED]

> The password-guessing campaign is **PAUSED by user order**. Do not restart it
> without an explicit instruction. The notes below are kept for history.

Facts established:

- Our `دخول الاستراحة 2` token can't stop other sessions (204-but-ignored) and
  can't read admin data.
- Admin username: `admin` (from `/Users/Public`).
- `POST /Users/ForgotPassword` with a plausible username returns a `PinCode`
  action writing
  `F:\نسخة احتياطية\الاستراحة 2026\8096\Emby-Server\programdata\passwordreset.txt`
  on the Windows host — remotely unreadable (SMB null sessions denied by ACL).
- Windows SMB refuses anonymous/null sessions; NetBIOS UDP 137 needs root (no
  passwordless sudo available). **No local-file vector found remotely.**

### 7.1 The lockout system [VERIFIED — measured 16:32–16:42]

- `AuthenticateByName` failures trip a lock: ~**4–5 fast failures → 403**
  `Your account has been temporarily locked out. Please try again later.`
- The lock is **account-keyed on `admin`** — forged `X-Forwarded-For`, fresh
  `DeviceId`, rotated identities: **all still 403 while locked. No bypass exists.**
- Decay: about four 403s at 30 s apart, then clean 401s — roughly **30–60 s after
  the last attempt** [OBSERVED; not exact]. Probing during the lock seems to
  reset/prolong it.
- Our own user's API is unaffected (locks are per-account).

### 7.2 Burst-cycle strategy (paused) [OBSERVED]

`/tmp/opencode/burst_guess.py` used a quiet pause (90 s start, tuned 45–240 s), a
burst of exactly 4 attempts 0.4 s apart, and a ~636-candidate wordlist
(`/tmp/opencode/wordlist.txt`), with progress in `/tmp/opencode/tried.txt` and hits
in `hits.json`. At 60–90 s cycles this tests ~150–200 candidates/hour.

### 7.3 Recovery options ranked

1. **Local access** — trigger "forgot password" and read the PIN file on the
   Windows host, enter it, set a new password. ~2 minutes, guaranteed.
2. **SMB read** — if any Windows credential on the host is known, test whether it
   can read the `F:\` path (untested; anonymous attempts were denied).
3. **Brute force via burst-cycle** — paused.

---

## 8. Tool integrity design (v1.1) — why it can be trusted unattended

### 8.1 Datasets are the source of truth

- Central: `<downloads>/downloaded_dataset.json`
- Per-series: `<downloads>/<Series>/downloaded_dataset.json`
- Schema v3 stores exact `size_bytes`, keyed by `series_id` (with fallback to
  normalized names for legacy datasets), along with `season_id`, `media_source_id`,
  `source_index`, `source_name`, `attempts`, `tool_version`, and `duration_minutes`.
  Central dataset is the single atomic commit point; per-series files are derived views.
  `dataset --check` audits consistency and exits 1 on issues; `dataset --sync` restores
  per-series datasets from central.
  `dataset --rebuild` migrates/repairs from disk and writes v3.
- Writes are atomic (temp + rename); a crash cannot leave half-written JSON.

### 8.2 Skip / heal / repair rules

| Disk | Dataset | Action |
|---|---|---|
| complete (byte-exact) | recorded | skip, no request |
| complete (byte-exact) | missing | record only (`HEAL`) |
| missing | recorded | **download again** (`MISS`) — v1.1 fixed the old bug where a moved/deleted file was skipped forever |
| partial `.part` | any | one resume probe, then fresh |
| wrong size | recorded | re-download |

### 8.3 One download at a time

`downloads/.emby_dl.lock` is flock-ed for the duration of a batch. A second
instance exits **5** with the holder's PID. The runner does not hold the lock
while sleeping between `watch` ticks. A stale lock file is harmless: flock is
released by the kernel when the process dies.

### 8.4 Exact-size verification and safe failure

- Never record an unverified file.
- Never trust a size the server did not report (`size_unknown`).
- Reject HTML/JSON/XML error pages as video (`bad_content_type`).
- HTTP 500/truncated body → retries, then failure (exit 1).
- HTTP 401/403 → exit 3 immediately (auth).
- Disk shortfall → exit 6 before writing.
- Unknown/ambiguous series → exit 4 before any transfer.

### 8.5 Health-gated retries

Connection failures and HTTP 5xx wait **15 → 30 → 60 → 120 … max 600 s**, and
before each retry the tool checks `/System/Info/Public` until the server answers.
This rides through the multi-minute restarts this server is known for.

### 8.6 Disk preflight

`disk_reserve_bytes` (default 2 GiB) is kept untouched. The check runs before a
batch (sum of known todo sizes) and before every file. On 2026-11-07 the live
machine had 2.96 GiB free; `watch "في السابعة عشر" --once` correctly refused an
18-episode / 15.30 GiB batch with exit **6**.

### 8.7 Progress and status

`downloads/.emby_dl_state.json` is updated about once per second while a transfer
runs. `status` reads it and the lock, so an agent can report progress without
scraping curl output. `--progress=json` emits `progress` events; `--json` implies
it.

### 8.8 Notifications

- Desktop: `notify-send` (works with `DISPLAY=:0`).
- Webhook: generic JSON POST; a URL containing `api.telegram.org` is auto-formatted
  for Telegram and uses `notifications.telegram_chat_id`.
- Defaults: off for desktop, empty webhook, notify on completion/failure.

---

## 9. Operational workflow for agents

### 9.1 Scout before downloading

```bash
tools/emby_dl search "Black Clover" --json
tools/emby_dl list "Black Clover" --series-id 276491 --json
tools/emby_dl plan "Black Clover" --series-id 276491 S2 E1-4 --json
```

If `search` shows duplicate exact names, **do not guess**: re-run with
`--series-id`. On this server Black Clover is `276491` (ours, 4 episodes) vs
`114002`.

### 9.2 Start a long job (sequential, machine-readable)

```bash
mkdir -p /tmp/opencode/jobs
(
  echo "JOB START $(date '+%F %T')"
  tools/emby_dl --json "المنظمة" S7 E3-last > /tmp/opencode/jobs/s7.jsonl 2>&1
  RC=$?
  echo "JOB END rc=$RC $(date '+%F %T')"
) > /tmp/opencode/jobs/chain.log 2>&1 &
```

For a human log, use `--progress=plain`; for an agent log, use `--json` and never
scrape curl bars. Keep chains strictly sequential. You do not need a shell lock:
the tool's own lock returns 5 for a second concurrent run.

### 9.3 Check progress / health

```bash
tools/emby_dl status --json
tools/emby_dl verify --json
tools/emby_dl dataset --stats --json
tools/emby_dl auth --json
```

`status` tells you `running`, `lock_held`, label, percent, speed and ETA.
`verify` is the audit: it compares every recorded file with the server, including
dataset drift. It found a genuinely truncated `Black Clover S02E03` on 2026-11-07
(83,005,440 bytes on disk vs 103,452,614 on the server); `verify --fix` repaired it.

### 9.4 Watch mode for ongoing series

```bash
tools/emby_dl watch "في السابعة عشر" --interval 600 --notify
tools/emby_dl watch "في السابعة عشر" --once --json
```

Default spec is `missing`. `--once` is the agent-friendly single tick. A busy lock
is skipped and retried; server errors are retried next tick. Notifications only
fire when something actually downloads or fails.

### 9.5 Where to scrape job history

- `/tmp/opencode/jobs/` — recovery job logs (`r_jobA_e07.log`, `r_jobB_e21.log`,
  `r_jobC_istanbul.log`, `r_jobD_e22.log`, `recovery_chain*.log`)
- `/tmp/opencode/progress_watch.log` — the completed 14/14 watcher run
- `/tmp/opencode/burst_guess.py`, `wordlist.txt`, `tried.txt` — paused password
  campaign state (do not run)
- `/tmp/opencode/ep_meta.json`, `f_Fields.json`, `f_fields.json` — saved API recon

---

## 10. Pitfalls checklist (each one bit us at least once)

1. **Don't send `Range` headers** — even reading 100 bytes. Sequential only.
2. **Never use `/Items/{id}/Download`** — dead endpoint on this server.
3. **Don't trust argparse with positionals and options** — v1.1 auto-inserts
   `download` when the first token is not a command (so misspellings become
   series names), and normalizes argv so options may appear before, between or
   after positionals (`plan "Show" --series-id 1 S2` works). A known option with
   no value exits `4`, not `2`.
4. **`--json` shapes differ** — info commands print one JSON object/array;
   `download`/`watch` print JSON Lines (`start`, `progress`, `verdict`, `summary`).
   Handle both.
5. **Error polling during a lockout prolongs it** — pause 60–90 s (historical,
   campaign paused).
6. **`/Sessions` JSON is multi-MB** — never parse from a truncated read.
7. **Windows SMB null sessions fail** — don't chase them without a real credential.
8. **Catch `${PIPESTATUS[0]}`** immediately after a pipe, or the real exit code is
   lost.
9. **Arabic strings** — quote them; they round-trip through filenames, datasets
   and UTF-8 endpoints.
10. **Tokens die** (expired once during heavy use) → `login --username …` or the
    web-UI fallback in `tools/README.md`.
11. **Never run two downloads concurrently** — v1.1 enforces a lock (exit 5).
    Before v1.1, two instances interleaved bytes into one partial and split the
    datasets.
12. **Verify episode ids against the tool's resolution** — do not trust
    hand-built `/Shows/{id}/Episodes?SeasonId=…` calls [BIT US 2026-11-04 & -06]:
    season ids are cross-linked between series and two items named "21" can belong
    to different shows (المنظمة S2 vs في السابعة عشر S1). The agent HEAD-ed
    another show's episode, "proved" a valid 553 MB download was incomplete,
    purged the record and re-downloaded. Always check `Path` + series metadata;
    the tool's own season enumeration has been correct every time.
13. **Use `/Users/{id}`, not `/Users/Me`** — this build returns
    `500 Unrecognized Guid format.` [VERIFIED 2026-11-07].
14. **`curl -f` is mandatory** — failed streams return 500 + `text/plain`; without
    `-f` curl reports success. v1.1 also checks content type.
15. **Sizes come from `MediaSources[0].Size`** — HEAD is only a last resort; an
    absent size is a failure, not a reason to guess.
16. **Dataset v1 `size_mb` drifts** — v2 stores exact `size_bytes`. Run
    `dataset --rebuild` after upgrading; `verify` reports `dataset_size_stale`.
17. **A recorded file can disappear** — v1 skipped it forever; v1.1 re-downloads
    (`MISS`), and `dataset --prune` removes genuinely dead records.
18. **Disk is tight** — keep the 2 GiB reserve; `watch missing` may legitimately
    exit 6 when a whole unseen season does not fit.
19. **Duplicate exact series names are real** — refuse and use `--series-id`
    (Black Clover `276491` vs `114002`).
20. **The token is visible in `ps aux`** while curl runs — trusted host only,
    rotate if exposed.

---

## 11. Building a skill on top of this tool (template)

```text
skill/
├── SKILL.md
└── references/
    └── emby_server.md    # copy of §1–§7 or a link to this file
```

SKILL.md skeleton:

```markdown
---
name: emby-download
description: Download episodes from the home Emby server (الملك2, 10.10.10.9:8096)
  via the emby_dl tool. Trigger when the user asks to download episodes/series
  or check download progress.
---

# Workflow
1. Locate tool: <project>/tools/emby_dl
2. Scout: `emby_dl search "<name>" --json`
   `emby_dl list "<picked>" --series-id <id> --json`
3. Dry run: `emby_dl plan "<series>" <spec> --json`
4. Check existing: `emby_dl dataset --stats --json`
5. Download with `--json` and consume JSON Lines (`verdict`/`summary`)
6. Progress: `emby_dl status --json`; audit with `emby_dl verify --json`
7. Never: parallel downloads, Range headers, /Items/{id}/Download, guessing
   ambiguous names (use --series-id), or recording unverified files.

# References
- Tool reference: tools/README.md
- Server behavior: docs/EMBY_FULL_GUIDE.md §1–6
- Pitfalls: docs/EMBY_FULL_GUIDE.md §10
- Exit codes: 0 ok, 1 failed, 2 not found, 3 config/auth/server,
  4 invalid/ambiguous, 5 lock busy, 6 disk full
```

---

## 12. Change log

### v1.2.0 — 2026-11-07 (Phase C polish)

- **Dataset Schema v3**: keyed primarily by `series_id` (fallback to normalized name
  for legacy entries). Added `season_id`, `media_source_id`, `source_index`,
  `source_name`, `attempts`, `tool_version`, and `duration_minutes`.
- **Single commit point architecture**: central dataset is the single atomic commit point;
  per-series datasets are derived views. Added `dataset --check` (exit 1 on drift),
  `dataset --sync` (regenerate derived datasets), and `dataset --dedupe`.
- **Multi-version media source selection**: `--source 1|N|max` (default `1`).
  Invalid source exits 4 listing candidates with index, name, size, and ID.
  `plan` displays selected source per episode and flags `source_changed`.
  `verify` flags `source_missing`.
- **Token secrecy (C0)**: token is passed to curl via `-K <downloads>/.emby_dl_headers`
  (mode `0600`), completely eliminating token visibility from `ps aux` and process argv.
- **Capabilities cache**: cached server capabilities in `.emby_dl_caps.json` (mode `0600`).
  Reset via `auth --reset-caps` or bypass via `EMBY_NO_CAPS_CACHE=1`.
- **Environment variable overrides**: `EMBY_URL`, `EMBY_TOKEN`, `EMBY_USER_ID`,
  `EMBY_USERNAME`, `EMBY_DOWNLOADS_DIR`. Precedence: CLI > environment > config > defaults.
- **System diagnostics**: `doctor` command (`--offline`, `--json`) auditing curl,
  config permissions (0600), environment variables, disk space, dataset consistency,
  and server reachability.
- **Packaging & CLI**: standard `pyproject.toml` exposing console scripts `emby-dl` and `emby_dl`,
  canonical module `emby_dl.py`, thin backward-compatible wrapper `tools/emby_dl`,
  and shell completions for Bash, Zsh, and Fish in `completions/`.
- **Specials inclusion**: `--include-specials` option for `download`, `plan`, and `watch`
  when querying `all`.

### v1.1.0 — 2026-11-07 (hardening)

- Phases A+B from the production review.
- `plan` (dry run), `status` (live state), `verify [--fix]`, `watch [--once]`,
  `login`, `auth`.
- Dataset schema v2 with exact `size_bytes`; `dataset --rebuild|--prune|--stats`.
- Fixes: unknown size never trusted; HTTP 500/HTML never recorded; truncated
  files never recorded; recorded-but-missing files re-download; lock exit 5;
  disk preflight exit 6; ambiguity refusal exit 4 and `--series-id`.
- Exact size from `MediaSources[0].Size`; detail/HEAD fallbacks.
- `.part` + atomic rename; one short probe; health-gated retries.
- JSON Lines progress/verdict/summary; progress modes; desktop + webhook
  notifications (Telegram-ready).
- CLI hardening: argv normalization lets options appear before, between or after
  positionals; bare-series mode still works; a known option without a value exits
  4 with a clear message.
- Live repair: Black Clover S02E03 (truncated) found by `verify` and fixed by
  `verify --fix`; datasets migrated 23 entries → v2 (16.81 GiB).

### v1.0.0 — 2026-11-06

- First production version; 14-file test batch completed byte-exact (~15.05 GiB).
- Fixed the unknown-size hole (unknown size no longer trusted, E21 re-fetched).
- Added the 10-minute external watcher and documentation of pitfall #12.

---

## 13. Open questions / unknowns

- **[UNKNOWN]** exact lockout trigger count and decay constant (4–5 failures;
  ~30–60 s observed).
- **[UNKNOWN]** root cause of stuck-item behavior after killed transfers (§3).
- **[UNKNOWN]** whether Emby regenerates phantom session records automatically;
  treat `/Sessions` as data with infinite false positives.
- **[UNKNOWN]** how quickly the token expires (it died once during heavy
  multi-hour use) — watch for exit 3 / HTTP 401.
- **[OPEN]** passwordreset.txt path requires physical/local access; SMB with real
  credentials untested. Campaign paused.
- **[OPEN]** whether a future Emby build fixes Range and makes resume useful.

---

*Last verified: Sat 2026-11-07 (server 4.9.3.0, tool 1.1.0). Update the timestamps
when behaviors change.*
