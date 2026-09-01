# SPDX-License-Identifier: CC0-1.0

"""Kill a background process this kin started."""

from ._exec_state import kill_background_process


def kill_process(pid: int, agent_name: str = "") -> str:
    """Kill a background process this kin started via `exec(background=True)`. Pass the `pid` from `list_processes`. Use this when a background task is stuck, no longer needed, or you want to stop something before it finishes naturally.

    Refuses PIDs not tracked for this kin — the framework only tracks
    processes you spawned, so passing an arbitrary system PID returns
    a "no such process tracked" error rather than killing it. That's
    the trust boundary: one kin can't reach into another's processes
    via this path, and no kin can kill arbitrary host processes by
    guessing PIDs.

    Returns `killed pid=N` on success, an "already exited" note if the
    process finished on its own before the call (nothing to kill), or a
    "no such process tracked" message if the PID isn't in your tracked
    set (already reaped, or never spawned by you).
    """
    if not agent_name:
        return "kill_process: no kin context (framework bug)."
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return f"kill_process: pid must be an integer, got {pid!r}"
    status = kill_background_process(agent_name, pid_int)
    if status == "killed":
        return f"kill_process: killed pid={pid_int}"
    if status == "already-exited":
        return (
            f"kill_process: pid={pid_int} had already exited on its own; "
            f"nothing to kill (removed from tracking)."
        )
    return (
        f"kill_process: no such process pid={pid_int} tracked for this kin"
    )
