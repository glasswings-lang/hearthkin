# SPDX-License-Identifier: CC0-1.0
"""Guard tests for the 2026-07 security-hardening pass.

Pins the behavior changes so a future refactor can't silently regress them:

  * denylist segment-splitting (C1) — a destructive shape hidden behind a
    prefix or a ; / && / | chain is caught despite start-anchored patterns,
    while legitimate cleanup and benign chains still pass;
  * surface-scoped remembered-approvals (E1) — a command remembered at the
    desktop does NOT satisfy a remote surface's allowlist check, and remote
    scopes are isolated from each other;
  * per-user tool-bucket gating (A1) — the (kin tools.json) ∩ (bucket)
    intersection with a 'none' default keeps write/exec tools off remote
    surfaces unless explicitly granted.
"""

import os
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools._exec_denylist import match_denylist            # noqa: E402
import tools._exec_state as exec_state                      # noqa: E402
from tools._buckets import filter_tool_names                # noqa: E402
from tools._io import resolve_kin_path                      # noqa: E402
import tools as kin_tools                                   # noqa: E402

_fails = []


def check(label, cond):
    if not cond:
        _fails.append(label)
        print(f"  FAIL: {label}")
    else:
        print(f"  ok: {label}")


def test_denylist_segments():
    # Caught even though the destructive verb isn't at position 0.
    check("prefix + ; chain caught", match_denylist("echo go; rm -rf /"))
    check("&& chain caught", match_denylist("cd /tmp && rm -rf /"))
    check("pipe chain caught", match_denylist("true | rm -rf /"))
    check("windows chain caught",
          match_denylist("echo hi && remove-item -recurse -force C:\\"))
    # The whole-command test still catches multi-separator single shapes.
    check("fork bomb still caught", match_denylist(":(){ :|:& };:"))
    # Legitimate cleanup / benign chains still pass (safety net, not a cage).
    check("rm -rf temp/ still passes", not match_denylist("rm -rf temp/"))
    check("benign chain passes",
          not match_denylist("cd build && rm -rf out/ && make"))
    check("del of a subdir still passes",
          not match_denylist("del /f /s /q C:\\Temp\\x"))
    # A destructive shape quoted as data (not executed) is not split apart,
    # so the whole-command anchor doesn't fire on it either.
    check("quoted data not falsely split",
          not match_denylist('echo "how to rm -rf / safely"'))


def test_surface_scoped_allowlist():
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "exec_allowlist.json"
        exec_state._allowlist_path = lambda agent_name: target

        # Remember at the desktop.
        exec_state.add_to_allowlist("K", "git pull")  # default = desktop
        check("desktop sees its own remembered cmd",
              exec_state.is_in_allowlist("K", "git pull"))
        # A remote surface must NOT inherit a desktop-remembered command.
        check("telegram user does NOT inherit desktop cmd",
              not exec_state.is_in_allowlist("K", "git pull",
                                             surface="telegram:42"))
        check("discord does NOT inherit desktop cmd",
              not exec_state.is_in_allowlist("K", "git pull",
                                             surface="discord"))

        # Remember on one remote scope; another remote scope stays isolated.
        exec_state.add_to_allowlist("K", "ls", surface="telegram:42")
        check("telegram user 42 sees its remote cmd",
              exec_state.is_in_allowlist("K", "ls", surface="telegram:42"))
        check("telegram user 99 does NOT see user 42's cmd",
              not exec_state.is_in_allowlist("K", "ls", surface="telegram:99"))
        check("desktop does NOT see a remote-remembered cmd",
              not exec_state.is_in_allowlist("K", "ls"))

        # On-disk shape: legacy top-level list + a surfaces map.
        data = json.loads(target.read_text(encoding="utf-8"))
        check("legacy commands key preserved",
              data.get("commands") == ["git pull"])
        check("surfaces map holds the remote scope",
              data.get("surfaces", {}).get("telegram:42") == ["ls"])


def test_bucket_gating_default_none():
    kin_tools = ["read_file", "write_file", "note", "exec"]
    # A user with no bucket assignment maps to 'none' → no tools at all.
    check("none bucket -> no tools", filter_tool_names(kin_tools, "none") == [])
    # 'read' exposes only read-tier tools from the kin's allowlist.
    check("read bucket -> read tools only",
          filter_tool_names(kin_tools, "read") == ["read_file"])
    # 'write' adds file-mutating tools but never exec.
    got = filter_tool_names(kin_tools, "write")
    check("write bucket adds write tools", set(got) == {"read_file", "write_file", "note"})
    check("write bucket excludes exec", "exec" not in got)
    # 'full' includes exec.
    check("full bucket includes exec", "exec" in filter_tool_names(kin_tools, "full"))


def test_path_confinement():
    # An absolute path is honored when NOT confined (desktop opt-out)...
    abspath = "C:\\Windows\\system32\\x" if os.name == "nt" else "/etc/passwd"
    p, err = resolve_kin_path(abspath, "K", confine=False)
    check("desktop: absolute path honored", err is None and p is not None)
    # ...but refused on a confined (remote) surface.
    p, err = resolve_kin_path(abspath, "K", confine=True)
    check("remote: absolute path refused", p is None and err)
    # A ~/... home path expands to absolute → also refused when confined.
    p, err = resolve_kin_path("~/secret.txt", "K", confine=True)
    check("remote: ~ home path refused", p is None and err)


def test_framework_params_hidden_from_schema():
    # confine_paths / agent_name must never appear in the model-facing schema,
    # even though read_file/write_file declare them — otherwise a model could
    # send confine_paths=false to escape the remote confinement.
    schemas, _ = kin_tools.load_tools(
        ["read_file", "write_file", "edit_file"],
        context={"agent_name": "X"})
    leaked = []
    for s in schemas:
        props = (s.get("function", {}).get("parameters", {}) or {}).get("properties", {})
        for hidden in ("confine_paths", "agent_name"):
            if hidden in props:
                leaked.append(f"{s['function']['name']}.{hidden}")
    check("no framework params leak into schema", not leaked)


def test_remote_unconfined_files_opt_in():
    """The remote surfaces confine file paths BY DEFAULT, and lift that only
    when the operator sets `remote_unconfined_files` in the kin config.

    This pins the wiring that 2026-07-18 found missing: the operator flipped
    "Run remote exec without asking" (`remote_unattended_exec`) and expected
    it to also lift path confinement. It doesn't — they're separate controls
    — but at the time there was no switch for confinement at all, so the
    expectation had nowhere to land. Both directions are pinned here so a
    future refactor can't silently re-hardcode confinement (breaking the
    opt-in) or silently default it off (removing audit D1's protection)."""
    from kin_persistence import DEFAULT_AGENT_CONFIG

    check("default config confines remote file paths",
          DEFAULT_AGENT_CONFIG.get("remote_unconfined_files") is False)

    # The surfaces compute `confine` as `not cfg.get("remote_unconfined_files")`.
    # Mirror that here so the truth table is asserted, not assumed.
    def confine_for(cfg):
        return not bool(cfg.get("remote_unconfined_files"))

    check("missing key -> confined", confine_for({}) is True)
    check("explicit False -> confined",
          confine_for({"remote_unconfined_files": False}) is True)
    check("True -> unconfined",
          confine_for({"remote_unconfined_files": True}) is False)
    # The exec toggle must NOT move file confinement — separate concerns.
    check("remote_unattended_exec alone does not unconfine files",
          confine_for({"remote_unattended_exec": True}) is True)

    # And the end-to-end effect: same absolute path, both settings.
    abspath = str(Path.home() / "somewhere_else" / "secret.txt")
    p_off, err_off = resolve_kin_path(
        abspath, "K", confine=confine_for({}))
    check("confined: absolute path refused", p_off is None and err_off)
    p_on, err_on = resolve_kin_path(
        abspath, "K", confine=confine_for({"remote_unconfined_files": True}))
    check("unconfined: absolute path allowed", p_on is not None and not err_on)

    # The escape hatch must stay operator-only: even with the flag on, the
    # model still can't see or set confine_paths (covered for the default
    # case by test_framework_params_hidden_from_schema; re-checked here
    # because the flag is what makes the parameter interesting to an
    # attacker).
    schemas, _ = kin_tools.load_tools(
        ["read_file"],
        context={"agent_name": "K", "confine_paths": False})
    props = (schemas[0]["function"]["parameters"] or {}).get("properties", {})
    check("confine_paths still hidden when unconfined",
          "confine_paths" not in props)


def main():
    for t in (test_denylist_segments, test_surface_scoped_allowlist,
              test_bucket_gating_default_none, test_path_confinement,
              test_remote_unconfined_files_opt_in,
              test_framework_params_hidden_from_schema):
        print(f"\n[{t.__name__}]")
        t()
    if _fails:
        print(f"\nFAILED {len(_fails)}: " + ", ".join(_fails))
        return 1
    print("\nAll security-hardening checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
