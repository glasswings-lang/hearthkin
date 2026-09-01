# SPDX-License-Identifier: CC0-1.0

"""List background processes this kin has running."""

import time

from ._exec_state import list_background_processes


def list_processes(agent_name: str = "") -> str:
    """List background processes that this kin started via `exec(background=True)` and are still running. Returns one line per process with PID, elapsed runtime, and the command. Use this when you've started something with `background=True` and want to confirm it's still alive, or to look up a PID before calling `kill_process`.

    Only your own processes are visible — you can't see other kin's
    processes, or system processes started outside Hearthkin. Returns
    a plain "no background processes" message when nothing is tracked
    for you.

    Format: `pid=<N> elapsed=<h><m><s> command=<repr>`. Long commands
    are truncated in the listing (call exec to inspect the full
    command's logs if you need them).
    """
    if not agent_name:
        return "list_processes: no kin context (framework bug)."
    procs = list_background_processes(agent_name)
    if not procs:
        return "list_processes: no background processes currently running."
    now = time.time()
    lines = []
    for pid, command, started_at in procs:
        elapsed = max(0, int(now - started_at))
        if elapsed >= 3600:
            elapsed_str = f"{elapsed // 3600}h{(elapsed % 3600) // 60}m"
        elif elapsed >= 60:
            elapsed_str = f"{elapsed // 60}m{elapsed % 60}s"
        else:
            elapsed_str = f"{elapsed}s"
        cmd_short = command if len(command) <= 80 else command[:77] + "..."
        lines.append(f"pid={pid} elapsed={elapsed_str} command={cmd_short!r}")
    return "\n".join(lines)
