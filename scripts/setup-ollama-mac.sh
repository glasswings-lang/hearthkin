#!/bin/sh
# setup-ollama-mac.sh — one-time setup for Ollama on macOS.
#
# Run this ONCE on the Mac that runs your models. It does two things:
#
#   1. TUNING — turns on flash attention and an 8-bit KV cache, so replies are
#      much faster on long conversations and stop slowing down as history grows.
#   2. SUPERVISION — runs Ollama as a background service that macOS keeps alive,
#      so it starts on login and RELAUNCHES ITSELF if it ever crashes, is
#      killed, or you restart it. You never start Ollama by hand again.
#
# You do not need to understand what it does. Run it once:
#
#     sh setup-ollama-mac.sh
#
# After it finishes, STOP opening the Ollama menu-bar app — the service owns
# port 11434 now, and the app would just collide with it.
#
# A NOTE ON REBOOTS: a background service only starts once someone is logged in.
# If FileVault disk encryption is on (the macOS default), a full reboot stops at
# the disk-unlock screen until someone types the password there in person — that
# can't be automated over the network. After that one unlock the service starts
# and self-heals. (Turning FileVault off would allow full auto-login but leaves
# the disk unencrypted at rest — a deliberate security trade, not a default. A
# small UPS, so the Mac never loses power, is usually the better answer.)
#
# For the curious: OLLAMA_FLASH_ATTENTION=1 = a faster way of doing the same math
# (identical replies, no quality change); OLLAMA_KV_CACHE_TYPE=q8_0 halves the
# memory the conversation cache uses so larger contexts stay on the GPU;
# OLLAMA_HOST=0.0.0.0 lets other machines on your network reach it. All standard
# for local inference on Apple Silicon.

set -e

# Find the ollama binary. The macOS app symlinks it into /usr/local/bin;
# Homebrew puts it in /opt/homebrew/bin. Fall back to whatever is on PATH.
OLLAMA_BIN="$(command -v ollama 2>/dev/null || true)"
if [ -z "$OLLAMA_BIN" ]; then
    for c in /usr/local/bin/ollama /opt/homebrew/bin/ollama; do
        [ -x "$c" ] && OLLAMA_BIN="$c" && break
    done
fi
if [ -z "$OLLAMA_BIN" ]; then
    echo "Could not find the 'ollama' command. Install Ollama first, then re-run."
    exit 1
fi

AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/com.hearthkin.ollama-serve.plist"
mkdir -p "$AGENTS" "$HOME/.ollama/logs"

# Note: unquoted heredoc so $OLLAMA_BIN / $HOME expand into the plist.
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.hearthkin.ollama-serve</string>
    <key>ProgramArguments</key>
    <array>
        <string>$OLLAMA_BIN</string>
        <string>serve</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>OLLAMA_HOST</key>
        <string>0.0.0.0</string>
        <key>OLLAMA_FLASH_ATTENTION</key>
        <string>1</string>
        <key>OLLAMA_KV_CACHE_TYPE</key>
        <string>q8_0</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>$HOME/.ollama/logs/serve-agent.out.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/.ollama/logs/serve-agent.err.log</string>
</dict>
</plist>
PLISTEOF

# If the menu-bar app is running, quit it so the service can own port 11434.
osascript -e 'tell application "Ollama" to quit' 2>/dev/null || true
# Wait (up to ~15s) for its server to release the port.
i=0
while [ "$i" -lt 15 ]; do
    lsof -nP -iTCP:11434 -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 1
    i=$((i + 1))
done

# Load (or reload) the service.
UID_NUM="$(id -u)"
# A pre-1.0 version of this script registered the service under a different
# label. That only ever existed on the original developer's machine and was
# migrated by hand, so there's no legacy label to boot out here — if you ever
# do hit "address already in use" on 11434, something else owns the port:
#   launchctl list | grep -i ollama
# will name it, and `launchctl bootout gui/$UID/<label>` clears it.
launchctl bootout "gui/$UID_NUM/com.hearthkin.ollama-serve" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>/dev/null || launchctl load "$PLIST" 2>/dev/null || true
launchctl kickstart "gui/$UID_NUM/com.hearthkin.ollama-serve" 2>/dev/null || true

# Verify it answers.
i=0
while [ "$i" -lt 25 ]; do
    curl -s --max-time 3 http://localhost:11434/api/version >/dev/null 2>&1 && break
    sleep 1
    i=$((i + 1))
done

if curl -s --max-time 5 http://localhost:11434/api/version >/dev/null 2>&1; then
    echo "Installed. Ollama is now a self-healing service: it starts on login and"
    echo "relaunches itself if it ever stops. You won't need to launch it by hand."
    echo "IMPORTANT: do NOT open the Ollama menu-bar app anymore — the service owns"
    echo "port 11434, and the app would collide with it."
else
    echo "Installed the service, but it isn't answering yet. Check the log:"
    echo "    $HOME/.ollama/logs/serve-agent.err.log"
fi
