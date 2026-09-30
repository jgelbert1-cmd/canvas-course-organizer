# Canvas Organizer App

A local browser interface for the Canvas downloader and the quarter organizer. It replaces the Terminal menu for everyday use.

## Start

Double-click **Canvas Organizer.command**. A browser tab opens at a `127.0.0.1` address. Keep the Terminal window open while you use it, and press Control-C there to stop.

It needs only the `python3` that ships with macOS. No installs.

## The four steps

1. **Canvas access**: shows whether a token is in Keychain. The setup button opens the existing launcher in Terminal, where you paste the token into the secure Keychain prompt (option 1). The token is never typed into the web page.
2. **Courses**: loads your visible Canvas courses as a checklist. Saving writes the same `courses.txt` the launcher and daily sync use.
3. **Download**: preview, download new files, or refresh instructor updates (asks you to type REFRESH). Output streams live and a running task can be cancelled.
4. **Organize**: previews course-to-folder mappings from the existing organizer. Courses without a match get a folder picker. Apply stays disabled until a preview has no unresolved courses.

## Safety

- The server listens on `127.0.0.1` only, rejects other Host headers, and requires a per-launch session token on every request.
- It never reads or returns the Canvas token. Downloads still go through `lib/common.zsh`, including the binary checksum check.
- Organizing uses `organize_canvas_files.py` unchanged, so the no-overwrite and conflict-copy rules still apply.

## Environment variables

`CANVAS_UI_PORT` (fixed port), `CANVAS_CLASSES_ROOT` (default `~/Documents/ASU/Classes`), `CANVAS_UI_NO_BROWSER=1`.

## Known limits

- Tested end to end against a stand-in downloader, not the real Apple Silicon binary or a real Canvas account. The course list parser expects lines like `* CODE - NAME` under `Courses found:`. If your output differs, the log panel shows the raw text.
- Daily sync, download folder, and token removal remain in the Terminal launcher.
