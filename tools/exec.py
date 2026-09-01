# SPDX-License-Identifier: CC0-1.0

"""Run a shell command in the kin's working directory.

This tool is gated by harness-side approval. The framework wraps the
executor in hearthkin.pyw (`_wrap_exec_executor`) so that before
the actual subprocess fires, the wrapper consults the kin's
`tool_trust` level and the denylist in `tools/_exec_denylist.py`. If
approval is needed, a dialog blocks the worker thread until the user
picks allow/remember/deny. None of that machinery is visible here — by
the time `exec()` runs, the decision has already been made.

Foreground (the default) blocks until the command finishes or times
out, returns stdout/stderr/exit code. Background (`background=True`)
spawns the process detached with stdin/stdout/stderr → DEVNULL and
returns immediately with a tracking handle. The handle can be passed
to `kill_process`; the active set is visible via `list_processes`.

Shell choice: PowerShell on Windows by default with
`-NoProfile -NonInteractive -Command`, bash on Linux/macOS with
`-c`. `-NonInteractive` prevents PowerShell's own prompts but does
NOT prevent commands inside from prompting — e.g. `git commit` without
`-m` will hang to the timeout (and then get killed). Expected
behavior; model shouldn't be running interactive commands anyway."""

import subprocess
import sys
import time
from pathlib import Path

from hearthkin_paths import kin_dir as kin_folder

from ._exec_state import register_background_process


_DEFAULT_TIMEOUT = 30
_MIN_TIMEOUT = 1
_MAX_TIMEOUT = 600   # 10 min sanity cap; use background=True for longer
_OUTPUT_CAP_STDOUT = 32_768
_OUTPUT_CAP_STDERR = 32_768

# Suppress the console window that Windows would otherwise allocate when
# a console-subsystem child (powershell, cmd) is spawned from pythonw.exe.
# Without this, every exec() call flashes a black window onscreen.
# Zero on non-Windows so it's a harmless no-op when OR'd into other flags.
_NO_WINDOW_FLAG = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _build_shell_argv(command, shell):
    """Map (command, shell-name) to a concrete argv list for subprocess.
    Unknown shell names fall back to per-platform default rather than
    erroring — the model shouldn't ever know about shell names, this is
    here so per-kin config can override later."""
    s = (shell or "").strip().lower()
    if s == "pwsh":
        return ["pwsh", "-NoProfile", "-NonInteractive", "-Command", command]
    if s == "powershell":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    if s == "cmd":
        return ["cmd", "/c", command]
    if s == "bash":
        return ["bash", "-c", command]
    if s == "sh":
        return ["sh", "-c", command]
    # Default per-platform.
    if sys.platform == "win32":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["sh", "-c", command]


def _cap_output(text, limit):
    if not text:
        return ""
    if len(text) > limit:
        return text[:limit] + f"\n[truncated at {limit} chars]"
    return text


def _resolve_cwd(cwd, kin_dir):
    """Resolve the cwd argument to an absolute Path. Empty → kin folder.
    Relative → resolved against kin folder. Absolute → used as-is. We
    don't restrict cwd to inside the kin folder — exec is exec, and the
    user already approved the call via the harness gate."""
    if not cwd:
        return kin_dir
    p = Path(cwd)
    if not p.is_absolute():
        p = kin_dir / p
    return p


def exec(
    command: str,
    cwd: str = "",
    timeout: int = _DEFAULT_TIMEOUT,
    background: bool = False,
    agent_name: str = "",
) -> str:
    """Run a shell command and return its output. Use this when you need to do something on the host system that no other tool covers — running a script, checking git status, listing files, installing a package, etc. The shell defaults to PowerShell on Windows and bash elsewhere; the working directory defaults to your kin folder.

    Parameters:
      - command: the shell command to run, as a single string.
      - cwd: optional working directory. Relative paths resolve against
        your kin folder; absolute paths are used as-is. Empty means
        kin folder.
      - timeout: seconds to wait before killing the process. Default 30,
        max 600. Use background=True for longer-running work.
      - background: when true, spawns the process detached and returns
        immediately with `[background pid=N]`. Use list_processes to
        see what's still running and kill_process to stop one.

    Foreground returns:
      ```
      exit_code: <N>
      stdout:
      <up to 32K chars; "[truncated at 32768 chars]" if cut>
      stderr:
      <up to 32K chars; "[truncated at 32768 chars]" if cut>
      ```

    If the command hits the timeout, exit_code is -1 and a
    `[killed after Ns]` marker appears before stdout/stderr. The model
    can grep `exit_code: 0` to check success without parsing the rest.

    This tool gates on user approval — the user may see a dialog before
    your command runs, depending on their trust settings and whether
    the command matches a denylist pattern. You don't need to handle
    that — just call as normal. If denied, you'll get a
    "[denied by user]" result back.
    """
    if not command or not isinstance(command, str):
        return "exec: command was empty."
    if not agent_name:
        return "exec: no kin context (framework bug)."

    kin_dir = kin_folder(agent_name)
    cwd_path = _resolve_cwd(cwd, kin_dir)
    if not cwd_path.exists():
        return f"exec: cwd {str(cwd_path)!r} does not exist."

    try:
        timeout_int = int(timeout)
    except (TypeError, ValueError):
        timeout_int = _DEFAULT_TIMEOUT
    timeout_int = max(_MIN_TIMEOUT, min(timeout_int, _MAX_TIMEOUT))

    shell = ""  # per-kin override would read from cfg here; default for now
    argv = _build_shell_argv(command, shell)

    if background:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW_FLAG,
            )
        except FileNotFoundError as e:
            return f"exec: shell not available for background: {e}"
        except Exception as e:
            return f"exec: failed to spawn background process: {e}"
        register_background_process(
            agent_name, proc.pid, command, time.time(), proc
        )
        return (
            f"[background pid={proc.pid}] {command!r} started in "
            f"{str(cwd_path)!r}"
        )

    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            timeout=timeout_int,
            errors="replace",
            creationflags=_NO_WINDOW_FLAG,
        )
    except subprocess.TimeoutExpired as e:
        stdout = _cap_output(e.stdout or "", _OUTPUT_CAP_STDOUT)
        stderr = _cap_output(e.stderr or "", _OUTPUT_CAP_STDERR)
        return (
            f"exit_code: -1\n"
            f"[killed after {timeout_int}s]\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )
    except FileNotFoundError as e:
        return f"exec: shell not available: {e}"
    except Exception as e:
        return f"exec: failed: {e}"

    stdout = _cap_output(result.stdout or "", _OUTPUT_CAP_STDOUT)
    stderr = _cap_output(result.stderr or "", _OUTPUT_CAP_STDERR)
    return (
        f"exit_code: {result.returncode}\n"
        f"stdout:\n{stdout}\n"
        f"stderr:\n{stderr}"
    )
