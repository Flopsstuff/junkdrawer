# Persistent claude sessions via dedicated tmux socket.
# Closing the terminal does NOT kill claude (tmux server keeps running).
# Source this file from ~/.bash_aliases — it defines a single function `cs`.
cs() {
  local sock="/tmp/claude-tmux-${UID}.sock"
  local tmux="tmux -L claude -S $sock"
  local cmd="${1:-pick}"; shift 2>/dev/null

  _cs_list() { $tmux ls 2>/dev/null; }

  _cs_attach() {
    local name="$1"
    if [ -n "$TMUX" ]; then
      $tmux switch-client -t "$name" 2>/dev/null || $tmux attach -t "$name"
    else
      $tmux attach -t "$name"
    fi
  }

  _cs_new() {
    local dir="${2:-$PWD}"
    [ -d "$dir" ] || { echo "cs: dir not found: $dir" >&2; return 1; }
    local raw="${1:-$(basename "$dir")}"
    local base; base=$(printf '%s' "$raw" | tr -c 'A-Za-z0-9._-' '_')
    local name="$base" i=2
    while $tmux has-session -t "=$name" 2>/dev/null; do
      name="${base}-${i}"; i=$((i + 1))
    done
    if [ -n "$TMUX" ]; then
      $tmux new-session -d -s "$name" -c "$dir" "claude" \
        && $tmux switch-client -t "$name"
    else
      $tmux new-session -s "$name" -c "$dir" "claude"
    fi
  }

  case "$cmd" in
    ls)   _cs_list || echo "cs: no sessions" ;;
    new)  _cs_new "$@" ;;
    kill) [ -z "$1" ] && { echo "usage: cs kill <name>"; return 2; }
          $tmux kill-session -t "$1" ;;
    help|-h|--help)
          echo "cs              list+pick (or [new])"
          echo "cs new [n] [d]  start new session"
          echo "                default name: basename of dir"
          echo "                default dir:  \$PWD"
          echo "cs ls           list sessions"
          echo "cs kill <n>     kill a session"
          ;;
    pick)
      local sessions=() line
      while IFS= read -r line; do sessions+=("${line%%:*}"); done < <(_cs_list)
      sessions+=("[new]")
      PS3="claude session> "
      select choice in "${sessions[@]}"; do
        [ -z "$choice" ] && continue
        if [ "$choice" = "[new]" ]; then _cs_new; else _cs_attach "$choice"; fi
        break
      done ;;
    *)
      if _cs_list | grep -q "^${cmd}:"; then _cs_attach "$cmd"
      else echo "cs: unknown subcommand or session: $cmd"; return 2; fi ;;
  esac
}
