#!/bin/zsh
# Double-click to open the Canvas Organizer interface in your browser.
cd "${0:A:h}" || exit 1
exec /usr/bin/python3 server.py
