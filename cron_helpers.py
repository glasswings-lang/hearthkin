# SPDX-License-Identifier: CC0-1.0

"""Shared helpers for the cron path.

Both `hearthkin.pyw` (the GUI) and `hearthkin_cron.py` (the
Task-Scheduler-invoked subprocess) need primitives for the lock-file
lifecycle, the request-directory location, dead-PID detection, the
journal-write format, and the cron-error-log format. They live here
so the subprocess can pull them in without importing wxPython.

This module also wraps `schtasks.exe` for creating and deleting
per-kin Windows Task Scheduler entries — used by the Settings
dialog whenever the user adds/removes/edits a cron entry.

No GUI dependencies in this module. Standard library + the
kin_persistence layer only."""

import datetime
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from hearthkin_paths import config_dir


def _norm_hhmm(t):
    """Return a canonical 'HH:MM' 24-hour string for t, or None if invalid."""
    m = re.match(r"^\s*([0-1]?\d|2[0-3]):([0-5]\d)\s*$", str(t or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def cron_entry_fire_times(entry):
    """The sorted, de-duplicated list of 'HH:MM' times a cron entry fires at.

    One logical entry can fire at several times a day. Three shapes are
    supported, newest first:

      - {"times": ["09:00", "15:00", "21:00"]} — an explicit list of times.
      - {"every_minutes": N, "active_start": "09:00", "active_end": "21:00"}
        — an interval: fire every N minutes from active_start through
        active_end (inclusive). Missing bounds default to 00:00 / 23:59.
      - {"time": "09:00"} — the legacy single-time shape (still honoured).

    Returns [] when nothing valid is present. This is the one place the
    entry -> fire-times expansion lives, so the scheduler, the collision
    check, and the UI all agree."""
    if not isinstance(entry, dict):
        return []
    out = []
    times = entry.get("times")
    if isinstance(times, list) and times:
        for t in times:
            n = _norm_hhmm(t)
            if n:
                out.append(n)
    elif entry.get("every_minutes"):
        try:
            step = int(entry.get("every_minutes"))
        except (TypeError, ValueError):
            step = 0
        if step > 0:
            start = _norm_hhmm(entry.get("active_start")) or "00:00"
            end = _norm_hhmm(entry.get("active_end")) or "23:59"
            cur = int(start[:2]) * 60 + int(start[3:])
            last = int(end[:2]) * 60 + int(end[3:])
            guard = 0
            while cur <= last and guard < 24 * 60:
                out.append(f"{cur // 60:02d}:{cur % 60:02d}")
                cur += step
                guard += 1
    else:
        n = _norm_hhmm(entry.get("time"))
        if n:
            out.append(n)
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return sorted(uniq)


# --- Paths ------------------------------------------------------------ #

def hearthkin_dir():
    return config_dir()


def lock_file_path():
    """The lock file Hearthkin writes on start and deletes on close.
    Existence (and a still-running PID inside) means Hearthkin's main
    process is up; the cron subprocess uses this to decide between
    isolated-mode and inject-into-running-mode."""
    return hearthkin_dir() / ".running.lock"


def request_dir():
    """Where the cron subprocess drops request files for the running
    Hearthkin to consume. Created on first write."""
    return hearthkin_dir() / "cron_requests"


def cron_error_log_path():
    return hearthkin_dir() / "logs" / "cron_errors.log"


def unattended_turns_path():
    """Where a wake-up that ran with Hearthkin CLOSED records that it added
    turns, so the app can count them when it next starts.

    Distillation has two triggers. The percentage one measures the undistilled
    tail against the context window, reading both off disk, so it sees these
    turns by itself. The "every N messages" one counts in memory, in the
    running app — which a subprocess cannot reach. So a kin whose tending
    happens overnight while the app is closed had those turns count toward one
    trigger and not the other, and somebody using only the every-N setting had
    a kin quietly accumulating conversation that never reached memory."""
    return hearthkin_dir() / "cron_unattended_turns.json"


def note_unattended_turns(agent_name, scope_key="desktop", count=1):
    """Record `count` turns added by an unattended wake-up. Additive and
    best-effort: the worst a fault here can do is lose a tick, and the worst a
    stale file can do is distill slightly SOONER than asked — which is the
    right direction for the failure to point, since the opposite is a kin
    whose memory silently stops being written."""
    try:
        path = unattended_turns_path()
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8")) or {}
            except Exception:
                data = {}
        key = f"{agent_name}\x1f{scope_key}"
        data[key] = int(data.get(key, 0) or 0) + int(count)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def take_unattended_turns():
    """Read and CLEAR the recorded counts. Returns [(kin, scope_key, count)].

    Read-and-clear in one step, deliberately: counted twice is a distillation
    that fires early, counted never is a kin that stops remembering, and the
    file is deleted before the caller acts so a crash mid-fold cannot replay
    the same turns on every startup forever."""
    out = []
    try:
        path = unattended_turns_path()
        if not path.exists():
            return out
        try:
            data = json.loads(path.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
        try:
            path.unlink()
        except Exception:
            pass
        for key, count in data.items():
            if "\x1f" not in str(key):
                continue
            kin, scope = str(key).split("\x1f", 1)
            try:
                n = int(count)
            except (TypeError, ValueError):
                continue
            if kin and n > 0:
                out.append((kin, scope, n))
    except Exception:
        return []
    return out


def kin_journal_path(agent_name, day=None):
    """Path to the kin's daily journal file. `day` is a `date` object;
    defaults to today's local date. Format: memory/journal/YYYY-MM-DD.md
    under the kin's agent folder."""
    if day is None:
        day = datetime.date.today()
    base = hearthkin_dir() / "kin" / agent_name / "memory" / "journal"
    return base / f"{day.isoformat()}.md"


# --- PID-running check ----------------------------------------------- #

def pid_is_running(pid):
    """Return True if `pid` is currently a running process on this host.
    Windows path uses ctypes.OpenProcess (no psutil dep). Non-Windows
    uses os.kill(pid, 0). Returns False on any check failure rather than
    raising — staleness recovery wants a definitive yes/no, and "I can't
    tell" defaults to "treat as stale" so we err on the side of cleaning
    up dead locks."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.windll.kernel32
            # Declare the signatures. Without a restype, ctypes assumes C
            # int and truncates a 64-bit HANDLE to 32 bits — usually
            # harmless because handle values are small, but it means the
            # value we hand to CloseHandle isn't reliably the one we got.
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                        wintypes.DWORD]
            k32.GetExitCodeProcess.restype = wintypes.BOOL
            k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                               ctypes.POINTER(wintypes.DWORD)]
            # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000, sufficient to
            # check existence without higher-rights flags. Returns NULL
            # handle on failure (process doesn't exist or access denied).
            handle = k32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                # OpenProcess succeeding is NOT proof the process is alive,
                # and treating it as proof was a real bug. Windows keeps a
                # terminated process's kernel object — and therefore its
                # PID — resolvable for as long as ANYONE still holds a
                # handle to it, and a parent, a debugger or the Task
                # Scheduler service routinely does. So an exited cron
                # subprocess opened cleanly, the marker sweep in
                # cron_running_kin concluded it was still working, and the
                # app reported a kin as mid-wake-up for the better part of
                # a day: the confirm-on-close dialog nagged about it, the
                # repeating "still working" cue sounded every 30 seconds,
                # and heartbeats stood down machine-wide because something
                # looked busy. Observed 2026-07-28 on a marker left at
                # 23:00 the previous night.
                #
                # GetExitCodeProcess is the definitive check: STILL_ACTIVE
                # (259) means running, anything else means it has exited.
                code = wintypes.DWORD()
                if k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == 259   # STILL_ACTIVE
                # Couldn't read the exit code — fall back to "the kernel
                # still knows this PID". Same answer as before this fix,
                # which is the conservative direction for the LOCK caller
                # (better to believe Hearthkin is running than to start a
                # second one over a live install).
                return True
            finally:
                k32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _process_image_name(pid):
    """Best-effort image filename (basename, e.g. "Hearthkin.exe" or
    "pythonw.exe") of a running PID via QueryFullProcessImageNameW.
    Returns "" when the API fails, access is denied, or we're not on
    Windows — callers must treat "" as "couldn't tell", not "not
    Hearthkin"."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        # PROCESS_QUERY_LIMITED_INFORMATION — same flag pid_is_running
        # uses; enough for QueryFullProcessImageNameW.
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(size)
            )
            if not ok:
                return ""
            return os.path.basename(buf.value)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return ""


def _pid_looks_like_hearthkin(pid):
    """True when the live process at `pid` plausibly IS Hearthkin —
    the frozen Hearthkin.exe or a python/pythonw interpreter running
    from source. Guards the lock-file check against PID reuse: after
    a crash, Windows can hand the recorded PID to an unrelated
    process, which would make the cron subprocess drop request files
    nothing consumes (audit M-P5). When the image name can't be
    determined (API failure, access denied, non-Windows) this returns
    True — i.e. behave exactly as the PID-only check always did,
    rather than aggressively treating an uninspectable lock as stale."""
    name = _process_image_name(pid)
    if not name:
        return True
    low = name.lower()
    return ("hearthkin" in low) or ("python" in low)


# --- Lock-file lifecycle --------------------------------------------- #

def read_lock():
    """Return the lock-file contents as a dict, or None if absent or
    unreadable. Doesn't check whether the recorded PID is alive — the
    caller does that via pid_is_running()."""
    p = lock_file_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_lock(pid=None):
    """Write the lock file with the given PID (defaults to this process's
    PID), an ISO timestamp, and the process image name. Overwrites any
    existing file.

    The image name is forensic context for the PID-reuse guard (audit
    M-P5): readers verify the LIVE process's image name at check time
    (see _pid_looks_like_hearthkin) rather than trusting the recorded
    one, but having it in the file makes stale-lock debugging direct.
    Additive key — older readers ignore it."""
    target_pid = pid if pid is not None else os.getpid()
    image = _process_image_name(target_pid)
    if not image and target_pid == os.getpid():
        # API fallback for our own process — sys.executable is
        # authoritative for the running interpreter / frozen exe.
        image = os.path.basename(sys.executable or "")
    p = lock_file_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "pid": target_pid,
        "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "image": image,
    }
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def delete_lock():
    """Best-effort delete. Doesn't raise if the file is already gone."""
    p = lock_file_path()
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def lock_indicates_running():
    """Convenience: True if a lock file exists AND the recorded PID is
    currently a running process AND that process's image name looks
    like a Hearthkin (PID-reuse guard, audit M-P5). All checks must
    pass — a stale lock (process crashed without cleanup, PID since
    handed to an unrelated process) returns False so the cron path
    treats it as "Hearthkin not running" and runs isolated."""
    data = read_lock()
    if data is None:
        return False
    pid = data.get("pid")
    if not isinstance(pid, int):
        return False
    return pid_is_running(pid) and _pid_looks_like_hearthkin(pid)


def recover_stale_lock():
    """Called from Hearthkin's __init__. If a lock file exists from a
    previous run whose PID is no longer alive (or has been reused by
    an unrelated process — audit M-P5), delete it so this run can
    write its own. Returns True if a stale lock was cleared."""
    data = read_lock()
    if data is None:
        return False
    pid = data.get("pid")
    if (isinstance(pid, int) and pid_is_running(pid)
            and _pid_looks_like_hearthkin(pid)):
        # Another Hearthkin is genuinely running — leave the lock alone.
        return False
    delete_lock()
    return True


# --- Request-file I/O ------------------------------------------------ #

def write_request_file(kin, prompt, entry_index, scheduled_at=None,
                       time_label=None):
    """Drop a request file for the running Hearthkin to consume. Uses a
    UUID-suffixed filename so concurrent fires can't collide on the
    same path. Returns the path written to.

    `time_label` is the HH:MM this fire was scheduled for. It MUST be
    carried here rather than re-derived by the consumer: a multi-time
    entry has no single "time" key to look up (it has "times": [...]),
    so only the firing task knows WHICH of its times went off — that's
    why the scheduled task passes --time-label. Before this was
    threaded through, a multi-time entry consumed by a running
    Hearthkin journaled as "(no time)" and its wake-up framing carried
    no time anchor, while the same entry fired with the app closed
    (subprocess path, which reads the CLI arg directly) came out
    correct. None keeps the legacy consumer-side derivation."""
    d = request_dir()
    d.mkdir(parents=True, exist_ok=True)
    if scheduled_at is None:
        scheduled_at = datetime.datetime.now().isoformat(timespec="seconds")
    payload = {
        "kin": kin,
        "prompt": prompt,
        "entry_index": entry_index,
        "scheduled_at": scheduled_at,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    if time_label:
        payload["time_label"] = str(time_label).strip()
    name = f"{kin}-{uuid.uuid4().hex[:8]}.json"
    path = d / name
    # Atomic temp + os.replace so the consumer's 5-second timer can
    # never read a half-written file (audit M-P4). The ".tmp" suffix
    # keeps the in-flight file out of list_pending_request_files'
    # "*.json" glob.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def list_pending_request_files():
    """Sorted list of pending request files in the request dir. Sorted
    by name (which sorts by kin then by random suffix, which is
    fine — there's no meaningful ordering across concurrent fires)."""
    d = request_dir()
    if not d.exists():
        return []
    return sorted(d.glob("*.json"))


# How long an unparseable request file is left in place before we
# give up on it. Covers a slow/interrupted writer or an AV hold; a
# file still unparseable after this long is genuinely corrupt.
_REQUEST_FILE_STALE_SECS = 600


def read_and_delete_request_file(path):
    """Read a request file's payload, then delete the file. Returns the
    payload dict or None if unreadable. Deleting after a successful
    read prevents the same request firing twice if the consumer
    crashes mid-handling.

    An UNPARSEABLE file is left in place for the next tick rather than
    deleted — deleting on parse failure used to silently eat a wake-up
    if the read raced a writer or an AV scan held the handle (audit
    M-P4; the write side is atomic now, but older writers and partial
    disk states still exist in the wild). Only once the file has sat
    unparseable past _REQUEST_FILE_STALE_SECS do we delete it and log
    to cron_errors.log, so a permanently-corrupt file can't wedge the
    request dir forever."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as e:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return None
        if age > _REQUEST_FILE_STALE_SECS:
            try:
                path.unlink()
            except Exception:
                pass
            # Filename shape is "<kin>-<uuid8>.json" — best-effort kin
            # for the log line.
            kin_guess = path.stem.rsplit("-", 1)[0] or "?"
            log_cron_error(
                kin_guess, "request_file_unparseable",
                f"{path.name}: {e} (deleted after {int(age)}s unparseable)",
            )
        return None
    try:
        path.unlink()
    except Exception:
        pass
    return payload


# --- Journal-write + error log --------------------------------------- #

def _append_journal_block(agent_name, block, fired_at):
    """Shared scaffolding for append_journal / append_journal_error_marker:
    create the journal/ directory and the day's file (with its date
    header) on first write, then append `block`."""
    journal = kin_journal_path(agent_name, fired_at.date())
    journal.parent.mkdir(parents=True, exist_ok=True)
    if not journal.exists():
        header = f"# {fired_at.date().isoformat()}\n"
        journal.write_text(header + block, encoding="utf-8")
    else:
        with journal.open("a", encoding="utf-8") as f:
            f.write(block)


def append_journal(agent_name, time_label, prompt, reply, fired_at=None):
    """Append a cron-fire entry to the kin's daily journal file. Creates
    the file (and the journal/ directory) on first write of the day.
    The "## HH:MM — Daily wake-up" header is what memory_search picks up
    when the kin searches their journal later."""
    if fired_at is None:
        fired_at = datetime.datetime.now()
    block = (
        f"\n## {time_label} — Cron wake-up\n\n"
        f"**Prompt:** {prompt}\n\n"
        f"**Reply:** {reply}\n\n---\n"
    )
    _append_journal_block(agent_name, block, fired_at)


def log_cron_error(agent_name, error_type, message):
    """Append one line to ~/.hearthkin/logs/cron_errors.log. Always-on
    (like empty_replies.log) — the cron's failure mode is silent enough
    that we want a definitive record regardless of the per-session
    logging toggle."""
    path = cron_error_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Always-on logs grow without bound otherwise (audit L-B29).
        # Lazy import — kin_persistence imports the tools package;
        # keep cron_helpers importable standalone if that ever breaks.
        from kin_persistence import _maybe_trim_log
        _maybe_trim_log(path)
    except Exception:
        pass
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    line = f"{stamp} [{agent_name}] {error_type}: {message}\n"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# --- Windows Task Scheduler shell-outs -------------------------------- #

def _cron_script_path():
    """Absolute path to hearthkin_cron.py — passed to schtasks as the
    command Task Scheduler invokes when running from source. (Frozen
    EXE builds use sys.executable + a --cron flag instead; see
    _cron_invocation_for_schtasks.)"""
    return Path(__file__).resolve().parent / "hearthkin_cron.py"


def _running_as_frozen_exe():
    """True when this process is the PyInstaller-built Hearthkin.exe.
    Used to decide whether schtasks should invoke the EXE directly
    (frozen) or the python+script pair (source)."""
    return bool(getattr(sys, "frozen", False))


def cron_invocation_argv_run_now(kin, entry_index):
    """Argv list for invoking the cron subprocess in --run-now mode
    from the Settings dialog's Test Now button. Same EXE-vs-source
    decision as _cron_invocation_for_schtasks below, but returns an
    argv list (suitable for subprocess.run) rather than the formatted
    /tr string schtasks needs. Without this, Test Now in the frozen
    EXE distribution would try to run python.exe against
    _MEIPASS/hearthkin_cron.py which doesn't exist on disk."""
    if _running_as_frozen_exe():
        return [
            sys.executable, "--cron",
            "--kin", kin,
            "--entry-index", str(entry_index),
            "--run-now",
        ]
    return [
        sys.executable, str(_cron_script_path()),
        "--kin", kin,
        "--entry-index", str(entry_index),
        "--run-now",
    ]


def _cron_invocation_for_schtasks(kin, entry_index, time_label=None):
    """Return the /tr value string passed to schtasks /create.

    `time_label` (HH:MM), when given, is passed through as `--time-label` so
    the fired subprocess journals the scheduled time even for a multi-time
    entry (whose config no longer has a single `time` field).

    Two shapes depending on how Hearthkin is currently running:

    - Frozen EXE build: invoke the installed Hearthkin.exe itself with
      `--cron --kin X --entry-index N`. hearthkin.pyw's main() detects
      --cron early and delegates to hearthkin_cron.main(), so the EXE
      knows how to be both the GUI and the cron subprocess. This is the
      shape end users get from the GitHub release installer — robust
      against worktree-path drift and PyInstaller's per-session
      _MEIPASS temp dir disappearing on exit.

    - Source run (developer): invoke the current Python interpreter
      against hearthkin_cron.py at whatever path __file__ resolves to.
      Source devs who care about cron-from-source should be aware that
      this binds the schtasks entry to wherever Hearthkin was launched
      from. Running from a Claude Code worktree (which can later get
      garbage-collected) is the known footgun — the startup re-sync
      below auto-heals it the next time Hearthkin launches from the
      canonical install."""
    tl = f' --time-label {_norm_hhmm(time_label)}' if _norm_hhmm(time_label) else ""
    if _running_as_frozen_exe():
        return (
            f'"{sys.executable}" --cron '
            f'--kin "{kin}" --entry-index {entry_index}{tl}'
        )
    cron_script = _cron_script_path()
    return (
        f'"{sys.executable}" "{cron_script}" '
        f'--kin "{kin}" --entry-index {entry_index}{tl}'
    )


def schtasks_task_name(kin, entry_index, time_index=None):
    """Stable name template — `Hearthkin-<Kin>-Cron-<N>` for a single-time
    entry, or `Hearthkin-<Kin>-Cron-<N>-<T>` for the T-th fire-time of a
    multi-time entry. `Hearthkin-<Kin>-Cron-` is the common prefix used to
    find (and sweep) all of a kin's cron tasks."""
    base = f"Hearthkin-{kin}-Cron-{entry_index}"
    return base if time_index is None else f"{base}-{time_index}"


def _no_window_kwargs():
    """subprocess.run kwargs that suppress the Windows console window.
    Without these, every schtasks.exe invocation flashes a console
    window briefly. When Hearthkin runs from pythonw.exe or the frozen
    EXE (which has no parent console for the child to inherit), each
    flash steals window focus and gives it back — and on the
    accessibility tree that fires WM_SETFOCUS events on whatever widget
    currently holds focus in the wx window, causing NVDA to re-announce
    the focused control once per subprocess call. For the startup
    sync_all_kins_blocking sweep (32 deletes + 1 create per kin) that
    means NVDA reading "Talk with a kin radio button" 33 times in a
    row on launch. CREATE_NO_WINDOW eliminates the flash; capture_output
    still works because the process's stdio handles are passed
    explicitly by subprocess. No-op outside Windows."""
    if sys.platform != "win32":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def schtasks_supported():
    """True if `schtasks.exe` is available on PATH. Non-Windows hosts
    return False; the cron section of the Settings UI shows a stub
    message instead of trying to register entries that can't run."""
    if sys.platform != "win32":
        return False
    try:
        r = subprocess.run(
            ["schtasks", "/?"],
            capture_output=True, text=True, timeout=5, check=False,
            **_no_window_kwargs(),
        )
        return r.returncode == 0
    except Exception:
        return False


def schtasks_create_daily(kin, entry_index, time_hhmm, time_index=None):
    """Register (or update) a daily Task Scheduler entry that fires
    hearthkin_cron.py at `time_hhmm` (HH:MM, 24-hour local time). Uses
    /f to silently overwrite an existing task with the same name.
    `time_index` distinguishes the several fire-times of one multi-time
    entry (each gets its own task + slot). Returns (True, "") on success or
    (False, stderr-or-message) on failure — caller surfaces the error in
    the UI rather than crashing."""
    if not schtasks_supported():
        return False, "schtasks is not available on this host (Windows-only)."
    task_name = schtasks_task_name(kin, entry_index, time_index)
    # /tr (TaskRun) is one schtasks argument that itself looks like a
    # command line. Internal double-quotes protect paths with spaces;
    # subprocess.run with shell=False passes the whole /tr value as one
    # argv element so we don't have to deal with cmd's escape rules.
    # _cron_invocation_for_schtasks picks between EXE and source shapes and
    # threads the fire-time through as --time-label.
    tr_value = _cron_invocation_for_schtasks(kin, entry_index, time_label=time_hhmm)
    try:
        r = subprocess.run(
            [
                "schtasks", "/create",
                "/tn", task_name,
                "/tr", tr_value,
                "/sc", "DAILY",
                "/st", time_hhmm,
                "/f",
            ],
            capture_output=True, text=True, timeout=15, check=False,
            **_no_window_kwargs(),
        )
    except Exception as e:
        return False, f"schtasks /create failed to launch: {e}"
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip() or f"exit {r.returncode}"
        return False, msg
    return True, ""


def schtasks_delete(kin, entry_index):
    """Delete the Task Scheduler entry for this kin/index pair, if any.
    Idempotent — a delete on a non-existent task returns success-shape
    output, which we treat as fine. Returns (True, "") on success or
    no-op, (False, stderr) on a real failure."""
    if not schtasks_supported():
        return False, "schtasks is not available on this host (Windows-only)."
    task_name = schtasks_task_name(kin, entry_index)
    try:
        r = subprocess.run(
            ["schtasks", "/delete", "/tn", task_name, "/f"],
            capture_output=True, text=True, timeout=15, check=False,
            **_no_window_kwargs(),
        )
    except Exception as e:
        return False, f"schtasks /delete failed to launch: {e}"
    if r.returncode != 0:
        out = (r.stdout or r.stderr or "").strip()
        # "ERROR: The system cannot find the file specified" means the
        # task doesn't exist — treat as success since we're aiming for
        # idempotent delete.
        if "cannot find" in out.lower() or "does not exist" in out.lower():
            return True, ""
        # The text match above is English-only; on a non-English
        # Windows the not-found error reads differently and every
        # sync would report ~30 spurious failures (audit M-P3).
        # Locale-independent probe: /query the task — a nonzero exit
        # means it doesn't exist, so the delete was a no-op success.
        try:
            q = subprocess.run(
                ["schtasks", "/query", "/tn", task_name],
                capture_output=True, text=True, timeout=15, check=False,
                **_no_window_kwargs(),
            )
            if q.returncode != 0:
                return True, ""
        except Exception:
            pass
        return False, out or f"exit {r.returncode}"
    return True, ""


def _list_cron_task_names(kin):
    """This kin's existing Task Scheduler task names (prefix-matched), via
    `schtasks /query`. Catches BOTH the old single-index names and the new
    per-time names, so a sweep by this list migrates old tasks cleanly.
    Returns a list, or None if the query itself failed (caller falls back to
    the legacy fixed-slot sweep)."""
    prefix = f"Hearthkin-{kin}-Cron-"
    try:
        r = subprocess.run(
            ["schtasks", "/query", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=15, check=False,
            **_no_window_kwargs(),
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    import csv
    import io
    names = []
    try:
        for row in csv.reader(io.StringIO(r.stdout or "")):
            if not row:
                continue
            # Task path is the first column, e.g. "\Hearthkin-<kin>-Cron-0-1".
            name = (row[0] or "").strip().lstrip("\\")
            if name.startswith(prefix):
                names.append(name)
    except Exception:
        return None
    return names


def _schtasks_delete_by_name(task_name):
    """Delete one task by exact name. Idempotent: a missing task counts as
    success. Returns (ok, message)."""
    try:
        r = subprocess.run(
            ["schtasks", "/delete", "/tn", task_name, "/f"],
            capture_output=True, text=True, timeout=15, check=False,
            **_no_window_kwargs(),
        )
    except Exception as e:
        return False, f"schtasks /delete failed to launch: {e}"
    if r.returncode != 0:
        out = (r.stdout or r.stderr or "").strip()
        if "cannot find" in out.lower() or "does not exist" in out.lower():
            return True, ""
        return False, out or f"exit {r.returncode}"
    return True, ""


def schtasks_sync_kin(kin, cron_entries):
    """Reconcile Task Scheduler against this kin's current cron_entries list.

    Strategy: delete every existing task under this kin's `Hearthkin-<Kin>-
    Cron-` prefix (found via /query — proportional, and it catches both old
    single-index names and the new per-time names so migration is automatic),
    then create one task per (enabled entry, fire-time). A single entry with
    times ["09:00","15:00","21:00"] becomes three tasks; the interval shape
    expands the same way. Each task fires the same `--entry-index` (so the
    subprocess reads the same prompt) with the fire-time as `--time-label`.

    If the /query fails, falls back to the legacy fixed 0..31 single-index
    sweep so old installs still get cleaned.

    Returns (ok, errors) where ok is True if all attempted operations
    succeeded, and errors is a list of (task_name, message) tuples."""
    if not isinstance(cron_entries, list):
        cron_entries = []
    errors = []
    existing = _list_cron_task_names(kin)
    if existing is not None:
        for name in existing:
            ok, msg = _schtasks_delete_by_name(name)
            if not ok:
                errors.append((name, msg))
    else:
        # Query unavailable — fall back to the old fixed-slot sweep.
        for i in range(32):
            ok, msg = schtasks_delete(kin, i)
            if not ok:
                errors.append((schtasks_task_name(kin, i), msg))
    # Recreate: one task per fire-time of each enabled entry.
    for i, entry in enumerate(cron_entries):
        if not isinstance(entry, dict) or not entry.get("enabled"):
            continue
        for t_idx, hhmm in enumerate(cron_entry_fire_times(entry)):
            ok, msg = schtasks_create_daily(kin, i, hhmm, time_index=t_idx)
            if not ok:
                errors.append((schtasks_task_name(kin, i, t_idx), msg))
    return (not errors), errors


def frame_wake_up_prompt(prompt, time_label, fired_at=None, kin_name=None):
    """Wrap a cron prompt with harness framing so the kin reads it as a
    scheduled wake-up rather than a chat message.

    Without this, a prompt like "I'm <kin>. What do I want to do today?"
    lands as a plain user turn and the model interprets it as the user
    typing those words — leading to confused replies about who's
    speaking. With the framing prepended, the model knows the next text
    is from its own scheduler, nobody is currently typing, and the time
    + day anchor lets it orient (so cron wake-ups can actually solve
    the "what day is it" problem they're meant to).

    The framing is intentionally neutral about the *intent* of the cron
    (self-check-in vs research task vs maintenance) — that lives in the
    prompt itself, written by the human in Settings → Cron. The harness
    only contributes the things only the harness knows: this is
    scheduled, not user input, and here's when it fired."""
    if fired_at is None:
        fired_at = datetime.datetime.now()
    # "Saturday, May 16, 2026" — readable, locale-independent.
    day_label = fired_at.strftime("%A, %B %d, %Y")
    time_pretty = time_label.strip() if time_label else fired_at.strftime("%H:%M")
    # Framing text lives in the editable ~/.hearthkin/prompts/wake_up_frame.md.
    # {prompt} is substituted LAST so a cron prompt containing literal braces
    # can't disturb the {time}/{day} slots. Local import avoids a load cycle.
    from kin_persistence import load_app_prompt
    return (
        load_app_prompt("wake_up_frame", kin_name)
        .replace("{time}", time_pretty)
        .replace("{day}", day_label)
        .replace("{prompt}", prompt)
    )


def sync_all_kins_blocking(agents_root=None, log_errors=True):
    """Re-register every kin's cron entries against Windows Task
    Scheduler. Called on Hearthkin startup so a stale schtasks command
    (e.g. one pointing at a vanished worktree script or a
    last-session's PyInstaller temp dir) gets healed without the user
    having to know it was broken.

    Walks ~/.hearthkin/kin/<kin>/config.json, reads cron_entries,
    calls schtasks_sync_kin per kin. Blocking — caller spawns a thread
    if they don't want to wait on subprocess latency.

    Idempotent and no-op on non-Windows hosts. Errors get logged to
    cron_errors.log but don't propagate; this runs in the background
    and shouldn't block app startup over a transient schtasks failure."""
    if not schtasks_supported():
        return
    if agents_root is None:
        agents_root = hearthkin_dir() / "kin"
    if not agents_root.exists():
        return
    try:
        kin_dirs = [p for p in agents_root.iterdir() if p.is_dir()]
    except Exception:
        return
    for kin_dir in kin_dirs:
        kin = kin_dir.name
        cfg_path = kin_dir / "config.json"
        if not cfg_path.exists():
            continue
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        entries = cfg.get("cron_entries") or []
        if not isinstance(entries, list) or not entries:
            # Nothing to register. Also nothing to clean up — if the
            # user removed all entries via the Settings dialog, that
            # path already called schtasks_sync_kin with an empty list,
            # which sweeps the slots clean. Skip the empty case to
            # avoid 32 schtasks /delete calls per kin per startup.
            continue
        try:
            ok, errors = schtasks_sync_kin(kin, entries)
        except Exception as e:
            if log_errors:
                log_cron_error(kin, "startup_sync_exception", str(e))
            continue
        if not ok and log_errors:
            for task_name, msg in errors:
                log_cron_error(kin, "startup_sync_failure", f"{task_name}: {msg}")


def append_journal_error_marker(agent_name, time_label, prompt, error_summary, fired_at=None):
    """Drop a "[cron wake-up failed]" marker into the journal so the kin
    reading their own journal later sees that something tried. Lives
    alongside successful wake-ups in the same daily file."""
    if fired_at is None:
        fired_at = datetime.datetime.now()
    block = (
        f"\n## {time_label} — Cron wake-up FAILED\n\n"
        f"**Prompt:** {prompt}\n\n"
        f"**Error:** {error_summary}\n\n"
        f"(Full details in `~/.hearthkin/logs/cron_errors.log`.)\n\n---\n"
    )
    _append_journal_block(agent_name, block, fired_at)

def running_dir():
    """Marker directory for cron work happening in a SEPARATE process.

    The desktop app tracks its own background threads, but a cron wake-up that
    runs in the standalone `hearthkin_cron` subprocess is invisible to it —
    different process, no shared state. That gap meant quitting Hearthkin
    during a scheduled wake-up closed silently and abandoned it, with the
    confirm-on-close dialog reporting nothing in flight because, from where it
    was standing, nothing was.

    One small file per running turn, named with the pid so a crashed
    subprocess can be told from a live one rather than leaving a phantom that
    nags on every future quit.
    """
    d = hearthkin_dir() / "cron_running"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def mark_cron_running(kin, label=""):
    """Claim a marker for this process. Returns the path, or None."""
    import os
    try:
        p = running_dir() / f"{kin}-{os.getpid()}.marker"
        p.write_text("\n".join([kin, str(label), str(os.getpid())]) + "\n",
                     encoding="utf-8")
        return p
    except Exception:
        return None


def clear_cron_running(path):
    try:
        if path is not None:
            Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def cron_running_kin():
    """[(kin, label)] for every cron subprocess actually alive right now.

    Markers whose pid is gone are swept — a subprocess killed mid-turn must not
    leave something that claims to be working forever.
    """
    out = []
    try:
        for f in running_dir().glob("*.marker"):
            try:
                parts = f.read_text(encoding="utf-8").splitlines()
                kin = parts[0] if parts else f.stem
                label = parts[1] if len(parts) > 1 else ""
                pid = int(parts[2]) if len(parts) > 2 else 0
            except Exception:
                continue
            if pid and pid_is_running(pid):
                out.append((kin, label))
            else:
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception:
        pass
    return out
