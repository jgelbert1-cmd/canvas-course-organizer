---
name: canvas-quarter-organizer
description: Set up or resync an ASU Canvas quarter by selecting Canvas courses, downloading course materials with the installed Canvas Module Downloader, and safely organizing copies into the user's existing ASU class folders. Use for new-quarter setup or whenever the user asks to download, refresh, or organize Canvas class files.
---

# Canvas Quarter Organizer

Use the installed Canvas downloader as the source and organize copies under `~/Documents/ASU/Classes`. Keep the downloader cache intact because the scheduled sync uses it to determine what is already downloaded.

## Installed locations

- Runtime and configuration: `~/Library/Application Support/Canvas Module Downloader`
- Download cache: read `destination.txt`; the usual location is the `downloads` folder beside it.
- Class folders: `~/Documents/ASU/Classes/Current - <Term>`; a numeric sorting prefix is also supported.
- Secure token: macOS Keychain service `Canvas Module Downloader API Token`. Never display, log, or copy it.

Use the bundled scripts from this skill rather than rebuilding the commands.

## Choose the mode

- **New quarter:** discover visible courses, select the new student courses, download them, preview mappings, then organize them.
- **Sync now:** download missing files for the saved course selection, then organize new files.
- **Refresh instructor updates:** only when the user explicitly asks to replace cached files with newer Canvas versions; then organize the updates.
- **Organize only:** skip Canvas access and work from the existing download cache.

## Canvas download workflow

Run these from the skill directory:

```zsh
scripts/download_canvas.zsh list
scripts/download_canvas.zsh select 'EXACT-COURSE-CODE' 'ANOTHER-EXACT-CODE'
scripts/download_canvas.zsh sync
```

`list` is a dry run and shows every visible Canvas course. At a new quarter, identify the relevant current-term student courses from that output. Do not select concluded courses or staff/TA shells unless the user asks. `select` replaces the saved selection and also changes what the existing daily background sync watches.

File current classes only under the single `Current - <Term>` directory, with or without a numeric sorting prefix. Never place new Canvas files in a completed-courses archive. If the Canvas semester and the current-directory label disagree, stop and ask which classes should remain active; do not automatically archive or move class folders because semester-long courses may cross a quarter boundary.

Use `scripts/download_canvas.zsh refresh` only after an explicit request for updated instructor copies. Use `scripts/download_canvas.zsh selected` for a read-only preview of the saved selection.

## Map and organize

First run a preview:

```zsh
python3 scripts/organize_canvas_files.py
```

The organizer matches a Canvas course to an existing current-term class directory by course number, such as `MGT502` to `Current - Fall 2026/MGT 502 - Org Behavior`. It reuses familiar folders including `Assignment 1`, `Assignments`, `Class Slides`, `Course Material`, `Weekly Modules`, `Handouts`, `Syllabus & Schedule`, and `Syllabus+` when present.

Treat slides as course material. Reuse an existing top-level `Class Slides` folder, but for a new class put them under `Course Material/Class Slides`. Put other module files under `Course Material/Modules` unless the class already has a more specific `Weekly Modules` or `Handouts` structure.

If a course has one exact existing match, proceed without asking. If it has no match, use the Canvas display title to propose a concise folder in the established form `SUBJECT NNN - Short Name`. If multiple matches or the short name is genuinely ambiguous, ask the user with 2–4 concise choices.

Supply new or corrected mappings with one `--map` per course directory:

```zsh
python3 scripts/organize_canvas_files.py \
  --map '2026FallB-X-ABC123-12345=ABC 123 - Short Name'
```

After the preview is correct, apply it:

```zsh
python3 scripts/organize_canvas_files.py \
  --map '2026FallB-X-ABC123-12345=ABC 123 - Short Name' \
  --apply
```

Omit `--map` when all mappings resolve automatically. The user's request to set up, sync, or organize the quarter authorizes the apply step after a clean preview. Stop for direction if mappings remain unresolved or the preview reports conflicts needing judgment.

## Safety and verification

- Copy files from the cache; never move or delete the cache.
- Exclude submissions and raw Canvas JSON from class folders.
- Never overwrite an untracked or user-edited class file. The organizer writes a clearly labeled Canvas conflict copy instead.
- Keep organizer state in `~/Library/Application Support/Canvas Quarter Organizer/state.json`.
- After applying, rerun the organizer without `--apply`. A clean result has no new, updated, or conflict copies pending.
- Report the course-to-folder mappings, copied/updated counts, conflicts, and any unresolved courses.
