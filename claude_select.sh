# Persistent claude sessions via a dedicated tmux socket, grouped by directory.
# Closing the terminal does NOT kill claude (the tmux server keeps running).
# Each session is tagged with the dir it was started in (session option @cs_dir);
# the picker and `cs ls` only show sessions belonging to the current $PWD, and the
# session name is prefixed with that dir's basename.
# Source this file from ~/.bash_aliases; it defines `claude_select` (alias: cs).
claude_select() {
  local sock="/tmp/claude-tmux-${UID}.sock"
  local tmux="tmux -L claude -S $sock"
  local cmd="${1:-pick}"; shift 2>/dev/null

  # sanitize a string into a tmux-safe token ('.' and ':' are forbidden by tmux)
  _cs_san() { printf '%s' "$1" | tr -c 'A-Za-z0-9_-' '_'; }

  # every session as "name<TAB>dir" (dir empty for legacy/untagged sessions)
  _cs_all() { $tmux ls -F '#{session_name}	#{@cs_dir}' 2>/dev/null; }

  # names of sessions started in the current directory
  _cs_here() { _cs_all | awk -F'\t' -v d="$PWD" '$2==d{print $1}'; }

  _cs_attach() {
    local name="$1"
    if [ -n "$TMUX" ]; then
      $tmux switch-client -t "=$name" 2>/dev/null || $tmux attach -t "=$name"
    else
      $tmux attach -t "=$name"
    fi
  }

  _cs_new() {
    local dir="${2:-$PWD}"
    dir=$(cd "$dir" 2>/dev/null && pwd) || { echo "cs: dir not found: ${2:-$PWD}" >&2; return 1; }
    local tag; tag=$(_cs_san "$(basename "$dir")")
    local base="$tag"; [ -n "$1" ] && base="${tag}-$(_cs_san "$1")"
    local name="$base" i=2
    while $tmux has-session -t "=$name" 2>/dev/null; do
      name="${base}-${i}"; i=$((i + 1))
    done
    # note: set-option's -t is a target-pane and does NOT accept the '=' exact
    # prefix; the plain name resolves to the just-created session unambiguously.
    $tmux new-session -d -s "$name" -c "$dir" "claude" \
      && $tmux set-option -t "$name" @cs_dir "$dir" || return 1
    if [ -n "$TMUX" ]; then $tmux switch-client -t "=$name"; else $tmux attach -t "=$name"; fi
  }

  case "$cmd" in
    ls)
      if [ "$1" = "all" ] || [ "$1" = "-a" ]; then
        _cs_all | awk -F'\t' '{printf "%-28s %s\n", $1, ($2==""?"(untagged)":$2)}'
        [ -n "$(_cs_all)" ] || echo "cs: no sessions"
      else
        local here; here=$(_cs_here)
        [ -n "$here" ] && printf '%s\n' "$here" || echo "cs: no sessions in $PWD"
      fi ;;
    new)  _cs_new "$@" ;;
    kill) [ -z "$1" ] && { echo "usage: cs kill <name>"; return 2; }
          $tmux kill-session -t "=$1" ;;
    help|-h|--help)
          cat <<'EOF'
cs                     list+pick sessions started in this dir (or [new])
cs new [label] [dir]   start a session; name = <dir-basename>[-label]
                       default dir: $PWD
cs ls                  list sessions started in this dir
cs ls all              list every session across all dirs
cs kill <name>         kill a session by name
EOF
          ;;
    pick)
      local sessions=() line
      while IFS= read -r line; do sessions+=("$line"); done < <(_cs_here)
      sessions+=("[new]")
      PS3="claude session [$PWD]> "
      select choice in "${sessions[@]}"; do
        [ -z "$choice" ] && continue
        if [ "$choice" = "[new]" ]; then _cs_new; else _cs_attach "$choice"; fi
        break
      done ;;
    *)
      if _cs_all | awk -F'\t' -v n="$cmd" '$1==n{f=1} END{exit !f}'; then _cs_attach "$cmd"
      else echo "cs: unknown subcommand or session: $cmd"; return 2; fi ;;
  esac
}
