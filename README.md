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
- **Multi-Version Source Selection:** Choose versions via `--source 1`, `--source 2`, or `--source max` (highest bitrate / largest file).
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

### 2. Verify System Health

Run the diagnostic doctor to confirm all systems are ready:

```bash
emby-dl doctor
```

### 3. Dry-Run Planning (`plan`)

Always check what will be downloaded before initiating transfers:

```bash
emby-dl plan "Black Clover" S1 E1-5
```

### 4. Download Episodes

```bash
# Download by series name and spec
emby-dl "Black Clover" S1 E1

# Explicit subcommand
emby-dl download "Black Clover" S2 all

# Select specific media version (e.g. 4K / highest bitrate)
emby-dl "Black Clover" S2 E1 --source max
```

---

## Command Reference

| Command | Description |
|---|---|
| `init` | Create default configuration and downloads folder (interactive or scripted). |
| `download` | Download selected episodes (default when first argument is a series name). |
| `plan` | Dry-run resolution of episodes, sizes, skips, and source versions. |
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
