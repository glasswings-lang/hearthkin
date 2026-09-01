# SPDX-License-Identifier: CC0-1.0

"""Shared helpers for the filesystem tools: atomic write, kin-scoped
path resolution, tolerant text decoding.

Imported by write_file, edit_file, read_file, memory_search, note —
anywhere a tool touches the filesystem and we want predictable
behavior:

  - atomic_write_text: all-or-nothing writes via temp-file rename.
  - resolve_kin_path: relative paths land inside the kin's own folder,
    not the Python process's cwd.
  - find_existing_path / heal_parent_dirs: forgiving fallback resolution
    for when a model gets a path ALMOST right (a phantom space after a
    paren, a case slip) — fuzzy-match each component against disk, but
    only when the match is unambiguous. Reads/edits heal the whole path;
    writes heal only the parent chain so a new-file write is never
    silently redirected onto an existing file.
  - robust_decode / robust_read_text: tolerant UTF-8 → cp1252 →
    UTF-8-with-replace decode chain so Windows-edited files with
    smart-character bytes (em-dash, en-dash, smart quotes) don't
    blow up the strict-UTF-8 readers.

Kept separate from `hearthkin.pyw`'s helpers to avoid the tools/
package having to import the main script (and the circular dependency
that would create)."""

import os
import re
import tempfile
from pathlib import Path

from hearthkin_paths import kin_dir


def robust_decode(data):
    """Decode bytes as text with a fallback chain that handles file shapes
    commonly seen on Windows. UTF-8 is tried first (matches anything the
    Python ecosystem writes); cp1252 catches files edited in Windows-
    native tools where em-dashes, en-dashes, and smart quotes ended up
    as single 0x80–0x9F bytes; UTF-8 with errors="replace" is the
    last-resort fallback so genuinely malformed input still returns a
    usable (if lossy) string rather than raising.

    Without the cp1252 fallback, memory_search crashed on a kin's own
    soul/memory because the prose used em-dashes (0x97 in cp1252) and
    the read pass was strict-UTF-8. With it, those characters round-trip
    correctly into Unicode em-dashes instead of mojibaking into `?`."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        pass
    return data.decode("utf-8", errors="replace")


def robust_read_text(path):
    """Read a file and decode it tolerantly via `robust_decode`. Raises
    only on file I/O errors (missing file, permission, etc.) — never on
    encoding."""
    return robust_decode(Path(path).read_bytes())


def atomic_write_text(path, content):
    """Write `content` to `path` atomically. Creates parent directories
    if missing. On any error during the write, the temp file is cleaned
    up and the exception re-raised — the destination is never partially
    overwritten."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def path_within_kin(path, agent_name):
    """True iff `path` resolves inside the kin's own directory.

    Used to RE-ASSERT containment after fuzzy path-healing on a confined
    (remote) surface: `_fuzzy_walk` only descends into on-disk entries so it
    can't traverse `..`, but a symlink already inside the kin dir could point
    out. `.resolve()` follows symlinks, so this catches that residual case
    (audit J1). Returns False on any error or missing agent_name."""
    if not agent_name:
        return False
    try:
        kin_root = kin_dir(agent_name).resolve()
        Path(path).resolve().relative_to(kin_root)
        return True
    except Exception:
        return False


def resolve_kin_path(path_str, agent_name, confine=False):
    """Resolve a model-provided path according to hearthkin's kin-scoping
    convention: relative paths land inside the kin's own directory and
    are not allowed to traverse out of it; absolute paths are honored
    as-is so the model can deliberately reach outside its folder when
    that's what it actually wants.

    `confine=True` REVOKES the absolute-path escape hatch: relative paths
    still resolve inside the kin dir, but absolute paths (and `~/...`, which
    expands to an absolute path) are refused. This is set by REMOTE surfaces
    (Telegram, Discord) so a remotely-driven or prompt-injected kin can't read
    or overwrite arbitrary host files — the absolute opt-out is only safe for
    the trusted local operator at the desktop (2026-07 security audit D1).

    The kin directory is `~/.hearthkin/kin/<agent_name>/`. Without
    this scoping, the Python process's cwd (whatever the user ran the
    app from — typically the repo folder) would silently become the
    base for every relative path the model produces, leading to "write
    test.md" landing next to the app's own source files rather than in
    the kin's own folder where the model intended.

    Returns `(Path, None)` on success or `(None, error_message)` on
    failure. The caller is responsible for prefixing the error with
    its tool name. The error string is model-facing — it should steer
    the next attempt.

    If `agent_name` is empty (which would be a framework bug, since the
    context-binding in tools/__init__.py injects it) the function falls
    back to a cwd-relative resolution rather than raising, so a tool
    invoked from a non-kin path (e.g. a future cron job) still works.
    """
    if not path_str:
        return None, "path was empty."
    # Models routinely wrap a path in stray whitespace ("  soul.md ") or
    # carry a trailing newline in from copied text. Leading whitespace also
    # makes an absolute path read as relative (" C:\\x" isn't is_absolute()),
    # silently misrouting it inside the kin dir. Strip it — a path component
    # that's genuinely meant to start/end with a space is essentially never
    # real, and Windows strips trailing spaces from names anyway.
    path_str = path_str.strip()
    if not path_str:
        return None, "path was empty."
    p = Path(path_str).expanduser()
    if p.is_absolute():
        if confine:
            return None, (
                "absolute paths aren't allowed here — this surface "
                "(Telegram/Discord) is restricted to your own kin folder. "
                "Use a relative path like 'memory.md' or 'notes/today.md'. "
                "If you need to reach outside the folder from here, the "
                "operator can turn it on in Settings → Tools → Tool "
                "settings → \"Let remote (Telegram/Discord) file tools "
                "reach outside the kin folder\" — it is off by default. "
                "(The desktop chat is not restricted.)"
            )
        return p, None
    if not agent_name:
        if confine:
            return None, "path could not be confined to a kin directory."
        return p.resolve(), None
    kin_root = kin_dir(agent_name).resolve()
    if not kin_root.exists():
        return None, f"no kin directory for {agent_name!r}."
    target = (kin_root / p).resolve()
    try:
        target.relative_to(kin_root)
    except ValueError:
        return None, (
            f"{path_str!r} resolves outside your kin directory. Use a "
            f"path inside your folder, or pass an absolute path "
            f"(e.g. C:\\Users\\... on Windows, or ~/... on POSIX) "
            f"if you really mean somewhere else."
        )
    return target, None


_WS_RE = re.compile(r"\s+")


def _norm_name(name):
    """Normalize a path component for fuzzy comparison: drop ALL whitespace
    and casefold. Catches the common small-model path fumbles — a phantom
    space after an opening paren (`notes ( drafts, misc)` vs the real
    `notes (drafts, misc)`), a doubled internal space, a stray case
    difference — without being so loose that it collapses genuinely
    different names together."""
    return _WS_RE.sub("", name).casefold()


def _fuzzy_walk(p, heal_final):
    """Walk `p` component-by-component from its (existing) anchor. When a
    literal component isn't on disk, fuzzy-match it (whitespace- and
    case-insensitively, via `_norm_name`) against the real directory
    listing. A component is substituted only when EXACTLY ONE on-disk entry
    matches its normalized form — zero matches or several candidates both
    mean "don't guess," and the walk bails.

    `heal_final=True` (reads/edits): the final component is fuzzy-matched
    like every other, so success means the whole path now points at a real
    file or directory.

    `heal_final=False` (writes): the final component — the filename the
    model wants to create — is kept verbatim; only the parent directory
    chain is healed. This is deliberate: writes create new files, so we
    must never silently redirect a new-file write onto some pre-existing
    file with a similar name.

    Returns `(Path, healed_bool)`. `healed_bool` is True iff at least one
    component was substituted. When nothing could be healed the returned
    Path is the input unchanged and the caller keeps its normal
    missing-file handling — always gate on `healed_bool`."""
    parts = p.parts
    if not parts:
        return p, False
    cur = Path(parts[0])
    if not cur.exists():
        return p, False  # anchor / drive itself is missing — nothing to do
    comps = parts[1:]
    last = len(comps) - 1
    healed = False
    for i, comp in enumerate(comps):
        candidate = cur / comp
        if candidate.exists():
            cur = candidate
            continue
        if i == last and not heal_final:
            cur = candidate  # keep the intended filename verbatim
            break
        target = _norm_name(comp)
        if not target:
            return p, False
        try:
            entries = list(os.scandir(cur))
        except OSError:
            return p, False
        matches = [e.name for e in entries if _norm_name(e.name) == target]
        if len(matches) != 1:
            return p, False  # zero or ambiguous — refuse to guess
        cur = cur / matches[0]
        healed = True
    return cur, healed


def find_existing_path(p):
    """For reads/edits: given a Path that isn't there as typed, try to
    locate the existing file the model probably meant by fuzzy-matching each
    path component against disk (see `_fuzzy_walk`). Returns the healed Path
    when a unique fuzzy match was found and it exists, else None so the
    caller keeps its normal "no file" error."""
    p = Path(p)
    if p.exists():
        return p
    healed_path, healed = _fuzzy_walk(p, heal_final=True)
    if healed and healed_path.exists():
        return healed_path
    return None


def heal_parent_dirs(p):
    """For writes: given a Path whose parent directory doesn't exist as
    typed, try to correct just the directory chain (keeping the intended
    filename) by fuzzy-matching the parent components against disk. Returns
    the healed Path — parent corrected, same filename — when a unique fuzzy
    match landed on an existing directory, else None.

    The filename itself is never fuzzed: without this, a write to
    `.../notes ( drafts, misc)/new.txt` would have `atomic_write_text`
    silently create a mis-spelled sibling `notes ( drafts, misc)` folder
    next to the real `notes (drafts, misc)`. Healing the parent redirects
    the write into the folder the model actually meant."""
    p = Path(p)
    if p.parent.exists():
        return None
    healed_path, healed = _fuzzy_walk(p, heal_final=False)
    if healed and healed_path.parent.exists():
        return healed_path
    return None


# ─── Identity files: soul.md and memory.md ────────────────────────── #

# The two files the app LOADS as a kin's identity: `load_soul()` reads
# <kin>/soul.md and the memory index is <kin>/memory.md. Nothing else is
# the soul, whatever it is named or wherever it sits.
IDENTITY_NAMES = ("soul.md", "memory.md")


def is_canonical_identity_file(p, agent_name):
    """True when `p` IS the soul / memory index the app actually loads.

    Compared against the kin's own root, not by name: a file called
    soul.md somewhere else is a different file and this must say so.
    """
    if not agent_name:
        return False
    try:
        return (Path(p).resolve().parent == kin_dir(agent_name).resolve()
                and Path(p).name.lower() in IDENTITY_NAMES)
    except Exception:
        return False


def identity_misplacement_note(p, agent_name):
    """A warning for a write to something NAMED like an identity file that
    isn't one, or "" when there is nothing to warn about.

    This exists because of a real, months-long failure that produced no
    error at any point. A kin wrote to `memory/soul.md` — a legal path
    inside its own folder — believing it was editing its soul. Every write
    succeeded and said so. The app loads `<kin>/soul.md`, one folder up, so
    none of it was ever loaded, and nothing anywhere said the two were
    different files.

    A success message that is true but misleading is worse than an error.
    """
    try:
        if Path(p).name.lower() not in IDENTITY_NAMES:
            return ""
        if is_canonical_identity_file(p, agent_name):
            return ""
        real = kin_dir(agent_name) / Path(p).name.lower()
    except Exception:
        return ""
    return (f"  NOTE: this is not the {Path(p).name} the app loads — that is "
            f"{real}. Nothing written here reaches your prompt.")


def backup_identity_file(p, agent_name):
    """Copy the soul / memory index aside before it is overwritten.

    `write_file` replaces a file whole, so one call can end a kin's
    identity with no error and no undo. The app already does exactly this
    for its own prompt templates (`_backup_prompt_file`); the kin's own
    identity had nothing. Copies to `<kin>/backups/<name>.<ts>.bak`.

    Best-effort by design: a backup failure must never block the write, or
    a full disk would stop a kin editing itself. Returns a short note for
    the tool result, or "" when nothing was backed up.
    """
    if not is_canonical_identity_file(p, agent_name):
        return ""
    src = Path(p)
    if not src.exists():
        return ""
    try:
        import shutil
        import time
        bdir = kin_dir(agent_name) / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        # Second resolution is not enough on its own: a kin correcting
        # itself twice in the same second would have the second backup land
        # on the first, and the version worth keeping is the OLDEST one --
        # the state before any of it started.
        #
        # The counter is ALWAYS present and zero-padded so the folder sorts
        # chronologically by name. An unsuffixed first file plus "-2", "-3"
        # sorts the original LAST ('-' < '.'), which is precisely backwards
        # for someone scanning the folder in a hurry.
        stamp = time.strftime("%Y%m%d_%H%M%S")
        n = 1
        dest = bdir / f"{src.name}.{stamp}-{n:02d}.bak"
        while dest.exists():
            n += 1
            dest = bdir / f"{src.name}.{stamp}-{n:02d}.bak"
        shutil.copy2(str(src), str(dest))
        return f"  (previous {src.name} saved to backups/{dest.name})"
    except Exception:
        return ""
