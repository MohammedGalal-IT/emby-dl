#!/usr/bin/env python3
"""Stdlib-only end-to-end tests for tools/emby_dl against a fake Emby server.

Run with:  python3 -m unittest discover -s tests -v
No pytest required; the tool is exercised as a subprocess with real curl.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

TOOL = Path(__file__).resolve().parent.parent / "tools" / "emby_dl"


def pattern_bytes(size: int, seed: int = 0) -> bytes:
    base = bytes((index + seed) % 256 for index in range(256))
    return (base * (size // 256 + 1))[:size]


def make_episode(item_id: str, number: int, name: str, size: int,
                 container: str = "mkv", unknown: bool = False) -> dict:
    source = {
        "Container": container,
        "Path": f"/media/{item_id}.{container}",
    }
    if not unknown:
        source["Size"] = size
    return {
        "Id": item_id,
        "IndexNumber": number,
        "Name": name,
        "Type": "Episode",
        "Path": f"/media/{item_id}.{container}",
        "MediaSources": [source],
    }


def make_multi_source_episode(item_id: str, number: int, name: str,
                              sources: list) -> dict:
    """Episode with several media sources: [(Id, Name, Size, Container), ...]."""
    media_sources = []
    for source_id, source_name, size, container in sources:
        media_sources.append({
            "Id": source_id,
            "Name": source_name,
            "Size": size,
            "Container": container,
            "Path": f"/media/{source_id}.{container}",
        })
    first = sources[0]
    return {
        "Id": item_id,
        "IndexNumber": number,
        "Name": name,
        "Type": "Episode",
        "Path": f"/media/{first[0]}.{first[3]}",
        "MediaSources": media_sources,
    }


def make_state() -> dict:
    sizes = {"e11": 60_000, "e12": 50_000, "e21": 40_000, "e22": 30_000}
    return {
        "series": [
            {"Id": "s1", "Name": "Alpha Show", "Type": "Series",
             "ProductionYear": 2020},
        ],
        "seasons": {
            "s1": [
                {"Id": "se1", "IndexNumber": 1, "Name": "Season 1"},
                {"Id": "se2", "IndexNumber": 2, "Name": "Season 2"},
            ],
        },
        "episodes": {
            "se1": [
                make_episode("e11", 1, "Pilot", sizes["e11"]),
                make_episode("e12", 2, "Second", sizes["e12"]),
            ],
            "se2": [
                make_episode("e21", 1, "S2 One", sizes["e21"]),
                make_episode("e22", 2, "S2 Two", sizes["e22"]),
            ],
        },
        "payloads": {
            item_id: pattern_bytes(size, index)
            for index, (item_id, size) in enumerate(sizes.items())
        },
        "source_payloads": {},
        "multi_sources": {
            # e13 has three versions: a small 480p, the default 1080p (70k),
            # and the largest 4K remux (90k).
            "e13": [
                ("ms13a", "480p", 10_000, "mp4"),
                ("ms13b", "1080p", 70_000, "mkv"),
                ("ms13c", "4K", 90_000, "mkv"),
            ],
        },
        "users": {"user1": {"Id": "user1", "Name": "tester",
                            "ServerId": "srv"}},
        "http500": set(),
        "truncate": set(),
        "unknown": set(),
        "stall_once": set(),
        "stalled": set(),
        "range_hang": set(),
        "stream_requests": [],
        "range_headers": [],
        "webhooks": [],
    }


class FakeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args, **kwargs):
        pass

    # -- helpers -----------------------------------------------------------

    def _json(self, payload, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _item(self, item_id: str):
        state = self.server.state
        for series in state["series"]:
            if series["Id"] == item_id:
                return series
        for episodes in state["episodes"].values():
            for episode in episodes:
                if episode["Id"] == item_id:
                    clone = json.loads(json.dumps(episode))
                    if item_id in state["unknown"]:
                        for source in clone.get("MediaSources", []):
                            source.pop("Size", None)
                    return clone
        return None

    # -- HTTP methods ------------------------------------------------------

    def do_GET(self):
        state = self.server.state
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/System/Info/Public":
            self._json({"ServerName": "FakeKing", "Version": "4.9.3.0",
                        "Id": "srv"})
            return
        if re.match(r"^/Users/[^/]+$", path):
            user_id = path.rsplit("/", 1)[-1]
            user = state["users"].get(user_id)
            if user:
                self._json(user)
            else:
                self._text(404, "no user")
            return
        if path == "/Items":
            if "Ids" in query:
                item = self._item(query["Ids"][0])
                self._json({"Items": [item] if item else []})
                return
            term = (query.get("SearchTerm") or [""])[0].casefold()
            results = [series for series in state["series"]
                       if term in str(series.get("Name", "")).casefold()]
            self._json({"Items": results})
            return
        match = re.match(r"^/Shows/([^/]+)/Seasons$", path)
        if match:
            self._json({"Items": state["seasons"].get(match.group(1), [])})
            return
        match = re.match(r"^/Shows/([^/]+)/Episodes$", path)
        if match:
            season_id = (query.get("seasonId") or [""])[0]
            episodes = []
            for episode in state["episodes"].get(season_id, []):
                clone = json.loads(json.dumps(episode))
                if clone["Id"] in state["unknown"]:
                    for source in clone.get("MediaSources", []):
                        source.pop("Size", None)
                episodes.append(clone)
            self._json({"Items": episodes})
            return
        match = re.match(r"^/Videos/([^/]+)/stream$", path)
        if match:
            media_source_id = (query.get("MediaSourceId") or [""])[0]
            self._stream(match.group(1), media_source_id)
            return
        self._text(404, "not found")

    def do_HEAD(self):
        state = self.server.state
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        match = re.match(r"^/Videos/([^/]+)/stream$", parsed.path)
        if not match:
            self._text(404, "not found")
            return
        item_id = match.group(1)
        media_source_id = (query.get("MediaSourceId") or [""])[0]
        payload = self._payload_for(item_id, media_source_id)
        if item_id in state["unknown"] or payload is None:
            self.send_response(200)
            self.send_header("Content-Type", "video/x-matroska")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/x-matroska")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()

    def do_POST(self):
        state = self.server.state
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if parsed.path == "/Users/AuthenticateByName":
            data = json.loads(body.decode("utf-8") or "{}")
            if data.get("Pw") != "hunter2":
                self._text(401, "bad password")
                return
            self._json({
                "AccessToken": "newtoken",
                "ServerId": "srv",
                "User": {"Id": "user1", "Name": data.get("Username", "tester"),
                         "ServerId": "srv"},
            })
            return
        if parsed.path == "/webhook":
            state["webhooks"].append(body.decode("utf-8"))
            self._json({"ok": True})
            return
        self._text(404, "not found")

    def _payload_for(self, item_id: str, media_source_id: str):
        """Payload for an item, honouring a MediaSourceId selector."""
        state = self.server.state
        if media_source_id:
            payload = state["source_payloads"].get(media_source_id)
            if payload is None:
                return None
            return payload
        payload = state["payloads"].get(item_id)
        if payload is not None:
            return payload
        # Multi-source items default to their first-listed payload.
        sources = state.get("multi_sources", {}).get(item_id) or []
        if sources:
            return state["source_payloads"].get(sources[0][0])
        return None

    def _stream(self, item_id: str, media_source_id: str = "") -> None:
        state = self.server.state
        state["stream_requests"].append((item_id, media_source_id))
        range_header = self.headers.get("Range")
        if range_header:
            state["range_headers"].append(range_header)
        payload = self._payload_for(item_id, media_source_id)
        if payload is None:
            self._text(404, "no such source")
            return

        if item_id in state["http500"]:
            self._text(500, "server exploded")
            return
        if item_id in state["range_hang"] and range_header:
            self.send_response(206)
            self.send_header("Content-Type", "video/x-matroska")
            self.send_header("Content-Range",
                             f"bytes 1000-{len(payload) - 1}/{len(payload)}")
            self.send_header("Content-Length", str(max(0, len(payload) - 1000)))
            self.end_headers()
            self.wfile.flush()
            time.sleep(5)
            return
        if item_id in state["stall_once"] and item_id not in state["stalled"]:
            state["stalled"].add(item_id)
            self.send_response(200)
            self.send_header("Content-Type", "video/x-matroska")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload[:1000])
            self.wfile.flush()
            time.sleep(5)
            return
        if item_id in state["truncate"]:
            self.send_response(200)
            self.send_header("Content-Type", "video/x-matroska")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload[: len(payload) // 2])
            self.wfile.flush()
            self.close_connection = True
            return
        if item_id in state["unknown"]:
            self.send_response(200)
            self.send_header("Content-Type", "video/x-matroska")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
            self.close_connection = True
            return

        self.send_response(200)
        self.send_header("Content-Type", "video/x-matroska")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class FakeServer:
    def __init__(self):
        self.state = make_state()
        self.httpd = QuietServer(("127.0.0.1", 0), FakeHandler)
        self.httpd.daemon_threads = True
        self.httpd.state = self.state
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class EmbyToolTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="emby_dl_test_"))
        self.downloads = self.tempdir / "downloads"
        self.downloads.mkdir()
        self.server = FakeServer()
        self.state = self.server.state
        self.config_path = self.tempdir / "config.json"
        self.write_config()

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tempdir, ignore_errors=True)

    # -- helpers -----------------------------------------------------------

    def write_config(self, **overrides):
        config = {
            "server_url": self.server.url,
            "server_id": "srv",
            "user_id": "user1",
            "access_token": "token",
            "username": "tester",
            "downloads_path": str(self.downloads),
            "download_settings": {
                "speed_limit_bytes": 5000,
                "speed_time_seconds": 1,
                "max_retries": 2,
                "retry_delay_seconds": 0,
                "retry_backoff_initial_seconds": 0,
                "retry_backoff_max_seconds": 0,
                "resume_enabled": True,
                "resume_stall_seconds": 1,
                "disk_reserve_bytes": 0,
                "api_timeout_seconds": 5,
                "api_retries": 0,
                "head_timeout_seconds": 2,
            },
            "notifications": {
                "desktop": False,
                "webhook_url": "",
                "telegram_chat_id": "",
                "notify_on_start": False,
                "notify_on_complete": True,
                "notify_on_failure": True,
            },
        }
        config = deep_merge(config, overrides)
        self.config_path.write_text(json.dumps(config, ensure_ascii=False),
                                    encoding="utf-8")
        os.chmod(self.config_path, 0o600)
        return config

    def run_tool(self, *args, env=None):
        command = [sys.executable, str(TOOL), "--config", str(self.config_path), *args]
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=120, env=full_env)

    @staticmethod
    def last_json(output: str):
        for line in reversed(output.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        raise AssertionError(f"no JSON in output:\n{output}")

    def central(self):
        path = self.downloads / "downloaded_dataset.json"
        if not path.exists():
            return {"version": 2, "downloads": [], "total_episodes": 0}
        return json.loads(path.read_text(encoding="utf-8"))

    def stream_count(self, item_id: str, media_source_id: str = "") -> int:
        return sum(1 for request in self.state["stream_requests"]
                   if request[0] == item_id
                   and (not media_source_id or request[1] == media_source_id))

    def entry(self, season: int, episode: int, series: str = "Alpha Show"):
        for item in self.central().get("downloads", []):
            if (item.get("series_name") == series
                    and int(item.get("season_number") or 0) == season
                    and int(item.get("episode_number") or 0) == episode):
                return item
        return None

    def dest(self, season: int, episode: int, container: str = "mkv") -> Path:
        return (self.downloads / "Alpha Show" / f"Season {season}"
                / f"Alpha Show - S{season:02d}E{episode:02d}.{container}")

    # -- tests -------------------------------------------------------------

    def test_fresh_download_bare_mode_and_dataset_v2(self):
        result = self.run_tool("Alpha Show", "S1", "E1")
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.dest(1, 1)
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), self.state["payloads"]["e11"])
        self.assertFalse(Path(str(path) + ".part").exists())
        central = self.central()
        self.assertEqual(central["version"], 3)
        entry = self.entry(1, 1)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["size_bytes"], 60_000)
        self.assertEqual(entry["episode_id"], "e11")
        self.assertEqual(entry["series_id"], "s1")
        self.assertEqual(entry["relative_path"],
                         "Alpha Show/Season 1/Alpha Show - S01E01.mkv")
        self.assertEqual(entry["tool_version"], "1.3.0")
        self.assertGreaterEqual(int(entry["attempts"]), 1)
        series_dataset = json.loads(
            (self.downloads / "Alpha Show" / "downloaded_dataset.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(series_dataset["version"], 3)
        self.assertEqual(series_dataset["series_id"], "s1")
        self.assertEqual(series_dataset["total_episodes"], 1)

        # --json before the bare series must be hoisted and remain JSONL.
        result = self.run_tool("--json", "Alpha Show", "S1", "E2")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = self.last_json(result.stdout)
        self.assertEqual(summary["event"], "summary")
        self.assertEqual(summary["downloaded"], 1)
        self.assertTrue(self.dest(1, 2).exists())

    def test_second_run_skips_without_touching_file(self):
        first = self.run_tool("Alpha Show", "S1", "E1")
        self.assertEqual(first.returncode, 0, first.stderr)
        path = self.dest(1, 1)
        before = path.stat().st_mtime_ns
        second = self.run_tool("Alpha Show", "S1", "E1")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("SKIP", second.stdout)
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.assertEqual(self.stream_count("e11"), 1)

    def test_http_500_fails_and_records_nothing(self):
        self.state["http500"].add("e11")
        result = self.run_tool("--json", "Alpha Show", "S1", "E1")
        self.assertEqual(result.returncode, 1)
        summary = self.last_json(result.stdout)
        self.assertEqual(summary["failed"], 1)
        self.assertFalse(self.dest(1, 1).exists())
        self.assertFalse(Path(str(self.dest(1, 1)) + ".part").exists())
        self.assertIsNone(self.entry(1, 1))

    def test_truncated_200_fails(self):
        self.state["truncate"].add("e11")
        result = self.run_tool("--json", "Alpha Show", "S1", "E1")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.dest(1, 1).exists())
        self.assertIsNone(self.entry(1, 1))

    def test_unknown_size_is_never_trusted(self):
        self.state["unknown"].add("e11")
        result = self.run_tool("--json", "Alpha Show", "S1", "E1")
        self.assertEqual(result.returncode, 1)
        summary = self.last_json(result.stdout)
        self.assertEqual(summary["failed"], 1)
        self.assertIn("size_unknown", summary["failures"][0]["reason"])
        self.assertFalse(self.dest(1, 1).exists())
        self.assertIsNone(self.entry(1, 1))
        self.assertEqual(self.stream_count("e11"), 0)

    def test_stall_then_retry_succeeds(self):
        self.state["stall_once"].add("e11")
        result = self.run_tool("--json", "Alpha Show", "S1", "E1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.dest(1, 1).read_bytes(), self.state["payloads"]["e11"])
        self.assertGreaterEqual(self.stream_count("e11"), 2)
        self.assertEqual(self.entry(1, 1)["size_bytes"], 60_000)

    def test_range_hang_probe_restarts_fresh(self):
        self.state["range_hang"].add("e11")
        part = Path(str(self.dest(1, 1)) + ".part")
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(self.state["payloads"]["e11"][:1000])
        result = self.run_tool("--json", "Alpha Show", "S1", "E1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.dest(1, 1).read_bytes(), self.state["payloads"]["e11"])
        self.assertTrue(self.state["range_headers"])
        self.assertGreaterEqual(self.stream_count("e11"), 2)

    def test_recorded_but_missing_file_is_redownloaded(self):
        first = self.run_tool("Alpha Show", "S1", "E1")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.dest(1, 1).unlink()
        second = self.run_tool("Alpha Show", "S1", "E1")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertTrue(self.dest(1, 1).exists())
        self.assertEqual(self.stream_count("e11"), 2)

    def test_lock_busy_exit_5(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from emby_dl import DownloadLock
        with DownloadLock(str(self.downloads)):
            result = self.run_tool("--json", "Alpha Show", "S1", "E1")
        self.assertEqual(result.returncode, 5)
        payload = self.last_json(result.stdout)
        self.assertEqual(payload["code"], 5)
        self.assertFalse(self.dest(1, 1).exists())

    def test_ambiguous_name_exit_4_and_series_id_override(self):
        self.state["series"].append(
            {"Id": "s2", "Name": "Alpha Show", "Type": "Series",
             "ProductionYear": 2021})
        self.state["seasons"]["s2"] = [
            {"Id": "se9", "IndexNumber": 1, "Name": "Season 1"}]
        self.state["episodes"]["se9"] = [
            make_episode("e91", 1, "Other", 20_000)]
        self.state["payloads"]["e91"] = pattern_bytes(20_000, 9)

        result = self.run_tool("plan", "Alpha Show", "S1", "--json")
        self.assertEqual(result.returncode, 4)
        error = self.last_json(result.stdout)
        self.assertEqual(error["code"], 4)
        self.assertIn("Ambiguous", error["error"])
        self.assertIn("s1", error["error"])
        self.assertIn("s2", error["error"])

        result = self.run_tool("plan", "--series-id", "s1", "--json",
                               "Alpha Show", "S1")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.last_json(result.stdout)
        self.assertEqual(plan["series"]["id"], "s1")
        self.assertEqual(plan["summary"]["total"], 2)

    def test_options_between_positionals_are_normalized(self):
        # argparse would otherwise drop `S1` after `--series-id`; the CLI
        # reorders options ahead of positionals so both forms work.
        result = self.run_tool("plan", "Alpha Show", "--series-id", "s1",
                               "S1", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.last_json(result.stdout)
        self.assertEqual(plan["series"]["id"], "s1")
        self.assertEqual(plan["summary"]["total"], 2)

        result = self.run_tool("--series-id", "s1", "plan", "Alpha Show",
                               "S1", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.last_json(result.stdout)["summary"]["total"], 2)

    def test_missing_option_value_exits_4(self):
        result = self.run_tool("plan", "Alpha Show", "S2", "--series-id")
        self.assertEqual(result.returncode, 4)
        self.assertIn("requires a value", result.stderr)

        result = self.run_tool("plan", "Alpha Show", "--series-id", "--json", "S2")
        self.assertEqual(result.returncode, 4)
        payload = self.last_json(result.stdout)
        self.assertEqual(payload["code"], 4)
        self.assertIn("requires a value", payload["error"])

    def test_invalid_and_missing_specs(self):
        result = self.run_tool("plan", "Alpha Show", "bogus")
        self.assertEqual(result.returncode, 4)
        result = self.run_tool("plan", "Alpha Show", "S9")
        self.assertEqual(result.returncode, 2)
        result = self.run_tool("plan", "Alpha Show", "S1", "E4-2")
        self.assertEqual(result.returncode, 4)

    def test_plan_does_not_download(self):
        result = self.run_tool("plan", "Alpha Show", "S1", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.last_json(result.stdout)
        self.assertEqual(plan["summary"]["todo"], 2)
        self.assertEqual(plan["summary"]["skip"], 0)
        self.assertEqual(self.state["stream_requests"], [])
        self.assertFalse((self.downloads / "Alpha Show").exists())

    def test_missing_and_new_selections(self):
        first = self.run_tool("Alpha Show", "S1", "E1")
        self.assertEqual(first.returncode, 0, first.stderr)

        missing = self.run_tool("plan", "Alpha Show", "missing", "--json")
        self.assertEqual(missing.returncode, 0, missing.stderr)
        plan = self.last_json(missing.stdout)
        self.assertEqual(plan["summary"]["todo"], 3)
        self.assertNotIn((1, 1),
                         [(item["season"], item["episode"]) for item in plan["items"]])

        new = self.run_tool("plan", "Alpha Show", "new", "--json")
        self.assertEqual(new.returncode, 0, new.stderr)
        plan = self.last_json(new.stdout)
        self.assertEqual(
            [(item["season"], item["episode"]) for item in plan["items"]],
            [(1, 2), (2, 1), (2, 2)],
        )
        self.assertTrue(all(item["status"] == "todo" for item in plan["items"]))

    def test_verify_detects_and_fixes_truncated_file(self):
        first = self.run_tool("Alpha Show", "S1", "E1")
        self.assertEqual(first.returncode, 0, first.stderr)
        path = self.dest(1, 1)
        path.write_bytes(path.read_bytes()[:10_000])

        check = self.run_tool("verify", "--json")
        self.assertEqual(check.returncode, 1)
        report = self.last_json(check.stdout)
        self.assertEqual(report["summary"]["issues"], 1)
        self.assertEqual(report["items"][0]["issue"], "size_mismatch")

        repaired = self.run_tool("verify", "--fix", "--json")
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        report = self.last_json(repaired.stdout)
        self.assertEqual(report["summary"]["fixed"], 1)
        self.assertEqual(report["summary"]["remaining"], 0)
        self.assertEqual(path.read_bytes(), self.state["payloads"]["e11"])

    def test_dataset_rebuild_stats_and_prune(self):
        season_dir = self.downloads / "Alpha Show" / "Season 1"
        season_dir.mkdir(parents=True)
        (season_dir / "Alpha Show - S01E01.mkv").write_bytes(
            self.state["payloads"]["e11"])
        (season_dir / "notes.txt").write_text("ignore me", encoding="utf-8")

        rebuilt = self.run_tool("dataset", "--rebuild", "--json")
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        payload = self.last_json(rebuilt.stdout)
        self.assertEqual(payload["entries"], 1)
        self.assertEqual(len(payload["skipped_files"]), 1)
        entry = self.entry(1, 1)
        self.assertEqual(entry["size_bytes"], 60_000)
        self.assertEqual(self.central()["version"], 3)

        stats = self.run_tool("dataset", "--stats", "--json")
        self.assertEqual(stats.returncode, 0, stats.stderr)
        payload = self.last_json(stats.stdout)
        self.assertEqual(payload["total_episodes"], 1)
        self.assertEqual(payload["missing"], 0)

        (season_dir / "Alpha Show - S01E01.mkv").unlink()
        pruned = self.run_tool("dataset", "--prune", "--json")
        self.assertEqual(pruned.returncode, 0, pruned.stderr)
        self.assertEqual(len(self.last_json(pruned.stdout)["removed"]), 1)
        self.assertEqual(self.central()["total_episodes"], 0)

    def test_webhook_is_posted_on_completion(self):
        self.write_config(notifications={
            "webhook_url": self.server.url + "/webhook",
            "notify_on_complete": True,
        })
        result = self.run_tool("--json", "Alpha Show", "S1", "E1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.state["webhooks"]), 1)
        payload = json.loads(self.state["webhooks"][0])
        self.assertIn("Alpha Show", json.dumps(payload, ensure_ascii=False))

    def test_login_saves_token_and_auth_check(self):
        result = self.run_tool("login", "--username", "tester",
                               "--password", "hunter2", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("newtoken", result.stdout)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["access_token"], "newtoken")
        if os.name != "nt":
            self.assertEqual(oct(self.config_path.stat().st_mode & 0o777), "0o600")

        auth = self.run_tool("auth", "--json")
        self.assertEqual(auth.returncode, 0, auth.stderr)
        payload = self.last_json(auth.stdout)
        self.assertEqual(payload["server_name"], "FakeKing")
        self.assertEqual(payload["user_id"], "user1")

    def test_search_and_list(self):
        search = self.run_tool("search", "alpha", "--json")
        self.assertEqual(search.returncode, 0, search.stderr)
        payload = self.last_json(search.stdout)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["id"], "s1")

        listing = self.run_tool("list", "Alpha Show", "--json")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        payload = self.last_json(listing.stdout)
        self.assertEqual(payload["series_name"], "Alpha Show")
        self.assertEqual([season["episode_count"] for season in payload["seasons"]],
                         [2, 2])

    def test_watch_once_does_not_loop(self):
        result = self.run_tool("watch", "Alpha Show", "--once",
                               "--interval", "10", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = self.last_json(result.stdout)
        self.assertEqual(summary["event"], "summary")
        self.assertEqual(summary["downloaded"], 4)

    # -- Phase C: multi-source, v3 dataset, env, secrecy ----------------------

    def add_multi_source_episode(self):
        """Give season 1 a third episode with three media sources."""
        source_payloads = {
            "ms13a": pattern_bytes(10_000, 10),
            "ms13b": pattern_bytes(70_000, 11),
            "ms13c": pattern_bytes(90_000, 12),
        }
        self.state["source_payloads"].update(source_payloads)
        self.state["episodes"]["se1"].append(make_multi_source_episode(
            "e13", 3, "Multi", [
                ("ms13a", "480p", 10_000, "mp4"),
                ("ms13b", "1080p", 70_000, "mkv"),
                ("ms13c", "4K", 90_000, "mkv"),
            ]))

    def test_source_default_is_first_and_records_source_fields(self):
        self.add_multi_source_episode()
        result = self.run_tool("Alpha Show", "S1", "E3", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.entry(1, 3)
        self.assertEqual(entry["media_source_id"], "ms13a")
        self.assertEqual(entry["source_index"], 1)
        self.assertEqual(entry["source_name"], "480p")
        self.assertEqual(entry["size_bytes"], 10_000)
        path = self.dest(1, 3, "mp4")
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), self.state["source_payloads"]["ms13a"])
        self.assertEqual(self.stream_count("e13", "ms13a"), 1)

    def test_source_index_selects_nth_version(self):
        self.add_multi_source_episode()
        result = self.run_tool("Alpha Show", "S1", "E3", "--source", "2", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.entry(1, 3)
        self.assertEqual(entry["media_source_id"], "ms13b")
        self.assertEqual(entry["source_index"], 2)
        self.assertEqual(entry["size_bytes"], 70_000)
        self.assertEqual(self.dest(1, 3).read_bytes(),
                         self.state["source_payloads"]["ms13b"])

    def test_source_max_picks_largest_version(self):
        self.add_multi_source_episode()
        result = self.run_tool("Alpha Show", "S1", "E3", "--source", "max", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.entry(1, 3)
        self.assertEqual(entry["media_source_id"], "ms13c")
        self.assertEqual(entry["source_index"], 3)
        self.assertEqual(entry["size_bytes"], 90_000)
        self.assertEqual(self.dest(1, 3).read_bytes(),
                         self.state["source_payloads"]["ms13c"])

    def test_source_invalid_exits_4_and_lists_candidates(self):
        self.add_multi_source_episode()
        result = self.run_tool("Alpha Show", "S1", "E3", "--source", "9")
        self.assertEqual(result.returncode, 4)
        self.assertIn("source", result.stderr.lower())
        self.assertIn("ms13a", result.stderr)

    def test_source_change_marks_item_todo(self):
        self.add_multi_source_episode()
        first = self.run_tool("Alpha Show", "S1", "E3")
        self.assertEqual(first.returncode, 0, first.stderr)
        # A different source has a different size, so the plan re-downloads.
        plan = self.run_tool("plan", "Alpha Show", "S1", "E3",
                             "--source", "3", "--json")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        payload = self.last_json(plan.stdout)
        item = payload["items"][0]
        self.assertEqual(item["status"], "todo")
        self.assertTrue(item["source_changed"])

    def test_v2_dataset_migrates_to_v3_on_read(self):
        # Simulate a legacy v2 central dataset with one entry.
        season_dir = self.downloads / "Alpha Show" / "Season 1"
        season_dir.mkdir(parents=True)
        (season_dir / "Alpha Show - S01E01.mkv").write_bytes(
            self.state["payloads"]["e11"])
        legacy = {
            "version": 2,
            "downloads": [{
                "series_name": "Alpha Show",
                "season_number": 1,
                "episode_number": 1,
                "episode_id": "e11",
                "file_name": "Alpha Show - S01E01.mkv",
                "relative_path": "Alpha Show/Season 1/Alpha Show - S01E01.mkv",
                "size_bytes": 60_000,
                "size_mb": 58,
                "container": "mkv",
            }],
            "total_episodes": 1,
        }
        (self.downloads / "downloaded_dataset.json").write_text(
            json.dumps(legacy), encoding="utf-8")

        # A read-only command normalizes to v3 and flags only the missing
        # derived per-series file (a v2 layout never had one for this entry).
        result = self.run_tool("dataset", "--check", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = self.last_json(result.stdout)
        self.assertEqual(payload["version"], 3)
        self.assertEqual(payload["entries"], 1)
        self.assertFalse(payload["ok"])
        # The only issue is the missing derived per-series file for a v2 layout.
        kinds = {issue["kind"] for issue in payload["issues"]}
        self.assertEqual(kinds, {"stale_series_dataset"})

        # Sync regenerates the derived file and the check goes green.
        result = self.run_tool("dataset", "--sync", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_tool("dataset", "--check", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.last_json(result.stdout)
        self.assertEqual(payload["version"], 3)
        self.assertEqual(payload["entries"], 1)
        self.assertTrue(payload["ok"])

        # Downloading a neighbour entry persists v3 with the legacy entry kept.
        second = self.run_tool("Alpha Show", "S1", "E2", "--json")
        self.assertEqual(second.returncode, 0, second.stderr)
        central = self.central()
        self.assertEqual(central["version"], 3)
        migrated = self.entry(1, 1)
        self.assertEqual(migrated["size_bytes"], 60_000)
        self.assertEqual(migrated.get("series_id", ""), "")

    def test_dataset_check_sync_and_dedupe(self):
        first = self.run_tool("Alpha Show", "S1", "E1", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)

        # check: all green initially.
        check = self.run_tool("dataset", "--check", "--json")
        self.assertEqual(check.returncode, 0, check.stderr)
        report = self.last_json(check.stdout)
        self.assertTrue(report["ok"])

        # Corrupt the derived per-series file; check reports it, sync repairs it.
        series_file = self.downloads / "Alpha Show" / "downloaded_dataset.json"
        data = json.loads(series_file.read_text(encoding="utf-8"))
        data["downloads"] = []
        data["total_episodes"] = 0
        series_file.write_text(json.dumps(data), encoding="utf-8")
        check = self.run_tool("dataset", "--check", "--json")
        self.assertEqual(check.returncode, 1)
        report = self.last_json(check.stdout)
        self.assertFalse(report["ok"])
        kinds = {issue["kind"] for issue in report["issues"]}
        self.assertIn("stale_series_dataset", kinds)

        synced = self.run_tool("dataset", "--sync", "--json")
        self.assertEqual(synced.returncode, 0, synced.stderr)
        data = json.loads(series_file.read_text(encoding="utf-8"))
        self.assertEqual(data["total_episodes"], 1)
        check = self.run_tool("dataset", "--check", "--json")
        self.assertEqual(check.returncode, 0, check.stderr)

        # A duplicated central entry is deduped, keeping the on-disk record.
        central_file = self.downloads / "downloaded_dataset.json"
        central = json.loads(central_file.read_text(encoding="utf-8"))
        central["downloads"].append(dict(central["downloads"][0]))
        central["total_episodes"] = len(central["downloads"])
        central_file.write_text(json.dumps(central), encoding="utf-8")
        check = self.run_tool("dataset", "--check", "--json")
        self.assertEqual(check.returncode, 1)
        deduped = self.run_tool("dataset", "--dedupe", "--json")
        self.assertEqual(deduped.returncode, 0, deduped.stderr)
        self.assertEqual(len(self.last_json(deduped.stdout)["removed"]), 1)
        central = self.central()
        self.assertEqual(central["total_episodes"], 1)

    def test_env_overrides_beat_config(self):
        env = {"EMBY_TOKEN": "envtoken"}
        result = self.run_tool("auth", "--json", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        # The fake server accepts any token, so auth works; check config output
        # shows the env override in effect via _env_applied behaviour.
        config_view = self.run_tool("config", "--json", env=env)
        payload = self.last_json(config_view.stdout)
        # The saved file still holds the original token; the env one is masked.
        self.assertEqual(payload["config"]["access_token"], "***hidden***")

        # A wrong server URL from the env is honoured over the config file.
        env_bad = {"EMBY_URL": "http://127.0.0.1:1"}
        result = self.run_tool("auth", "--json", env=env_bad)
        self.assertEqual(result.returncode, 3)

    def test_token_never_appears_in_argv_or_headers_file_perms(self):
        result = self.run_tool("Alpha Show", "S1", "E1", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        header_file = self.downloads / ".emby_dl_headers"
        self.assertTrue(header_file.exists())
        if os.name != "nt":
            self.assertEqual(oct(header_file.stat().st_mode & 0o777), "0o600")
        self.assertIn("token", header_file.read_text(encoding="utf-8"))
        # The process is gone by now; the key property is that no argv ever
        # contains the token: the download completed using -K <file>.
        self.assertNotIn("token", result.stdout)

    def test_caps_cache_written_and_reset(self):
        cache = self.downloads / ".emby_dl_caps.json"
        first = self.run_tool("auth", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue(cache.exists())
        data = json.loads(cache.read_text(encoding="utf-8"))
        self.assertEqual(data["server_name"], "FakeKing")
        self.assertEqual(data["tool_version"], "1.3.0")
        original = data["updated_at"]

        # --reset-caps clears the cache; the same command re-probes and
        # rewrites it, so the payload reports the reset and updated_at moves.
        time.sleep(1.1)
        reset = self.run_tool("auth", "--reset-caps", "--json")
        self.assertEqual(reset.returncode, 0, reset.stderr)
        self.assertTrue(self.last_json(reset.stdout)["caps_cache_reset"])
        data = json.loads(cache.read_text(encoding="utf-8"))
        self.assertGreater(data["updated_at"], original)

    def test_doctor_reports_health(self):
        result = self.run_tool("doctor", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.last_json(result.stdout)
        self.assertTrue(payload["ok"])
        names = {check["name"] for check in payload["checks"]}
        self.assertIn("curl", names)
        self.assertIn("access_token", names)
        self.assertIn("dataset", names)
        self.assertIn("server_reachable", names)

    def test_doctor_offline_skips_server(self):
        result = self.run_tool("doctor", "--offline", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.last_json(result.stdout)
        names = {check["name"] for check in payload["checks"]}
        self.assertNotIn("server_reachable", names)

    def test_doctor_flags_missing_token(self):
        self.write_config(access_token="")
        result = self.run_tool("doctor", "--offline", "--json")
        self.assertEqual(result.returncode, 1)
        payload = self.last_json(result.stdout)
        self.assertFalse(payload["ok"])
        failed = {check["name"] for check in payload["checks"] if not check["ok"]}
        self.assertIn("access_token", failed)

    def test_include_specials_extends_all(self):
        # Season 0 with one special.
        self.state["seasons"]["s1"].insert(
            0, {"Id": "se0", "IndexNumber": 0, "Name": "Specials"})
        self.state["episodes"]["se0"] = [
            make_episode("e01", 1, "OVA", 20_000)]

        plain = self.run_tool("plan", "Alpha Show", "all", "--json")
        payload = self.last_json(plain.stdout)
        self.assertNotIn(0, [item["season"] for item in payload["items"]])

        with_specials = self.run_tool("plan", "Alpha Show", "all",
                                     "--include-specials", "--json")
        payload = self.last_json(with_specials.stdout)
        self.assertIn(0, [item["season"] for item in payload["items"]])

    def test_init_command_creates_config_and_downloads_dir(self):
        fresh_config = self.tempdir / "custom_config.json"
        fresh_downloads = self.tempdir / "custom_downloads"
        result = self.run_tool("init", "--config", str(fresh_config),
                               "--downloads-dir", str(fresh_downloads),
                               "--server-url", self.server.url,
                               "--username", "tester",
                               "--password", "hunter2",
                               "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(fresh_config.exists())
        self.assertTrue(fresh_downloads.exists())
        saved = json.loads(fresh_config.read_text(encoding="utf-8"))
        self.assertEqual(saved["server_url"], self.server.url)
        self.assertEqual(saved["access_token"], "newtoken")
        self.assertEqual(saved["username"], "tester")
        self.assertEqual(saved["downloads_path"], str(fresh_downloads))

    def test_welcome_message_when_no_config(self):
        empty_dir = self.tempdir / "empty_home"
        empty_dir.mkdir()
        cmd = [sys.executable, str(TOOL), "--config", str(empty_dir / "nonexistent.json")]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Welcome to emby_dl", res.stdout)
        self.assertIn("init", res.stdout)

    def test_queue_add_list_remove_clear(self):
        res = self.run_tool("queue", "add", "Alpha Show", "S1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Added to queue", res.stdout)

        res = self.run_tool("queue", "add", "Beta Show", "all")
        self.assertEqual(res.returncode, 0, res.stderr)

        list_res = self.run_tool("queue", "list", "--json")
        self.assertEqual(list_res.returncode, 0, list_res.stderr)
        items = json.loads(list_res.stdout)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["series"], "Alpha Show")
        self.assertEqual(items[0]["spec"], ["S1"])
        self.assertEqual(items[1]["series"], "Beta Show")

        rem_res = self.run_tool("queue", "remove", "1")
        self.assertEqual(rem_res.returncode, 0, rem_res.stderr)
        self.assertIn("Removed [1]", rem_res.stdout)

        clear_res = self.run_tool("queue", "clear")
        self.assertEqual(clear_res.returncode, 0, clear_res.stderr)
        empty_list = self.run_tool("queue", "list", "--json")
        self.assertEqual(json.loads(empty_list.stdout), [])

    def test_queue_plan_args_and_file(self):
        self.state["series"].append(
            {"Id": "s2", "Name": "Beta Show", "Type": "Series", "ProductionYear": 2021})
        self.state["seasons"]["s2"] = [
            {"Id": "se9", "IndexNumber": 1, "Name": "Season 1"}]
        self.state["episodes"]["se9"] = [
            make_episode("e91", 1, "Beta Pilot", 20_000)]
        self.state["payloads"]["e91"] = pattern_bytes(20_000, 9)

        # 1. Multiple args
        res = self.run_tool("queue", "plan", "Alpha Show", "S1", ";", "Beta Show", "all")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Alpha Show", res.stdout)
        self.assertIn("Beta Show", res.stdout)
        self.assertIn("Grand Total:", res.stdout)

        # 2. From file
        q_file = self.tempdir / "queue_test.txt"
        q_file.write_text("Alpha Show S1\nBeta Show all\n", encoding="utf-8")
        res2 = self.run_tool("plan", "-f", str(q_file))
        self.assertEqual(res2.returncode, 0, res2.stderr)
        self.assertIn("Alpha Show", res2.stdout)
        self.assertIn("Beta Show", res2.stdout)

    def test_queue_download_file(self):
        self.state["series"].append(
            {"Id": "s2", "Name": "Beta Show", "Type": "Series", "ProductionYear": 2021})
        self.state["seasons"]["s2"] = [
            {"Id": "se9", "IndexNumber": 1, "Name": "Season 1"}]
        self.state["episodes"]["se9"] = [
            make_episode("e91", 1, "Beta Pilot", 20_000)]
        self.state["payloads"]["e91"] = pattern_bytes(20_000, 9)

        q_file = self.tempdir / "queue_dl.txt"
        q_file.write_text("Alpha Show S1 E1\nBeta Show S1 E1\n", encoding="utf-8")
        res = self.run_tool("download", "-f", str(q_file))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(self.dest(1, 1, "mkv").exists())
        beta_dest = self.downloads / "Beta Show" / "Season 1" / "Beta Show - S01E01.mkv"
        self.assertTrue(beta_dest.exists())
        central = self.central()
        self.assertEqual(central["total_episodes"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
