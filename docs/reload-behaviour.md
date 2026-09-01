# What reloads without a restart

Which files Hearthkin re-reads on the fly, and which are snapshotted in
memory until something forces a refresh. Traced from the send path
(`frame/chat_send_mixin.py`) on 2026-08-19.

## Hot — picked up on the very next message

| File | How |
|---|---|
| `~/.hearthkin/base_prompt.md` | Re-read inside `build_system_prompt` every send (`kin_persistence.py:6059`) |
| Per-kin base prompt override | Same path, same turn |
| `kin/<name>/soul.md` | Cached, but `_refresh_kin_text_cache_if_stale()` runs before every send (`chat_send_mixin.py:119`) and re-reads on an mtime+size change |
| `kin/<name>/memory.md` | Same check, same call |
| Memory depth logs | Same signature check (folder contents are part of the signature) |

The soul/memory check is mtime-based rather than call-site-based on
purpose: it also catches writes made by a kin's own file tools, the
distiller, the consolidation pass, and cron subprocesses running in a
different process entirely.

## Cold — snapshotted until something refreshes it

**Everything in `kin/<name>/config.json`.** The send path reads
`self.agent_cfg` (`chat_send_mixin.py:204-211`), which is a snapshot,
not a fresh load. That covers:

- `model`
- `temperature`, `top_p`, `top_k`, `min_p`
- `repeat_penalty`, `presence_penalty`, `frequency_penalty`
- `num_ctx`
- `think_effort`, `show_thinking`, `think_max_chars`
- `cache`, `cache_ttl`, `ollama_host_name`, openrouter provider order
- every other key in that file

Three things replace the snapshot:

1. **Selecting the kin** — `frame/kin_mgmt_mixin.py:133`
2. **Swapping the model through the GUI** — `frame/kin_mgmt_mixin.py:820`
3. **Closing the Settings dialog** — `frame/menus_mixin.py:1008`, in
   `_on_edit_kin`, which reloads from disk after `dlg.Destroy()`

### Practical consequence

Edits made **in Settings** go live when the dialog closes.

Edits made **by hand to `config.json`** do not go live on their own. To
pick them up without restarting: switch to another kin and back, or open
Settings and close it again. The reload on close happens regardless of
whether anything was changed in the dialog, so an open-and-close is
enough.

## Once per process

| File | How |
|---|---|
| `kin/<name>/calibration.json` | Read once per kin per process, guarded by the `_calibration_loaded` set (`llm_backend.py:501-523`) |

## Legacy field worth knowing

`config.json` still carries a `think` boolean. It is dead —
`kin_persistence.py:940` marks it "legacy, kept for forward compat", and
`think_effort_of()` only falls back to it when `think_effort` is absent.
`think_effort` is what actually decides whether the model reasons.
A kin can have `"think": false` and still be thinking.
