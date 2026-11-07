#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
printf 'PURAH Kokoro installer 1.1.3\n'
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  printf 'PURAH: не найден %s\nСначала создай .venv и установи requirements.txt.\n' "$PYTHON" >&2
  exit 1
fi
if ! command -v espeak-ng >/dev/null 2>&1; then
  echo 'Нужен espeak-ng для фонемизации. На Arch: sudo pacman -S --needed espeak-ng' >&2
  exit 1
fi
"$PYTHON" "$ROOT/ensure_config.py"
# ru_g2p.py imports `misaki.espeak`, which calls the legacy
# EspeakWrapper.set_data_path API. Vanilla `phonemizer` 3.4 does NOT expose
# that method; use the compatible fork and remove the earlier vanilla package
# first, otherwise both distributions can overwrite the same import namespace.
if "$PYTHON" -m pip show phonemizer >/dev/null 2>&1; then
  echo 'Заменяю несовместимый vanilla phonemizer на phonemizer-fork…'
  "$PYTHON" -m pip uninstall -y phonemizer
fi
"$PYTHON" -m pip install -r "$ROOT/requirements-kokoro-ru.txt"
# Misaki 0.9.4 currently declares Python <3.14, but its G2P module used here is
# pure Python. The user's Arch venv can be Python 3.14, so only for >=3.14 bypass
# that metadata cap, then immediately validate the actual import/API before any
# model download. For officially declared versions, install normally.
if "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 14) else 1)'; then
  echo 'Python 3.14+: устанавливаю Misaki с обходом пока не обновлённого python_requires (будет проверен smoke test)…'
  "$PYTHON" -m pip install --ignore-requires-python 'misaki==0.9.4'
else
  "$PYTHON" -m pip install 'misaki==0.9.4'
fi
# Fail early when any part of the G2P/eSpeak dependency chain is missing or
# when vanilla phonemizer won the import namespace race.
"$PYTHON" - <<'PYDEP'
try:
    import misaki
    import espeakng_loader
    from phonemizer.backend.espeak.wrapper import EspeakWrapper
    if not callable(getattr(EspeakWrapper, "set_data_path", None)):
        raise RuntimeError(
            "EspeakWrapper.set_data_path отсутствует: установлен vanilla phonemizer, "
            "а нужен phonemizer-fork. Повтори ./setup_kokoro_ru.sh."
        )
    from misaki import espeak  # noqa: F401; configures bundled eSpeak data paths
except Exception as exc:
    raise SystemExit(
        "Не собралась цепочка Kokoro G2P. Нужны misaki==0.9.4, espeakng-loader "
        "и phonemizer-fork (не пакет phonemizer). Повтори ./setup_kokoro_ru.sh.\n"
        f"Подробности: {type(exc).__name__}: {exc}"
    )
print("Kokoro G2P dependencies/API: OK (misaki + espeakng-loader + phonemizer-fork)")
PYDEP
DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/purah/kokoro-ru"
mkdir -p "$DATA_ROOT"
export PURAH_KOKORO_DATA_ROOT="$DATA_ROOT"
printf '\nСкачиваю Russian Kokoro-ru ONNX, G2P-конфиг и два женских голоса Masha/Sveta…\n'
"$PYTHON" - <<'PY'
import json
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path
from huggingface_hub import hf_hub_download, snapshot_download

repo = "zaakirio/kokoro-ru"
revision = "main"
root = Path(os.environ["PURAH_KOKORO_DATA_ROOT"]).expanduser()
root.mkdir(parents=True, exist_ok=True)

# The G2P module accesses kokoro-config.json by an absolute path next to
# ru_g2p.py. Keep this as an explicit, independently validated step rather
# than relying only on the regular file manifest (older installers omitted it).
def valid_g2p_config(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < 2_000:
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        vocab = data.get("vocab")
        return isinstance(vocab, dict) and len(vocab) > 50
    except (OSError, ValueError, TypeError):
        return False

config_target = root / "kokoro-config.json"
if not valid_g2p_config(config_target):
    print("  -> kokoro-config.json (mandatory G2P vocabulary; explicit download)", flush=True)
    hub_error = None
    try:
        downloaded = Path(hf_hub_download(
            repo_id=repo,
            filename="kokoro-config.json",
            revision=revision,
            local_dir=str(root),
        ))
        # Recent huggingface_hub versions usually return root/kokoro-config.json;
        # copy from the returned path too, in case the hub returns its cache path.
        if downloaded.is_file() and downloaded.resolve() != config_target.resolve():
            shutil.copy2(downloaded, config_target)
    except Exception as exc:
        hub_error = exc
        print(f"  Hugging Face downloader failed for kokoro-config.json: {hub_error}", flush=True)

    # A plain HTTPS fallback avoids a bad local_dir/cache interaction and gives
    # a clear error if the upstream file is genuinely unavailable.
    if not valid_g2p_config(config_target):
        url = f"https://huggingface.co/{repo}/resolve/{revision}/kokoro-config.json?download=true"
        fd, temporary_name = tempfile.mkstemp(prefix="purah-kokoro-config-", suffix=".json", dir=str(root))
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "PURAH/1.1.3"})
            with urllib.request.urlopen(request, timeout=45) as response, temporary.open("wb") as out:
                shutil.copyfileobj(response, out)
            if not valid_g2p_config(temporary):
                raise RuntimeError("Downloaded file is not a valid Kokoro G2P vocabulary JSON.")
            temporary.replace(config_target)
        except Exception as fallback_error:
            temporary.unlink(missing_ok=True)
            raise SystemExit(
                "Не удалось загрузить обязательный kokoro-config.json. "
                "Проверь доступ к huggingface.co и повтори ./setup_kokoro_ru.sh. "
                f"Hub error: {type(hub_error).__name__ + ': ' + str(hub_error) if hub_error else 'download returned an invalid file'}; "
                f"HTTPS fallback: {type(fallback_error).__name__}: {fallback_error}"
            )
if not valid_g2p_config(config_target):
    raise SystemExit(f"kokoro-config.json отсутствует или некорректен: {config_target}")
config_data = json.loads(config_target.read_text(encoding="utf-8"))
print(f"  -> kokoro-config.json verified ({config_target.stat().st_size} bytes, {len(config_data['vocab'])} vocabulary symbols)", flush=True)

# Download only the runtime files needed by PURAH; do not fetch the extra
# fp32 and male checkpoints. kokoro-config.json was handled explicitly above.
files = [
    "config.json",
    "ru_g2p.py",
    "onnx/model_quantized.onnx",
    "voices/masha.bin",
    "voices/sveta.bin",
]
for filename in files:
    print(f"  -> {filename}", flush=True)
    hf_hub_download(repo_id=repo, filename=filename, revision=revision, local_dir=str(root))

# ru_g2p.py requires the repository's acute-aware eSpeak data files, not just
# the system `espeak-ng` binary. Download only this ~10 MB subtree: a full
# snapshot would also fetch the multi-hundred-MB PyTorch checkpoints.
print("  -> espeak-data/ (acute-aware dictionaries and voice data; ~10 MB)", flush=True)
snapshot_download(
    repo_id=repo,
    repo_type="model",
    revision=revision,
    local_dir=str(root),
    allow_patterns=["espeak-data/*", "espeak-data/**"],
    max_workers=4,
)

checks = {
    root / "config.json": 500,
    # ru_g2p.py loads this model vocabulary/config independently of config.json.
    root / "kokoro-config.json": 2_000,
    root / "ru_g2p.py": 500,
    root / "onnx/model_quantized.onnx": 50_000_000,
    root / "voices/masha.bin": 100_000,
    root / "voices/sveta.bin": 100_000,
}
for path, minimum in checks.items():
    if not path.is_file() or path.stat().st_size < minimum:
        raise SystemExit(f"Download check failed for {path}; expected at least {minimum} bytes")
try:
    g2p_config = json.loads((root / "kokoro-config.json").read_text(encoding="utf-8"))
except Exception as exc:
    raise SystemExit(f"Invalid kokoro-config.json after download: {exc}")
if not isinstance(g2p_config.get("vocab"), dict) or len(g2p_config["vocab"]) <= 50:
    raise SystemExit("kokoro-config.json does not contain the expected G2P vocab mapping")

espeak_root = root / "espeak-data"
if not espeak_root.is_dir():
    raise SystemExit(
        f"Missing {espeak_root}. ru_g2p.py needs the acute-aware espeak-data/ "
        "directory from zaakirio/kokoro-ru; rerun ./setup_kokoro_ru.sh with internet access."
    )
for required_dir in (espeak_root / "lang", espeak_root / "voices"):
    if not required_dir.is_dir():
        raise SystemExit(f"Incomplete eSpeak data: missing directory {required_dir}")
file_count = sum(1 for item in espeak_root.rglob("*") if item.is_file())
total_bytes = sum(item.stat().st_size for item in espeak_root.rglob("*") if item.is_file())
if file_count < 100 or total_bytes < 5_000_000:
    raise SystemExit(
        f"Incomplete eSpeak data at {espeak_root}: {file_count} files / "
        f"{total_bytes} bytes; expected the complete ~10 MB repository subtree. "
        "Rerun ./setup_kokoro_ru.sh."
    )
print(f"eSpeak data verified: {file_count} files, {total_bytes / 1_000_000:.1f} MB")
print(f"Kokoro-ru assets installed in {root}")
PY
printf '\nПроверяю, что голос действительно синтезирует русскую речь…\n'
"$PYTHON" - "$ROOT" "$DATA_ROOT" <<'PY'
import sys, tempfile
from pathlib import Path
project_root = Path(sys.argv[1]).resolve()
model_dir = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(project_root))
from kokoro_ru_tts import get_kokoro_ru_engine
engine = get_kokoro_ru_engine(model_dir=model_dir, voice="masha", threads=4)
with tempfile.NamedTemporaryFile(prefix="purah-kokoro-smoke-", suffix=".wav", delete=False) as f:
    audio_path = Path(f.name)
try:
    engine.synthesize_wav("Привет! Я Пура. Проверка русского голоса.", audio_path, speed=1.04)
    if not audio_path.is_file() or audio_path.stat().st_size < 1024:
        raise SystemExit("Kokoro-ru smoke test returned an empty WAV.")
    print(f"Kokoro-ru smoke test passed ({audio_path.stat().st_size} bytes).")
finally:
    audio_path.unlink(missing_ok=True)
PY
# Pick the local Russian Kokoro voice by default after the user explicitly ran this setup.
"$PYTHON" - <<'PY'
import os, re, shutil, tomllib
from pathlib import Path
config = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "purah" / "config.toml"
text = config.read_text(encoding="utf-8")
parsed = tomllib.loads(text)
if "tts" not in parsed:
    raise SystemExit("Config has no [tts] section; run python ensure_config.py again.")
backup = config.with_suffix(".toml.pre-kokoro-backup")
if not backup.exists():
    shutil.copy2(config, backup)
headers = list(re.finditer(r"(?m)^\[([^\]]+)\]\s*$", text))
header = next(m for m in headers if m.group(1).strip() == "tts")
end_match = next((m for m in headers if m.start() > header.start()), None)
end = end_match.start() if end_match else len(text)
block = text[header.start():end]
values = {
    "engine": '"kokoro_ru"',
    "kokoro_model_dir": '"' + str(Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "purah" / "kokoro-ru") + '"',
    "kokoro_voice": '"masha"',
    "kokoro_speed": "1.10",
    "kokoro_threads": "4",
    "warmup_on_start": "false",
}
for key, value in values.items():
    pat = re.compile(rf"(?m)^(\s*{re.escape(key)}\s*=\s*).*$")
    if pat.search(block):
        block = pat.sub(lambda m: m.group(1) + value, block, count=1)
    else:
        block = block.rstrip() + f"\n{key} = {value}\n"
text = text[:header.start()] + block + text[end:]
tomllib.loads(text)
config.write_text(text, encoding="utf-8")
os.chmod(config, 0o600)
print(f"Updated [tts] in {config}; previous config backed up to {backup}")
PY
printf '\nГотово. Kokoro-ru включён (женский голос Masha). Запусти PURAH заново.\n'
printf 'Голос можно поменять на "sveta", а скорость — на 0.95–1.15 в ~/.config/purah/config.toml.\n'
