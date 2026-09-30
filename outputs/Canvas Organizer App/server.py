#!/usr/bin/env python3
"""Local web interface for the Canvas Module Downloader and quarter organizer.

Runs only on 127.0.0.1, uses the Python standard library, and never reads or
returns the Canvas token. The downloader itself still reads the token from
macOS Keychain through the existing shell helpers.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

APP_DIR = Path(__file__).resolve().parent
OUTPUTS = APP_DIR.parent
DOWNLOADER_DIR = OUTPUTS / "Canvas Module Downloader"
COMMON_ZSH = DOWNLOADER_DIR / "lib/common.zsh"
LAUNCHER = DOWNLOADER_DIR / "Canvas Module Downloader.command"
ORGANIZER = OUTPUTS / "canvas-quarter-organizer/scripts/organize_canvas_files.py"
STATIC = APP_DIR / "static"

HOME = Path.home()
CONFIG_ROOT = Path(
    os.environ.get("CANVAS_MODULE_DOWNLOADER_CONFIG_DIR")
    or HOME / "Library/Application Support/Canvas Module Downloader"
)
KEYCHAIN_SERVICE = "Canvas Module Downloader API Token"
AGENT_FILE = HOME / "Library/LaunchAgents/com.local.canvas-module-downloader.plist"
CLASSES_ROOT = Path(os.environ.get("CANVAS_CLASSES_ROOT") or HOME / "Documents/ASU/Classes")
CURRENT_DIR_RE = re.compile(r"^(?:\d+\s+)?Current(?:\s*-\s*.+)?$", re.IGNORECASE)

# Test hook: replace the real downloader with another executable.
FAKE_DOWNLOADER = os.environ.get("CANVAS_UI_DOWNLOADER_CMD")

SESSION_TOKEN = secrets.token_urlsafe(24)
MAX_BODY = 64 * 1024


def read_line(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def canvas_url() -> str:
    return read_line(CONFIG_ROOT / "canvas-url.txt").rstrip("/")


def destination() -> Path:
    value = read_line(CONFIG_ROOT / "destination.txt")
    return Path(value) if value.startswith("/") else HOME / "Documents/Canvas Downloads"


def selected_courses() -> list[str]:
    try:
        lines = (CONFIG_ROOT / "courses.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip()]


def token_saved(url: str) -> bool:
    if FAKE_DOWNLOADER:
        return True
    if not url or not shutil.which("security"):
        return False
    result = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-a", url, "-s", KEYCHAIN_SERVICE],
        capture_output=True,
    )
    return result.returncode == 0


def current_class_folders() -> dict:
    if not CLASSES_ROOT.is_dir():
        return {"root": str(CLASSES_ROOT), "current": None, "folders": []}
    current = sorted(p for p in CLASSES_ROOT.iterdir() if p.is_dir() and CURRENT_DIR_RE.fullmatch(p.name))
    base = current[0] if len(current) == 1 else CLASSES_ROOT
    folders = sorted((p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")), key=str.casefold)
    return {"root": str(CLASSES_ROOT), "current": base.name if base != CLASSES_ROOT else None, "folders": folders}


def status() -> dict:
    url = canvas_url()
    return {
        "configured": bool(url),
        "canvas_url": url,
        "token_saved": token_saved(url),
        "selected": selected_courses(),
        "destination": str(destination()),
        "destination_exists": destination().is_dir(),
        "daily_sync": AGENT_FILE.is_file(),
        "classes": current_class_folders(),
        "fake": bool(FAKE_DOWNLOADER),
    }


# ---------------------------------------------------------------- jobs

class Job:
    def __init__(self, title: str, cmd: list[str]):
        self.id = secrets.token_hex(4)
        self.title = title
        self.cmd = cmd
        self.lines: list[str] = []
        self.done = False
        self.code: int | None = None
        self.proc: subprocess.Popen | None = None
        self.cancelled = False
        self.started = time.time()
        self.lock = threading.Lock()

    def run(self) -> None:
        try:
            self.proc = subprocess.Popen(
                self.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                bufsize=1, errors="replace", stdin=subprocess.DEVNULL,
            )
            assert self.proc.stdout
            for line in self.proc.stdout:
                self.add(line.rstrip("\n"))
            self.code = self.proc.wait()
        except OSError as exc:
            self.add(f"Could not start: {exc}")
            self.code = 127
        finally:
            self.done = True

    def add(self, line: str) -> None:
        with self.lock:
            self.lines.append(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", line.split("\r")[-1]))
            if len(self.lines) > 20000:
                del self.lines[:5000]

    def snapshot(self, start: int) -> dict:
        with self.lock:
            return {
                "id": self.id, "title": self.title, "done": self.done, "code": self.code,
                "cancelled": self.cancelled, "total": len(self.lines), "lines": self.lines[start:],
            }


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def current_job() -> Job | None:
    with JOBS_LOCK:
        return next((j for j in JOBS.values() if not j.done), None)


def start_job(title: str, cmd: list[str]) -> tuple[Job | None, str | None]:
    with JOBS_LOCK:
        if any(not j.done for j in JOBS.values()):
            return None, "Another task is still running."
        job = Job(title, cmd)
        JOBS[job.id] = job
    threading.Thread(target=job.run, daemon=True).start()
    return job, None


def downloader_cmd(extra: list[str], courses: list[str] | None) -> list[str]:
    args = ["--destination-folder", str(destination()), "--no-submissions", "--no-raw", *extra]
    if courses:
        args += ["-c", *courses]
    if FAKE_DOWNLOADER:
        return [FAKE_DOWNLOADER, *args]
    script = 'source "$1" && shift && cmdl_verify_binary && cmdl_run_downloader "$@"'
    return ["zsh", "-c", script, "zsh", str(COMMON_ZSH), *args]


def organizer_cmd(maps: dict[str, str], apply: bool) -> list[str]:
    cmd = [sys.executable, str(ORGANIZER), "--classes-root", str(CLASSES_ROOT),
           "--downloads-root", str(destination()), "--verbose"]
    for course in selected_courses():
        cmd += ["--course", course]
    for course, folder in maps.items():
        cmd += ["--map", f"{course}={folder}"]
    if apply:
        cmd.append("--apply")
    return cmd


def valid_name(value: object) -> bool:
    return (
        isinstance(value, str) and value.strip() == value and 0 < len(value) <= 200
        and not value.startswith("-") and not re.search(r"[\r\n\x00/]", value)
    )


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    server_version = "CanvasOrganizerUI"

    def log_message(self, fmt: str, *args) -> None:  # keep the terminal quiet
        pass

    def send_json(self, payload: object, code: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in {"127.0.0.1", "localhost"}

    def authorized(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Session-Token", ""), SESSION_TOKEN)

    def do_GET(self) -> None:
        if not self.host_ok():
            return self.send_json({"error": "bad host"}, 403)
        path = urlparse(self.path).path
        if path == "/":
            html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__SESSION_TOKEN__", SESSION_TOKEN)
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if not self.authorized():
            return self.send_json({"error": "unauthorized"}, 403)
        if path == "/api/status":
            return self.send_json(status())
        match = re.fullmatch(r"/api/jobs/([0-9a-f]+)", path)
        if match:
            job = JOBS.get(match.group(1))
            if not job:
                return self.send_json({"error": "unknown job"}, 404)
            query = urlparse(self.path).query
            start = int(query.split("from=")[1].split("&")[0]) if "from=" in query else 0
            return self.send_json(job.snapshot(start))
        self.send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self.host_ok() or not self.authorized():
            return self.send_json({"error": "forbidden"}, 403)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self.send_json({"error": "too large"}, 413)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
            assert isinstance(data, dict)
        except (ValueError, AssertionError):
            return self.send_json({"error": "bad json"}, 400)
        self.route_post(urlparse(self.path).path, data)

    def job_response(self, title: str, cmd: list[str]) -> None:
        job, error = start_job(title, cmd)
        if error:
            return self.send_json({"error": error}, 409)
        self.send_json({"job": job.id})

    def route_post(self, path: str, data: dict) -> None:
        st = status()
        needs_canvas = path in {"/api/courses/list", "/api/download/preview", "/api/download/sync", "/api/download/refresh"}
        if needs_canvas and not (st["configured"] and st["token_saved"]):
            return self.send_json({"error": "Canvas access is not set up yet."}, 400)

        if path == "/api/open-setup":
            if not shutil.which("open"):
                return self.send_json({"error": "Only available on macOS."}, 400)
            subprocess.Popen(["open", str(LAUNCHER)])
            return self.send_json({"ok": True})

        if path == "/api/courses/list":
            return self.job_response("List Canvas courses", downloader_cmd(["--dry-run"], None))

        if path == "/api/courses/select":
            courses = data.get("courses")
            if not isinstance(courses, list) or not courses or not all(valid_name(c) for c in courses):
                return self.send_json({"error": "Pick at least one course."}, 400)
            CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
            target = CONFIG_ROOT / "courses.txt"
            if target.is_file():
                shutil.copy2(target, CONFIG_ROOT / "courses.txt.previous")
            tmp = CONFIG_ROOT / ".courses.txt.tmp"
            tmp.write_text("\n".join(courses) + "\n", encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(target)
            return self.send_json({"ok": True, "selected": courses})

        if path in {"/api/download/preview", "/api/download/sync", "/api/download/refresh"}:
            courses = selected_courses()
            if not courses:
                return self.send_json({"error": "Select courses first."}, 400)
            destination().mkdir(parents=True, exist_ok=True)
            if path.endswith("preview"):
                return self.job_response("Preview downloads", downloader_cmd(["--dry-run"], courses))
            if path.endswith("sync"):
                return self.job_response("Download new files", downloader_cmd([], courses))
            if data.get("confirm") != "REFRESH":
                return self.send_json({"error": "Type REFRESH to confirm."}, 400)
            return self.job_response("Refresh instructor updates", downloader_cmd(["--download-newer"], courses))

        if path in {"/api/organize/preview", "/api/organize/apply"}:
            maps = data.get("maps") or {}
            if not isinstance(maps, dict) or not all(valid_name(k) and valid_name(v) for k, v in maps.items()):
                return self.send_json({"error": "Invalid folder mapping."}, 400)
            return self.job_response(
                "Organize preview" if path.endswith("preview") else "Organize files",
                organizer_cmd(maps, path.endswith("apply")),
            )

        if path == "/api/jobs/cancel":
            job = current_job()
            if job and job.proc:
                job.cancelled = True
                job.proc.terminate()
            return self.send_json({"ok": True})

        self.send_json({"error": "not found"}, 404)


def main() -> int:
    port = int(os.environ.get("CANVAS_UI_PORT", "0"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Canvas Organizer is running at {url}")
    print("Keep this window open while you use it. Press Control-C to stop.")
    if not os.environ.get("CANVAS_UI_NO_BROWSER"):
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
