# SPDX-License-Identifier: CC0-1.0
"""One list of per-person settings, shared by every remote surface.

Telegram and Discord each keep a list of people a kin talks to, and each
person has settings: a name, tool access, a tool-call limit, whether their
commands ask first, and so on. Those used to be added to each surface by hand,
one at a time, which is how Discord fell behind: by November 2026 it had three
of Telegram's eight.

So the settings are defined ONCE, here. Both person windows are built from
this list (dialogs/person_window.py), the settings window saves and loads
through it (load_person / save_person / remove_person), and the bots read it
through the same helpers. A setting added here appears on both surfaces, in
the same place, with the same name. A setting that genuinely can't apply to a
surface still appears, and says so in its explainer, rather than going
missing.

Storage stays where it always was: each setting is a dict keyed by person ID
inside the surface's own config section (cfg["telegram"]["user_tools"],
cfg["discord"]["user_tools"], ...), so existing configs load unchanged.

No wx here: the bots import this.
"""

# kind:
#   "text"   — free text; "" means none.
#   "bucket" — tool access: none / read / write / full.
#   "bool"   — on / off.
#   "choice" — one of `choices` (first one is the default). When the first
#              choice is "default" it means "use the kin's setting", and the
#              person's entry is removed rather than stored.
#   "cap"    — a whole number, or None for "use the kin's setting".
#
# store: the per-surface config key. A dict for the one setting whose key
# grew a surface name before this list existed.

PERSON_SETTINGS = [
    {"key": "label", "kind": "text", "default": "",
     "store": "user_labels"},
    {"key": "bucket", "kind": "bucket", "default": "none",
     "store": "user_tools"},
    {"key": "share_desktop", "kind": "bool", "default": False,
     "store": "user_share_desktop"},
    {"key": "mirror", "kind": "bool", "default": False,
     "store": {"telegram": "user_mirror_to_telegram",
               "discord": "user_mirror_desktop"}},
    {"key": "webcam", "kind": "choice", "choices": ("ask", "auto", "deny"),
     "default": "ask", "store": "user_webcam_permission"},
    {"key": "tool_cap", "kind": "cap", "default": None,
     "store": "user_tool_caps"},
    {"key": "exec_approval", "kind": "choice",
     "choices": ("default", "ask", "auto"), "default": "default",
     "store": "user_exec_approval"},
    {"key": "file_reach", "kind": "choice",
     "choices": ("default", "inside", "outside"), "default": "default",
     "store": "user_file_reach"},
]

SURFACES = ("telegram", "discord")
SETTING_KEYS = tuple(s["key"] for s in PERSON_SETTINGS)


def store_key(setting, surface):
    st = setting["store"]
    return st[surface] if isinstance(st, dict) else st


def _setting(key):
    for s in PERSON_SETTINGS:
        if s["key"] == key:
            return s
    raise KeyError(key)


def _clean(setting, value):
    """Coerce a stored or entered value to the setting's kind; anything
    unusable becomes the default."""
    kind, default = setting["kind"], setting["default"]
    if kind == "text":
        return value.strip() if isinstance(value, str) else default
    if kind == "bucket":
        from tools._buckets import BUCKET_ORDER
        return value if value in BUCKET_ORDER else default
    if kind == "bool":
        return bool(value)
    if kind == "choice":
        v = str(value).lower() if value is not None else ""
        return v if v in setting["choices"] else default
    if kind == "cap":
        if value is None or value == "":
            return None
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return None
    return default


def _is_default_sentinel(setting, value):
    """True when the value means "use the kin's setting" and so is stored
    by removing the person's entry."""
    if setting["kind"] == "cap":
        return value is None
    if setting["kind"] == "choice" and setting["choices"][0] == "default":
        return value == "default"
    return False


def load_person(section, surface, uid, fallbacks=None):
    """This person's values from one surface's config section, as
    {key: value}, every setting present. `fallbacks` supplies a value for a
    setting the person has no entry for (e.g. Discord's old kin-wide
    share_desktop)."""
    section = section if isinstance(section, dict) else {}
    fallbacks = fallbacks or {}
    out = {}
    for s in PERSON_SETTINGS:
        d = section.get(store_key(s, surface)) or {}
        if isinstance(d, dict) and str(uid) in d:
            out[s["key"]] = _clean(s, d[str(uid)])
        elif s["key"] in fallbacks:
            out[s["key"]] = _clean(s, fallbacks[s["key"]])
        else:
            out[s["key"]] = s["default"]
    return out


def save_person(section, surface, uid_old, uid_new, values):
    """Return a NEW section with this person saved: on the allow list, every
    setting written (or removed, for "use the kin's setting"), and any
    entries under a changed ID dropped."""
    sec = dict(section or {})
    uid_old = str(uid_old) if uid_old not in (None, "") else ""
    uid_new = str(uid_new)
    allow = [str(x) for x in (sec.get("allow_from") or [])]
    if uid_old and uid_old != uid_new:
        allow = [a for a in allow if a != uid_old]
    if uid_new not in allow:
        allow.append(uid_new)
    sec["allow_from"] = allow
    for s in PERSON_SETTINGS:
        k = store_key(s, surface)
        d = dict(sec.get(k) or {})
        if uid_old:
            d.pop(uid_old, None)
        v = _clean(s, values.get(s["key"], s["default"]))
        if _is_default_sentinel(s, v):
            d.pop(uid_new, None)
        else:
            d[uid_new] = v
        sec[k] = d
    return sec


def remove_person(section, surface, uid):
    """Return a NEW section with this person gone from the allow list and
    from every setting."""
    sec = dict(section or {})
    uid = str(uid)
    sec["allow_from"] = [str(x) for x in (sec.get("allow_from") or [])
                         if str(x) != uid]
    for s in PERSON_SETTINGS:
        k = store_key(s, surface)
        d = dict(sec.get(k) or {})
        d.pop(uid, None)
        sec[k] = d
    return sec


def person_value(section, surface, uid, key, fallback=None):
    """One setting for one person, for the bots. Falls back to "*" (anyone)
    and then to `fallback`, or the setting's default."""
    s = _setting(key)
    d = (section if isinstance(section, dict) else {}).get(
        store_key(s, surface)) or {}
    if isinstance(d, dict):
        for k in (str(uid), "*"):
            if k in d:
                return _clean(s, d[k])
    return s["default"] if fallback is None else _clean(s, fallback)


def resolve_file_reach(section, surface, uid, kin_cfg):
    """True when this person's file tools (and fenced saves) are kept inside
    the kin's folder. Their own setting wins; otherwise the kin-wide box
    "Let remote file tools reach outside the kin folder" decides."""
    v = person_value(section, surface, uid, "file_reach")
    if v == "inside":
        return True
    if v == "outside":
        return False
    return not bool((kin_cfg or {}).get("remote_unconfined_files"))
