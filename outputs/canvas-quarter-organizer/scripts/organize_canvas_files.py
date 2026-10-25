#!/usr/bin/env python3
"""Safely copy a Canvas download cache into the user's ASU class folders."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


HOME = Path.home()
DOWNLOADER_ROOT = HOME / "Library/Application Support/Canvas Module Downloader"
DEFAULT_CLASSES_ROOT = HOME / "Documents/ASU/Classes"
DEFAULT_STATE_FILE = HOME / "Library/Application Support/Canvas Quarter Organizer/state.json"
COURSE_RE = re.compile(r"(?:^|[-_])([A-Z]{2,4})(\d{3})(?:[-_]|$)", re.IGNORECASE)
TERM_RE = re.compile(r"^(20\d{2})(Spring|Summer|Fall)", re.IGNORECASE)
CURRENT_DIR_RE = re.compile(r"^(?:\d+\s+)?Current(?:\s*-\s*.+)?$", re.IGNORECASE)
SLIDE_EXTENSIONS = {".ppt", ".pptx", ".key"}
SLIDE_WORDS = re.compile(r"(?:^|[^a-z])(slide|slides|lecture|deck)(?:[^a-z]|$)", re.IGNORECASE)
SOURCE_PRIORITY = {"modules": 1, "files": 2, "assignments": 3, "announcements": 4, "discussions": 5}
HTML_EXTENSIONS = {".html", ".htm"}
LINK_EXTENSIONS = {".url", ".webloc"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview or apply safe organization of downloaded Canvas files."
    )
    parser.add_argument("--downloads-root", type=Path, help="Override the Canvas download cache")
    parser.add_argument("--classes-root", type=Path, default=DEFAULT_CLASSES_ROOT)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--course", action="append", default=[], help="Process one exact cache course directory")
    parser.add_argument("--all-courses", action="store_true", help="Process all cached courses, not only the saved selection")
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="COURSE=CLASS_FOLDER",
        help="Map a cache course directory to a direct child of the classes root",
    )
    parser.add_argument(
        "--include-pages",
        action="store_true",
        help="Also copy Canvas page copies (.html) and the images embedded in them",
    )
    parser.add_argument("--skip", action="append", default=[], metavar="COURSE/RELATIVE_PATH", help="Leave one downloaded file out (repeatable)")
    parser.add_argument("--json-plan", type=Path, help="Also write the planned mappings and file actions to this JSON file")
    parser.add_argument(
        "--removed-dir",
        type=Path,
        help="Where to put earlier copies that are no longer wanted (default: next to the state file)",
    )
    parser.add_argument("--apply", action="store_true", help="Create folders and copy files")
    parser.add_argument("--verbose", action="store_true", help="Print every planned file action")
    return parser.parse_args()


def default_downloads_root() -> Path:
    destination_file = DOWNLOADER_ROOT / "destination.txt"
    if destination_file.is_file():
        value = destination_file.read_text(encoding="utf-8").strip()
        if value:
            return Path(value).expanduser()
    return DOWNLOADER_ROOT / "downloads"


def saved_course_selection() -> set[str]:
    selection_file = DOWNLOADER_ROOT / "courses.txt"
    if not selection_file.is_file():
        return set()
    return {
        line.strip()
        for line in selection_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_state(path: Path) -> dict:
    if not path.is_file():
        return {"version": 1, "course_mappings": {}, "files": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read organizer state: {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise RuntimeError(f"Unsupported organizer state format: {path}")
    data.setdefault("course_mappings", {})
    data.setdefault("files", {})
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def parse_mappings(values: list[str]) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Invalid --map value: {value!r}; expected COURSE=CLASS_FOLDER")
        course, folder = (piece.strip() for piece in value.split("=", 1))
        if not course or not folder or Path(folder).name != folder or folder in {".", ".."}:
            raise ValueError(f"Invalid --map value: {value!r}")
        mappings[course] = folder
    return mappings


def course_key(course_name: str) -> str | None:
    match = COURSE_RE.search(course_name.upper())
    if not match:
        return None
    return f"{match.group(1).upper()} {match.group(2)}"


def course_term(course_name: str) -> str | None:
    match = TERM_RE.search(course_name)
    if not match:
        return None
    season = match.group(2).capitalize()
    return f"{season} {match.group(1)}"


def current_classes_root(classes_root: Path) -> Path:
    current = sorted(
        item
        for item in classes_root.iterdir()
        if item.is_dir() and CURRENT_DIR_RE.fullmatch(item.name)
    )
    if len(current) > 1:
        raise RuntimeError(
            "Multiple current-term class folders found: " + ", ".join(item.name for item in current)
        )
    return current[0] if current else classes_root


def exact_class_matches(classes_root: Path, key: str) -> list[Path]:
    subject, number = key.split()
    pattern = re.compile(rf"^{re.escape(subject)}\s+{re.escape(number)}(?:\s*-|$)", re.IGNORECASE)
    return sorted(
        (item for item in classes_root.iterdir() if item.is_dir() and pattern.search(item.name)),
        key=lambda item: item.name.casefold(),
    )


def existing_child(class_dir: Path, names: list[str]) -> str | None:
    if not class_dir.is_dir():
        return None
    children = {item.name.casefold(): item.name for item in class_dir.iterdir() if item.is_dir()}
    for name in names:
        if name.casefold() in children:
            return children[name.casefold()]
    return None


def slide_file(source: Path, relative: Path) -> bool:
    if source.suffix.casefold() in SLIDE_EXTENSIONS:
        return True
    return source.suffix.casefold() == ".pdf" and bool(SLIDE_WORDS.search(relative.as_posix()))


def assignment_destination(class_dir: Path, remainder: tuple[str, ...]) -> Path:
    if not remainder:
        return Path("Assignments")
    first = Path(remainder[0])
    assignment_name = first.stem if len(remainder) == 1 else first.name
    match = re.match(r"^Assignment\s+(\d+)\b", assignment_name, re.IGNORECASE)
    candidates = [assignment_name]
    if match:
        candidates.insert(0, f"Assignment {match.group(1)}")
    assignment_folder = existing_child(class_dir, candidates)
    if assignment_folder:
        tail = remainder[1:] if len(remainder) > 1 else (remainder[0],)
        return Path(assignment_folder, "Canvas Materials", *tail)
    base = existing_child(class_dir, ["Assignments"]) or "Assignments"
    return Path(base, *remainder)


def destination_relative(class_dir: Path, relative: Path, source: Path) -> Path:
    parts = relative.parts
    top = parts[0].casefold() if len(parts) > 1 else ""
    remainder = parts[1:] if top else parts

    existing_slides = existing_child(class_dir, ["Class Slides", "Class Powerpoints", "Slides"])
    slides_base = Path(existing_slides) if existing_slides else Path("Course Material", "Class Slides")
    existing_material = existing_child(class_dir, ["Course Material", "Course Materials"])
    material_base = Path(existing_material) if existing_material else Path("Course Material")

    if top and top != "files" and slide_file(source, relative):
        return Path(slides_base, *remainder)
    if top == "assignments":
        return assignment_destination(class_dir, remainder)
    if top == "modules":
        weekly_modules = existing_child(class_dir, ["Weekly Modules"])
        if weekly_modules:
            return Path(weekly_modules, *remainder)
        handouts = existing_child(class_dir, ["Handouts"])
        if handouts:
            return Path(handouts, *remainder)
        return Path(material_base, "Modules", *remainder)
    if top == "files":
        if slide_file(source, relative):
            return Path(slides_base, *remainder)
        return Path(material_base, "Files", *remainder)
    if top in {"announcements", "discussions"}:
        return Path("Canvas Reference", top.title(), *remainder)

    if "syllabus" in source.name.casefold():
        syllabus = existing_child(class_dir, ["Syllabus & Schedule", "Syllabus+"])
        filename = "Canvas Syllabus.html" if source.name.casefold() == "syllabus.html" else source.name
        return Path(syllabus, filename) if syllabus else Path(filename)
    return Path(material_base, *remainder)


def collapse_repeats(path: Path) -> Path:
    """Turn "Intro Page/Intro Page/file.pdf" into "Intro Page/file.pdf".

    The downloader nests files linked from a module page in a folder named
    after the page, inside another folder with the same name.
    """
    parts: list[str] = []
    for part in path.parts:
        if not parts or part.casefold() != parts[-1].casefold():
            parts.append(part)
    return Path(*parts)


def source_sort_key(item: tuple[Path, Path]) -> tuple[int, str]:
    relative, _source = item
    top = relative.parts[0].casefold() if len(relative.parts) > 1 else ""
    priority = 0 if "syllabus" in relative.name.casefold() else SOURCE_PRIORITY.get(top, 6)
    return priority, relative.as_posix().casefold()


def course_files(course_dir: Path) -> list[tuple[Path, Path]]:
    found: list[tuple[Path, Path]] = []
    for source in course_dir.rglob("*"):
        if not source.is_file() or source.is_symlink():
            continue
        relative = source.relative_to(course_dir)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if source.name == ".DS_Store" or source.suffix.casefold() == ".part":
            continue
        found.append((relative, source))
    return sorted(found, key=source_sort_key)


def canvas_page_file(source: Path) -> bool:
    """True for Canvas page copies and the images pulled out of their bodies.

    The downloader saves each page, assignment, discussion and announcement as
    <title>.html and puts files embedded in it in a sibling <title>/ folder.
    The syllabus is kept because it is usually worth having offline.
    """
    suffix = source.suffix.casefold()
    if suffix in HTML_EXTENSIONS:
        return "syllabus" not in source.name.casefold()
    if suffix in IMAGE_EXTENSIONS:
        parent = source.parent
        return any((parent.parent / f"{parent.name}{ext}").is_file() for ext in HTML_EXTENSIONS)
    return False


class ClassFolderIndex:
    """Find files already somewhere in a class folder by content, hashing lazily."""

    def __init__(self, class_dir: Path, ignore: set[Path] = frozenset()) -> None:
        self.by_size: dict[int, list[Path]] = {}
        self.hashes: dict[Path, str] = {}
        if not class_dir.is_dir():
            return
        for item in class_dir.rglob("*"):
            relative = item.relative_to(class_dir)
            if any(part.startswith(".") for part in relative.parts):
                continue
            if item.is_file() and not item.is_symlink() and item not in ignore:
                self.by_size.setdefault(item.stat().st_size, []).append(item)

    def find(self, size: int, digest: str) -> Path | None:
        for item in self.by_size.get(size, []):
            if item not in self.hashes:
                try:
                    self.hashes[item] = sha256(item)
                except OSError:
                    self.hashes[item] = ""
            if self.hashes[item] == digest:
                return item
        return None


def tracked_copies(state: dict, course: str, classes_root: Path, class_dir: Path) -> dict[Path, str]:
    """Earlier copies this organizer made for a course that nobody has edited since."""
    found: dict[Path, str] = {}
    for key, entry in state["files"].items():
        if not key.startswith(course + "/"):
            continue
        path = classes_root / entry.get("destination", "")
        if not within_root(path, class_dir) or not path.is_file() or path.is_symlink():
            continue
        try:
            if sha256(path) == entry.get("source_hash"):
                found[path] = key
        except OSError:
            continue
    return found


def set_aside(path: Path, class_dir: Path, removed_root: Path) -> None:
    """Move an unwanted earlier copy out of the class folder, keeping its layout."""
    target = removed_root / class_dir.name / path.relative_to(class_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    counter = 2
    while target.exists():
        target = target.with_name(f"{path.stem} {counter}{path.suffix}")
        counter += 1
    shutil.move(str(path), str(target))
    parent = path.parent
    while parent != class_dir and within_root(parent, class_dir):
        leftovers = [item for item in parent.iterdir() if item.name != ".DS_Store"]
        if leftovers:
            break
        for item in parent.iterdir():
            item.unlink()
        parent.rmdir()
        parent = parent.parent


def within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def conflict_destination(destination: Path, source_hash: str) -> Path:
    label = f" (Canvas {source_hash[:8]})"
    candidate = destination.with_name(f"{destination.stem}{label}{destination.suffix}")
    counter = 2
    while candidate.exists() and sha256(candidate) != source_hash:
        candidate = destination.with_name(f"{destination.stem}{label} {counter}{destination.suffix}")
        counter += 1
    return candidate


def main() -> int:
    args = parse_args()
    downloads_root = (args.downloads_root or default_downloads_root()).expanduser()
    classes_root = args.classes_root.expanduser()
    state_file = args.state_file.expanduser()

    if not downloads_root.is_dir():
        print(f"Canvas download cache not found: {downloads_root}", file=sys.stderr)
        return 1
    if not classes_root.is_dir():
        print(f"Classes root not found: {classes_root}", file=sys.stderr)
        return 1

    try:
        explicit = parse_mappings(args.map)
        state = load_state(state_file)
        active_classes_root = current_classes_root(classes_root)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    all_courses = sorted(
        item for item in downloads_root.iterdir() if item.is_dir() and item.name != "raw" and not item.name.startswith(".")
    )
    requested = set(args.course)
    if not requested and not args.all_courses:
        requested = saved_course_selection()
    if requested:
        missing = requested - {item.name for item in all_courses}
        for name in sorted(missing):
            print(f"Not downloaded yet, skipping: {name}")
        all_courses = [item for item in all_courses if item.name in requested]

    if not all_courses:
        print("No Canvas course directories were found.", file=sys.stderr)
        return 1

    selected_terms = {term for item in all_courses if (term := course_term(item.name))}
    if active_classes_root != classes_root and len(selected_terms) == 1:
        selected_term = next(iter(selected_terms))
        season, year = selected_term.casefold().split()
        folder_words = re.findall(r"[a-z]+|\d+", active_classes_root.name.casefold())
        if season not in folder_words or year not in folder_words:
            print(
                f"Canvas selection is {selected_term}, but the current class folder is "
                f"{active_classes_root.name}. Resolve the term transition before organizing.",
                file=sys.stderr,
            )
            return 2

    plan: list[dict] = []
    unresolved: list[tuple[str, str]] = []
    mappings: list[tuple[str, str, str]] = []
    seen_by_course: dict[str, set[str]] = {}
    counters: dict[str, Counter] = {}
    report: list[dict] = []
    removals: list[dict] = []
    skipped = set(args.skip)

    for course_dir in all_courses:
        key = course_key(course_dir.name)
        if not key:
            unresolved.append((course_dir.name, "course number not recognized"))
            continue

        match_source = ""
        folder_name = explicit.get(course_dir.name) or explicit.get(key)
        if folder_name:
            match_source = "explicit"
        else:
            matches = exact_class_matches(active_classes_root, key)
            if len(matches) == 1:
                folder_name = matches[0].name
                match_source = "existing"
            elif len(matches) > 1:
                unresolved.append((course_dir.name, f"multiple {key} class folders"))
                continue
            else:
                folder_name = state["course_mappings"].get(course_dir.name)
                if folder_name:
                    match_source = "saved"

        if not folder_name:
            unresolved.append((course_dir.name, f"no {key} class folder; supply --map"))
            continue

        class_dir = active_classes_root / folder_name
        if not within_root(class_dir, active_classes_root) or class_dir.parent.resolve(strict=False) != active_classes_root.resolve(strict=False):
            print(f"Unsafe class mapping rejected: {course_dir.name} -> {folder_name}", file=sys.stderr)
            return 1

        folder_display = str(class_dir.relative_to(classes_root))
        mappings.append((course_dir.name, folder_name, folder_display, match_source))
        seen_hashes = seen_by_course.setdefault(course_dir.name, set())
        counts = counters.setdefault(course_dir.name, Counter())
        # Earlier copies the organizer made are re-planned from scratch, so a
        # file that now belongs elsewhere (or not at all) gets moved out.
        earlier = tracked_copies(state, course_dir.name, classes_root, class_dir)
        kept: set[Path] = set()
        existing_files = ClassFolderIndex(class_dir, set(earlier))

        for relative, source in course_files(course_dir):
            source_key = f"{course_dir.name}/{relative.as_posix()}"
            if source_key in skipped:
                kept.update(path for path, k in earlier.items() if k == source_key)
                counts["user-skipped"] += 1
                report.append({"action": "user-skipped", "course": course_dir.name, "key": source_key, "to": ""})
                continue
            size = source.stat().st_size
            if size == 0:
                counts["empty-skipped"] += 1
                report.append({"action": "empty-skipped", "course": course_dir.name, "key": source_key, "to": ""})
                continue
            if source.suffix.casefold() in LINK_EXTENSIONS:
                counts["link-skipped"] += 1
                report.append({"action": "link-skipped", "course": course_dir.name, "key": source_key, "to": ""})
                continue
            if not args.include_pages and canvas_page_file(source):
                counts["canvas-page-skipped"] += 1
                report.append({"action": "canvas-page-skipped", "course": course_dir.name, "key": source_key, "to": ""})
                continue
            source_hash = sha256(source)
            deduplicate = source.suffix.casefold() not in HTML_EXTENSIONS
            if deduplicate and source_hash in seen_hashes:
                counts["duplicate-skipped"] += 1
                report.append({"action": "duplicate-skipped", "course": course_dir.name, "key": source_key, "to": ""})
                continue
            if deduplicate:
                seen_hashes.add(source_hash)

            destination_rel = collapse_repeats(destination_relative(class_dir, relative, source))
            destination = class_dir / destination_rel
            if not within_root(destination, class_dir):
                print(f"Unsafe destination rejected: {destination}", file=sys.stderr)
                return 1

            source_state_key = f"{course_dir.name}/{relative.as_posix()}"
            prior = state["files"].get(source_state_key, {})
            expected_state_dest = str((class_dir / destination_rel).relative_to(classes_root))

            if not destination.exists():
                action = "copy"
                final_destination = destination
            else:
                destination_hash = sha256(destination)
                if destination_hash == source_hash:
                    action = "unchanged"
                    final_destination = destination
                elif prior.get("destination") == expected_state_dest and prior.get("source_hash") == destination_hash:
                    action = "update"
                    final_destination = destination
                else:
                    final_destination = conflict_destination(destination, source_hash)
                    if final_destination.exists() and sha256(final_destination) == source_hash:
                        action = "unchanged-conflict"
                    else:
                        action = "conflict-copy"

            # The same file may already be filed under another name or folder,
            # for example from an earlier quarter's layout or a manual copy.
            already = existing_files.find(size, source_hash) if action in {"copy", "conflict-copy"} else None
            if already:
                counts["already-in-class-folder"] += 1
                report.append({
                    "action": "already-in-class-folder", "course": course_dir.name, "key": source_state_key,
                    "to": already.relative_to(class_dir).as_posix(),
                })
                continue

            if final_destination in earlier:
                kept.add(final_destination)
            counts[action] += 1
            report.append({
                "action": action, "course": course_dir.name, "key": source_state_key,
                "to": final_destination.relative_to(class_dir).as_posix(),
            })
            plan.append(
                {
                    "action": action,
                    "course": course_dir.name,
                    "source": source,
                    "source_hash": source_hash,
                    "source_state_key": source_state_key,
                    "destination": final_destination,
                    "destination_state": str(final_destination.relative_to(classes_root)),
                }
            )

        for path, old_key in sorted(earlier.items()):
            if path in kept:
                continue
            counts["tidy-remove"] += 1
            report.append({"action": "tidy-remove", "course": course_dir.name, "key": old_key,
                           "to": path.relative_to(class_dir).as_posix()})
            removals.append({"path": path, "class_dir": class_dir, "key": old_key})

    print(f"Mode: {'APPLY' if args.apply else 'PREVIEW'}")
    print(f"Canvas cache: {downloads_root}")
    print(f"Classes root: {classes_root}")
    print(f"Current-term root: {active_classes_root}")
    print("\nCourse mappings:")
    for course, _folder, folder_display, source in mappings:
        print(f"  {course} -> {folder_display} [{source}]")
    for course, reason in unresolved:
        print(f"  {course} -> UNRESOLVED ({reason})")

    print("\nPlanned results:")
    for course, counts in counters.items():
        ordered = ", ".join(f"{name}={counts[name]}" for name in sorted(counts))
        print(f"  {course}: {ordered or 'no files'}")

    if args.verbose:
        print("\nFile actions:")
        for item in plan:
            if item["action"] not in {"unchanged", "unchanged-conflict"}:
                print(f"  {item['action']}: {item['destination']}")

    if args.json_plan:
        payload = {
            "mappings": [
                {"course": c, "folder": f, "display": d, "source": src} for c, f, d, src in mappings
            ],
            "unresolved": [{"course": c, "reason": r} for c, r in unresolved],
            "files": report,
        }
        args.json_plan.parent.mkdir(parents=True, exist_ok=True)
        args.json_plan.write_text(json.dumps(payload), encoding="utf-8")

    if unresolved:
        print("\nResolve every course mapping before applying.", file=sys.stderr)
        return 2

    if not args.apply:
        actionable = sum(
            counts[name]
            for counts in counters.values()
            for name in ("copy", "update", "conflict-copy", "tidy-remove")
        )
        print(f"\nPreview only. {actionable} file action(s) would be applied.")
        return 0

    for course, folder, _folder_display, _source in mappings:
        (active_classes_root / folder).mkdir(parents=True, exist_ok=True)
        state["course_mappings"][course] = folder

    removed_root = (args.removed_dir or state_file.parent / "Removed from class folders").expanduser()
    removed_root = removed_root / datetime.now().strftime("%Y-%m-%d %H-%M-%S")
    for item in removals:
        set_aside(item["path"], item["class_dir"], removed_root)
        state["files"].pop(item["key"], None)
    if removals:
        print(f"Moved {len(removals)} earlier cop{'y' if len(removals) == 1 else 'ies'} out of class folders to: {removed_root}")

    for item in plan:
        action = item["action"]
        if action in {"copy", "update", "conflict-copy"}:
            destination = item["destination"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item["source"], destination)
        state["files"][item["source_state_key"]] = {
            "destination": item["destination_state"],
            "source_hash": item["source_hash"],
        }

    state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
    save_state(state_file, state)
    changed = sum(
        counts[name]
        for counts in counters.values()
        for name in ("copy", "update", "conflict-copy")
    )
    conflicts = sum(counts["conflict-copy"] for counts in counters.values())
    print(f"\nApplied {changed} file action(s); conflict copies: {conflicts}.")
    print(f"State: {state_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
