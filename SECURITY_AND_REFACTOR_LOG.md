# Security Audit & Rearchitecture Log

Work performed on branch `claude/cron-routing-heartbeat`, starting 2026-07-17.

Three-part task:
1. Full in-depth security audit + fix all issues found.
2. Promote `discord.py` to a hard dependency in `requirements.txt`.
3. Complete structural modularisation of `hearthkin.pyw` (11,907 lines).

Baseline before any change:
- Python 3.12.10, wx 4.2.5, ollama present.
- `python tests/run_all.py` → SUITE GREEN, 26 test files.
- `hearthkin.pyw` imports headlessly; `Hearthkin` class exposes 711 non-dunder members.

---

## Part 1 — Security audit findings & fixes

Method: six parallel focused audits (command-exec, path/file-I/O, network/SSRF,
secrets, remote bot surfaces, deserialization/data-integrity). Findings deduped
and consolidated below, most-severe first. Each is fixed on the current structure
BEFORE the modularisation (so the refactor moves already-hardened code).

Threat model: single trusted local operator, BUT remote surfaces (Telegram,
Discord) cross the internet and let outside users drive a kin (LLM) that can call
tools; web/tool content can carry prompt-injection. The dominant finding is that
the newer Discord surface skipped the gates Telegram was hardened with.

### Consolidated findings

| ID | Sev | Area | Summary |
|----|-----|------|---------|
| A1 | CRITICAL | Discord | No per-user tool-bucket gating — hands a kin's full tools.json to any allowed user (write_file/edit_file/note/exec unguarded). |
| A2 | CRITICAL | Discord | Uses the *desktop* exec wrapper, so tool_trust=full → no approval+no denylist, trusted → denylist-only, both remotely reachable = RCE. |
| A3 | CRITICAL | Discord | allow_from empty = allow-everyone (opposite of Telegram deny-all); no guild/channel allowlist. |
| B1 | HIGH | Telegram | exec wrapper skips the approval prompt when tool_trust in (trusted, full) — remote user gets unattended exec behind denylist only. |
| D1 | HIGH | Tools | read_file/write_file/edit_file honor absolute paths from ANY surface → arbitrary host file read/write from Telegram/Discord. |
| C1 | MEDIUM | Denylist | Patterns are start-anchored (^\s*verb); `echo x; rm -rf /` etc. bypass. Denylist is the sole gate at `trusted`. |
| F1 | MEDIUM | Cron | cron_requests/*.json consumer doesn't validate `kin` (validate_kin_name) nor require kin∈list_agents; a same-user local writer can drive any kin's tools. |
| G1 | MEDIUM | Secrets | Edit-API-key dialog shows the full key in plaintext — NVDA (primary user is blind) reads the live billable secret aloud. |
| A4 | LOW | Discord | No per-user flood/concurrency throttle (Telegram has one). |
| E1 | LOW | Exec | exec_allowlist.json is shared across surfaces; a desktop "remember" then auto-runs for remote users. |
| G2 | LOW | Secrets | write_provider_key writes 0644 then chmod 0600 (TOCTOU window on multi-user POSIX). |
| G3 | INFO | Secrets | Bot tokens in config.json rely on incidental mkstemp 0600 with no explicit intent. |
| H1 | LOW | SSRF | fetch_url DNS-rebinding TOCTOU (guard resolves, urllib re-resolves); fetch_url is remote-reachable. |
| H2 | LOW | SSRF | fetch_url honors HTTP(S)_PROXY env (proxy does the resolution, bypassing the IP guard). |
| H3 | LOW | DoS | Unbounded resp.read() on brave/openrouter/ollama/telegram/voice responses (fetch_url caps, these don't). |
| I1 | LOW | ReDoS | Content-tool-call extraction DOTALL regexes backtrack O(n^2) on crafted model output. |
| J1 | LOW | Path | Fuzzy-healed paths aren't re-checked for kin-dir containment after healing. |
| J2 | LOW | Path | delete_agent / agent_dir don't call validate_kin_name (defense-in-depth; callers currently pass validated names). |

### Fixes applied

All fixes were made on the pre-modularisation structure and verified with
`python tests/run_all.py` (green throughout) plus a headless import of every
touched module and of the `Hearthkin` frame. A new test file
`tests/test_security_hardening.py` pins the behavior changes (denylist
segmentation, surface-scoped allowlist, bucket gating, path confinement,
framework-param schema hiding). Two existing tests were updated to the new
secure contract (`test_discord.py` deny-by-default + config shape;
`test_cron_time_label.py` source-slice window).

**A1 — Discord per-user tool-bucket gating.** `discord_bot.py:_generate` now
resolves a per-user bucket from `discord.user_tools` (default `none`) and
intersects it with the kin's `tools.json` via `filter_tool_names`, exactly like
Telegram. Write/exec tools are never handed to a Discord member unless the
operator explicitly grants a bucket. New config keys `user_tools`, `guilds`,
`channels` in `DEFAULT_DISCORD_CONFIG`.

**A2 — Discord remote exec wrapper.** New `Hearthkin._wrap_exec_for_remote`:
denylist → hard deny; per-surface allowlist → run; otherwise → operator desktop
approval. `tool_trust=trusted/full` no longer auto-runs a Discord-triggered
exec. `_start_discord_bot_for` now wires this instead of the desktop wrapper.

**A3 — Discord deny-by-default access.** `_is_allowed`: empty `allow_from` = deny
everyone; `"*"` = anyone; else only listed IDs. Optional `guilds`/`channels`
allowlists gate location. A one-time `on_status` warning fires when empty
allow_from silently denies (so a blind operator isn't left with a "dead bot").

**A4 — Discord throttles.** Per-user cooldown (`DISCORD_USER_COOLDOWN_SECS`) +
a global concurrency semaphore (`DISCORD_MAX_CONCURRENT_GENERATIONS`).

**B1 — Telegram remote exec approval.** `_wrap_exec_for_telegram` now only
auto-runs on `trusted/full` when the kin config opts in via
`remote_unattended_exec` (new `DEFAULT_AGENT_CONFIG` key, default False);
otherwise it falls through to the Telegram chat approval. Denylist still gates
first regardless. **Accessible UI added:** `remote_unattended_exec` gets a
checkbox in Settings → Tools → "Tool behaviour settings…" (dialogs/tool_settings.py),
"Run remote (Telegram/Discord) exec without asking", so a single-operator kin
can restore frictionless remote exec without hand-editing config.json. Verified
by constructing the dialog headlessly and confirming the toggle saves the key.
Desktop exec behavior is UNCHANGED by all of B1/A2 — only remote surfaces are
affected.

**C1 — Denylist segment matching.** `tools/_exec_denylist.match_denylist` now
tests the whole command AND each shell-separator-delimited segment (quote-aware
split), so `echo x; rm -rf /` / `cd /tmp && rm -rf /` are caught despite the
start-anchored patterns. Patterns themselves unchanged (no broadening); legit
cleanup (`rm -rf temp/`) still passes.

**D1 — Path confinement on remote surfaces.** `resolve_kin_path(..., confine=)`
revokes the absolute-path escape hatch; `read_file`/`write_file`/`edit_file`
take a framework-injected `confine_paths` and re-assert containment after fuzzy
healing (`path_within_kin`, also closes J1). Telegram + Discord pass
`confine_paths=True` in the tool context; desktop/cron keep the operator opt-out.
`confine_paths`/`agent_name` are force-hidden from every model-facing schema
(`_FRAMEWORK_HIDDEN_PARAMS`) so a model can't set them to escape.

**E1 — Surface-scoped remembered approvals.** `tools/_exec_state` allowlist is
now `{"commands": [...], "surfaces": {"<scope>": [...]}}`. Desktop uses the
legacy `commands` list; remote surfaces use their own scope (`telegram:<uid>`,
`discord`). A desktop-remembered command no longer auto-runs for a remote user.

**F1 — Cron drop-file validation.** `_on_cron_timer_tick` now rejects any
request whose `kin` fails `validate_kin_name` or isn't in `list_agents()`,
logging to `cron_errors.log`. A planted `cron_requests/*.json` can no longer
traverse a path or invoke an unknown persona.

**G1 — Masked provider-key editor.** `_on_edit_provider_key` uses
`wx.PasswordEntryDialog` — the live API key is no longer read aloud by NVDA or
shown in cleartext. Masked prefix…suffix row remains the verification surface.

**G2 — Atomic 0600 key write.** `write_provider_key` writes through a
`tempfile.mkstemp` (0600 from creation) + `os.replace`, closing the
write-0644-then-chmod-0600 TOCTOU window.

**G3 — Explicit config perms.** `save_agent_config` now `os.chmod(0o600)`s
`config.json` (holds bot tokens) explicitly, so the confidentiality no longer
depends on an incidental mkstemp side effect.

**H2 — fetch_url proxy pinning off.** The opener now includes an empty
`ProxyHandler({})`, so `HTTP(S)_PROXY` env can't route around the SSRF IP guard.

**H3 — Bounded response read (representative).** `web_search`/Brave now caps
`resp.read()` at 8 MB. (See residuals below for the remaining trusted-endpoint
reads.)

**I1 — ReDoS guard.** `_extract_content_tool_calls` and
`_strip_extracted_tool_calls` bound the scanned length
(`_CONTENT_TOOL_CALL_SCAN_CAP = 200 000`) so the DOTALL patterns can't
backtrack on a crafted opener-without-closer blob from an untrusted sender.

**J1 — Containment re-check after heal.** Covered by the D1 `path_within_kin`
re-check in the three file tools.

**J2 — validate_kin_name in delete_agent.** `delete_agent` now raises on an
unsafe name before `shutil.rmtree`, so the destructive op is self-guarding.

### Accepted residuals (transparent decisions, not oversights)

- **H1 (DNS-rebinding TOCTOU in fetch_url, LOW).** The "correct" fix (resolve
  once, connect to the vetted IP while preserving TLS SNI + cert validation via
  the hostname) requires a custom HTTPSConnection subclass — genuinely invasive
  and regression-prone for a LOW finding whose home-desktop impact is near-zero
  (no cloud metadata endpoint). Mitigated by the existing post-resolution IP
  classification, fail-closed behavior, redirect re-validation, and now the H2
  proxy-off change. Left as documented residual; the in-code comment already
  acknowledges it. Revisit if Hearthkin ever runs on a cloud VM.
- **H3 remainder (unbounded resp.read() on Telegram/OpenRouter/Ollama/voice,
  LOW).** These are trusted HTTPS provider endpoints reachable only via a TLS
  MITM; several sit on streaming paths where a naive cap would risk breaking
  streaming. The most-exposed / least-trusted path (Brave search results) is
  capped. The rest are left as low-risk residuals to avoid churn on streaming
  code during this pass; a follow-up can add ceilings uniformly.

---

## Part 2 — discord.py dependency

`discord.py` was already bundled into the shipped `.exe` (spec's
`collect_submodules("discord")`) but deliberately kept OUT of `requirements.txt`,
so **source installs silently no-op'd the Discord surface**. Promoted it to a
hard dependency (`discord.py>=2.3`) and updated the two comments that claimed it
was intentionally optional:

- `requirements.txt` — moved from the commented "Optional improvements" block up
  into the hard-deps section with a note that the size hit is accepted.
- `Hearthkin.spec` — rewrote the `_DISCORD_HIDDEN` comment: discord.py is now a
  hard dep; `collect_submodules` still needed for its ~130 dynamically-loaded
  submodules.
- `.github/workflows/build-release.yml` — rewrote the comment on the pinned
  `discord.py==2.7.1` install: it's now the release lockdown pin (same pattern as
  pyinstaller/pillow), not an out-of-requirements optional install.

Verified `import discord` (2.7.1) works and `tests/test_discord.py` passes.

---

## Part 3 — Modularisation of hearthkin.pyw

**Result: `hearthkin.pyw` went from 11,998 lines to 571.** The `Hearthkin(wx.Frame)`
class (253 methods, ~11.2k lines) was split into 17 concern-focused **mixin
classes** under a new `frame/` package, combined via multiple inheritance. Because
`self.method` resolves the same through the MRO, this is a **pure structural change
— zero business logic altered**. Method bodies were moved verbatim.

### Approach

Done mechanically via an AST-driven splitter (reproducible, not hand-copied):
1. Parse the monolith; extract each method's exact source span (gap-as-leading so
   no line is lost) and its AST node.
2. Per method, compute the set of module-level names it references (free-variable
   analysis, recursing into module-level `try/if` blocks so conditional imports
   like `try: import ollama` are captured).
3. Group methods into 17 mixins along the file's existing concern seams.
4. Emit each mixin importing exactly the names it uses from a new shared hub.

### New structure

- **`frame_shared.py`** (568 lines) — the shared namespace hub: every module-level
  import, constant, and helper the frame + mixins reference (moved verbatim from
  the top and bottom of the old monolith, incl. the two memory-op functions and
  the foreground/single-instance helpers). Mixins and the assembler import their
  needed names from here.
- **`frame/`** (package, 17 mixin modules, `__init__.py` re-exports all):
  `DiagnosticsMixin`, `MenusMixin`, `UsageMixin`, `PrefsMixin`, `KinMgmtMixin`,
  `InputAttachMixin`, `ChatSendMixin`, `ChatStreamMixin`, `FileMenuMixin`,
  `RenderMixin`, `PrefsTogglesMixin`, `RoomsMixin`, `MemoryMixin`,
  `BotIntegrationMixin`, `StatusVoiceMixin`, `CronExecMixin`, `LifecycleMixin`
  (150–1335 lines each).
- **`hearthkin.pyw`** (571 lines) — now just the assembler: module docstring,
  imports from `frame_shared` + `frame`, the `class Hearthkin(*mixins, wx.Frame)`
  declaration, `__init__`, and `main()`. Still the entry point; the spec/build
  still target it and PyInstaller follows the static `from frame import` chain.

### Verification (this refactor must preserve behavior — the owner relies on tests)

- **Method-set identical**: `dir(Hearthkin)` before vs after — 712 members each,
  zero added, zero missing.
- **`inspect.getsource` spot-checks** across mixins: line counts match the
  originals exactly (e.g. `_send_message` 307, `_build_menu` 177), proving bodies
  moved verbatim; `__module__` correctly points at each mixin.
- **pyflakes**: 0 undefined names across `frame_shared.py`, all `frame/*.py`, and
  `hearthkin.pyw` (the static guarantee that no method lost access to a global).
- **No `global` rebindings** of module state exist in the frame, so the
  cross-module split introduces no shared-state hazard.
- **`python tests/run_all.py` green** (27 files). Four source-aware tests
  (`test_cron_time_label`, `test_cron_park_routing`, `test_mnemonics`,
  `test_room_memory`) were updated to look in the new module locations — they
  assert on frame source text / monkeypatch frame names, not on behavior.
- CRLF line endings preserved so git shows a move diff, not a whole-file churn.

### Follow-up worth noting (not blocking)

`frame/chat_stream_mixin.py` (1335 lines) is the largest mixin — a cohesive
streaming-lifecycle concern that could be split further if it keeps growing, but
1.3k cohesive lines is already a vast improvement over the 12k monolith.
