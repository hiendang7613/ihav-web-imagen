#!/bin/sh
# ihav-web-imagen: install the plugin into Claude Code and Codex with their own plugin commands.
#   curl -fsSL https://raw.githubusercontent.com/hiendang7613/ihav-web-imagen/main/install.sh | sh
# Re-run it to update. It only calls `claude plugin ...` and `codex plugin ...`.
# From a clone it installs that clone; set IHAV_WEB_IMAGEN_SOURCE (owner/repo, a git URL or a path) to choose another source.
set -u
NAME=ihav-web-imagen
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd)
if [ -n "${IHAV_WEB_IMAGEN_SOURCE:-}" ]; then SRC="$IHAV_WEB_IMAGEN_SOURCE"
elif [ -f "$HERE/.claude-plugin/marketplace.json" ]; then SRC="$HERE"   # run from a clone (a piped run has no file beside it)
else SRC="hiendang7613/ihav-web-imagen"; fi
LOG=$(mktemp 2>/dev/null || echo /tmp/ihav-web-imagen-install.log)
failed=0
found=0

step() { printf '\n$ %s\n' "$*" >> "$LOG"; "$@" >> "$LOG" 2>&1; }

install_with() {                       # install_with <cli> <install verb> <update step> <label> <use-line>
  cli="$1"; verb="$2"; refresh="$3"; label="$4"; use="$5"
  command -v "$cli" >/dev/null 2>&1 || return 0
  found=1
  printf '%s: installing...\n' "$label"
  if step "$cli" plugin marketplace add "$SRC" \
     && { step "$cli" plugin marketplace "$refresh" "$NAME" || true; } \
     && step "$cli" plugin "$verb" "$NAME@$NAME"; then
    [ "$cli" = claude ] && { step claude plugin update "$NAME@$NAME" || true; }      # an already-installed plugin keeps its cached version until updated
    printf '  installed (alpha: until the browser runtime is set up it stops with "runtime is missing", see the README, Status).\n  Use it: %s\n' "$use"
  else
    printf '  failed. The last lines of what it said:\n'
    tail -n 6 "$LOG" | sed 's/^/    /'
    printf '  Run it yourself to see all of it:\n    %s plugin marketplace add '"'"'%s'"'"' && %s plugin %s %s@%s\n' "$cli" "$SRC" "$cli" "$verb" "$NAME" "$NAME"
    failed=1
  fi
}

install_with claude install update  "Claude Code" "/$NAME:imagine a red fox"
install_with codex  add     upgrade "Codex"       "\$$NAME:$NAME a red fox"

if [ "$found" = 0 ]; then
  echo "Neither the claude nor the codex command was found on your PATH. Install Claude Code or Codex first, then run this again."
  exit 1
fi
[ "$failed" = 0 ] || exit 1
echo
echo "Next: open a new session. This is an alpha: v0.1 runs only where its browser runtime is installed (README, Status); nothing is sent unless you ask."
