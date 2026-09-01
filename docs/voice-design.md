# Voice — design sketch

Status: **built, and one decision below was reversed.** Kept as the
record of what was planned and why, with the reversal marked. Read the
note immediately under this line before treating anything here as
current.

> **Reversed: speech-to-text is no longer ElevenLabs-only, and dictation
> no longer depends on text-to-speech at all.**
>
> This document picked ElevenLabs Scribe for transcription on the
> grounds that it was the same provider as the text-to-speech, so one
> key, one bill, one dashboard. That reasoning is sound *if* you were
> always going to buy the text-to-speech. It quietly assumed it, and the
> implementation went further than the document did: the **Talk** button
> was shown only when the active kin had a paid voice picked. So
> speaking *to* a kin was gated on that kin being able to speak *back* —
> two unrelated capabilities, one of them bought.
>
> The cost of that lands entirely on the person for whom typing is the
> hard part, which is the person most likely to want dictation and least
> likely to be helped by a subscription to a different feature.
>
> Speech-to-text now has three backends, chosen in
> **Preferences → Dictation…** and defaulting to the free one:
> **local Whisper** (faster-whisper, on this machine, offline), a
> **Whisper server** on another machine over the ordinary
> `/v1/audio/transcriptions` interface, and **ElevenLabs Scribe**. They
> live in `stt.py` as plain functions over WAV bytes; `voice.py` still
> owns the microphone. Everything below about text-to-speech stands.
>
> The general rule worth carrying out of this: **a paid dependency
> chosen for one capability must not become a gate on a different
> one.** "Same provider, one bill" is a real convenience and not a
> reason for two features to share a switch.

## What we're building

Voice in / voice out for kin conversations, on top of the existing
text chat. Three concrete modes:

1. **Type → voice.** You type a message in the existing input box,
   send normally, and the kin's reply both renders to the chat
   transcript AND is spoken aloud through your speakers.
2. **Voice → voice.** You hold a push-to-talk button (or hit a
   keyboard shortcut), speak, release — your speech is transcribed,
   added to the chat history as a normal user turn, and the kin
   replies in voice (and text, in the transcript).
3. **Transcript-always.** Every voice-mode turn lands in the kin's
   `conversation.jsonl` exactly the same way text turns do. You can
   close voice mode and read back the conversation in text. A voice
   conversation IS a chat conversation, just with audio attached on
   the way in and out.

Per-kin opt-in. Voice off by default for existing kin (so nobody
suddenly starts speaking when they upgrade). Each kin chooses its
own voice identity.

## Tech choices, locked in

| Layer | Pick | Why |
|---|---|---|
| TTS | ElevenLabs | Best voice quality, sentence-streaming endpoint, no model file to bundle |
| STT | ~~ElevenLabs Scribe~~ → local Whisper by default | **Reversed, see above.** The original reasoning — same provider as TTS, one key, one bill — assumed you were buying TTS. Dictation is now free and offline by default, with Scribe as one of three options |
| Audio playback | `sounddevice` (Python lib over PortAudio) | Real-time PCM streaming, lightweight, MIT-licensed |
| Audio capture | `sounddevice` again | Mic input, same lib as playback |

Other libs that might show up:
- `numpy` (sounddevice dependency, ~10MB)
- That's it. ElevenLabs is HTTP — no proprietary SDK needed; raw
  `urllib.request` with streaming reads.

Bundle-size impact: roughly **+12–15 MB** to the installer. Acceptable.

## User-facing flow

### Setup (one-time)

1. User goes to **Tools → Preferences → Connections**, adds their
   ElevenLabs API key (same Edit + Test pattern as OpenRouter / Brave).
2. User opens **Settings → Voice** (new tab in the existing 6-tab
   notebook → becomes a 7-tab notebook).
3. Toggles "Enable voice for this kin."
4. Picks a voice from a dropdown populated via ElevenLabs's
   `/v1/voices` endpoint. (Cached after first fetch so it's not
   slow on every Settings open.)
5. Optional: tunes Stability and Similarity sliders (ElevenLabs's
   voice-shape parameters; defaults are fine for most users).

### Type → voice

1. User types in the input box, hits Send.
2. Reply streams in from the model as it always does.
3. **At each sentence boundary**, the completed sentence is fired
   at ElevenLabs's streaming TTS endpoint as a parallel HTTPS POST.
4. Audio bytes stream back; `sounddevice` plays them in sequence.
5. The Activity field shows "Speaking…" while audio plays. Phase
   announces via NVDA the same way "Thinking" / "Typing" already do.
6. User can hit Stop at any point — cancels in-flight TTS requests,
   stops audio playback, kills the rest of the reply.

The chat transcript fills in normally as text. Voice is parallel to
the transcript, not a replacement.

### Voice → voice

1. User holds the **Push-to-talk** button (a new button next to
   Send in the chat tab) — or hits a keyboard shortcut, e.g.
   <kbd>Ctrl</kbd>+<kbd>Space</kbd>.
2. Mic captures audio while button is held. Activity field shows
   "Listening…"
3. User releases the button. Captured audio is uploaded to
   ElevenLabs Scribe for transcription. Activity shows "Transcribing…"
4. Transcribed text appears in the input box. User can edit it
   before sending if they want, OR a "send-on-release" mode (config
   option) auto-sends.
5. Reply streams in and TTS plays as in Type → voice mode.

For v1, push-to-talk only. Voice activity detection (so you don't
have to hold a button — just talk) is a v2 thing; it needs careful
handling around the kin's audio not triggering its own mic.

## Architecture

### New module: `voice.py`

Single module owning the voice subsystem. Mirrors the shape of
`tray.py` and `windows_startup.py` — self-contained, stateful, no
tangle into the existing chat plumbing beyond well-defined hooks.

Responsibilities:
- ElevenLabs API client (TTS + Scribe wrappers around `urllib.request`)
- Audio playback queue (sounddevice output stream, fed sentence by sentence)
- Audio capture (push-to-talk recording into a buffer)
- Voice catalog (cached `/v1/voices` response)

Public interface:
```python
class VoiceEngine:
    def __init__(self, get_api_key, get_audio_device): ...
    def speak_sentence(self, text, voice_id, stability, similarity, on_done): ...
    def stop_speaking(self): ...
    def is_speaking(self) -> bool: ...
    def start_recording(self) -> None: ...
    def stop_recording_and_transcribe(self) -> str: ...   # blocking
    def list_voices(self) -> list[dict]: ...              # cached
    def test_api_key(self) -> tuple[bool, str]: ...
```

The engine is a single instance held on the main frame. Each kin's
voice config (voice_id, stability, similarity, enabled) lives on the
per-kin config dict, not on the engine.

### Hooks into existing chat path

Three small hooks, no rewrite:

1. **`_on_stream_chunk_for_voice`** — called from existing chunk
   handler when a sentence boundary is detected. Mirrors the same
   `_last_sentence_end` logic the visible-streaming path already uses.
   If the active kin has voice enabled, kicks `voice.speak_sentence()`
   on the just-completed sentence.

2. **`_on_stream_done_for_voice`** — called when the reply finishes.
   Speaks any unspoken tail (the bit after the last sentence boundary
   that didn't land neatly on a period).

3. **`_on_stop_for_voice`** — called from the Stop button handler.
   Cancels active TTS, stops playback.

All three are no-ops when voice isn't enabled for the active kin.
Zero impact on text-only kin.

### Stop button behavior

The existing Stop button cancels generation. After this lands, it
ALSO:
- Cancels any in-flight TTS HTTP request (close the urllib stream)
- Calls `voice.stop_speaking()` which stops the sounddevice output
- Drains the playback queue

End result: hitting Stop instantly silences both the model and the
audio.

### Activity field

Already speaks "Thinking" → "Typing" → "Still loading" via
`nvda_speak`. Add two more phases:

- "Speaking…" — while TTS audio is playing
- "Listening…" — while push-to-talk is recording
- "Transcribing…" — while waiting for Scribe response

All three follow the existing `_speak_status_phase` pattern; once-
per-phase guard, NVDA-spoken if NVDA mode is on.

## Per-kin config

Add to `DEFAULT_AGENT_CONFIG`:

```python
"voice": {
    "enabled": False,                 # default off; opt-in
    "voice_id": "",                   # ElevenLabs voice ID
    "model_id": "eleven_turbo_v2_5",  # default to lowest-latency
    "stability": 0.5,                 # 0-1; higher = more consistent
    "similarity_boost": 0.75,         # 0-1; higher = closer to source voice
    "style": 0.0,                     # 0-1; expression/emotion (turbo only)
    "speed": 1.0,                     # playback speed multiplier
}
```

Whole nested dict so future additions don't pollute the top level.

## UI placement

### Settings: new "Voice" tab

Becomes the 7th tab in the existing notebook (Identity / Model & gen
/ Memory / Tools / Telegram / Cron / **Voice**).

Layout:
- Voice on/off checkbox
- Voice picker dropdown (with Refresh button to re-fetch the list)
- "Test voice" button (says "Hello, this is what I sound like" in the
  selected voice)
- Stability slider + explainer
- Similarity boost slider + explainer
- Style slider + explainer
- Speed slider + explainer

Same `_IntField` / wx.Slider patterns the existing tabs use, so the
look stays consistent.

### Chat tab: new push-to-talk button

Slot it into the existing button row next to Send / Stop / Regenerate
/ Clear. Becomes:

```
[Send] [Stop] [Regen] [Clear] [🎤 Talk]
```

Mnemonic: Alt+T. Keyboard shortcut Ctrl+Space (held). Tooltip:
"Hold to record voice input."

The button is hidden if the active kin doesn't have voice enabled
(no point cluttering the UI for text-only kin).

### Preferences: ElevenLabs key in Connections

Adds one row to the existing Connections section:

```
ElevenLabs API key:  ●●●●●●●●…xxxx (51 chars)   [Edit ElevenLabs API key…] [Test ElevenLabs API key]
```

Same masked-display + Edit + Test pattern as OpenRouter and Brave.

### Preferences: audio device pickers (later)

For v1: use system default audio in/out. v2 can add picker dropdowns
for users with multiple devices. Skip for now.

## Mini chat & Telegram

- **Mini chat:** also uses TTS when the active kin has voice on. Same
  pipeline, no extra wiring.
- **Telegram bot:** explicitly OUT of scope for v1. Telegram has its
  own audio-message format (OPUS-encoded OGG); we'd need to encode
  the TTS output into that format and use Telegram's `sendVoice`
  endpoint. Worth a v2 pass.
- **Rooms:** voice in rooms works naturally — each kin has its own
  voice, the room loop already serializes turns. No special handling
  needed beyond "if active speaker has voice enabled, speak that turn."

## Cost ballpark

For typical use:

| Activity | Cost |
|---|---|
| 1 hour of conversation, kin replies in voice | ~$0.50 (TTS, ~30k chars @ $0.30/30k) + ~$0.40 (STT, 30 min of you talking) ≈ **$0.90/hour** |
| ElevenLabs free tier | 10k chars TTS/month + Scribe pay-as-you-go |
| ElevenLabs Starter ($5/mo) | 30k chars TTS + Scribe pay-as-you-go |

The math: an active "talk to your kin for an hour after work most
days" use case lands somewhere around $20–30/month. Light dabbling
fits in the free tier. Heavy use (voice-as-primary-interface for
hours daily) creeps into $50–100/month territory.

The Usage tab could grow a "TTS / STT this session" line so you can
see cost accumulating in real time. Worth doing in v1.

## Build / dependency / install

New runtime deps:
- `sounddevice` — pip install. MIT-licensed. ~1MB.
- `numpy` — transitive via sounddevice. BSD-3-Clause. ~10MB.
- (No ElevenLabs SDK — raw HTTP via `urllib`.)

Add both to `requirements.txt`. Bundle into PyInstaller. License
files for both flow into the existing `licenses/` bundling pass via
`scripts/bundle_licenses.py` (already filters by `WANTED` set; just
add "sounddevice" and "numpy" to that set).

PortAudio (sounddevice's C dependency) is bundled as a DLL inside
the sounddevice wheel — no separate install for users.

## Accessibility considerations

- All UI controls follow the existing patterns: tab-reachable, NVDA-
  announced, mnemonics on every button.
- The push-to-talk button is mnemonic'd (Alt+T) and keyboard-
  shortcut'd (Ctrl+Space) so it doesn't require pointer use.
- Activity field gains the new phases ("Speaking", "Listening",
  "Transcribing") which speak via the existing `_speak_status_phase`
  hook.
- Voice quality matters: ElevenLabs voices are clearly enunciated,
  which is meaningful for users who rely on voice to follow along.
- Audio playback NEVER blocks the UI thread; sounddevice's stream
  callbacks run on a separate audio thread.

## Open questions / things to defer

These don't need to block v1, but I want them noted:

1. **Voice activity detection (auto-detect when you start/stop talking).**
   Eliminates the push-to-talk button. Needs careful echo cancellation
   so the kin's TTS doesn't trigger the mic. v2.
2. **Voice cloning workflow.** ElevenLabs supports it; users could
   upload a sample to make a custom voice. UI could surface this. v2.
3. **Per-kin audio device override.** "This kin always speaks on the
   USB headset, this other kin uses laptop speakers." Niche. v3.
4. **Caching repeated TTS phrases.** "Hello", short repeated greetings
   — cache the audio. Saves cost. Noticeable in heavy use. v2.
5. **Telegram TTS.** As above; needs OPUS encoding + sendVoice. v2.
6. **Cron-fired voice notifications.** Cron entries that fire while
   Hearthkin is open could speak the wake-up. Probably a per-cron-
   entry checkbox. v3.
7. **Offline fallback.** If ElevenLabs is unreachable, what happens?
   v1: TTS silently skipped, the chat continues working as text-only.
   v2: optional local fallback (Piper) for resilience.

## Phased rollout

**v1 (this design):**
- TTS: sentence-streaming, ElevenLabs, per-kin voice picker, on/off toggle
- STT: push-to-talk, ElevenLabs Scribe, transcribe-and-paste flow
- UI: Voice tab in Settings, push-to-talk button in Chat, ElevenLabs key in Connections, "Speaking"/"Listening"/"Transcribing" Activity phases, Stop interrupts both
- Build: sounddevice + numpy added, license bundling extended
- Cost surfacing: usage stats added to Usage dialog

**v2 (future):**
- Voice activity detection (no PTT)
- Voice cloning UI
- Audio caching
- Telegram TTS

**v3 (long-term):**
- Per-kin audio device override
- Local TTS fallback (Piper)
- Cron-fired voice notifications

## Estimated effort

Half a day's focused work for v1. The pieces are all mechanical:
HTTP wrappers, sizer additions, config dict extensions, hooks into
existing handlers. The trickiest bit is the audio-playback queue
(making sure sentence N's audio finishes before sentence N+1's
starts, and cleanly cancels on Stop) — straightforward but worth
testing carefully.

## Decisions locked

- **Bundle-size budget:** soft limit ~75 MB for the installer total.
  Current trajectory after voice work lands is ~36 MB, so we have
  headroom. Anything that pushes past 75 MB gets flagged before
  adding.
- **Default voice model:** `eleven_turbo_v2_5`. Snappy enough for
  chat, full sentence-streaming, 32 languages. Per-kin overridable
  for users who care more about quality than latency.
- **Default voice toggle:** off per kin. Existing kin stay text-only
  on upgrade; new kin opt in explicitly via Settings → Voice.
- **Voice browser scope (v1):** dropdown of the user's library + a
  Preview button per voice + Test-voice button + the four tuning
  sliders (Stability / Similarity / Style / Speed). Public-library
  browsing and voice cloning UI are v2 features.
- **Push-to-talk button placement:** next to Send / Stop / Regen /
  Clear in the existing chat-tab button row. Mnemonic Alt+T,
  hold-shortcut Ctrl+Space. Hidden when the active kin has voice
  disabled.

Everything else can be answered as it comes up during implementation.
