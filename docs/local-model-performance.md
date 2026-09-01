# Running local models well (Ollama, especially on a Mac)

The practical guide to making a local model — one served by Ollama, often on a
separate Mac — fast and pleasant to talk to. This exists because getting it
right is genuinely fiddly and the knowledge used to live only in operator chat
sessions. Three audiences: the operator who just wants it fast (start here),
The operator debugging a slow kin (middle), and a developer who wants the
mechanics (end).

## Quick setup (do this once)

If your models run on a **separate Mac**, there is a one-time setup that makes
a real difference — especially for a kin who carries a lot of history.

Copy `scripts/setup-ollama-mac.sh` to the Mac and run it once:

    sh setup-ollama-mac.sh

Then fully quit and reopen Ollama (`killall Ollama; open -a Ollama`). That is
it — you never touch it again; it survives reboots. You do not need to
understand what it does (it turns on "flash attention," a faster way of doing
the same math — same replies, just quicker, and it stops slowing down as
conversations get long).

In the kin's **Settings → Model**, two settings also matter for local models:

- **Keep model loaded → "Until I close Ollama (never unload)"** for a kin you
  use regularly. Otherwise Ollama unloads the model after ~5 minutes idle and
  every reply after that pays a 20–60 second cold-load.
- **Context window (num_ctx)** controls how much conversation history is
  re-read each turn. Bigger = the kin keeps more in-context but every reply is
  heavier. Smaller = faster replies, less in-context history (the kin's
  distilled long-term memory in `memory.md` is unaffected either way).

## Make Ollama start and stay up by itself (never relaunch it by hand)

The Quick Setup above *tunes* Ollama, but it doesn't *supervise* it. If you run
Ollama by launching the menu-bar app, a crash, a quit, or a reboot leaves it
down until someone reopens it — and on a headless or hard-to-reach Mac, that
means the kins go silent and stay silent. The fix is to run Ollama as a
background service macOS keeps alive for you.

`scripts/setup-ollama-mac.sh` installs this: a LaunchAgent
(`com.hearthkin.ollama-serve`) that runs `ollama serve` directly and **relaunches
it automatically any time it exits** — crash, manual kill, anything. Kill it and
it's back in seconds; you never start it by hand again. It also pins the network
binding (`OLLAMA_HOST=0.0.0.0`, so other machines can reach it) and carries the
flash-attention / KV-cache tuning. **Once it's installed, stop launching the
Ollama menu-bar app** — only one process can own port 11434, so the app would
just collide with the service. (Quitting the app does not stop the service;
they're separate things.)

**The one gap it can't close by itself: a full cold reboot.** A LaunchAgent only
starts once someone is logged in. You'd normally close that gap with auto-login
— but **macOS blocks auto-login while FileVault disk encryption is on** (the
default). So after a power loss or full reboot, someone has to type the disk
password at the Mac's boot screen once; that can't be done over SSH (it happens
before the network is even up). After that single unlock, the service starts and
self-heals from then on. If unattended reboot recovery matters more to you than
at-rest encryption, you can turn FileVault off and enable auto-login — a
deliberate security trade, not a default. A small UPS (so the Mac never loses
power in the first place) is usually the better answer.

## "Why is the first reply slow, then everything's fast?"

Normal and expected — not a bug. The first message after the model loads hits a
cold cache, so it reads your whole conversation history into memory once: that
is the one slow reply. Every message after reuses that warmed cache and only
processes the new text you typed, so they are fast.

With **Keep model loaded → never unload**, the warmed cache stays put, so you
only hit the slow first reply after a genuine cold start — a Mac reboot, an
Ollama restart, or switching to a different kin and back (which can evict the
first kin's cache). Day to day you won't see it.

If **every** reply is slow (not just the first), something is off — see below.

## Debugging a slow local kin

**Start on the Hearthkin side, not the Mac.** The warm cache described above
only holds if the prompt is *identical from the start* each turn — a model keeps
its work for an unbroken run from the very beginning, and the first place two
prompts differ is where the reuse stops. So a prompt that quietly changes near
the front makes every reply behave like a cold one, on a model that is perfectly
healthy. This has been the answer more often than anything on the Mac, and it
was misdiagnosed as "the model is slow" three separate times before anyone
measured it. Measure first:

```bash
python scripts/check_reply_speed.py
```

Above 85% is healthy. A run of 0% means the prompt is being rewritten each turn
and no amount of Ollama tuning will help. It needs three or four turns of a
conversation to say anything — the first call after a launch has nothing to
compare against.

Two Hearthkin-side causes worth knowing, both fixed and both liable to be
reintroduced: notes the app files into a kin's history being gathered to the
front of the prompt (see `docs/design/prompt-cache-system-fold.md`), and a kin
whose memory notes are a long way behind distilling constantly — a large call on
the *same* model, which evicts the chat's cache and eats the model's time. On
one real kin that came to 66 minutes distilling against 24 minutes of
conversation in a day. Backlogs are paced now (Settings → Memory), but if a kin
was recently given a big imported history, expect it to be busy for a while.

If the reuse figure is healthy and replies are still slow, it's the host. Work
the Ollama machine (the Mac). From an SSH session or Terminal:

- **Is the model spilling to CPU?** `curl -s http://localhost:11434/api/ps` —
  compare `size_vram` to `size`. Equal = fully on GPU (good). `size_vram`
  smaller = part of the model spilled to CPU (slow); the context is too big for
  the machine's memory, or the model is too large. Drop num_ctx or use a
  smaller / more-quantized model.
- **What context is it loaded at?** Same command, `context_length` field. Far
  bigger than expected = a request asked for more than you intended (check the
  kin's num_ctx).
- **Is flash attention on?** `launchctl getenv OLLAMA_FLASH_ATTENTION` should
  print `1`. Blank = the Quick Setup above didn't run or didn't stick.
- **What's the prefill rate?** `tail -n 40 ~/.ollama/logs/server.log`, look at
  the `prompt eval ... tokens per second` line. A rate that *declines* as the
  token count climbs is the signature of flash attention being OFF.

A model that "runs fine in `ollama run` but is slow through Hearthkin" is
almost always context: `ollama run` sends a tiny prompt; Hearthkin sends the
full history. The slowness is prefill of that history, not the model itself.

## Quantization (size vs. quality vs. speed)

A model's quantization level (Q4, Q5, Q8...) is **bits per weight** — *higher
number = less compression = bigger, higher-quality, slower*. Q8 is near the
original precision (big, slow); Q4 is more compressed (smaller, faster, a touch
less precise). For local chat on Apple Silicon, **Q4_K_M or Q5_K_M** is usually
the sweet spot — roughly half the size and double the speed of Q8, with quality
loss that is usually subtle.

Quantization affects size and speed, **not character**. If a model doesn't
sound like the kin it is meant to be, that is model fit / fine-tuning, not the
quant — a Q8 of the same model sounds the same. To confirm, A/B the same prompt
through both quants; the voices match. Character is won with the right base
model and fine-tuning, not with a sampling setting or a re-quant.

## Mechanics (for developers)

- **`OLLAMA_FLASH_ATTENTION=1`** — without it, attention prefill is O(n²) in
  prompt length, so long prompts slow progressively (declining tok/s in the
  `prompt eval` log lines). Flash attention computes the identical result
  tile-by-tile without materializing the full attention matrix: same outputs,
  large speedup at long context, no quality change. Off only because some
  Ollama builds don't default it on.
- **`OLLAMA_KV_CACHE_TYPE=q8_0`** — quantizes the KV cache to 8-bit (requires
  flash attention). ~Halves KV-cache memory so larger contexts stay on the GPU.
  Near-lossless, but technically a precision reduction distinct from flash
  attention's exactness.
- **`launchctl setenv` does not survive a reboot** — the LaunchAgent installed
  by `setup-ollama-mac.sh` re-applies it at every login (`RunAtLoad`). If
  Ollama auto-launches at login it can occasionally start before the agent; a
  one-time "quit and reopen Ollama" fixes it.
- **`keep_alive`** — Ollama wants a *number* for second-counts and sentinels
  (-1 = forever, 0 = unload now) and a *string* only for unit durations
  ("30m"). A bare-integer string like "-1" trips Ollama's Go duration parser
  (`time: missing unit in duration "-1"`, HTTP 400). Hearthkin coerces this in
  `llm_backend._coerce_keep_alive` at every Ollama send point.
- **Preload warm-up** — `_maybe_preload_ollama_model` must warm at the kin's
  `num_ctx`; Ollama keys a resident instance by (model, num_ctx), so warming at
  the default context leaves the real send to cold-load anyway.
- **Prompt assembly** — `llm_backend._truncate_messages` keeps a stable system
  prefix (base + soul + memory) and front-drops oldest history pairs once over
  the `num_ctx`-derived cap. The stable prefix is cacheable across turns; the
  per-turn cost is prefill of the history window, which the slot cache largely
  reuses turn-to-turn (hence "slow first reply, fast after").
