#!/bin/zsh

emulate -R zsh
setopt pipe_fail no_aliases

typeset -gr CMDL_MAIN_DIR="${0:A:h}"
source "${CMDL_MAIN_DIR}/lib/common.zsh"

pause_for_user() {
  print
  read -r "?Press Return to continue..."
}

configure_access() {
  cmdl_ensure_config_root || return 1

  local saved_url="https://canvas.asu.edu"
  if [[ -f "$CMDL_URL_FILE" ]]; then
    IFS= read -r saved_url < "$CMDL_URL_FILE" || true
  fi

  print
  print -r -- "Canvas URL [$saved_url]:"
  local canvas_url
  IFS= read -r canvas_url
  [[ -z "$canvas_url" ]] && canvas_url="$saved_url"
  canvas_url="${canvas_url%/}"

  if [[ "$canvas_url" != https://* || "$canvas_url" == *[[:space:]]* ]]; then
    cmdl_error "Enter a complete HTTPS address, such as https://canvas.asu.edu"
    return 1
  fi

  print
  print -r -- "Canvas will show the token only once. Paste it into the secure Keychain prompt."
  print -r -- "The token will not appear while you type or paste."
  print

  /usr/bin/security add-generic-password \
    -U \
    -a "$canvas_url" \
    -s "$CMDL_KEYCHAIN_SERVICE" \
    -l "Canvas API token for $canvas_url" \
    -w || {
      cmdl_error "The token was not saved."
      return 1
    }

  local old_umask
  old_umask="$(umask)"
  umask 077
  print -r -- "$canvas_url" >| "$CMDL_URL_FILE"
  umask "$old_umask"
  /bin/chmod 600 "$CMDL_URL_FILE" 2>/dev/null || true

  print
  cmdl_info "Saved the Canvas URL and stored the token in macOS Keychain."
  cmdl_info "Testing access and listing available courses..."
  print
  local destination
  destination="$(cmdl_destination)" || return 1
  /bin/mkdir -p "$destination"
  cmdl_run_downloader --destination-folder "$destination" --no-submissions
}

choose_courses() {
  print
  cmdl_info "Available courses:"
  print
  local destination
  destination="$(cmdl_destination)" || return 1
  /bin/mkdir -p "$destination"
  cmdl_run_downloader --destination-folder "$destination" --no-submissions || return 1

  print
  print -r -- "Enter each exact course code or course name shown above."
  print -r -- "Press Return on an empty line when finished."
  print

  cmdl_ensure_config_root || return 1
  local temporary_courses
  temporary_courses="$(/usr/bin/mktemp "${TMPDIR:-/tmp}/canvas-courses.XXXXXX")" || return 1
  local course count=0
  while true; do
    read -r "course?Course $(( count + 1 )): "
    [[ -z "$course" ]] && break
    print -r -- "$course" >> "$temporary_courses"
    (( count += 1 ))
  done

  if (( count == 0 )); then
    /bin/rm -f -- "$temporary_courses"
    cmdl_error "No courses were entered. The previous selection was preserved."
    return 1
  fi

  /bin/chmod 600 "$temporary_courses"
  /bin/mv -f -- "$temporary_courses" "$CMDL_COURSES_FILE"
  cmdl_info "Saved $count course selection(s)."
}

choose_destination() {
  cmdl_ensure_config_root || return 1
  local current_destination
  current_destination="$(cmdl_destination)" || return 1
  print
  print -r -- "Download folder [$current_destination]:"
  local destination
  IFS= read -r destination
  [[ -z "$destination" ]] && destination="$current_destination"
  if [[ "$destination" != /* ]]; then
    cmdl_error "Use a full path beginning with /."
    return 1
  fi
  /bin/mkdir -p "$destination" || return 1
  print -r -- "$destination" >| "$CMDL_DESTINATION_FILE"
  /bin/chmod 600 "$CMDL_DESTINATION_FILE" 2>/dev/null || true
  cmdl_info "Downloads will be saved to: $destination"
}

run_selected_courses() {
  local mode="$1"
  cmdl_load_course_args || return 1
  local destination
  destination="$(cmdl_destination)" || return 1
  /bin/mkdir -p "$destination" || return 1

  case "$mode" in
    preview)
      cmdl_run_downloader --destination-folder "$destination" --no-submissions --dry-run "${CMDL_COURSE_ARGS[@]}"
      ;;
    download)
      cmdl_run_downloader --destination-folder "$destination" --no-submissions "${CMDL_COURSE_ARGS[@]}"
      ;;
    refresh)
      print
      print -r -- "This may overwrite local files when Canvas has a newer copy."
      read -r "confirm?Type REFRESH to continue: "
      [[ "$confirm" == "REFRESH" ]] || {
        cmdl_info "Refresh cancelled."
        return 0
      }
      cmdl_run_downloader --destination-folder "$destination" --no-submissions --download-newer "${CMDL_COURSE_ARGS[@]}"
      ;;
  esac
}

install_daily_sync() {
  cmdl_load_course_args || return 1
  cmdl_canvas_url >/dev/null || return 1
  cmdl_ensure_config_root || return 1

  # LaunchAgents cannot reliably execute scripts stored in privacy-protected
  # Documents folders. Install a private runtime under Application Support.
  local runtime_root="${CMDL_CONFIG_ROOT}/runtime"
  /bin/mkdir -p "${runtime_root}/bin" "${runtime_root}/lib" "${runtime_root}/scripts" || {
    cmdl_error "Could not create the background runtime folder."
    return 1
  }
  /bin/cp -f "$CMDL_BINARY" "${runtime_root}/bin/canvas-downloader" || return 1
  /bin/cp -f "${CMDL_APP_DIR}/lib/common.zsh" "${runtime_root}/lib/common.zsh" || return 1
  /bin/cp -f "${CMDL_APP_DIR}/scripts/background-sync.zsh" "${runtime_root}/scripts/background-sync.zsh" || return 1
  /bin/chmod 700 "$runtime_root" "${runtime_root}/bin" "${runtime_root}/lib" "${runtime_root}/scripts"
  /bin/chmod 700 "${runtime_root}/bin/canvas-downloader" "${runtime_root}/lib/common.zsh" "${runtime_root}/scripts/background-sync.zsh"

  local hour minute
  print
  read -r "hour?Daily sync hour, 0-23 [7]: "
  [[ -z "$hour" ]] && hour=7
  read -r "minute?Minute, 0-59 [0]: "
  [[ -z "$minute" ]] && minute=0
  if [[ "$hour" != <0-23> || "$minute" != <0-59> ]]; then
    cmdl_error "Enter a valid 24-hour time."
    return 1
  fi

  /bin/mkdir -p "${HOME}/Library/LaunchAgents" "$CMDL_LOG_DIR"
  local temporary_plist
  temporary_plist="$(/usr/bin/mktemp "${TMPDIR:-/tmp}/canvas-launch-agent.XXXXXX")" || return 1
  {
    print -r -- '<?xml version="1.0" encoding="UTF-8"?>'
    print -r -- '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    print -r -- '<plist version="1.0">'
    print -r -- '<dict>'
    print -r -- '  <key>Label</key>'
    print -r -- "  <string>$(cmdl_xml_escape "$CMDL_AGENT_LABEL")</string>"
    print -r -- '  <key>ProgramArguments</key>'
    print -r -- '  <array>'
    print -r -- '    <string>/bin/zsh</string>'
    print -r -- "    <string>$(cmdl_xml_escape "${runtime_root}/scripts/background-sync.zsh")</string>"
    print -r -- '  </array>'
    print -r -- '  <key>StartCalendarInterval</key>'
    print -r -- '  <dict>'
    print -r -- '    <key>Hour</key>'
    print -r -- "    <integer>$hour</integer>"
    print -r -- '    <key>Minute</key>'
    print -r -- "    <integer>$minute</integer>"
    print -r -- '  </dict>'
    print -r -- '  <key>StandardOutPath</key>'
    print -r -- "  <string>$(cmdl_xml_escape "${CMDL_LOG_DIR}/daily-sync.log")</string>"
    print -r -- '  <key>StandardErrorPath</key>'
    print -r -- "  <string>$(cmdl_xml_escape "${CMDL_LOG_DIR}/daily-sync-errors.log")</string>"
    print -r -- '  <key>ProcessType</key>'
    print -r -- '  <string>Background</string>'
    print -r -- '</dict>'
    print -r -- '</plist>'
  } >| "$temporary_plist"

  /usr/bin/plutil -lint "$temporary_plist" >/dev/null || {
    /bin/rm -f -- "$temporary_plist"
    cmdl_error "Could not create a valid daily-sync schedule."
    return 1
  }

  /bin/chmod 600 "$temporary_plist"
  /bin/launchctl bootout "gui/$(/usr/bin/id -u)" "$CMDL_AGENT_FILE" >/dev/null 2>&1 || true
  /bin/mv -f -- "$temporary_plist" "$CMDL_AGENT_FILE"
  /bin/launchctl bootstrap "gui/$(/usr/bin/id -u)" "$CMDL_AGENT_FILE" || {
    cmdl_error "The schedule file was created, but macOS did not activate it."
    return 1
  }

  cmdl_info "Daily new-file sync enabled for $(printf '%02d:%02d' "$hour" "$minute")."
  cmdl_info "It will not overwrite existing local files."
}

disable_daily_sync() {
  /bin/launchctl bootout "gui/$(/usr/bin/id -u)" "$CMDL_AGENT_FILE" >/dev/null 2>&1 || true
  if [[ -f "$CMDL_AGENT_FILE" ]]; then
    /bin/rm -f -- "$CMDL_AGENT_FILE"
  fi
  cmdl_info "Daily syncing is disabled."
}

remove_saved_token() {
  local canvas_url
  canvas_url="$(cmdl_canvas_url)" || return 1
  print
  read -r "confirm?Type REMOVE to delete the saved Canvas token: "
  [[ "$confirm" == "REMOVE" ]] || {
    cmdl_info "Token removal cancelled."
    return 0
  }
  /usr/bin/security delete-generic-password -a "$canvas_url" -s "$CMDL_KEYCHAIN_SERVICE" >/dev/null || {
    cmdl_error "No matching token was found in Keychain."
    return 1
  }
  cmdl_info "Removed the Canvas token from Keychain."
}

main_menu() {
  while true; do
    clear
    print -r -- "Canvas Module Downloader"
    print -r -- "========================"
    print
    print -r -- "1. Set up or replace Canvas access"
    print -r -- "2. Choose courses"
    print -r -- "3. Preview downloads"
    print -r -- "4. Download new course files now"
    print -r -- "5. Refresh files updated by instructors"
    print -r -- "6. Change download folder"
    print -r -- "7. Enable daily new-file sync"
    print -r -- "8. Disable daily sync"
    print -r -- "9. Remove saved Canvas token"
    print -r -- "Q. Quit"
    print

    local choice
    read -r "choice?Choose an option: "
    case "${choice:l}" in
      1) configure_access; pause_for_user ;;
      2) choose_courses; pause_for_user ;;
      3) run_selected_courses preview; pause_for_user ;;
      4) run_selected_courses download; pause_for_user ;;
      5) run_selected_courses refresh; pause_for_user ;;
      6) choose_destination; pause_for_user ;;
      7) install_daily_sync; pause_for_user ;;
      8) disable_daily_sync; pause_for_user ;;
      9) remove_saved_token; pause_for_user ;;
      q) return 0 ;;
      *) cmdl_error "Choose 1-9 or Q."; pause_for_user ;;
    esac
  done
}

cmdl_verify_binary || {
  pause_for_user
  exit 1
}
main_menu
