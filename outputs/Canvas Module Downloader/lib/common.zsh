#!/bin/zsh

# Shared runtime helpers for Canvas Module Downloader.
# The Canvas token is read from macOS Keychain and sent to the downloader
# through a temporary named pipe. It is never written to a regular file.

emulate -R zsh
setopt pipe_fail no_aliases

typeset -gr CMDL_LIB_DIR="${${(%):-%N}:A:h}"
typeset -gr CMDL_APP_DIR="${CMDL_LIB_DIR:h}"
typeset -gr CMDL_BINARY="${CMDL_APP_DIR}/bin/canvas-downloader"
typeset -gr CMDL_EXPECTED_BINARY_SHA256="2fc8ee545ef018d842fe8dbfc72d3dd11c913fc1acc37963f8371e7480a3dd25"
typeset -gr CMDL_CONFIG_ROOT="${CANVAS_MODULE_DOWNLOADER_CONFIG_DIR:-${HOME}/Library/Application Support/Canvas Module Downloader}"
typeset -gr CMDL_URL_FILE="${CMDL_CONFIG_ROOT}/canvas-url.txt"
typeset -gr CMDL_COURSES_FILE="${CMDL_CONFIG_ROOT}/courses.txt"
typeset -gr CMDL_DESTINATION_FILE="${CMDL_CONFIG_ROOT}/destination.txt"
typeset -gr CMDL_KEYCHAIN_SERVICE="Canvas Module Downloader API Token"
typeset -gr CMDL_AGENT_LABEL="com.local.canvas-module-downloader"
typeset -gr CMDL_AGENT_FILE="${HOME}/Library/LaunchAgents/${CMDL_AGENT_LABEL}.plist"
typeset -gr CMDL_LOG_DIR="${CMDL_CONFIG_ROOT}/logs"

typeset -ga CMDL_COURSE_ARGS

cmdl_error() {
  print -u2 -r -- "Error: $*"
}

cmdl_info() {
  print -r -- "$*"
}

cmdl_verify_binary() {
  if [[ ! -x "$CMDL_BINARY" ]]; then
    cmdl_error "The bundled downloader is missing or is not executable."
    return 1
  fi

  local actual_hash
  actual_hash="$(/usr/bin/shasum -a 256 "$CMDL_BINARY" | /usr/bin/awk '{print $1}')" || return 1
  if [[ "$actual_hash" != "$CMDL_EXPECTED_BINARY_SHA256" ]]; then
    cmdl_error "The bundled downloader failed its integrity check."
    cmdl_error "Expected: $CMDL_EXPECTED_BINARY_SHA256"
    cmdl_error "Actual:   $actual_hash"
    return 1
  fi
}

cmdl_ensure_config_root() {
  /bin/mkdir -p "$CMDL_CONFIG_ROOT" || {
    cmdl_error "Could not create the settings folder: $CMDL_CONFIG_ROOT"
    return 1
  }
  /bin/chmod 700 "$CMDL_CONFIG_ROOT" 2>/dev/null || true
}

cmdl_canvas_url() {
  if [[ ! -f "$CMDL_URL_FILE" ]]; then
    cmdl_error "Canvas access is not configured. Choose option 1 first."
    return 1
  fi

  local canvas_url
  IFS= read -r canvas_url < "$CMDL_URL_FILE" || return 1
  canvas_url="${canvas_url%/}"
  if [[ "$canvas_url" != https://* ]]; then
    cmdl_error "The saved Canvas URL is invalid. Choose option 1 to replace it."
    return 1
  fi
  print -r -- "$canvas_url"
}

cmdl_destination() {
  local destination="${HOME}/Documents/Canvas Downloads"
  if [[ -f "$CMDL_DESTINATION_FILE" ]]; then
    IFS= read -r destination < "$CMDL_DESTINATION_FILE" || true
  fi
  if [[ "$destination" != /* ]]; then
    cmdl_error "The saved download folder must be an absolute path."
    return 1
  fi
  print -r -- "$destination"
}

cmdl_load_course_args() {
  CMDL_COURSE_ARGS=()
  if [[ ! -s "$CMDL_COURSES_FILE" ]]; then
    cmdl_error "No courses are selected. Choose option 2 first."
    return 1
  fi

  CMDL_COURSE_ARGS=(-c)
  local course
  while IFS= read -r course || [[ -n "$course" ]]; do
    [[ -z "$course" ]] && continue
    CMDL_COURSE_ARGS+=("$course")
  done < "$CMDL_COURSES_FILE"

  if (( ${#CMDL_COURSE_ARGS[@]} < 2 )); then
    cmdl_error "No valid courses were found in the saved selection."
    return 1
  fi
}

cmdl_toml_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  print -rn -- "$value"
}

cmdl_cleanup_runtime() {
  local runtime_dir="$1"
  local allowed_prefix="${TMPDIR:-/tmp}/canvas-module-downloader."
  if [[ -n "$runtime_dir" && "$runtime_dir" == ${allowed_prefix}* && -d "$runtime_dir" ]]; then
    /bin/rm -f -- "$runtime_dir/config.toml"
    /bin/rmdir -- "$runtime_dir" 2>/dev/null || true
  fi
}

cmdl_run_downloader() {
  cmdl_verify_binary || return 1

  local canvas_url token runtime_dir config_pipe writer_pid exit_code
  canvas_url="$(cmdl_canvas_url)" || return 1
  token="$(/usr/bin/security find-generic-password -a "$canvas_url" -s "$CMDL_KEYCHAIN_SERVICE" -w 2>/dev/null)" || {
    cmdl_error "No Canvas token was found in Keychain. Choose option 1 first."
    return 1
  }

  runtime_dir="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/canvas-module-downloader.XXXXXX")" || {
    unset token
    cmdl_error "Could not create a protected temporary folder."
    return 1
  }
  /bin/chmod 700 "$runtime_dir"
  config_pipe="${runtime_dir}/config.toml"
  /usr/bin/mkfifo "$config_pipe" || {
    unset token
    cmdl_cleanup_runtime "$runtime_dir"
    cmdl_error "Could not create the in-memory configuration pipe."
    return 1
  }

  {
    print -r -- "canvas_url = \"$(cmdl_toml_escape "$canvas_url")\""
    print -r -- "canvas_token = \"$(cmdl_toml_escape "$token")\""
    print -r -- "no_submissions = true"
  } > "$config_pipe" &
  writer_pid=$!

  "$CMDL_BINARY" --config "$config_pipe" "$@"
  exit_code=$?

  /bin/kill "$writer_pid" 2>/dev/null || true
  wait "$writer_pid" 2>/dev/null || true
  unset token
  cmdl_cleanup_runtime "$runtime_dir"
  return "$exit_code"
}

cmdl_xml_escape() {
  local value="$1"
  value="${value//&/&amp;}"
  value="${value//</&lt;}"
  value="${value//>/&gt;}"
  value="${value//\"/&quot;}"
  value="${value//\'/&apos;}"
  print -rn -- "$value"
}
