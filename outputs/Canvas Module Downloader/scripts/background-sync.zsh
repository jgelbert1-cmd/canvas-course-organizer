#!/bin/zsh

emulate -R zsh
setopt pipe_fail no_aliases

typeset -gr CMDL_SCRIPT_DIR="${0:A:h}"
source "${CMDL_SCRIPT_DIR}/../lib/common.zsh"

cmdl_load_course_args || exit 1
typeset destination
destination="$(cmdl_destination)" || exit 1
/bin/mkdir -p "$destination" || exit 1

print -r -- "[$(/bin/date '+%Y-%m-%d %H:%M:%S')] Starting Canvas new-file sync"
cmdl_run_downloader \
  --destination-folder "$destination" \
  --no-submissions \
  "${CMDL_COURSE_ARGS[@]}"
exit_code=$?
print -r -- "[$(/bin/date '+%Y-%m-%d %H:%M:%S')] Finished with status $exit_code"
exit "$exit_code"
