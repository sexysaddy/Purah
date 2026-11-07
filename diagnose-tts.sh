#!/usr/bin/env bash
set -euo pipefail
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/purah/config.toml"
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

printf '\n=== PURAH TTS diagnostic ===\n'
if [[ -f "$CONFIG" ]]; then
  python3 - "$CONFIG" <<'PY'
import sys, tomllib
from pathlib import Path
path = Path(sys.argv[1])
data = tomllib.loads(path.read_text(encoding='utf-8'))
tts = data.get('tts', {})
for key in ('enabled', 'engine', 'voice', 'espeak_voice', 'piper_model', 'kokoro_model_dir', 'warmup_on_start'):
    if key in tts:
        print(f"config tts.{key} = {tts[key]}")
PY
else
  echo "Config not found: $CONFIG"
fi

printf '\n=== TTS-related environment in this shell ===\n'
env | grep -E '^(PURAH_TTS_ENGINE|CHOOM_TTS_ENGINE|PURAH_KOKORO_WARMUP_ON_START)=' || echo '(no TTS engine overrides exported in current shell)'
printf '\n=== Running PURAH / Quickshell processes (RSS in KiB) ===\n'
ps -eo pid,ppid,rss,etimes,args --sort=-rss | grep -E '[m]ain.py --tray|[q]s -p .*/quickshell/purah' || true
printf '\n=== TTS environment inherited by a running PURAH process ===\n'
found=0
while read -r pid; do
  [[ -n "$pid" && -r "/proc/$pid/environ" ]] || continue
  found=1
  echo "PID $pid:"
  tr '\0' '\n' < "/proc/$pid/environ" | grep -E '^(PURAH_TTS_ENGINE|CHOOM_TTS_ENGINE|PURAH_KOKORO_WARMUP_ON_START)=' || echo '  (no related variables in process environment)'
done < <(pgrep -f '[/]main.py --tray' || true)
[[ "$found" -eq 1 ]] || echo '(no matching running PURAH --tray process)'
printf '\nRSS is approximate. For exact engine, check startup log line: PURAH ... TTS ... engine=...\n'
