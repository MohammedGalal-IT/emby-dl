---
name: emby-dl
description: Expert operational guide and knowledge base for using and maintaining emby-dl, the resilient Emby CLI downloader and dataset manager.
triggers:
  - emby
  - emby-dl
  - emby_dl
  - download series
  - emby media
  - emby server
  - الملك2
---

# `emby-dl` Skill — Agent Operational Manual & Server Knowledge Base

This skill equips any AI coding agent (OpenCode, Claude Code, Cursor, Windsurf, etc.) with the complete operational rules, execution patterns, and institutional knowledge needed to reliably interact with Emby media servers (specifically the home server **الملك2** and standard Emby 4.x servers).

---

## 1. Quick Installation & Verification

### Linux / macOS
```bash
# Recommended via pipx
pipx install .

# Or standard pip / editable
pip install -e .

# Or direct invocation via Python or the thin wrapper
./tools/emby_dl --version
python3 emby_dl.py --version
```

### Windows (PowerShell / Command Prompt)
```powershell
# Install into Python environment
pip install .

# Or run directly
python emby_dl.py --version
```

### Pre-flight Health Check
Always verify the environment and credentials before running download batches:
```bash
emby-dl doctor --json
```
If `ok: true`, everything (curl, permissions, disk space, config, dataset, and server connectivity) is verified.

---

## 2. Core Agent Invariants (Non-Negotiable Rules)

1. **Machine-Wide Single Stream:**
   - **Never run parallel downloads.** An exclusive machine-wide file lock (`.emby_dl.lock`) guarantees only one download runs at a time.
   - If another process holds the lock, the tool immediately exits with **code 5**. Do not hammer; inspect with `emby-dl status`.

2. **Always Run `plan --json` Before `download`:**
   - Never initiate an unverified download batch. Run `emby-dl plan <series> <spec> --json` to inspect:
     - `summary.todo`: count of episodes needing transfer.
     - `summary.todo_bytes`: exact network transfer required.
     - `summary.unknown_sizes`: items where server could not confirm size.
     - `items[].status`: `skip` (already verified complete), `todo` (needs download), `heal` (file exists, dataset updated).

3. **Always Use `--json` in Automated Sessions:**
   - All commands support `--json`. In download/watch operations, this streams real-time JSON Lines events (`start`, `attempt`, `progress`, `verdict`, `summary`) directly to stdout for clean parsing.

4. **Always Specify `--series-id` When Prompted:**
   - Some media libraries have multiple series with identical or similar names (e.g., *Black Clover* has ID `276491` and duplicate `114002`).
   - The tool strictly refuses ambiguous queries and exits with **code 4**, listing candidates. Always supply `--series-id <id>` when targeting a specific show.

5. **Never Use Raw `curl` or `Range:` Requests Directly:**
   - Emby servers (notably 4.9.x on Windows) hang indefinitely on HTTP `Range:` headers. `emby-dl` contains specialized probe-and-restart logic designed specifically for this bug.

---

## 3. Command Reference & Spec Grammar

### Common Commands
```bash
# First-time setup (interactive or non-interactive)
emby-dl init --server-url "http://10.10.10.9:8096" --username "user" --password "secret"

# Check server reachability and token validity
emby-dl auth

# System diagnostics (skip live network with --offline)
emby-dl doctor [--offline] [--json]

# Dry run / pre-flight plan
emby-dl plan "Series Name" [spec] [--series-id ID] [--source 1|N|max] [--json]

# Download episodes
emby-dl download "Series Name" [spec] [--series-id ID] [--source 1|N|max] [--json]

# Real-time transfer status of running download
emby-dl status [--json]

# Verify integrity of downloaded files against server
emby-dl verify ["Series Name"] [--fix] [--json]

# Watch for new/missing episodes and download periodically
emby-dl watch "Series Name" [spec] [--interval 600] [--once] [--json]

# Dataset inspection & maintenance
emby-dl dataset --stats
emby-dl dataset --check    # Audit disk vs dataset (exits 1 on issues)
emby-dl dataset --sync     # Regenerate per-series datasets from central
emby-dl dataset --dedupe   # Remove duplicate records
emby-dl dataset --rebuild  # Rebuild from scratch by scanning disk
emby-dl dataset --prune    # Remove records whose files were deleted
```

### Spec Grammar
| Spec | Behavior |
|---|---|
| `<series>` | Default: downloads first episode of first real season (Season ≥ 1). |
| `<series> all` | Every episode across all real seasons (add `--include-specials` for S0). |
| `<series> last` | The latest episode of the latest season. |
| `<series> missing` | All episodes from real seasons not yet complete on disk. |
| `<series> new` | All episodes numbered higher than the current highest recorded. |
| `<series> S2` | Entire season 2. |
| `<series> S2 E5` | Season 2, episode 5. |
| `<series> S2 E1-10` | Season 2, episodes 1 through 10 (inclusive). |
| `<series> S2 E5-last`| Season 2, from episode 5 through the end of the season. |

### Multi-Version Source Selection (`--source`)
- `--source 1`: Primary / default stream (default).
- `--source <N>`: 1-based index of available media source version.
- `--source max`: Version with the largest file size (highest bitrate).
- If source version is changed on an already-downloaded episode, `plan` marks it `source_changed` (`SRC`) and retrieves the new version.

---

## 4. Exit Codes & Error Handling Playbook

| Code | Category | Meaning | Recovery Action |
|:---:|---|---|---|
| **0** | Success | All requested operations succeeded. | Proceed to next task. |
| **1** | Failed | Download failed, verification error, or `doctor` found issues. | Inspect stderr / JSON events for specific reason (e.g. `size_mismatch`, `http_500`). Run `emby-dl doctor` or `emby-dl verify --fix`. |
| **2** | Not Found | Series name does not match any items on server. | Run `emby-dl search <query>` to find correct name or ID. |
| **3** | Server/Auth | Server unreachable, token invalid, or config unreadable. | Check network connectivity. Run `emby-dl login` to refresh token. |
| **4** | Usage Error | Ambiguous series, invalid spec range, or invalid `--source`. | Check error candidate list; supply `--series-id <id>` or valid `--source`. |
| **5** | Lock Busy | Another download is actively executing. | Check `emby-dl status`. Wait for the transfer to finish; do not kill the lock file unless the process is dead. |
| **6** | Disk Full | Free disk space is lower than required transfer + reserve. | Free disk space or reduce download selection. |

---

## 5. Institutional Memory — Emby Server Quirks

These facts are verified against Emby Server 4.9.x (host: **الملك2** `http://10.10.10.9:8096`):

1. **Broken Range Requests (HTTP 206 / Resume Hang):**
   - The server advertises `Accept-Ranges: bytes`, but requests containing `Range: bytes=...` stall indefinitely with zero bytes sent.
   - **How `emby-dl` handles this:** Performs one short resume probe (default 10s timeout). If the probe fails to progress, it purges `.part` and downloads fresh. Never try manual Range resume.

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

5. **Token Secrecy via `curl -K`:**
   - Tokens are passed via a temporary private header file (`.emby_dl_headers`, mode `0600`) passed to curl with `-K`.
   - The token never appears in `ps aux`, `/proc/<pid>/cmdline`, or shell logs.

6. **Known Series IDs on "الملك2":**
   - **في السابعة عشر:** Series ID `267519` (Season 1 ID `267520`)
   - **المنظمة:** Series ID `145073`
   - **اسطنبول راساً على عقب:** Series ID `267954`
   - **Black Clover:** Primary Series ID `276491` (Duplicate ID: `114002`)
