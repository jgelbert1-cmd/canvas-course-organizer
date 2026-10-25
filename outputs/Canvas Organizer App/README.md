# Canvas Organizer App

A local browser interface for the Canvas downloader and the quarter organizer. It replaces the Terminal menu for everyday use.

## Start

Double-click **Canvas Organizer.app**. No Terminal window opens. Your browser loads the page, and the **Quit app** button on the page stops it. Opening the app again while it is running just reopens the page.

The first time, macOS may say it can't verify the app. Right-click it, choose **Open**, then **Open** again. Needs a `python3` (the Homebrew one works). If the app won't launch, **Canvas Organizer.command** starts the same thing in a Terminal window.

## The steps

1. **Canvas access:** pick your school (or type another address), paste your access token, and click Save. The token goes into macOS Keychain without touching a file or a command line.
2. **Courses:** loads your visible Canvas courses as a checklist, grouped by Canvas term with the newest first. **Check newest term only** picks this quarter's classes in one click, and saved courses that no longer show up on Canvas are flagged and unchecked. Saving writes the same `courses.txt` the Terminal launcher and daily sync use.
3. **Download:** preview, download new files, or refresh instructor updates (asks you to type REFRESH). The download folder has a Change button that opens the normal Mac folder picker. Output streams live and a running task can be cancelled.
4. **Organize:** previews course-to-folder mappings from the existing organizer. Courses without a match get a dropdown of your existing class folders, or "New folder…" with a suggested name. The Classes folder has a Change button. Apply stays disabled until a preview has no unresolved courses. By default it skips Canvas page copies (the `.html` saves of pages, assignments, announcements and discussions, plus banner images pulled from them), skips the same file showing up in more than one place on Canvas, and skips anything already somewhere in the class folder, even under another name. The syllabus is always kept. A checkbox brings the page copies back.
5. **Daily sync (optional):** choose a time and turn it on or off. This replaces menu options 7 and 8.

## Safety

- The server listens on `127.0.0.1` only, rejects other Host headers, and requires a per-launch session token on every request.
- After you save it, the token is never read back or returned by the page. Downloads still go through `lib/common.zsh`, including the binary checksum check.
- Organizing uses `organize_canvas_files.py`, so the no-overwrite and conflict-copy rules still apply. The download folder itself is left alone, so it still holds every copy Canvas sends.

## Environment variables

`CANVAS_UI_PORT` (default 47813), `CANVAS_CLASSES_ROOT` (overrides the saved Classes folder; default `~/Documents/ASU/Classes`), `CANVAS_UI_NO_BROWSER=1`.

## Known limits

- Tested end to end against a stand-in downloader, not the real Apple Silicon binary or a real Canvas account. The course list parser reads the `Term ID | Course Code | Course Name` table that the bundled v0.4.2 downloader prints (checked against its source code). If your output differs, the log panel shows the raw text.
- A class only shows up once Canvas lists it for you. Courses the instructor hasn't published, or that are hidden until the start date, aren't returned by Canvas at all.
- The Keychain, folder picker, and daily sync steps were tested with stand-in tools, not on a real Mac. If one fails, the page shows an error and the Terminal launcher still does the same job.
