#!/usr/bin/env python3
"""Local web interface for the Canvas Module Downloader and quarter organizer.

Runs only on 127.0.0.1, uses the Python standard library, and never reads or
returns the Canvas token. The downloader itself still reads the token from
macOS Keychain through the existing shell helpers.
"""

from __future__ import annotations

import json
import os
import plistlib
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
ORGANIZER = OUTPUTS / "canvas-quarter-organizer/scripts/organize_canvas_files.py"
STATIC = APP_DIR / "static"

HOME = Path.home()
CONFIG_ROOT = Path(
    os.environ.get("CANVAS_MODULE_DOWNLOADER_CONFIG_DIR")
    or HOME / "Library/Application Support/Canvas Module Downloader"
)
KEYCHAIN_SERVICE = "Canvas Module Downloader API Token"
AGENT_FILE = HOME / "Library/LaunchAgents/com.local.canvas-module-downloader.plist"
AGENT_LABEL = "com.local.canvas-module-downloader"
SECURITY = os.environ.get("CANVAS_UI_SECURITY", "/usr/bin/security")
LAUNCHCTL = os.environ.get("CANVAS_UI_LAUNCHCTL", "/bin/launchctl")
SETTINGS_FILE = CONFIG_ROOT / "ui-settings.json"
PLAN_FILE = CONFIG_ROOT / "organize-plan.json"
AUTO_ORGANIZE_FILE = CONFIG_ROOT / "auto-organize.txt"
LAST_SYNC_FILE = CONFIG_ROOT / "logs/last-sync.json"
DEFAULT_PORT = 47813
TOKEN_RE = re.compile(r"^[A-Za-z0-9~_.\-]{10,300}$")
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


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_setting(key: str, value: object) -> None:
    ensure_config_root()
    data = load_settings()
    data[key] = value
    SETTINGS_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    SETTINGS_FILE.chmod(0o600)


def classes_root() -> Path:
    env = os.environ.get("CANVAS_CLASSES_ROOT")
    saved = load_settings().get("classes_root")
    return Path(env or saved or HOME / "Documents/ASU/Classes")


def ensure_config_root() -> None:
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    CONFIG_ROOT.chmod(0o700)


def write_private(path: Path, text: str) -> None:
    ensure_config_root()
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def auto_organize_on() -> bool:
    return bool(load_settings().get("auto_organize"))


def sync_auto_organize_file() -> None:
    """background-sync.zsh looks for this file; it holds the Classes folder to file into."""
    if auto_organize_on():
        write_private(AUTO_ORGANIZE_FILE, str(classes_root()) + "\n")
    else:
        AUTO_ORGANIZE_FILE.unlink(missing_ok=True)


def last_sync() -> dict | None:
    try:
        data = json.loads(LAST_SYNC_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def token_saved(url: str) -> bool:
    if FAKE_DOWNLOADER:
        return True
    if not url or not Path(SECURITY).exists():
        return False
    result = subprocess.run(
        [SECURITY, "find-generic-password", "-a", url, "-s", KEYCHAIN_SERVICE],
        capture_output=True,
    )
    return result.returncode == 0


def current_class_folders() -> dict:
    root = classes_root()
    if not root.is_dir():
        return {"root": str(root), "exists": False, "current": None, "folders": []}
    current = sorted(p for p in root.iterdir() if p.is_dir() and CURRENT_DIR_RE.fullmatch(p.name))
    base = current[0] if len(current) == 1 else root
    folders = sorted((p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")), key=str.casefold)
    return {"root": str(root), "exists": True, "current": base.name if base != root else None, "folders": folders}


def sync_schedule() -> dict | None:
    try:
        plist = plistlib.loads(AGENT_FILE.read_bytes())
        when = plist["StartCalendarInterval"]
        return {"hour": int(when["Hour"]), "minute": int(when["Minute"])}
    except (OSError, KeyError, ValueError, plistlib.InvalidFileException):
        return None


def status() -> dict:
    url = canvas_url()
    return {
        "configured": bool(url),
        "canvas_url": url,
        "token_saved": token_saved(url),
        "selected": selected_courses(),
        "destination": str(destination()),
        "destination_exists": destination().is_dir(),
        "daily_sync": sync_schedule(),
        "auto_organize": auto_organize_on(),
        "last_sync": last_sync(),
        "classes": current_class_folders(),
        "fake": bool(FAKE_DOWNLOADER),
    }


def save_access(url: str, token: str) -> str | None:
    """Store the token in Keychain via `security -i`, so it never appears in argv."""
    if not re.fullmatch(r"https://[A-Za-z0-9.\-]+(:\d+)?", url):
        return "Enter a web address like https://canvas.asu.edu"
    if not TOKEN_RE.fullmatch(token):
        return "That does not look like a Canvas token. Copy it again without spaces."
    if not Path(SECURITY).exists():
        return "Keychain is only available on macOS."
    label = f"Canvas API token for {url}"
    command = f'add-generic-password -U -a "{url}" -s "{KEYCHAIN_SERVICE}" -l "{label}" -w "{token}"\n'
    subprocess.run([SECURITY, "-i"], input=command, capture_output=True, text=True)
    if not token_saved(url):
        return "macOS would not save the token to Keychain."
    write_private(CONFIG_ROOT / "canvas-url.txt", url + "\n")
    return None


def remove_token() -> str | None:
    url = canvas_url()
    result = subprocess.run([SECURITY, "delete-generic-password", "-a", url, "-s", KEYCHAIN_SERVICE],
                            capture_output=True)
    return None if result.returncode == 0 else "No saved token was found."


def pick_folder(prompt: str) -> str | None:
    script = f'POSIX path of (choose folder with prompt "{prompt}")'
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = result.stdout.strip()
    return path.rstrip("/") if result.returncode == 0 and path.startswith("/") else None


def enable_sync(hour: int, minute: int) -> str | None:
    runtime = CONFIG_ROOT / "runtime"
    try:
        for sub in ("bin", "lib", "scripts"):
            (runtime / sub).mkdir(parents=True, exist_ok=True)
        for src, dst in (
            (DOWNLOADER_DIR / "bin/canvas-downloader", runtime / "bin/canvas-downloader"),
            (COMMON_ZSH, runtime / "lib/common.zsh"),
            (DOWNLOADER_DIR / "scripts/background-sync.zsh", runtime / "scripts/background-sync.zsh"),
            (ORGANIZER, runtime / "scripts/organize_canvas_files.py"),
        ):
            shutil.copy2(src, dst)
            dst.chmod(0o700)
        (CONFIG_ROOT / "logs").mkdir(exist_ok=True)
        sync_auto_organize_file()
        plist = {
            "Label": AGENT_LABEL,
            "ProgramArguments": ["/bin/zsh", str(runtime / "scripts/background-sync.zsh")],
            "StartCalendarInterval": {"Hour": hour, "Minute": minute},
            "StandardOutPath": str(CONFIG_ROOT / "logs/daily-sync.log"),
            "StandardErrorPath": str(CONFIG_ROOT / "logs/daily-sync-errors.log"),
            "ProcessType": "Background",
        }
        AGENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = AGENT_FILE.with_name(f".{AGENT_FILE.name}.tmp")
        tmp.write_bytes(plistlib.dumps(plist))
        tmp.chmod(0o600)
        domain = f"gui/{os.getuid()}"
        subprocess.run([LAUNCHCTL, "bootout", domain, str(AGENT_FILE)], capture_output=True)
        tmp.replace(AGENT_FILE)
        result = subprocess.run([LAUNCHCTL, "bootstrap", domain, str(AGENT_FILE)], capture_output=True)
    except OSError as exc:
        return f"Could not set up the schedule: {exc}"
    return None if result.returncode == 0 else "The schedule file was saved, but macOS did not start it."


def disable_sync() -> None:
    subprocess.run([LAUNCHCTL, "bootout", f"gui/{os.getuid()}", str(AGENT_FILE)], capture_output=True)
    AGENT_FILE.unlink(missing_ok=True)


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


def organizer_cmd(maps: dict[str, str], apply: bool, skips: list[str] | None = None,
                  plan_file: Path | None = None) -> list[str]:
    cmd = [sys.executable, str(ORGANIZER), "--classes-root", str(classes_root()),
           "--downloads-root", str(destination()), "--verbose"]
    if plan_file:
        cmd += ["--json-plan", str(plan_file)]
    for key in skips or []:
        cmd.append(f"--skip={key}")
    for course in selected_courses():
        cmd += ["--course", course]
    for course, folder in maps.items():
        cmd += ["--map", f"{course}={folder}"]
    if apply:
        cmd.append("--apply")
    return cmd


def valid_key(value: object) -> bool:
    return (
        isinstance(value, str) and 0 < len(value) <= 600 and "/" in value
        and not re.search(r"[\r\n\x00]", value) and ".." not in value.split("/")
    )


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
        if path == "/api/ping":
            return self.send_json({"app": "canvas-organizer"})
        if not self.authorized():
            return self.send_json({"error": "unauthorized"}, 403)
        if path == "/api/organize/plan":
            try:
                return self.send_json(json.loads(PLAN_FILE.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                return self.send_json({"error": "No preview yet."}, 404)
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

        if path == "/api/access/save":
            error = save_access(str(data.get("url", "")).strip().rstrip("/"), str(data.get("token", "")).strip())
            return self.send_json({"error": error}, 400) if error else self.send_json({"ok": True})

        if path == "/api/access/remove":
            if data.get("confirm") is not True:
                return self.send_json({"error": "Confirmation required."}, 400)
            error = remove_token()
            return self.send_json({"error": error}, 400) if error else self.send_json({"ok": True})

        if path == "/api/pick-folder":
            purpose = data.get("purpose")
            if purpose not in {"destination", "classes"}:
                return self.send_json({"error": "bad request"}, 400)
            chosen = pick_folder("Choose the folder for downloaded Canvas files" if purpose == "destination"
                                 else "Choose your Classes folder")
            if not chosen:
                return self.send_json({"ok": False})
            if purpose == "destination":
                write_private(CONFIG_ROOT / "destination.txt", chosen + "\n")
            else:
                save_setting("classes_root", chosen)
                sync_auto_organize_file()
            return self.send_json({"ok": True})

        if path == "/api/open-folder":
            purpose = data.get("purpose")
            if purpose not in {"destination", "classes"}:
                return self.send_json({"error": "bad request"}, 400)
            target = destination() if purpose == "destination" else classes_root()
            if not target.is_dir():
                return self.send_json({"error": f"Folder not found: {target}"}, 400)
            subprocess.run(["open", str(target)], capture_output=True)
            return self.send_json({"ok": True})

        if path == "/api/sync/auto-organize":
            if not isinstance(data.get("enabled"), bool):
                return self.send_json({"error": "bad request"}, 400)
            save_setting("auto_organize", data["enabled"])
            sync_auto_organize_file()
            if sync_schedule():  # refresh the copied scripts the schedule runs
                when = sync_schedule()
                error = enable_sync(when["hour"], when["minute"])
                if error:
                    return self.send_json({"error": error}, 500)
            return self.send_json({"ok": True})

        if path == "/api/sync/enable":
            hour, minute = data.get("hour"), data.get("minute")
            if not (isinstance(hour, int) and isinstance(minute, int) and 0 <= hour <= 23 and 0 <= minute <= 59):
                return self.send_json({"error": "Pick a valid time."}, 400)
            if not (st["configured"] and st["token_saved"] and st["selected"]):
                return self.send_json({"error": "Set up access and choose courses first."}, 400)
            error = enable_sync(hour, minute)
            return self.send_json({"error": error}, 500) if error else self.send_json({"ok": True})

        if path == "/api/sync/disable":
            disable_sync()
            return self.send_json({"ok": True})

        if path == "/api/quit":
            self.send_json({"ok": True})
            threading.Timer(0.3, lambda: os._exit(0)).start()
            return

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
            skips = data.get("skips") or []
            if not isinstance(skips, list) or not all(valid_key(k) for k in skips):
                return self.send_json({"error": "Invalid skipped file."}, 400)
            preview = path.endswith("preview")
            if preview:
                PLAN_FILE.unlink(missing_ok=True)
            return self.job_response(
                "Organize preview" if preview else "Organize files",
                organizer_cmd(maps, not preview, skips, PLAN_FILE if preview else None),
            )

        if path == "/api/jobs/cancel":
            job = current_job()
            if job and job.proc:
                job.cancelled = True
                job.proc.terminate()
            return self.send_json({"ok": True})

        self.send_json({"error": "not found"}, 404)


def main() -> int:
    port = int(os.environ.get("CANVAS_UI_PORT", DEFAULT_PORT))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
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
