# Canvas Module Downloader

This is a local macOS launcher for downloading Canvas course materials by course and module. It uses the maintained open-source `aik2mlj/canvas-downloader` engine and adds safer credential handling plus optional daily syncing.

## Start here

1. In Canvas, open **Account → Settings → Approved Integrations → + New Access Token**.
2. Generate a token and copy it. Canvas shows it only once.
3. Double-click **Canvas Module Downloader.command**.
4. Choose **1** and paste the token into the macOS Keychain prompt.
5. Choose **2** to select your courses.
6. Choose **3** to preview every planned download without downloading files.
7. Choose **4** to download.

Never send the Canvas token to anyone or paste it into a chat. Treat it like a password.

## What it downloads

- Files attached directly to modules
- PDFs, PowerPoint files, Word files, spreadsheets, and other Canvas files
- Canvas pages and files embedded inside them
- Assignments, discussions, announcements, and syllabi
- Course and module folder structure

Your own assignment submissions are intentionally excluded. External tools such as Google Drive, Kaltura, Zoom, and some Panopto configurations may require separate handling.

## Automatic syncing

Menu option **7** creates a standard macOS LaunchAgent that checks for new files once per day. Daily syncing does not overwrite files already on your Mac. Use option **5** manually when you want to replace local files with newer instructor versions.

The automation installs a private runtime under the settings folder in Application Support, because macOS does not reliably allow background agents to execute scripts from Documents. You may move this package after enabling the schedule. Re-enable the schedule after updating the package so the private runtime receives the updated files.

## Storage and privacy

- Canvas token: macOS Keychain service `Canvas Module Downloader API Token`
- Non-secret settings: `~/Library/Application Support/Canvas Module Downloader/`
- Default downloads: `~/Documents/Canvas Downloads/`
- Daily-sync logs: inside the settings folder under `logs/`

The token is read from Keychain and passed to the downloader through a protected temporary named pipe. It is not stored in the settings files, download folders, shell arguments, or logs.

Use menu option **9** to remove the token from Keychain. Use option **8** to remove the daily schedule.

## Included open-source engine

- Project: [aik2mlj/canvas-downloader](https://github.com/aik2mlj/canvas-downloader)
- Included release: `v0.4.2`, Apple Silicon (`aarch64-apple-darwin`)
- Release archive SHA-256: `d7fae89dab7339f3f38f7ddf0983eec1318ce35a914555975b2a9bf06fe895f9`
- Included binary SHA-256: `2fc8ee545ef018d842fe8dbfc72d3dd11c913fc1acc37963f8371e7480a3dd25`
- License: GPL-3.0, included as `LICENSE-GPL-3.0.txt`

The launcher checks the bundled binary's checksum before every run. The upstream binary is ad-hoc signed rather than Apple-notarized.
