#!/usr/bin/env bash
# RSS feed generator — control menu.
#
#   ./start.sh              interactive menu
#   ./start.sh reader       start/restart/stop/status the local reader
#   ./start.sh logs         tail the troubleshooting log
#
# Every subcommand also accepts a direct action, e.g. `./start.sh enrich`,
# `./start.sh all`, `./start.sh reader restart`.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PYTHON="${PYTHON:-python3}"
READER="$DIR/scripts/start_reader.sh"

# ---- local secrets (gitignored .env) ----------------------------------------
# Export TMDB_API_KEY etc. for every pipeline step, not just enrichment.
# An already-exported value wins, so `TMDB_API_KEY=... ./start.sh enrich` still
# overrides the file.
if [ -f "$DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    if ! . "$DIR/.env"; then
        echo "Could not parse $DIR/.env — fix it or move it aside." >&2
        exit 1
    fi
    set +a
fi

# ---- logging (gitignored) ---------------------------------------------------
# Repo-local rather than /tmp, so it survives a reboot and can be attached to a
# bug report. Only the key's presence and length are logged, never its value.
LOG_DIR="$DIR/logs"
LOG_FILE="$LOG_DIR/start.log"
MAX_LOG_BYTES=$((1024 * 1024))   # rotate to start.log.1 past 1 MiB
SESSION_STARTED=""

rotate_log() {
    [ -f "$LOG_FILE" ] || return 0
    local size
    size=$(wc -c <"$LOG_FILE" 2>/dev/null || echo 0)
    [ "$size" -lt "$MAX_LOG_BYTES" ] || mv -f "$LOG_FILE" "$LOG_FILE.1"
}

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }

# Print a line to the console and append it to the log.
log() {
    printf '%s\n' "$*"
    printf '%s  %s\n' "$(timestamp)" "$*" >>"$LOG_FILE"
}

# Write a line to the log only.
log_only() { printf '%s  %s\n' "$(timestamp)" "$*" >>"$LOG_FILE"; }

has_tmdb_key() { [ -n "${TMDB_API_KEY:-}" ]; }

# Record the environment once per invocation: date, host, git sha, interpreter,
# and whether TMDb enrichment will actually run.
begin_session() {
    [ -n "$SESSION_STARTED" ] && return 0
    SESSION_STARTED=1
    mkdir -p "$LOG_DIR"
    rotate_log
    {
        printf '\n===== start.sh %s @ %s =====\n' "${CMD_LABEL:-run}" "$(timestamp)"
        printf 'host=%s user=%s cwd=%s\n' \
            "$(hostname 2>/dev/null || echo '?')" "$(id -un)" "$DIR"
        printf 'git=%s\n' \
            "$(git -C "$DIR" rev-parse --short HEAD 2>/dev/null || echo 'not-a-repo')"
        printf 'python=%s\n' "$("$PYTHON" -V 2>&1 | awk '{print $2}')"
        if has_tmdb_key; then
            printf 'TMDB_API_KEY=set (len=%d)\n' "${#TMDB_API_KEY}"
        else
            printf 'TMDB_API_KEY=NOT SET (TMDb enrichment will be skipped)\n'
        fi
    } >>"$LOG_FILE"
}

# Run a command, mirroring its output to the log and recording the exit code.
# Output is teed through a temp file rather than straight into the log so the
# ANSI colour codes stay on the console but are stripped from the file, and so
# tee cannot mask a non-zero status (hence PIPESTATUS).
run_logged() {
    local label="$1"; shift
    begin_session
    log_only "--- $label ---"
    log_only "\$ $*"
    local tmp rc
    tmp=$(mktemp)
    set +e
    "$@" 2>&1 | tee "$tmp"
    rc=${PIPESTATUS[0]}
    set -e
    sed -E $'s/\x1b\\[[0-9;]*[a-zA-Z]//g' "$tmp" >>"$LOG_FILE"
    rm -f "$tmp"
    log_only "--- $label exit=$rc ---"
    return "$rc"
}

# Run a pipeline script on the repo's PYTHONPATH, logged.
run_step() {
    local label="$1"; shift
    PYTHONPATH=. run_logged "$label" "$PYTHON" "$@"
}

tail_log() {
    if [ ! -f "$LOG_FILE" ]; then
        echo "No log yet ($LOG_FILE) — run any command first."
        return 0
    fi
    echo "=== $LOG_FILE (last 50 lines) ==="
    tail -n 50 "$LOG_FILE"
    echo
    echo "Full log: $LOG_FILE    Rotated: $LOG_FILE.1"
}

# ---- menu -------------------------------------------------------------------
show_menu() {
    echo "  RSS Feed Generator"
    echo "  ──────────────────"
    echo "  1) Generate all feeds"
    echo "  2) Enrich feeds (TMDb posters + article content)"
    echo "  3) Post-process feeds"
    echo "  4) Rebuild index"
    echo "  5) All (full pipeline)"
    echo "  6) Reader: start"
    echo "  7) Reader: restart"
    echo "  8) Reader: stop"
    echo "  9) Reader: status"
    if has_tmdb_key; then
        echo "  TMDb key: loaded from .env"
    else
        echo "  TMDb key: NOT set — enrichment will skip posters (add TMDB_API_KEY to .env)"
    fi
    echo "  Log: ${LOG_FILE#$DIR/}"
}

# Full pipeline, matching scripts/start_reader.sh regen and .github/workflows/update.yml.
run_all() {
    run_step generate scripts/generate_feeds.py
    run_step enrich   scripts/enrich_feeds.py
    run_step fix      scripts/fix_feeds.py
    run_step index    scripts/generate_index.py
}

# Forward a reader lifecycle action to scripts/start_reader.sh, logged.
reader() {
    local action="${1:-menu}"
    begin_session
    if [ -z "$action" ] || [ "$action" = "menu" ]; then
        log "reader: opening control menu"
        exec "$READER" menu
    fi
    run_logged "reader $action" "$READER" "$action"
}

do_generate() { run_step generate scripts/generate_feeds.py "$@"; }
do_enrich()   { run_step enrich   scripts/enrich_feeds.py   "$@"; }
do_fix()      { run_step fix      scripts/fix_feeds.py      "$@"; }
do_index()    { run_step index    scripts/generate_index.py "$@"; }

handle_choice() {
    case $1 in
        1) do_generate ;;
        2) do_enrich ;;
        3) do_fix ;;
        4) do_index ;;
        5) run_all ;;
        6) reader start ;;
        7) reader restart ;;
        8) reader stop ;;
        9) reader status ;;
        q|Q|"") ;;
        *) echo "Invalid" ;;
    esac
}

# ---- dispatch ---------------------------------------------------------------
# A leading "reader" keyword routes to the reader control script; everything
# else names a pipeline step directly. No args => interactive menu.
case "${1:-}" in
    -h|--help|help)
        sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
    *)
        CMD_LABEL="${1:-menu}"
        # `shift` on zero args is an error under `set -e`; the || true keeps it quiet.
        case "${1:-}" in
            generate|gen|build) shift || true; do_generate "$@" ;;
            enrich)             shift || true; do_enrich "$@" ;;
            fix|post)           shift || true; do_fix "$@" ;;
            index)              shift || true; do_index "$@" ;;
            all|pipeline)       shift || true; run_all ;;
            reader|serve)       shift || true; reader "${1:-menu}" ;;
            logs|log)           tail_log ;;
            menu)               show_menu; read -rp "  Choice [1-9, q]: " cmd; handle_choice "$cmd" ;;
            "")
                show_menu
                read -rp "  Choice [1-9, q]: " cmd
                handle_choice "$cmd"
                ;;
            *)
                printf 'Unknown command: %s\n' "$1" >&2
                printf '  Try: %s generate|enrich|fix|index|all|reader [start|restart|stop|status]|logs\n' \
                    "$(basename "$0")" >&2
                exit 2
                ;;
        esac
        ;;
esac
