#!/bin/zsh

emulate -R zsh
setopt pipe_fail no_aliases

typeset -r CONFIG_ROOT="${CANVAS_MODULE_DOWNLOADER_CONFIG_DIR:-${HOME}/Library/Application Support/Canvas Module Downloader}"
typeset -r RUNTIME_HELPER="${CONFIG_ROOT}/runtime/lib/common.zsh"

if [[ ! -r "$RUNTIME_HELPER" ]]; then
  print -u2 -r -- "Canvas Module Downloader is not installed at: $CONFIG_ROOT"
  exit 1
fi

source "$RUNTIME_HELPER"
cmdl_verify_binary || exit 1

usage() {
  print -r -- "Usage: ${0:t} list|selected|select COURSE...|sync|refresh|destination"
}

run_selected() {
  local mode="$1"
  cmdl_load_course_args || return 1
  local destination
  destination="$(cmdl_destination)" || return 1
  /bin/mkdir -p "$destination" || return 1

  case "$mode" in
    preview)
      cmdl_run_downloader --destination-folder "$destination" --no-submissions --no-raw --dry-run "${CMDL_COURSE_ARGS[@]}"
      ;;
    sync)
      cmdl_run_downloader --destination-folder "$destination" --no-submissions --no-raw "${CMDL_COURSE_ARGS[@]}"
      ;;
    refresh)
      cmdl_run_downloader --destination-folder "$destination" --no-submissions --no-raw --download-newer "${CMDL_COURSE_ARGS[@]}"
      ;;
  esac
}

save_selection() {
  (( $# > 0 )) || {
    print -u2 -r -- "Provide at least one exact Canvas course code."
    return 1
  }

  local course
  for course in "$@"; do
    if [[ -z "$course" || "$course" == -* || "$course" == *$'\n'* || "$course" == *$'\r'* ]]; then
      print -u2 -r -- "Invalid course code: $course"
      return 1
    fi
  done

  cmdl_ensure_config_root || return 1
  local selection_tmp
  selection_tmp="$(/usr/bin/mktemp "${TMPDIR:-/tmp}/canvas-courses.XXXXXX")" || return 1
  /bin/chmod 600 "$selection_tmp"

  for course in "$@"; do
    print -r -- "$course"
  done >| "$selection_tmp"

  if [[ -f "$CMDL_COURSES_FILE" ]]; then
    /bin/cp -f "$CMDL_COURSES_FILE" "${CMDL_COURSES_FILE}.previous" || {
      /bin/rm -f -- "$selection_tmp"
      return 1
    }
    /bin/chmod 600 "${CMDL_COURSES_FILE}.previous" 2>/dev/null || true
  fi

  /bin/mv -f -- "$selection_tmp" "$CMDL_COURSES_FILE"
  /bin/chmod 600 "$CMDL_COURSES_FILE"
  print -r -- "Saved ${#} Canvas course selection(s)."
}

case "${1:-}" in
  list)
    destination="$(cmdl_destination)" || exit 1
    cmdl_run_downloader --destination-folder "$destination" --no-submissions --no-raw --dry-run
    ;;
  selected)
    run_selected preview
    ;;
  select)
    shift
    save_selection "$@"
    ;;
  sync)
    run_selected sync
    ;;
  refresh)
    run_selected refresh
    ;;
  destination)
    cmdl_destination
    ;;
  *)
    usage
    exit 2
    ;;
esac
