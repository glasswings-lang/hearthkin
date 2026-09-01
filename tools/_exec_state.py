# SPDX-License-Identifier: CC0-1.0

"""Per-kin state for the exec tool family.

Two pieces:

  1. Background process tracking. In-memory only, scoped to the
     Hearthkin process. Doesn't persist across restarts — orphaned
     processes keep running but can't be killed via Hearthkin after
     restart. That's the deliberate default: killing background
     processes on shutdown would be surprising and potentially
     destructive (a long build or watch task shouldn't die because the
     chat window closed). User-facing docs note Task Manager as the
     fallback if you need to find an orphaned process post-restart.

  2. The per-kin "remembered approvals" allowlist file at
     ~/.hearthkin/kin/<kin>/exec_allowlist.json with
     {"commands": [...]}. Exact-string match — re-running the
     literally identical command skips the approval dialog; anything
     even slightly different (whitespace, flags) re-prompts. Exact
     match is the conservative starting point; pattern-based
     remembering can come later if it proves too chafing in
     practice."""

import json
import threading
from pathlib import Path

from hearthkin_paths import kin_dir as kin_folder


# {agent_name: {pid: {"command": str, "started_at": float, "proc": Popen}}}
_PROCESSES = {}
_PROCESSES_LOCK = threading.Lock()


def register_background_process(agent_name, pid, command, started_at, proc):
    """Record that the given kin started this background process. Called
    by exec() when background=True succeeds."""
    with _PROCESSES_LOCK:
        per_kin = _PROCESSES.setdefault(agent_name, {})
        per_kin[pid] = {
            "command": command,
            "started_at": started_at,
            "proc": proc,
        }


def list_background_processes(agent_name):
    """Returns a list of (pid, command, started_at) tuples for processes
    this kin started that are still running. Reaps any that have
    already exited (so the kin doesn't see stale PIDs in list_processes
    output)."""
    with _PROCESSES_LOCK:
        per_kin = _PROCESSES.get(agent_name, {})
        alive = []
        dead_pids = []
        for pid, info in per_kin.items():
            proc = info["proc"]
            try:
                still_running = proc.poll() is None
            except Exception:
                still_running = False
            if still_running:
                alive.append((pid, info["command"], info["started_at"]))
            else:
                dead_pids.append(pid)
        for pid in dead_pids:
            del per_kin[pid]
    return alive


def kill_background_process(agent_name, pid):
    """Kill a tracked background process. Returns "killed" if a kill
    signal was sent, "already-exited" if the process had already
    finished by the time of the call (no signal sent — on POSIX the OS
    can reuse a dead child's PID, so signalling without checking could
    hit an unrelated process), or None if the PID isn't tracked for
    this kin. In all non-None cases the PID is removed from tracking.

    Refusing untracked PIDs is the trust boundary — one kin can't reach
    into another's processes via this path, and no kin can kill
    arbitrary host processes by guessing PIDs."""
    with _PROCESSES_LOCK:
        per_kin = _PROCESSES.get(agent_name, {})
        info = per_kin.pop(pid, None)
    if info is None:
        return None
    proc = info["proc"]
    try:
        already_exited = proc.poll() is not None
    except Exception:
        already_exited = False
    if already_exited:
        return "already-exited"
    try:
        proc.kill()
    except Exception:
        pass
    return "killed"


# --- Per-kin allowlist file I/O ------------------------------------ #

def _allowlist_path(agent_name):
    return kin_folder(agent_name) / "exec_allowlist.json"


# Remembered approvals are SURFACE-SCOPED (2026-07 security audit E1). The
# legacy flat {"commands": [...]} list is the DESKTOP scope; remote surfaces
# (Telegram per-user, Discord) keep their own lists under
# {"surfaces": {"<scope>": [...]}}. A command the operator "remembers" at the
# desktop must NOT then auto-run, unprompted, for a remote user — so a remote
# check only ever consults that remote scope's list, never the desktop one.
DESKTOP_SCOPE = "desktop"


def _read_allowlist_file(agent_name):
    """Return the parsed allowlist dict ({"commands": [...], "surfaces": {...}})
    or an empty dict on missing file / parse error / bad shape — a corrupted
    allowlist must never crash the approval flow."""
    p = _allowlist_path(agent_name)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_allowlist(agent_name, surface=DESKTOP_SCOPE):
    """Read the remembered-approvals list for a given surface scope. The
    default DESKTOP_SCOPE reads the legacy top-level "commands" list (so
    existing files keep working); any other scope reads
    surfaces[scope]. Always returns a list of strings."""
    data = _read_allowlist_file(agent_name)
    if surface == DESKTOP_SCOPE:
        commands = data.get("commands", [])
    else:
        surfaces = data.get("surfaces")
        commands = (surfaces or {}).get(surface, []) if isinstance(surfaces, dict) else []
    if not isinstance(commands, list):
        return []
    return [c for c in commands if isinstance(c, str)]


def add_to_allowlist(agent_name, command, surface=DESKTOP_SCOPE):
    """Append `command` to the given surface's allowlist, idempotent on
    duplicates. Desktop writes the legacy "commands" list; remote scopes
    write surfaces[scope]."""
    if not command:
        return
    p = _allowlist_path(agent_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = _read_allowlist_file(agent_name)
    if surface == DESKTOP_SCOPE:
        current = data.get("commands")
        if not isinstance(current, list):
            current = []
        if command not in current:
            current.append(command)
        data["commands"] = current
    else:
        surfaces = data.get("surfaces")
        if not isinstance(surfaces, dict):
            surfaces = {}
        current = surfaces.get(surface)
        if not isinstance(current, list):
            current = []
        if command not in current:
            current.append(command)
        surfaces[surface] = current
        data["surfaces"] = surfaces
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def is_in_allowlist(agent_name, command, surface=DESKTOP_SCOPE):
    """Exact-string check within one surface scope. No normalization, no
    globbing (see the module docstring). A remote scope never matches a
    desktop-remembered command (audit E1)."""
    if not command:
        return False
    return command in load_allowlist(agent_name, surface=surface)
