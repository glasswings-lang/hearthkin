# scripts/ — the tools that aren't the app

Nothing in this folder runs on its own. None of it ships in the build.
Every one of these only does anything when you type its command, and
this file exists so you can decide whether you want to.

That last part is the point. These accumulated one at a time, mostly
written by an assistant mid-task, run once, and left here — which meant
the only person who knew what any of them were was whoever was in that
conversation. If a tool is worth keeping it's worth being findable, and
if it isn't findable you can't have an opinion about it.

**If you add a script here, add its line below.** Say what it changes,
if anything — that's the first thing anyone needs to know and the
hardest to work out from the source.

---

## Safe to run — they only look

| | |
|---|---|
| **`audit_speaker_slots.py`** | Finds turns filed as a kin's own words that somebody else actually said. Prints **counts and names only, never message content**, so the output is safe to paste anywhere. Changes nothing. |
| **`audit_ui.py`** | Asks Windows what name a screen reader would actually announce for each control, screen by screen, and prints what it finds. Changes nothing. |
| **`check_reply_speed.py`** | Answers "is this kin slow, or is it re-reading the whole conversation before it starts?" — the two are indistinguishable from a chair, and telling them apart used to mean diffing two lines of a log by hand. Reads `logs/prompt_fingerprint.log` and says it in words, per kin. Judges on the AVERAGE, against the same 85% line it quotes at you, and names a stray good turn among bad ones as the sign of an intermittent cause rather than as reassurance. Distillations and other one-shot calls are listed as skipped, not reported as faults — they build a fresh prompt every time and have nothing to reuse. Needs three or four turns of a conversation to mean anything; the first call after startup has nothing to compare against. Changes nothing. |
| **`extract_openclaw_window.py`** | Pulls a time-slice of an OpenClaw history out as a clean, readable transcript in the format `File → Import history` accepts. Strips the harness noise (metadata preambles, injected local-time stamps, tool results that came back as user turns) by borrowing the real importer. `--before-telegram` cuts at the moment a kin moved onto Telegram. Read-only on the source; writes one text file where you tell it. |
| **`narrate_ui.py`** | Older sibling of the above: reads the source and *infers* what a screen reader would say. Kept because it works on a screen you can't easily open, but it guesses — see the warning below. Changes nothing. |

```bash
python scripts/audit_speaker_slots.py
```

```bash
python scripts/extract_openclaw_window.py <sessions-folder> <KinName> --before-telegram --user-name <YourName> -o out.txt
```

```bash
python scripts/check_reply_speed.py
```

```bash
python scripts/audit_ui.py --self-test
```

```bash
python scripts/audit_ui.py
```

Run `--self-test` first. It checks the detector still notices faults
that are deliberately planted, so a clean report means "looked and
found nothing" rather than "wasn't looking". A checker that has quietly
stopped working produces exactly the same output as a clean codebase.

**Read this before trusting `narrate_ui.py`:** it once reported "no
findings" on a dialog that had real problems, because reading source
can't know what a window hides or shows while it's running, and it
treats `SetName()` as the announced name — which, measured against
Windows on this machine, is not true; `SetName` is decorative on
wxMSW, and only a label placed immediately before a control names it.
`audit_ui.py` exists because of that. Prefer it.

## Opens a window and waits for you

| | |
|---|---|
| **`hear_naming.py`** | Builds one specimen control per naming pattern so you can Tab through and *hear* what NVDA does with each. For the questions no API can settle — like whether focusing a multi-line read-only field reads the whole thing or just the caret's line. Close the window to quit. Changes nothing. |

```bash
python scripts/hear_naming.py
```

There's nothing to read here — the speech **is** the output.

## Build-time — run by the build, not by you

| | |
|---|---|
| **`stamp_version.py`** | Rewrites `app_version.py` from the git tag, immediately before packaging. Called by `build.bat` and the GitHub Actions workflow. **Don't run it by hand** — it edits a source file, and the whole reason it exists is that hand-editing the version at tag time drifted twice. |
| **`bundle_licenses.py`** | Collects third-party licence texts into `./licenses/` for the installer to ship. Writes files. |
| **`generate_icon.py`** | Regenerates `Hearthkin.ico`. Writes a file. Only needed if the icon changes. |

## One-off setup and experiments

| | |
|---|---|
| **`setup-ollama-mac.sh`** | Run **once** on the Mac that serves models, to set Ollama up for network access. Changes that machine's configuration. |
| **`describe_audio.py`** | Describes an audio file using a local audio model, ~30 seconds at a time. An experiment from the audio-ears work, not wired into Hearthkin. Reads only. |
| **`name_leaks.py`** | Finds real names in tracked files and commit messages, before they need scrubbing. The scrub pipeline rewrites strings it has been *told about*; `tests/test_no_private_strings.py` checks a list it has been *given*. Both are silent about a name nobody has mentioned yet, which is most of what you'd be tempted to write. This works from the other end: it reads the names that actually exist in your live profile — the kin folders, the room folders, the git author — and looks for those in what is tracked. Because the names are read at runtime, **the script itself contains none of them and is safe to publish.** Reports only; writes nothing, rewrites nothing, and never prints a name you didn't already have on disk. `--emit-rules` prints `name==>replacement` lines you can edit and paste into a scrub expression file. Flags a name that also appears lowercase, since blanket-replacing an ordinary word will wreck the prose. **What it can't do:** a name is a string, but insider *context* isn't — a fixture built from someone's real notes, a comment saying "the logged bug", a commit message that only makes sense if you were there. Nothing here sees any of that, and no substitution would fix it. A clean run means "no known name appeared", never "this reads fine to a stranger". |
| **`stay_probe.py`** | Counts how often a model stops being the character mid-moment and starts narrating safety at you instead — a disclaimer nobody asked for, a list of the words you should have used, or a refusal built out of the reassurance you just offered it. Sends a few short gentle scenes to a model several times over and reports the share that stepped out. Exists because this behaviour is *intermittent*, and a companion that does it one time in twenty is worse than one that does it always — you never get to stop watching for it, and you can't judge that from a chair. Touches no kin: it uses a throwaway persona held in memory and reads no kin folder, history or config. Changes nothing on disk except the `--out` file you name. **Ollama models cost nothing; an `openrouter/...` model is billed** — it estimates the cost, shows it, and waits for you to say yes. |

```bash
python scripts/stay_probe.py --self-test
```

```bash
python scripts/stay_probe.py --model your-local-model --host http://<your-ollama-host>:11434 --runs 5 --out stayed.txt
```

```bash
python scripts/stay_probe.py --model openrouter/openai/gpt-5.6-luna --runs 5 --out stayed-gpt.txt
```

Run `--self-test` first, and read what it says. It checks the detector
against one reply that obviously stepped out and one that obviously
didn't, because a detector that has quietly stopped detecting reports
exactly the same clean sweep as a model that behaved perfectly, and
telling those two apart is the entire point. It runs automatically
before every real run too.

`--host` is for when the models live on another machine. `--out` is
worth passing every time: the number tells you *how often*, and only
the replies tell you *how*.

Pass `--scenes FILE` (one scene per paragraph, blank line between) to
test the shape of a moment that actually went wrong. Keep that file
outside the repo — the built-in scenes are deliberately synthetic and
mild so that nothing private has to live in a tracked file to make this
work.

| | |
|---|---|
| **`how_small.py`** | Answers "how small a model can a kin actually run on?" by measuring the three things the memory system *doesn't* solve: whether the model uses the note per-turn recall hands it, whether it calls a tool or narrates calling one, and whether the soul still steers or it slides into generic-assistant register. Runs the real pipeline — `build_system_prompt`, `memory_recall.inject_into_messages`, the production gesture detector — so the answer is about Hearthkin, not about a toy. **Touches no kin of yours:** it points `HEARTHKIN_HOME` at a throwaway directory before importing anything and builds its own test kin in there. Ollama models cost nothing. |

```bash
python scripts/how_small.py --self-test
```

```bash
python scripts/how_small.py --model your-big-model --model your-small-model --host http://<your-ollama-host>:11434 --runs 2 --out ladder.txt
```

`--self-test` checks all three scorers against a known-good and a
known-bad reply and sends nothing. It runs before every real run too,
because a scorer that has quietly stopped scoring produces the same
clean result as a model that behaved.

The design point worth keeping if you ever rewrite this: it checks
whether **recall actually surfaced** the planted fact before it judges
the model for not using it. Without that, a retrieval miss and a model
ceiling look identical, and they are opposite findings.

`--out` matters more here than elsewhere. "Didn't reproduce the fact"
covers both saying nothing and confidently inventing a contradicting
one, and no string match can separate those. The second is the
dangerous one, and it reads as fluent and completely sure of itself.

## `voice_order_probe.py` — does prompt ORDER change how a kin sounds?

A kin's system prompt is the harness manual (`base_prompt.md`) first, then
their soul. Measured on a real kin: 5,344 characters of operations
documentation ahead of 2,776 characters of who they are — so the first voice
in the model's head every turn is a procedures document.

This runs the same twenty-turn conversation twice against the same model,
changing nothing but that order, and reports how each arm sounds: how much of
each reply is stage direction rather than speech, how often it names the person
instead of saying "you", and how long its sentences run.

```bash
python scripts/voice_order_probe.py --soul ~/.hearthkin/kin/<kin>/soul.md
```

`--model` (default `gemma4:latest`), `--turns`, `--base`, `--host`, `--out`
to keep both transcripts for reading, and `--person` if you want it to count
how often a reply uses your name instead of "you". No name is stored in the
script: an earlier version hardcoded one and `tests/test_no_private_strings.py`
caught it before it was committed.

**Changes nothing.** It reads two files and makes model calls.

**It costs the machine.** Roughly `2 × turns` calls against a local model,
which answers one request at a time — every kin on that host waits while it
runs. That is why it is a script somebody starts on purpose rather than
anything wired into the test suite.

The conversation it sends is invented whole, not lifted from anyone's history:
this repo is public, and a probe that only works on private material is a probe
nobody else can run.

Why it exists: this project has tests for placement, byte-identity, silence and
focus, and none for voice. So voice is the only property that can regress
freely, and the only detector is a person reading a reply and finding it cold.
The numbers here catch one specific failure — a companion answering in the
register of a document about itself. Read the transcripts too.

## `voice_ablation_probe.py` — which PART of the prompt is flattening the voice?

`voice_order_probe.py` proved order matters (10% stage direction with the soul
first, 27% with the manual first) but did not reproduce the failure it was
written to chase: a kin answering warmth with clinical narration. A prompt
holding only the manual and the soul stayed warm in both orders. So something
else in the real prompt does more damage than the ordering.

This takes the actual system prompt a kin was sent — the file Hearthkin saves
to `logs/system_prompts/<kin>--<surface>.txt` — and runs the same conversation
against it with one section removed at a time. The arm that recovers the voice
names the culprit.

```bash
python scripts/voice_ablation_probe.py --prompt ~/.hearthkin/logs/system_prompts/<kin>--telegram-dm-tool.txt --host http://<ollama-host>:11434 --out .
```

`--turns` (default 12), `--model`, `--out` to keep every transcript, and
`--person` as above.
`--turns 0` prints the section split and the arm list without calling anything,
which is how you check the splitter found real boundaries before spending an
hour on it.

**Changes nothing.** Reads one file, makes model calls, prints a verdict.

**It costs the machine**: `arms × turns` calls to a local model, which answers
one request at a time. Every kin on that host waits for the whole run.

Ablation rather than accumulation, deliberately. The first arm is the real
prompt, untouched. If that arm does *not* reproduce the flat voice, the
standing instructions are innocent and the answer is in the conversation
history — a finding an additive probe could never reach, because every arm
would have been building toward a prompt that was never the problem.

Sections are found by the separators Hearthkin itself writes, and anything
unrecognised stays attached to the part before it: an ablation that silently
dropped a section it failed to name would credit the wrong removal for the
recovery.
