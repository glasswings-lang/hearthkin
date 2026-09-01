# tff: "describe a critter" — give the AI side the human's make-a-species wizard

> **Status (2026-07-06): BUILT.** Shipped in the tff repo (`C:\git-src\tff`,
> remote glasswings-lang/time-for-family): new GUI-free `tff_species_author.py`
> holds the one shared writer (`write_species_files`) + `build_and_write_species`
> + the shared `NEW_SPECIES_DEFAULTS`; the wx `SpeciesEditorDialog.on_save` was
> refactored to call the same writer (so human- and kin-made species are
> byte-shape identical — proven in test); the multi-turn walkthrough
> ("let's make a new critter" → a few plain questions → an adoptable species)
> lives in `tff_play.py` and is reachable through the `tff` tool. Regression
> guard: `dev/species_author_smoke.py` (26 checks). Hearthkin side: added
> `tff_species_author.py` to the bundled-game file list in `Hearthkin.spec` so
> frozen builds include it. What the plan describes below is what landed;
> deferred bits are noted inline. The rest of this doc is kept as the design
> record.

**Origin (2026-07-06):** Brook (a local-model kin) *came up with the idea itself* of
making new creatures for the tff park, tried repeatedly, and could never do it —
every "write owl.json" attempt was a gesture (`*paws at keyboard* here we go!!`),
never any content. Diagnosis: a tff species file is a strict config (a dozen+
fields, ages in raw **seconds**, arrays, decimals). The **human** play mode has a
full wizard for this — `SpeciesEditorDialog` in `tff_editors.py`, titled "Add new
species," with sensible defaults and friendly duration inputs ("2 hours"), and the
*code* writes the JSON. The **headless / AI** path (`tff_play.py`, invoked by kin
via the `tff(command)` tool → `GameHost.run`) never got that wizard — we assumed the
model could write the file itself. It can't, and shouldn't have to. Reads work for
these models (tiny output); producing a whole structured file does not. The fix is
to cut the kin the same door humans already have.

## What exists to reuse (do NOT reinvent)
- `tff_editors.py` `SpeciesEditorDialog.on_save` (~954–1250): builds the `spec` dict
  and writes `SPECIES_DIR/<id>.json` + a text-pool dir under `TEXT_DIR/species/<text_directory>/`.
  **It's welded to wx** (`self.controls[...].GetValue()`, `wx.MessageBox` on error).
- Brand-new-species **defaults** (~388–423): starter ages, breeding/elder ages,
  twin/disability 0.0, labels ("Pet"/"litter"/"female"…), first room type pre-picked.
- Friendly **duration parser** ("5 minutes"/"2 hours"/"3 days" → seconds): `add_duration`
  helper (~461) + the parse used around ~553. Find and reuse the actual parse fn.
- Species shape (from `assets/types/species/*.json`, e.g. `bird.json`): name, name_plural,
  sex labels/shorts, care_action_label, litter labels, *_seconds age fields, twin_chance,
  disability_chance, compatible_room_types[], text_directory, name_generation.
- Text pools a species needs: name_pool_f/m, descriptions, pet_responses, colors,
  disabilities (empty is OK to start).

## Build plan
1. **Extract a GUI-free core.** New pure fn (in `tff_editors.py` or a new
   `tff_species_author.py`): `build_and_write_species(values: dict) -> (species_id, error)`.
   It takes plain values (name, name_plural, colors, room_type, growth pace phrases,
   litter size…), merges the brand-new-species defaults, parses friendly durations,
   validates (name/id present, id not taken, min≤max, room type non-empty), writes the
   `<id>.json` AND the text-pool dir. **Then refactor `on_save` to gather control values
   into that same dict and call this fn** — so GUI and headless share ONE writer and can't
   diverge. Keep `on_save`'s wx error popups by having it surface the returned error.
2. **Headless walkthrough** (the kin-facing side). Multi-turn, because each kin turn is one
   `tff()` call — so persist an in-progress species in the save (`pending_species` blob:
   answers-so-far + next-question index). Flow:
   - trigger: `tff("new critter")` / "let's make an owl" (or from park mode) → start session,
     return question 1.
   - each subsequent `tff(<answer>)` while a session is pending → store answer, return next Q.
   - Questions, plain, few (rest defaults): name (+plural), colors, where they live
     (indoor/aviary/outdoor → room type), roughly how fast they grow ("or I'll pick a normal
     pace"), litter size (optional). "skip"/"you pick"/empty → default that field.
   - On completion → call `build_and_write_species` → clear session → "Done — owls live in
     your park now." Forgiving: bad answer re-asks, doesn't crash.
   - Also accept a one-shot description for kin that'd rather say it all at once; map what's
     recognizable, default the rest.
3. **Wire the trigger** into `tff_play.py`'s command dispatch (and expose via the `tff` tool).
   Fits the tool-groups idea: this is the tff "author" capability, handed over on
   creation-intent phrases.

## Testing (must pass before calling it done)
- Headless: create a species via `build_and_write_species` with minimal answers → the JSON
  loads via the game's species loader and is shape-identical to a human-made one; text pools
  exist so name/description generation works.
- The human `SpeciesEditorDialog` still works after the refactor (run `dev/ui_smoke.py`).
- End-to-end: a scripted kin walkthrough (start → answers → file written → creature adoptable).

## Risks / notes
- Live game repo (`C:\git-src\tff`); the WIP at `C:\Users\<you>\tff` is separate — do NOT touch it.
- The load-bearing correctness rule: **one shared writer.** A parallel headless writer that
  drifts from the GUI one is the failure mode to avoid.
- Text pools are not optional — a species with no name pool / colors may fail at runtime.
- Keep every kin-facing question plain-language; the kin answers in prose, never sees JSON.
