#!/bin/zsh

emulate -R zsh
setopt pipe_fail no_aliases

typeset -gr CMDL_SCRIPT_DIR="${0:A:h}"
source "${CMDL_SCRIPT_DIR}/../lib/common.zsh"

cmdl_load_course_args || exit 1
typeset destination
destination="$(cmdl_destination)" || exit 1
/bin/mkdir -p "$destination" || exit 1
/bin/mkdir -p "$CMDL_LOG_DIR" || exit 1

typeset marker="${CMDL_LOG_DIR}/.sync-start"
: > "$marker"

print -r -- "[$(/bin/date '+%Y-%m-%d %H:%M:%S')] Starting Canvas new-file sync"
cmdl_run_downloader \
  --destination-folder "$destination" \
  --no-submissions \
  "${CMDL_COURSE_ARGS[@]}"
exit_code=$?
print -r -- "[$(/bin/date '+%Y-%m-%d %H:%M:%S')] Finished with status $exit_code"

# Files the downloader wrote during this run.
typeset -i new_files=0
if (( exit_code == 0 )); then
  new_files=$(/usr/bin/find "$destination" -type f -newer "$marker" ! -name '.*' ! -name '*.part' 2>/dev/null | /usr/bin/wc -l | /usr/bin/tr -d ' ')
fi

# Optional: file the new downloads into class folders. The organizer refuses to
# apply when any course has no class folder yet, so it never has to guess.
typeset note="" organized=""
typeset auto_file="${CMDL_CONFIG_ROOT}/auto-organize.txt"
typeset organizer="${CMDL_SCRIPT_DIR}/organize_canvas_files.py"
if (( exit_code == 0 )) && [[ -s "$auto_file" && -f "$organizer" ]]; then
  typeset classes_root course out
  typeset -i organizer_code
  classes_root="$(/usr/bin/head -n 1 "$auto_file")"
  typeset -a organizer_args
  organizer_args=(--classes-root "$classes_root" --downloads-root "$destination")
  while IFS= read -r course || [[ -n "$course" ]]; do
    [[ -n "$course" ]] && organizer_args+=(--course "$course")
  done < "$CMDL_COURSES_FILE"
  print -r -- "[$(/bin/date '+%Y-%m-%d %H:%M:%S')] Organizing into $classes_root"
  out="$(/usr/bin/python3 "$organizer" "${organizer_args[@]}" --apply 2>&1)"
  organizer_code=$?
  print -r -- "$out"
  if (( organizer_code == 0 )); then
    organized="$(print -r -- "$out" | /usr/bin/sed -n 's/^Applied \([0-9]*\) file action.*/\1/p' | /usr/bin/tail -n 1)"
  elif (( organizer_code == 2 )); then
    note="Some courses need a class folder; open Canvas Organizer to choose them."
  else
    note="Organizing failed; see daily-sync.log."
  fi
fi

# Record the result for the app, and notify when there is something to see.
typeset summary
if (( exit_code != 0 )); then
  summary="Canvas sync failed (status $exit_code)."
elif [[ -n "$organized" ]]; then
  summary="$new_files new file(s) downloaded, $organized filed into class folders."
else
  summary="$new_files new file(s) downloaded."
fi
[[ -n "$note" ]] && summary="$summary $note"

/usr/bin/python3 -c '
import json, sys, time
out, code, new, organized, note, summary = sys.argv[1:7]
json.dump({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "code": int(code), "new_files": int(new),
           "organized": int(organized) if organized else None, "note": note, "summary": summary},
          open(out, "w"))
' "${CMDL_LOG_DIR}/last-sync.json" "$exit_code" "$new_files" "$organized" "$note" "$summary"

if (( exit_code != 0 || new_files > 0 )) || [[ -n "$note" ]]; then
  /usr/bin/osascript -e 'on run argv
display notification (item 1 of argv) with title "Canvas Organizer"
end run' "$summary" >/dev/null 2>&1 || true
fi
exit "$exit_code"
