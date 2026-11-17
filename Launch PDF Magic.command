#!/bin/bash
# Double-click on macOS to launch the local toolbench in the default browser.
set -euo pipefail
cd -- "$(dirname -- "$0")"

url="http://127.0.0.1:4173/"

is_toolbench_running() {
  local page
  page="$(curl --silent --fail --max-time 2 "${url}api/status" 2>/dev/null)" || return 1
  [[ "$page" == *'"upload": true'* ]]
}

if is_toolbench_running; then
  echo "PDF Magic is already running. Opening it in your browser."
  open "$url"
  exit 0
fi

if lsof -nP -iTCP:4173 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port 4173 is occupied by an older static server or another application."
  echo "Stop it with Control-C, then start PDF Magic again to enable file imports."
  exit 1
fi

echo "Starting PDF Magic on $url"
python3 serve.py &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT

for ((attempt = 0; attempt < 50; attempt++)); do
  if is_toolbench_running; then
    open "$url"
    echo "The site is open. Leave this window running; press Control-C to stop."
    wait "$server_pid"
    exit 0
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "The local server exited before it could start."
    wait "$server_pid"
    exit 1
  fi
  sleep 0.1
done

echo "The local server did not become ready on port 4173."
exit 1
