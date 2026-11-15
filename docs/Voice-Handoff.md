# Voiced dialogue: handoff notes

Written 2026-11-15 in the Balloon repo and moved here when this project got
its own repo. It records what we learned and the design we converged on.
The addon skeleton and helper in this repo implement the design below;
`docs/Protocol.md` is the file contract between them.

## Goal

A Spoken-style reader for FFXI: NPC dialogue read aloud by AI-generated
voices, with Balloon supplying the text and the player able to let speech
drive the pacing (advance when the line finishes, hold while it plays).

## Constraints discovered

**The player uses a controller.** Windower's Lua `keyboard` event only sees
keyboard input and there is no gamepad event in Windower 4. **Solved** on
2026-11-15 by `native/balloonpad.c`: Windower addons can `require` a native
Lua C module linked against `LuaCore.dll` (XIPivot does this), so Balloon
polls XInput itself from `prerender`. A face-button press while a prompted
balloon is up now calls `advance()`. The same signal is what the voice
reader needs to know the player moved on. See the Gamepad section of
CLAUDE.md.

**Advancing dialogue sends no packet.** The client runs NPC event scripts
locally from its DAT files. The server starts an event with one packet and
hears back (outgoing `0x5B`) only at choice menus or when the event ends.
Paging through text is invisible on the wire. `//bl debug chunk` now logs
outgoing packet ids so this can be re-checked in game; it was not yet
confirmed empirically because the first attempt crashed the client (the old
incoming dump printed raw packet bytes; fixed in 2ee492e).

**State flags don't help.** `windower.ffxi.get_info().menu_open` and
`get_player().status == 4` (Event) stay set for the whole conversation and
don't change on advance.

**What an addon does learn.** The next line (`incoming text`) or the NPC
release packet (`0x52`) arrives immediately after any advance. For a voice
reader that is the same signal: stop the current line, speak the next or
stop. The only gap is a cutscene that animates after an advance with no
text, where speech runs on briefly.

**Native code is available after all.** The same native-module route could
read the client's dialogue-wait flag from memory or read choice-menu text
(which never reaches the chat log) if those ever matter; the plugin SDK is
not needed. Blocking controller input until speech ends is still not
possible this way, since the module only observes XInput. Ashita v4 exposes
memory reads and controller events to Lua addons, but it is a different
launcher from the Lumoria flatpak in use.

## What the Windower install already provides

Checked against `~/.var/app/net.windower.Lumoria/.../Windower4`:

- `addons/libs` ships LuaSocket with `ssl.lua` and `json.lua`, so an addon
  can call an HTTPS TTS API directly. Calls are blocking, so either use
  non-blocking sockets polled from `prerender` or hand off to a helper.
- `windower.play_sound(path)` plays a WAV. The installed `pop` addon uses
  it. There is no way to stop a sound once started.
- Windower's `setkey` console command injects a key press, which would let
  an addon send Enter to auto-advance. **Not yet verified under Wine**; this
  is the first thing to test.
- Balloon already extracts the speaker name, converts Shift-JIS, strips
  control codes, and knows whether a line is prompted (`0x7F 0x31`) or timed.

## Proposed architecture

A sibling addon (or a mode in Balloon) plus a small helper process on the
Linux host. The Wine prefix is a plain host directory, so a file-drop queue
needs no networking.

1. On each dialogue line, the addon writes `{speaker, text, prompted}` to a
   queue file and notes a sequence number.
2. The helper watches the queue, looks up a cached WAV keyed by
   hash(speaker, text), generates it if missing, plays it on the host, and
   writes a "done" marker.
3. The addon polls for the marker from `prerender` (every ~100 ms, not every
   frame). On a prompted line with auto-advance enabled it sends Enter via
   `setkey`. In manual mode it does nothing.
4. On a new line or `0x52`, the addon writes a "stop" marker; the helper
   kills playback and moves on.

Host-side playback is preferred over `play_sound` because it can be stopped
and avoids Wine audio. Local TTS (Piper or Kokoro) keeps generation free and
fast; a cloud engine can be swapped in later.

Voice casting can follow the portrait convention: a per-NPC map keyed by
exact in-game name, with race/gender defaults as fallback. FFXI dialogue is
static text, so lines can be pre-generated per quest and cached forever.

## How Spoken (WoW) does it, for reference

Open source, MIT: https://github.com/rusty-key/spoken-wow. Store page:
https://www.curseforge.com/wow/addons/spoken. Site: https://spoken.quest/.

- All audio is pre-generated and shipped as voice packs (~300 MB each, four
  packs for quests). Nothing is generated live in the addon.
- Engine is ElevenLabs. Default model `eleven_v3` because it honours inline
  audio tags; `generation.json` applies a tag per race (e.g. dwarves get
  "[Scottish accent]"). `eleven_multilingual_v2` where v3 doesn't fit. Lines
  with stage directions use the text-to-dialogue endpoint so narrator and
  NPC come back as one file. Settings: stability 0.5, similarity 0.75,
  style 0, speaker boost on.
- Casting: voices named `race-gender-flavor`; flavor taken from the game's
  greeting sound entries so NPCs sharing a greeting share a voice. 54 voices
  for the quest corpus, cloned from one-to-two-minute seed clips into
  ElevenLabs custom voice slots. Non-humanoids get a per-model voice;
  objects use a narrator.
- Pronunciation lexicon (IPA or respellings) kept in Postgres and uploaded
  to ElevenLabs as a dictionary.
- Generation runs on their website through a Postgres batch queue with
  review/replay/regenerate tools; every take is archived. Contributors spend
  from their own ElevenLabs key. Quoted rate: 0.55 per 1000 characters on a
  Creator plan, half for flash/turbo. Quest corpus ~17,500 lines, ~9,500
  files. Masters 128 kbps MP3, shipped as Ogg Vorbis q0 at 44.1 kHz.
- Also mentions fish.audio credits (another pipeline) and uses Claude for
  one lore-rewrite make target.
- Lines are keyed by broadcast text id plus voice; a text hash flags "text
  changed" for re-recording.

The Lyre addon (https://www.curseforge.com/wow/addons/lyre) is the live
alternative: a companion app supporting Edge TTS, Kokoro, Piper, OpenAI,
ElevenLabs, and system TTS. That is closer to the helper-process design
above.

## Next steps

1. In game, confirm the gamepad module loads and sees presses: `//bl pad`,
   then `//bl debug pad` while talking to an NPC. The prompt indicator
   should vanish on the first face-button press.
2. Confirm `setkey enter` advances dialogue under Wine
   (`//setkey enter down` then `//setkey enter up`, or via
   `windower.send_command`). Auto-advance for the voice reader depends on it.
3. Optionally run `//bl debug chunk` at an NPC and page through to confirm
   no outgoing packet fires per advance. Turn it off promptly.
4. Prototype the helper with Piper on the host and a file-drop queue; wire
   Balloon to emit lines and poll markers. `balloon.advanced` is the hook for
   "player moved on, stop speaking".
5. Decide voice map format and seed it from the existing portrait list.
