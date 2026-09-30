# Canvas Course Organizer

A private macOS toolkit for downloading Canvas course materials and organizing them into existing class folders.

## Included components

- `outputs/Canvas Module Downloader/`: a double-clickable macOS launcher with Keychain-based token storage and optional daily syncing.
- `outputs/canvas-quarter-organizer/`: a Codex skill for new-quarter setup, course selection, downloading, and safe class-folder organization.

The organizer keeps the downloader cache intact, copies materials into current class folders, avoids completed-course archives, and protects manually edited files from being overwritten.

## Privacy

This repository does **not** contain:

- A Canvas API token
- Downloaded course files
- Student submissions
- Local configuration or logs
- Organizer state

The Canvas token remains in macOS Keychain under `Canvas Module Downloader API Token`.

## macOS launcher

The packaged launcher targets Apple Silicon macOS. Start with:

```text
outputs/Canvas Module Downloader/Canvas Module Downloader.command
```

See its [README](outputs/Canvas%20Module%20Downloader/README.md) for setup, syncing, security, and removal instructions.

## Codex skill

Install the reusable skill with:

```zsh
cp -R outputs/canvas-quarter-organizer ~/.codex/skills/canvas-quarter-organizer
```

Then start a new Codex chat with:

```text
$canvas-quarter-organizer set up my new quarter
```

The skill previews course-to-folder mappings before applying them. Existing `Class Slides` folders are reused. For new classes, slides go under `Course Material/Class Slides`.

## Third-party downloader

The launcher bundles the unmodified Apple Silicon binary from [`aik2mlj/canvas-downloader`](https://github.com/aik2mlj/canvas-downloader) release `v0.4.2`.

- Binary SHA-256: `2fc8ee545ef018d842fe8dbfc72d3dd11c913fc1acc37963f8371e7480a3dd25`
- License: GNU GPL v3, included with the package
- Source and release details: [THIRD_PARTY.md](outputs/Canvas%20Module%20Downloader/THIRD_PARTY.md)
