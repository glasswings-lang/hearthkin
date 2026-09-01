# Room tool round-trip persistence

**Status:** Designed, not built. Deferred from the 2026-06-01 cross-surface empty-reply session because (a) the operator doesn't actively use rooms and (b) rooms are the destabilization-prone surface and warrant a fresher session for code work touching them.

**Source:** the operator 2026-06-01 (asked "should we fix that room thing while we're at it" after the cross-surface salvage port in commit 4d6c63e); the v1 limitation has been documented as a code comment in `_run_room_tool_loop_inline` (hearthkin.pyw) since the rooms-with-tools work landed in May.

---

## The limitation

When a kin in a multi-kin room uses a tool:

1. `_run_room_tool_loop_inline` calls `llm_backend.run_tool_loop`, which produces a `ChatResult` with `messages_added` containing the intermediate `assistant`-with-`tool_calls` turn(s) + the `role: tool` result turn(s).

2. The room handler stashes those into `_pending_room_tool_history` (added in commit 4d6c63e to enable the empty-reply salvage in rooms).

3. After the salvage scan, `_pending_room_tool_history` is cleared without ever appending its contents to `self.room_conversation`. Only the kin's final assistant turn — speaker-tagged — gets persisted.

4. On the kin's NEXT room turn, when `_run_room_streaming_inline` / `_run_room_tool_loop_inline` builds context, it reads `self.room_conversation` and sees only the final reply. The kin has no record of having called the tool, what it returned, or what they thought before responding.

**Symptom:** A room kin might re-run the same `memory_search` across multiple turns because they don't remember doing it. Or pull up `read_file` on the same file repeatedly. Annoying but not actively broken — no silent failures, no wrong-shape user experience. The salvage from commit 4d6c63e still covers the empty-after-tool case in rooms.

## Why it was deferred at v1

Look at the room context-builder loop at `hearthkin.pyw:6466` — it handles three turn shapes (user / other-kin's assistant / own assistant) and has no concept of tool round-trips. Persisting tool turns without updating that loop would either:

- Pass `role: tool` turns through unchanged into Anthropic/Ollama (provider errors if the tool_call_id isn't paired with the matching assistant-tool_calls turn the provider sees as immediately preceding).
- Have other kin "overhearing" each other's private memory (tool results contain the substance of what was looked up).

Both are bad. Fixing rooms-with-tools properly requires:

## Implementation sketch

Three pieces, in order:

### 1. Persist intermediate turns with speaker tag (~15 lines)

In `_on_room_kin_done` (around line 6853 in hearthkin.pyw), after the kin's final `room_msg` is appended to `self.room_conversation`, also append the contents of `self._pending_room_tool_history`:

- Each intermediate `assistant`-with-`tool_calls` turn gets `speaker: <self._current_room_speaker>` added.
- Each `role: tool` turn gets the same speaker tag.
- Order: intermediate turns INTERSPERSED with the final assistant turn would be more accurate (kin said X + called tool → tool returned Y → kin said Z final). But putting them BEFORE the final assistant turn (the current `_pending_tool_history` pattern from desktop) is structurally simpler and matches the OpenAI/Anthropic expected message order.

After appending, clear `_pending_room_tool_history` (already done; just move the clear after the append).

### 2. Update the room context-builder (~50 lines)

In `_run_room_streaming_inline` / `_run_room_tool_loop_inline` (around line 6466), the message-building loop needs to handle four more turn shapes:

| Turn shape | Current kin's? | Action |
|---|---|---|
| `assistant` + `tool_calls` | Own | Forward with `tool_calls` field intact + content. Model sees their own past tool call. |
| `assistant` + `tool_calls` | Other kin | Forward content only as `[OtherKin]: <content>`. Drop `tool_calls`. Other kin's content is fair game; their tool usage isn't. |
| `role: tool` | Own (by `speaker` tag) | Forward as `role: tool` with `tool_call_id` matching the preceding assistant-with-tool_calls. |
| `role: tool` | Other kin | Skip entirely. Overhearing another kin's tool result is overhearing private memory. |

The current branches at lines 6479-6495 add a fifth state: own tool-loop turns + filter other kins' to assistant-content-only.

Key edge case: provider message-shape validity. Anthropic and OpenRouter require `role: tool` turns to immediately follow an `assistant`-with-matching-`tool_calls` turn. If filtering other kins' tool turns leaves an orphaned own-kin tool turn (assistant-with-tool_calls survives but its tool result turn was somehow dropped), the provider 400s. Belt-and-suspenders: track tool_call_ids during the build pass and only emit a `role: tool` turn whose `tool_call_id` matches one in the immediately-preceding `assistant`-with-`tool_calls` turn. Drop unmatched tool turns.

### 3. Render and load handling

`_render_conversation` for rooms (referenced at hearthkin.pyw:6144) needs to handle the new turn shapes when re-painting the chat display on kin-switch / room reload. Probably matches the 1-on-1 path's tool-block rendering — `[tool: name(args)]\n<result>` paired blocks for own-kin turns, suppress for other kins.

`_clean_chat_message` in `kin_persistence.py` should already accept these turn shapes (it does for the 1-on-1 path per the Bug A fix from 2026-05-11). Verify on first build that the room load path doesn't reject the new turn shapes.

`save_room_conversation` writes the conversation list as JSON — handles whatever shape's in memory. Should "just work."

## Backward compatibility

Existing room files don't have intermediate tool turns. Loading them with the updated context-builder is a no-op (no `assistant`-with-`tool_calls` turns to process, no `role: tool` turns to filter). Pure additive change; no migration needed.

Existing room files with the future tool turns continue to load cleanly under older Hearthkin versions too — older versions just skip the unrecognized turn shapes (the current context-builder loop has an `else` that drops everything not matching the three known shapes).

## Open questions to settle before implementation

- **Cross-kin visibility of tool USAGE (vs results)**: should kin A see "Kin B used memory_search" as descriptive text, or be completely invisible? The "skip entirely" approach is safer (no overhearing). The descriptive approach gives richer multi-kin coordination ("SpeakerTen just looked something up, I should wait or pick up").
- **Tool-call display in chat**: when other kins use tools, does the chat display show their `[tool: name(args)]` block or just their final reply? Current rooms don't render any tool blocks for any kin (rooms don't currently call `_on_tool_call_display`). Implementing this is a separate visibility question.
- **Room conversation file growth**: persisting tool turns multiplies file size, especially for tool-heavy kin. Probably fine in practice but worth measuring.

## Cross-references

- Commit `4d6c63e` — empty-reply salvage in rooms. Operates on the per-turn `added_turns` from `run_tool_loop`, sidesteps this persistence gap entirely for the salvage case.
- `_run_room_tool_loop_inline` in `hearthkin.pyw` — current v1 stash of `_pending_room_tool_history`.
- `_run_room_streaming_inline` / room context-builder loop in `hearthkin.pyw` — line 6466ish, the loop that needs updating.
- Bug A fix from 2026-05-11 — established the 1-on-1 tool round-trip persistence pattern. The room fix mirrors it but adds the speaker-filtering layer.
- `docs/design/multi-kin-rooms-shared-history.md` — separate proposal for shared-vs-distinct kin context in rooms. Different concern but adjacent code path.
