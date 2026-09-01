"""RenderMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (_model_supports_thinking, datetime, format_ts_prefix,
                          json, load_app_prompt, os,
                          speaker_attribution_prefix)


class RenderMixin:

    # --- Display helpers --- #

    def _block_header(self, speaker, ts):
        if isinstance(ts, datetime.datetime):
            return f"{speaker} · {ts.strftime('%H:%M')}"
        return speaker

    def _append_block(self, speaker, text, ts=None):
        self.chat_display.AppendText(f"\n[{self._block_header(speaker, ts)}]\n{text}\n")

    def _append_block_header(self, speaker, ts=None):
        self.chat_display.AppendText(f"\n[{self._block_header(speaker, ts)}]\n")

    def _append_attachment_markers(self, refs):
        """Paint one '[image: filename.ext]' line per attachment ref.
        Used after _append_block for the user's outgoing turn so the
        chat transcript reflects what the model actually sees. Falls
        back to '(missing)' suffix if the file's gone, which can
        happen if the kin's attachments/ dir was pruned externally."""
        if not refs:
            return
        from kin_persistence import attachment_abspath
        for rel in refs:
            name = os.path.basename(rel) or rel
            ap = attachment_abspath(self.current_agent, rel) if self.current_agent else None
            suffix = "" if ap is not None else " (missing)"
            self.chat_display.AppendText(f"[image: {name}{suffix}]\n")

    def _guard_think_capability(self, model, requested_think, label):
        """Suppress think=True when the active model doesn't declare the
        `thinking` capability. Without this, Ollama 400s with "model does
        not support thinking" — common after a kin's model gets swapped to
        a non-reasoning model but the per-kin think toggle remains on.

        Returns the (possibly-corrected) think value to pass to chat().
        Unknown-capability case (None) is left alone so we don't second-
        guess remote / unseen models.

        OpenRouter models bypass this check: capability detection only
        works against the local Ollama daemon. OpenRouter's `reasoning`
        toggle is forwarded to the provider, who picks its own default."""
        if not requested_think:
            return False
        if isinstance(model, str) and model.startswith("openrouter/"):
            return requested_think
        supports = _model_supports_thinking(model)
        if supports is False:
            try:
                self._set_status(
                    f"{label}: model {model} doesn't support thinking; "
                    f"sending request without it."
                )
            except Exception:
                pass
            return False
        return requested_think

    def _guard_think_capability_effort(self, model, requested_effort, label):
        """Effort-tier variant of _guard_think_capability. If the model
        is local Ollama and doesn't declare the `thinking` capability,
        force the effort to "off" — sending a request with thinking on
        would 400 with "model does not support thinking".

        OpenRouter bypasses this check: capability detection only works
        against the local Ollama daemon. OpenRouter's reasoning controls
        are forwarded as-is and the provider decides what to honor."""
        if requested_effort == "off":
            return "off"
        if isinstance(model, str) and model.startswith("openrouter/"):
            return requested_effort
        supports = _model_supports_thinking(model)
        if supports is False:
            try:
                self._set_status(
                    f"{label}: model {model} doesn't support thinking; "
                    f"sending request without it."
                )
            except Exception:
                pass
            return "off"
        return requested_effort

    @staticmethod
    def _compaction_frontier(pair_count, keep_window):
        """Index of the first tool round-trip that is still sent VERBATIM.

        This exists because the obvious version — keep the last `keep_window`
        pairs — is quietly one of the most expensive lines in the app.

        A local model reuses its cached work only for an UNBROKEN run from the
        very start of the prompt. `pairs[-keep_window:]` is recomputed every
        turn, so each new tool call pushes one round-trip out of the window and
        rewrites it, mid-history, from full text into a one-line summary. That
        edit invalidates every token after it. Measured on a real kin: 22,000+
        tokens re-read from cold on turn after turn, about five minutes of
        prefill before a single word came back, on a conversation whose prefix
        had barely changed.

        So the frontier only moves in STEPS of `keep_window` pairs instead of
        one at a time. Between steps it is byte-identical, the prompt is
        genuinely append-only, and the cache holds. One turn in `keep_window`
        pays the re-read; the rest pay nothing.

        `ceil` rather than `floor` deliberately: the verbatim count then runs
        from 1 up to `keep_window` and never EXCEEDS it. Letting it overshoot
        would have grown the prompt past what the person configured, and an
        oversized context on local Ollama doesn't degrade — it returns nothing
        at all. The newest round-trip is always verbatim regardless, since the
        frontier can never reach `pair_count`.

        Pure and static: no state, no persistence, same answer for the same
        conversation every time. Any "remember what we compacted last turn"
        scheme would have to survive restarts and history edits to be correct.
        """
        if keep_window <= 0:
            return pair_count               # nothing survives; caller compacts all
        step = keep_window
        behind = pair_count - keep_window   # how many pairs are past the window
        if behind <= 0:
            return 0
        return -(-behind // step) * step    # ceil division, kept in ints

    def _compact_tool_history(self, conversation, keep_window):
        """Walk `conversation` and compact older tool-call round-trip pairs
        into single one-line system markers. Identifies a round-trip as an
        assistant turn with `tool_calls` followed by one or more `role=tool`
        result turns. The `keep_window` most recent pairs survive intact —
        the model gets full call/result detail for them, so multi-step
        tool sequences inside the recent window still work as expected.
        Older pairs are replaced by a single `role=system` line of the form

            [hearthkin: earlier tool call — name(args summary) → result preview]

        which costs ~80-200 tokens versus the original pair's potentially
        thousands. The kin still knows the call happened; verbose payload
        is gone. Distilled memory.md is where the long-term record of past
        tool actions accumulates (the distill prompt now surfaces tool
        calls as `[<kin> called tools: <names>]` annotations).

        Returns a new list — doesn't mutate the input. `conversation.json`
        on disk is unaffected; this only changes what gets sent to the
        model on the next request.

        `keep_window <= 0` means always compact (every pair becomes a
        marker, even the most recent). High values effectively disable
        compaction. 5 is the default — see DEFAULT_AGENT_CONFIG."""
        if not conversation:
            return []
        # Find all tool round-trip pair indices in chronological order.
        # A pair is (assistant_idx_with_tool_calls, [trailing role=tool indices]).
        pairs = []
        i = 0
        while i < len(conversation):
            msg = conversation[i]
            if (isinstance(msg, dict)
                    and msg.get("role") == "assistant"
                    and isinstance(msg.get("tool_calls"), list)
                    and msg["tool_calls"]):
                tool_idxs = []
                j = i + 1
                while (j < len(conversation)
                       and isinstance(conversation[j], dict)
                       and conversation[j].get("role") == "tool"):
                    tool_idxs.append(j)
                    j += 1
                pairs.append((i, tool_idxs))
                i = j
            else:
                i += 1
        if not pairs:
            return list(conversation)
        # Which pairs survive verbatim. NOT simply `pairs[-keep_window:]` —
        # see _compaction_frontier for why that one line cost minutes a turn.
        survivors = set()
        if keep_window > 0:
            for assistant_idx, tool_idxs in pairs[
                    self._compaction_frontier(len(pairs), keep_window):]:
                survivors.add(assistant_idx)
                survivors.update(tool_idxs)
        # Build per-assistant-idx summary message for the doomed pairs.
        summaries = {}
        drops = set()
        for assistant_idx, tool_idxs in pairs:
            if assistant_idx in survivors:
                continue
            assistant_msg = conversation[assistant_idx]
            tool_calls = assistant_msg.get("tool_calls") or []
            results_by_id = {}
            for t in tool_idxs:
                tmsg = conversation[t]
                results_by_id[tmsg.get("tool_call_id", "")] = tmsg.get("content") or ""
            parts = []
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name") or "?"
                args_raw = fn.get("arguments", "")
                args_summary = "..."
                try:
                    args = (json.loads(args_raw)
                            if isinstance(args_raw, str) and args_raw
                            else (args_raw or {}))
                    if isinstance(args, dict) and args:
                        # First key=value with short reprs; others elided.
                        items = []
                        for k, v in list(args.items())[:2]:
                            vr = repr(v)
                            if len(vr) > 40:
                                vr = vr[:37] + "..."
                            items.append(f"{k}={vr}")
                        args_summary = ", ".join(items)
                        if len(args) > 2:
                            args_summary += f", +{len(args) - 2} more"
                    elif isinstance(args, dict):
                        args_summary = ""
                except Exception:
                    pass
                result_text = results_by_id.get(tc.get("id", ""), "")
                preview = ""
                if result_text:
                    first_line = result_text.strip().splitlines()[:1]
                    preview = first_line[0] if first_line else ""
                    if len(preview) > 80:
                        preview = preview[:80] + "..."
                preview_part = f" → {preview}" if preview else ""
                parts.append(f"{name}({args_summary}){preview_part}")
            summary_text = load_app_prompt(
                "tool_compaction_marker").replace("{calls}", "; ".join(parts))
            summaries[assistant_idx] = {"role": "system", "content": summary_text}
            drops.update(tool_idxs)
        # Emit final list: replace assistant turns with summaries at their
        # positions, drop the matched tool turns, keep everything else
        # (user messages, non-tool assistant replies) untouched.
        out = []
        for idx, msg in enumerate(conversation):
            if idx in drops:
                continue
            if idx in summaries:
                out.append(summaries[idx])
                continue
            out.append(msg)
        return out

    def _history_entry_for_model(self, msg):
        """Translate one stored conversation message into the dict shape the
        chat backend expects. Preserves the fields the model needs to see:
        tool_calls on assistant turns (so the next turn pairs results with
        the call that asked for them), tool_call_id on role=tool turns
        (without it the API rejects the message), and thinking when present
        (gated by per-kin feed_thinking but kept on the stored side; the
        backend will pass it through if the model supports it).

        Surface filter: messages tagged `source="telegram:group:<id>"`
        belong to that group's context, not the desktop's. They live in
        conversation.jsonl for unified-storage purposes (so a desktop
        clear-chat wipes the group's history too), but the desktop's
        model context excludes them — otherwise the operator's chat
        would interleave with multi-participant group conversations
        and the kin would lose track of who it's actually talking to.
        Group surface reads conversation.jsonl with its own filter on
        the Telegram side (`_load_group_history`). Other source tags
        (`telegram:<user_id>` from shared DMs) ARE included — those
        ARE part of the operator's unified context.

        Returns None to drop malformed messages OR messages from
        another surface — better than feeding a broken shape to the
        API and getting a 400, or feeding cross-surface context that
        confuses the kin."""
        if not isinstance(msg, dict):
            return None
        source = (msg.get("source") or "")
        # Discord channels are multi-participant, same as Telegram groups:
        # stored in conversation.jsonl for unified storage (a desktop
        # clear-chat wipes them too) but kept OUT of the desktop's model
        # context so the operator's 1-on-1 chat doesn't interleave with a
        # server channel. Each Discord channel reads its own slice on the
        # Discord side (DiscordBot._channel_history).
        if source.startswith("telegram:group:") or source.startswith("discord:"):
            return None
        role = msg.get("role")
        if role not in ("user", "assistant", "system", "tool"):
            return None
        content = msg.get("content")
        tool_calls = msg.get("tool_calls")
        if role == "assistant" and content is None and isinstance(tool_calls, list):
            # "" (not None) for tool-call turns with no narrative.
            # Anthropic-via-OpenRouter degenerates the following turn
            # on null content; "" is the universal-safe shape. The
            # persistence layer's _clean_chat_message and the
            # llm_backend's chat() both also coerce — this is the
            # defensive boundary at the API-shape conversion point.
            entry = {"role": role, "content": "", "tool_calls": tool_calls}
        elif isinstance(content, str):
            entry = {"role": role, "content": content}
            if role == "assistant" and isinstance(tool_calls, list) and tool_calls:
                entry["tool_calls"] = tool_calls
        else:
            return None
        if role == "tool":
            tcid = msg.get("tool_call_id")
            if not isinstance(tcid, str):
                return None
            # Empty tool_call_id is allowed: Ollama-emitted tool calls often
            # have id="" (matched against the corresponding assistant turn's
            # tool_calls entry, which is also id=""). Dropping the message
            # here would orphan the assistant tool_calls turn — worse than
            # passing an empty-but-paired id through.
            entry["tool_call_id"] = tcid
        # Grounding in time: prepend "[YYYY-MM-DD HH:MM] " to USER turns
        # only. Applying it to assistant turns too (which we used to do)
        # made the kin see their own prior replies in the format they'd
        # been told not to emit — a self-reinforcing pattern that pulled
        # generation toward emitting matching prefixes and, in repeated
        # observation, contributed to degenerate-output episodes
        # (semantic chain walks, emoji walls). User-only keeps the
        # "when did the user say this" signal without the echo loop.
        # Tool / system turns skip — internal bookkeeping, no
        # timestamp needed.
        #
        # Sender attribution rides in the same prefix, for the same reason
        # the group surface puts it there: a multi-party history imported
        # from a file (a group log carried in from another platform) stores
        # each speaker's name in `sender_attribution` and strips it out of
        # the message body, so without this the kin reads three people as
        # one anonymous voice and merges them. Desktop-native turns carry
        # no attribution and stay bare — this only fires where a name was
        # actually recorded, so an ordinary 1-on-1 chat is unchanged.
        #
        # Safe only because the reply path strips speaker tags: naming
        # people in front of the model is what teaches a small model to
        # invent them. See the clean_kin_reply call in _on_stream_done —
        # that guard and this prefix are a package, don't ship one alone.
        if role == "user" and isinstance(entry.get("content"), str):
            ts_prefix = format_ts_prefix(msg.get("ts"))
            sender_prefix = speaker_attribution_prefix(
                msg.get("sender_attribution") or msg.get("sender_name") or "")
            if ts_prefix or sender_prefix:
                entry["content"] = ts_prefix + sender_prefix + entry["content"]
        # Pass image attachments through to the LLM dispatcher.
        # `attachments` is a list of relative paths under the kin
        # directory; llm_backend._expand_attachments_for_provider
        # reads each file and produces the provider-shaped payload
        # (Ollama images[] / OpenRouter image_url blocks) at send
        # time. We never inline base64 in the conversation file —
        # the kin's `attachments/` directory holds the bytes.
        if role == "user":
            atts = msg.get("attachments")
            if isinstance(atts, list) and atts:
                cleaned = [a for a in atts if isinstance(a, str) and a]
                if cleaned:
                    entry["attachments"] = cleaned
        # Pass `thinking` through to the model only when feed_thinking
        # is on for this kin. The field is always persisted (so the
        # record is complete and `recent_thinking` can surface it),
        # but feeding it back on subsequent turns costs context and
        # not every provider treats it as the model's own anyway.
        if isinstance(msg.get("thinking"), str) and msg["thinking"]:
            feed = False
            try:
                feed = bool((self.agent_cfg or {}).get("feed_thinking", False))
            except Exception:
                feed = False
            if feed:
                entry["thinking"] = msg["thinking"]
        return entry

    def _other_speakers_in_history(self):
        """Every name other than this kin's that appears as a speaker in the
        current conversation. Used to spot a reply that opens as one of
        them.

        These are exactly the names `_history_entry_for_model` inlines in
        front of the model, so this is the set the kin could be echoing —
        supplying it rather than pattern-matching brackets means a kin's own
        bracketed emote can never be mistaken for impersonation. Empty for
        an ordinary 1-on-1 chat, where no turn carries a speaker at all, so
        the extra pass costs nothing and does nothing there.

        Guarded throughout: this feeds a cleanup pass on the reply path, and
        a failure to enumerate names must never cost someone their reply."""
        names = set()
        try:
            own = (self.current_agent or "").strip().casefold()
            for msg in (self.conversation or []):
                if not isinstance(msg, dict):
                    continue
                for key in ("speaker", "sender_attribution", "sender_name"):
                    val = msg.get(key)
                    if not isinstance(val, str):
                        continue
                    val = val.strip().strip("[]").strip()
                    if val and val.casefold() != own:
                        names.add(val)
        except Exception:
            return set()
        return names

    def _refresh_load_older_button(self):
        """Show / hide the 'Load older messages' button based on
        whether the current render window covers everything. Called
        from _render_conversation after each repaint; also called
        from _load_agent and clear/regen paths via the same
        re-render hook. Re-runs the chat tab's sizer Layout so
        the chat_display reclaims the space when the button hides.
        """
        if not hasattr(self, "load_older_btn") or self.load_older_btn is None:
            return
        total = len(self.conversation)
        # `self.conversation` / `_render_window` / `current_agent` are all
        # SINGLE-KIN state, and _load_room clears none of them — the
        # _exit_room_mode() it calls first returns immediately when we came
        # from a 1-on-1 chat (its first line is `if self.current_room is
        # None: return`). So in a room, all three still describe whatever kin
        # you were last talking to privately, and this button would show up
        # wearing a count from THEIR conversation. Clicking it repainted that
        # kin's private 1-on-1 history over the room.
        #
        # Same class of bug the room path already guards for regen ("regen is
        # a single-kin concept", see _load_room) — this control just got
        # missed. Gate on current_room rather than trying to null out
        # current_agent, which _load_room deliberately keeps (it needs it for
        # the on-close distillation).
        should_show = (
            self.current_room is None
            and self.current_agent is not None
            and self._render_window > 0
            and self._render_window < total
        )
        try:
            if should_show:
                remaining = total - self._render_window
                self.load_older_btn.SetLabel(
                    f"Load &older messages ({remaining} older)"
                )
                if not self.load_older_btn.IsShown():
                    self.load_older_btn.Show()
                    parent = self.load_older_btn.GetParent()
                    if parent is not None:
                        parent.Layout()
            else:
                if self.load_older_btn.IsShown():
                    self.load_older_btn.Hide()
                    parent = self.load_older_btn.GetParent()
                    if parent is not None:
                        parent.Layout()
        except Exception:
            pass

    def _on_load_older(self, event):
        """Expand the render window by the same chunk size as the
        configured initial window, then repaint. If chat_history_window
        is 0 or missing (meaning render-all is the user's preference),
        fall back to loading 200 more per click — never want this
        button to be a no-op once it's visible.

        Status feedback: a brief message tells the user how many
        more messages just appeared. Keeps the action discoverable
        without a popup.
        """
        # Never in a room: self.conversation is the last 1-on-1 kin's private
        # history, and _render_conversation() below would paint it straight
        # over the room transcript. The button is hidden in rooms now (see
        # the visibility gate), but keep this guard — a hidden wx button can
        # still be reached, and the failure mode here is "your private chat
        # with one kin appears inside a room with three", which is not a
        # thing to leave to one layer.
        if self.current_room is not None:
            return
        if not self.current_agent:
            return
        total = len(self.conversation)
        if self._render_window >= total:
            return
        chunk = int(self.config.get("chat_history_window", 200) or 0)
        if chunk <= 0:
            chunk = 200
        prev = self._render_window
        self._render_window = min(total, self._render_window + chunk)
        self._render_conversation()
        added = self._render_window - prev
        self._set_status(
            f"Loaded {added} older messages "
            f"({self._render_window} of {total} now shown)."
        )

    def _render_conversation(self):
        """Repaint chat_display from self.conversation. Walks the message
        list rather than iterating once-per-message so tool-call round-trips
        (one assistant-with-tool_calls turn + one or more tool-result turns)
        can be rendered as paired [tool: ...] blocks matching the live
        _on_tool_call_display format. Without this, content=None assistant
        turns and role=tool turns would render as blocks reading "None" or
        as raw tool output attributed to the kin.

        Performance: collects all message blocks into a Python list and
        commits to the TextCtrl with a single SetValue() call. The
        previous shape called chat_display.AppendText() once per
        message, which is O(N²) on Win32 EDIT controls — each
        EM_REPLACESEL has to process the growing buffer, and with
        NVDA running each call also fires a UIA TextChanged event.
        For a kin with hundreds of messages of accumulated history,
        the per-message append showed up as visible load-lag plus
        proportional cost in the NVDA event queue. A single SetValue
        is O(N): one Win32 call, one paint, one accessibility event.

        Note: per-message styling (e.g. gray italic for reasoning
        blocks) isn't applied here. That styling lives on the live
        turn-end path (_on_tool_call_display, etc.); persisted
        history renders in default style. Adding per-block styling
        back would require either tagging each segment for a post-
        paint SetStyle pass or replacing the TextCtrl with a
        RichTextCtrl — both bigger changes than the lag fix needs.

        Windowing: only the LAST self._render_window messages are
        rendered. The full conversation stays in self.conversation
        (so regen, token estimates, and persistence still operate
        on everything). The starting index walks BACK to the
        nearest user message so a tool-call round-trip pair never
        gets split with the assistant half above the window and the
        tool result inside it — which would have rendered as an
        orphaned [tool: ...] block. A "Load older messages" button
        above the chat display expands the window in chunks of the
        configured size; the button updates its visibility through
        _refresh_load_older_button each time we re-render.
        """
        parts = []
        n = len(self.conversation)
        speaker = self.current_agent or "Model"
        # Compute the windowed start index. _render_window is set
        # in _load_agent based on app preference. 0 (no kin) or a
        # value >= n means "render everything".
        window = self._render_window if self._render_window > 0 else n
        if window >= n:
            i = 0
        else:
            i = max(0, n - window)
            # Walk back to a user message so we don't start at an
            # orphaned assistant-tool turn pair. Defensive isinstance
            # check on each step so a malformed (non-dict) entry can't
            # AttributeError mid-loop (audit H6).
            while i > 0:
                msg = self.conversation[i]
                if isinstance(msg, dict) and msg.get("role") == "user":
                    break
                i -= 1
        while i < n:
            msg = self.conversation[i]
            role = msg.get("role")
            ts = msg.get("ts", "")
            ts_obj = None
            if ts:
                try:
                    ts_obj = datetime.datetime.fromisoformat(ts)
                except ValueError:
                    ts_obj = None
            if role == "user":
                # "You" for the person at this keyboard; the recorded name
                # for anybody else. Imported multi-party history (a group
                # log carried in from another platform) puts several people
                # in the user slot, and labelling all of them "You" made a
                # three-way conversation read back as a monologue. Turns
                # with no recorded speaker — every desktop-native one — are
                # unchanged.
                who = (msg.get("speaker") or "").strip()
                try:
                    own_name = (self.config.get("user_name", "") or "").strip()
                except Exception:
                    own_name = ""
                if not who or (own_name and who == own_name):
                    who = "You"
                header = self._block_header(who, ts_obj)
                parts.append(f"\n[{header}]\n{msg.get('content', '') or ''}\n")
                # Image-attachment markers — one line per attached file,
                # rendered the same way as the live-send path so the
                # transcript stays consistent across save/reload.
                atts = msg.get("attachments")
                if isinstance(atts, list):
                    for rel in atts:
                        if not isinstance(rel, str) or not rel:
                            continue
                        name = os.path.basename(rel) or rel
                        parts.append(f"[image: {name}]\n")
                i += 1
            elif role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                content = msg.get("content")
                # Pair each tool_call with the tool-result turn that follows.
                j = i + 1
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name", "")
                    args_raw = fn.get("arguments", "{}")
                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    tcid = tc.get("id")
                    result_text = ""
                    if (j < n
                            and self.conversation[j].get("role") == "tool"
                            and self.conversation[j].get("tool_call_id") == tcid):
                        result_text = self.conversation[j].get("content", "") or ""
                        j += 1
                    try:
                        args_str = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
                    except Exception:
                        args_str = "?"
                    if len(args_str) > 200:
                        args_str = args_str[:200] + "..."
                    if len(result_text) > 500:
                        result_text = result_text[:500] + "\n... [truncated]"
                    header = self._block_header(speaker, ts_obj)
                    parts.append(
                        f"\n[{header}]\n[tool: {name}({args_str})]\n{result_text}\n"
                    )
                # The actual reply content (a tool-only turn has content=None
                # and is fully described by the tool blocks above).
                if isinstance(content, str) and content:
                    header = self._block_header(speaker, ts_obj)
                    parts.append(f"\n[{header}]\n{content}\n")
                i = j
            elif role == "tool":
                # Orphan tool turn (not paired with a preceding assistant in
                # the walk above). Shouldn't happen with well-formed history;
                # skip rather than render as a bare attribution.
                i += 1
            else:
                # system / unknown — skip
                i += 1
        # Single SetValue commits the whole rendered history in one
        # shot. ChangeValue would suppress the EVT_TEXT event; we
        # use SetValue because chat_display is read-only and isn't
        # bound to EVT_TEXT anyway — either method paints the same.
        self.chat_display.SetValue("".join(parts))
        # Show or hide the "Load older messages" button based on
        # whether the window covers the full conversation.
        self._refresh_load_older_button()
