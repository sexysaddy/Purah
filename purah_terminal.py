"""Background and managed-visible terminal execution for PURAH.

The GUI terminal is deliberately an opt-in mode.  Ordinary shell requests run in a
bounded subprocess and never steal focus.  When a user explicitly asks for a terminal,
PURAH starts one terminal containing a named tmux session and sends commands to that
session; tmux keeps the shell and its working directory alive between voice turns.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable


_FALSE = {"0", "false", "no", "off"}
_TMUX_SOCKET = "purah"
_SHELL_NAMES = {"bash", "zsh", "fish", "sh", "dash", "ksh", "nu", "elvish"}  # Never attach to an unrelated user tmux server.


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default).lower()).strip().casefold() not in _FALSE


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(float(os.getenv(name, str(default))))))
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_cwd(raw: str | os.PathLike[str] | None, fallback: Path) -> Path:
    value = Path(raw).expanduser() if raw else fallback
    if not value.is_absolute():
        value = fallback / value
    value = value.resolve(strict=False)
    if not value.is_dir():
        raise ValueError(f"Рабочий каталог не существует: {value}")
    return value


class TerminalManager:
    """Run shell commands without focus changes and, on request, in visible tmux."""

    def __init__(self, app_dir: Path, *, state_dir: Path | None = None,
                 popen: Callable[..., subprocess.Popen] | None = None,
                 run: Callable[..., subprocess.CompletedProcess] | None = None):
        self.app_dir = Path(app_dir).resolve()
        state_root = state_dir or Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "purah"
        self.state_dir = Path(state_root).expanduser()
        self.state_file = self.state_dir / "terminal.json"
        self._popen = popen or subprocess.Popen
        self._run = run or subprocess.run
        self._lock = threading.RLock()
        self._state = self._load_state()

    def _load_state(self) -> dict:
        try:
            item = json.loads(self.state_file.read_text(encoding="utf-8"))
            return item if isinstance(item, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_state(self) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.state_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(self.state_file)
        except OSError:
            # Terminal history is an optimization; a read-only state directory must
            # never prevent an otherwise valid command from running.
            pass

    def context(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _command_env(self) -> dict[str, str]:
        env = os.environ.copy()
        for key in list(env):
            if any(marker in key.upper() for marker in ("API_KEY", "API_HASH", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")):
                env.pop(key, None)
        venv = self.app_dir / ".venv" / "bin"
        if venv.is_dir():
            env["VIRTUAL_ENV"] = str(self.app_dir / ".venv")
            env["PATH"] = str(venv) + os.pathsep + env.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        env.pop("SUDO_ASKPASS", None)
        env.pop("SSH_ASKPASS_REQUIRE", None)
        allow_sudo = _env_bool("PURAH_TERMINAL_ALLOW_SUDO", True)
        askpass = self.app_dir / "sudo_askpass.py"
        if allow_sudo and askpass.is_file():
            env["SUDO_ASKPASS"] = str(askpass)
            env["SSH_ASKPASS_REQUIRE"] = "force"
        return env

    def _remember(self, *, mode: str, cwd: Path, command: str, output: str,
                  returncode: int | None, timed_out: bool = False, session: str = "") -> None:
        self._state.update({
            "mode": mode, "cwd": str(cwd), "last_command": command,
            "last_output": output[-12000:], "returncode": returncode,
            "timed_out": bool(timed_out), "updated_at": time.time(),
        })
        if session:
            self._state["session"] = session
        self._save_state()

    def _resolve_background_command(self, command: str, cwd: Path) -> tuple[str, Path]:
        """Make the common ``cd DIR && command`` voice pattern persistent."""
        match = re.match(r"^\s*cd\s+(?P<path>(?:'[^']*'|\"[^\"]*\"|[^;&]+?))\s*(?:&&|;)\s*(?P<rest>.+)$", command, re.S)
        if not match:
            return command, cwd
        try:
            destination = shlex.split(match.group("path"), posix=True)[0]
            next_cwd = _safe_cwd(destination, cwd)
        except (ValueError, IndexError):
            return command, cwd
        return match.group("rest").strip(), next_cwd

    def run_background(self, command: str, *, cwd: str = "", cancel_event=None) -> dict:
        with self._lock:
            try:
                previous = _safe_cwd(self._state.get("cwd", ""), self.app_dir)
            except ValueError:
                previous = self.app_dir
            workdir = _safe_cwd(cwd, previous) if cwd else previous
            command, workdir = self._resolve_background_command(command, workdir)
            timeout = _bounded_int("PURAH_TERMINAL_TIMEOUT", 60, 5, 300)
            limit = _bounded_int("PURAH_TERMINAL_MAX_OUTPUT_CHARS", 12000, 1024, 30000)
            env = self._command_env()
            proc = self._popen(
                ["/bin/bash", "--noprofile", "--norc", "-c", command],
                cwd=str(workdir), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", start_new_session=True, close_fds=True,
            )
            started = time.monotonic()
            output_box: list[str] = []
            overflow = [False]
            def _read_output() -> None:
                # Drain stdout continuously without accumulating unbounded data.
                # A noisy process (`yes`, build logs) must not exhaust agent RSS.
                remaining = limit
                if proc.stdout is None:
                    return
                try:
                    while True:
                        chunk = proc.stdout.read(4096)
                        if not chunk:
                            break
                        available = remaining
                        if available > 0:
                            output_box.append(chunk[:available])
                            remaining -= min(len(chunk), available)
                        if len(chunk) > available:
                            overflow[0] = True
                except Exception:
                    pass
            reader = threading.Thread(target=_read_output, name="purah-shell-reader", daemon=True)
            reader.start()
            timed_out = False
            while proc.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, AttributeError):
                        proc.terminate()
                    break
                if time.monotonic() - started >= timeout:
                    timed_out = True
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, AttributeError):
                        proc.terminate()
                    break
                time.sleep(0.05)
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, AttributeError):
                    proc.kill()
                proc.wait(timeout=1.5)
            reader.join(timeout=1.5)
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except OSError:
                pass
            output = "".join(output_box)
            cancelled = bool(cancel_event is not None and cancel_event.is_set())
            if cancelled and proc.returncode == 0:
                proc.returncode = -signal.SIGTERM
            truncated = overflow[0]
            shown = output + ("\n… [вывод обрезан]" if truncated else "")
            result = {
                "mode": "background", "command": command, "cwd": str(workdir),
                "returncode": proc.returncode, "timed_out": timed_out,
                "cancelled": cancelled, "output": shown.strip() or "(команда не вывела текст)",
                "output_truncated": truncated,
            }
            if timed_out:
                result["error"] = f"Команда остановлена по тайм-ауту ({timeout} с)."
            elif cancelled:
                result["error"] = "Команда отменена пользователем."
            self._remember(mode="background", cwd=workdir, command=command,
                           output=result["output"], returncode=proc.returncode,
                           timed_out=timed_out)
            result["ok"] = not timed_out and not cancelled and proc.returncode == 0
            return result

    def _tmux(self, args: list[str], *, check: bool = False) -> subprocess.CompletedProcess:
        return self._run(["tmux", "-L", _TMUX_SOCKET, *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=5, check=check)

    def _session_exists(self, session: str) -> bool:
        if not shutil.which("tmux"):
            return False
        result = self._tmux(["has-session", "-t", session])
        return result.returncode == 0

    def _hypr_active(self) -> dict:
        """Inspect compositor focus, with graceful fallback outside Hyprland."""
        if not shutil.which("hyprctl"):
            return {}
        try:
            result = self._run(["hyprctl", "-j", "activewindow"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=1)
            data = json.loads(result.stdout) if result.returncode == 0 else {}
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {}

    def _hypr_window_addresses(self) -> set[str]:
        if not shutil.which("hyprctl"):
            return set()
        try:
            result = self._run(["hyprctl", "-j", "clients"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=1)
            clients = json.loads(result.stdout) if result.returncode == 0 else []
            return {str(item.get("address", "")) for item in clients if isinstance(item, dict)}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return set()

    def _restore_launch_focus(self, previous: dict, launcher_pid: int, terminal_name: str,
                              previous_addresses: set[str] | None = None) -> bool:
        """Restore only if focus is on the terminal we just launched, not user work."""
        original = str(previous.get("address", ""))
        if not original or original in {"0x0", "0"}:
            return False
        # A new terminal may create a tmux session before its Wayland surface maps.
        terminal_classes = {"kitty", "foot", "alacritty", "wezterm", "org.wezfurlong.wezterm",
                            "konsole", "gnome-terminal", "gnome-terminal-server"}
        terminal_classes.add(Path(terminal_name).name.casefold())
        deadline = time.monotonic() + 0.45
        while time.monotonic() < deadline:
            active = self._hypr_active()
            current = str(active.get("address", ""))
            if current == original or not current:
                time.sleep(0.05)
                continue
            app_class = str(active.get("class", "")).casefold()
            # A matching PID is strong evidence; a matching known terminal class
            # is needed for launchers that fork into a remote singleton process.
            pid_match = int(active.get("pid", 0) or 0) == launcher_pid
            class_match = any(key in app_class for key in terminal_classes)
            # Class alone is not evidence when the user just switched to another
            # pre-existing terminal of the same type.
            newly_mapped = previous_addresses is not None and current not in previous_addresses
            if not (pid_match or (class_match and newly_mapped)):
                return False  # user focused a different window; never steal it
            try:
                dispatched = self._run(["hyprctl", "dispatch", "focuswindow", "address:" + original],
                                       capture_output=True, text=True, timeout=1)
                return dispatched.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                return False
        return False

    def _start_visible_session(self, session: str, cwd: Path) -> bool:
        if not shutil.which("tmux"):
            raise RuntimeError("Для режима видимого терминала нужен tmux; установи пакет tmux.")
        if self._session_exists(session):
            # tmux may outlive the terminal window. Re-open the terminal if the
            # session is detached; otherwise "open terminal" would show nothing.
            clients = self._tmux(["list-clients", "-t", session, "-F", "#{client_tty}"])
            if clients.returncode == 0 and (clients.stdout or "").strip():
                return False
        restore_focus = _env_bool("PURAH_VISIBLE_TERMINAL_RESTORE_FOCUS", True)
        previous_window = self._hypr_active() if restore_focus else {}
        previous_addresses = self._hypr_window_addresses() if previous_window else set()
        terminals = os.getenv("PURAH_TERMINAL_APP", "").strip().split()
        candidates = terminals or ["kitty", "foot", "alacritty", "wezterm", "konsole", "gnome-terminal"]
        executable = next((shutil.which(item) for item in candidates if shutil.which(item)), None)
        if not executable:
            raise RuntimeError("Не найден терминал для видимого режима (kitty/foot/alacritty/wezterm/konsole).")
        # -e is supported by the common terminal clients; the command itself is
        # argv-separated, so no extra shell is introduced around tmux.
        # Terminal emulators use different command-line conventions.
        tmux_argv = ["tmux", "-L", _TMUX_SOCKET, "new-session", "-A", "-s", session]
        terminal_name = Path(executable).name.casefold()
        if terminal_name == "gnome-terminal":
            launch_argv = [executable, "--", *tmux_argv]
        elif terminal_name == "wezterm":
            launch_argv = [executable, "start", "--", *tmux_argv]
        else:
            launch_argv = [executable, "-e", *tmux_argv]
        proc = self._popen(launch_argv,
                           cwd=str(cwd), env=self._command_env(), stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           start_new_session=True, close_fds=True)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self._session_exists(session):
                if restore_focus and previous_window:
                    return self._restore_launch_focus(previous_window, int(proc.pid), executable,
                                                      previous_addresses)
                return False
            time.sleep(0.1)
        raise RuntimeError("Видимый terminal/tmux не успел запуститься.")

    def run_visible(self, command: str, *, cwd: str = "", cancel_event=None) -> dict:
        with self._lock:
            session = str(self._state.get("session") or "purah-agent")
            try:
                previous = _safe_cwd(self._state.get("cwd", ""), self.app_dir)
            except ValueError:
                previous = self.app_dir
            workdir = _safe_cwd(cwd, previous) if cwd else previous
            focus_restored = self._start_visible_session(session, workdir)
            marker = uuid.uuid4().hex
            # The command is entered into the user's visible shell; it remains an
            # ordinary shell command and its stdout/stderr stay visible in the pane.
            command_to_send = command
            if cwd:
                command_to_send = f"cd -- {shlex.quote(str(workdir))} && {command}"
            wrapped = f"printf '\\n__PURAH_BEGIN_{marker}__\\n'; {command_to_send}; rc=$?; printf '\\n__PURAH_DONE_{marker}:%s__\\n' \"$rc\""
            pane_target = f"{session}:0.0"
            # A visible terminal may currently be running vim/python/ssh. Do not
            # type a shell command into its stdin and report it as executed.
            pane = self._tmux(["display-message", "-p", "-t", pane_target,
                               "#{pane_current_command}"])
            if pane.returncode != 0:
                raise RuntimeError("Managed tmux pane is unavailable.")
            foreground = Path((pane.stdout or "").strip()).name.casefold()
            if foreground not in _SHELL_NAMES:
                if foreground and command.casefold().find(foreground) != -1:
                    return {"ok": True, "mode": "visible", "command": command,
                            "cwd": str(workdir), "returncode": None, "timed_out": False,
                            "session": session, "focus_restored": focus_restored,
                            "interactive": True,
                            "output": f"{foreground} уже запущен в управляемом терминале."}
                raise RuntimeError("Managed terminal is busy with a foreground application: " + foreground[:70])
            typed = self._tmux(["send-keys", "-t", pane_target, "-l", wrapped])
            if typed.returncode != 0:
                raise RuntimeError("tmux could not write the command to the managed pane.")
            submitted = self._tmux(["send-keys", "-t", pane_target, "C-m"])
            if submitted.returncode != 0:
                raise RuntimeError("tmux could not submit the command to the managed pane.")
            deadline = time.monotonic() + _bounded_int("PURAH_TERMINAL_TIMEOUT", 60, 5, 300)
            captured = ""
            returncode = None
            interactive = False
            interactive_fg = ""
            grace = _bounded_int("PURAH_VISIBLE_INTERACTIVE_GRACE_MS", 4000, 500, 15000) / 1000.0
            started = time.monotonic()
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    self._tmux(["send-keys", "-t", f"{session}:0.0", "C-c"])
                    return {"ok": False, "mode": "visible", "command": command, "cancelled": True,
                            "session": session, "error": "Команда отменена пользователем."}
                result = self._tmux(["capture-pane", "-p", "-t", f"{session}:0.0", "-S", "-300"])
                captured = result.stdout or ""
                done = re.search(rf"__PURAH_DONE_{re.escape(marker)}:(-?\d+)__", captured)
                if done:
                    returncode = int(done.group(1))
                    break
                # Interactive/long-running foreground programs (nano, vim, htop,
                # python REPL, ...) never emit the DONE marker. After a short grace
                # period, treat them as successfully launched instead of blocking
                # the agent until the full timeout.
                if time.monotonic() - started >= grace:
                    fg = self._tmux(["display-message", "-p", "-t", pane_target,
                                     "#{pane_current_command}"])
                    fg_name = Path((fg.stdout or "").strip()).name.casefold() if fg.returncode == 0 else ""
                    if fg_name and fg_name not in _SHELL_NAMES:
                        interactive = True
                        interactive_fg = fg_name
                        break
                time.sleep(0.12)
            timed_out = returncode is None and not interactive
            if timed_out:
                self._tmux(["send-keys", "-t", f"{session}:0.0", "C-c"])
            if interactive:
                result = {"ok": True, "mode": "visible", "command": command,
                          "cwd": str(workdir), "returncode": None, "timed_out": False,
                          "session": session, "focus_restored": focus_restored,
                          "interactive": True,
                          "output": f"Запущено в управляемом терминале: {interactive_fg} (интерактивная программа)."}
                self._remember(mode="visible", cwd=workdir, command=command,
                               output=result["output"], returncode=None, timed_out=False, session=session)
                return result
            begin = captured.find(f"__PURAH_BEGIN_{marker}__")
            end = captured.find(f"__PURAH_DONE_{marker}:")
            begin_marker = f"__PURAH_BEGIN_{marker}__"
            output = captured[begin + len(begin_marker):end] if begin >= 0 and end > begin else captured
            output = output.strip()
            limit = _bounded_int("PURAH_TERMINAL_MAX_OUTPUT_CHARS", 12000, 1024, 30000)
            truncated = len(output) > limit
            shown = output[:limit] + ("\n… [вывод обрезан]" if truncated else "")
            result = {"ok": not timed_out and returncode == 0, "mode": "visible", "command": command,
                      "cwd": str(workdir), "returncode": returncode, "timed_out": timed_out,
                      "session": session, "focus_restored": focus_restored,
                      "output": shown or "(команда не вывела текст)",
                      "output_truncated": truncated}
            if timed_out:
                result["error"] = "Видимая команда не завершилась за отведённое время."
            self._remember(mode="visible", cwd=workdir, command=command, output=result["output"],
                           returncode=returncode, timed_out=timed_out, session=session)
            return result

    def read_visible_output(self, *, session: str = "") -> dict:
        with self._lock:
            session = session.strip() or str(self._state.get("session") or "purah-agent")
            if not self._session_exists(session):
                return {"ok": False, "error": "Управляемая terminal-сессия не запущена.", "session": session}
            result = self._tmux(["capture-pane", "-p", "-t", f"{session}:0.0", "-S", "-300"])
            output = (result.stdout or "").strip()
            return {"ok": result.returncode == 0, "session": session,
                    "output": output[-_bounded_int("PURAH_TERMINAL_MAX_OUTPUT_CHARS", 12000, 1024, 30000):]}

    def run(self, command: str, *, cwd: str = "", mode: str = "background", cancel_event=None) -> dict:
        mode = str(mode or "background").strip().casefold()
        if mode == "auto":
            mode = "background"
        if mode not in {"background", "visible"}:
            return {"ok": False, "error": "mode должен быть background или visible."}
        return self.run_visible(command, cwd=cwd, cancel_event=cancel_event) if mode == "visible" else self.run_background(command, cwd=cwd, cancel_event=cancel_event)


__all__ = ["TerminalManager"]
