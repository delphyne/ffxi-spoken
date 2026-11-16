# Spoken for FFXI

> **Work in progress. Not stable, not ready to use.** Nothing here is
> finished; most of it is scaffolding and notes. Expect it to break, change
> shape, or do nothing at all. There is no release.

A [Windower 4](https://www.windower.net/) addon for Final Fantasy XI that
reads NPC dialogue aloud with AI-generated voices, inspired by the
[Spoken](https://www.curseforge.com/wow/addons/spoken) addon for World of
Warcraft by rustykey ([source](https://github.com/rusty-key/spoken-wow),
[site](https://spoken.quest/)). Spoken gives every WoW quest giver a voice
matched to who they are. The aim here is the same for Vana'diel: when an NPC
talks, you hear them.

## How it is meant to work

FFXI dialogue is static text from the game's data files, and the chat log
receives every line with the speaker's name. The addon captures those lines
and plays the matching pre-rendered audio from downloadable packs, one per
storyline; a line with no downloaded audio is silent. Controller input is
observed natively so the reader knows when you have moved on. Nothing is
generated on the player's machine: the voices are produced ahead of time
from the game's own text, extracted from the client's data files, and
shipped as packs. The pack design is in
[docs/Audio-Packs.md](docs/Audio-Packs.md), the extraction plan in
[docs/Event-Decoding-Handoff.md](docs/Event-Decoding-Handoff.md), the
original design record in [docs/Voice-Handoff.md](docs/Voice-Handoff.md),
and the interim addon-to-helper contract in [docs/Protocol.md](docs/Protocol.md).

Everything below the addon itself is the generation pipeline, run by a
developer to produce packs.

Two parts:

- **The addon** (`Spoken.lua`, `packs.lua`, `libs/spokenplay.dll`) runs in
  Windower. It cleans each dialogue line (Shift-JIS, colour codes, prompt
  markers, element symbols), swaps the player's own name for `<player>`,
  looks the speaker and text up in the manifests of the packs installed
  under `audio/`, and plays the file through the native player: a small C
  module that decodes Opus and plays through winmm from its own thread, so
  the next line, a release, or zoning cuts the sound at once. A line no pack
  has is silent; `//spoken log` records each one in `data/misses.jsonl`
  with the raw bytes, zone, position, target, and the nearest lines the
  packs do have, so the gap can be traced. It can auto-advance a prompted
  line when the audio has finished.
- **The helper** (`helper/spoken_helper.py`) runs on the Linux host and is
  the developer's live path (`//spoken mode helper`): the addon writes each
  line to a queue file instead, and the helper synthesises it with a local
  engine, caches it, plays it, and reports back. Engines:
  [Kokoro](https://github.com/thewh1teagle/kokoro-onnx) (most natural,
  about fifty voices in one model; roughly a third of realtime on CPU so a
  long line waits a few seconds, six times faster on an NVIDIA GPU),
  [Piper](https://github.com/OHF-Voice/piper1-gpl) (near-instant, clear but
  flat), and espeak-ng as a last resort so the pipeline can be tested
  without downloading anything.

Why a helper for live speech rather than doing it all in Lua: Windower's
Lua can make HTTPS calls but they block the game thread, and the engines
need a Python environment nobody should have to install to play. Why a
native module for playback: Windower's own `play_sound` takes only WAV and
cannot stop a sound.

## Status

| Piece | State |
| --- | --- |
| Dialogue capture and cleanup | drafted, tested outside the game; the player's own name becomes `<player>`, the key form pack lines use |
| Pack playback in the addon | `packs.lua` indexes every installed `audio/<pack>/<version>/manifest.json` at load and `//spoken reload`; a captured line is looked up by speaker and text and played through the native module; misses are silent and, with `//spoken log`, written to `data/misses.jsonl` with everything needed to see why; tested outside the game, not yet in it |
| Queue file protocol | drafted; helper mode only (`//spoken mode helper`) |
| Host helper: watch, synthesise, play, stop | works in game with espeak, Piper, and Kokoro |
| Local TTS engines (Kokoro, Piper) | installed by `make venv` and `make voices`; Kokoro on CUDA with `make venv-cuda` |
| Auto-advance via Windower `setkey` | unverified under Wine; parked until the rest is stable |
| Voice casting per NPC | `helper/cast.py` casts Balloon's 230 portrait NPCs by hand; everyone else is cast by race from `helper/census.json` (13,845 names from LandSandBoat and the client) |
| Pronunciation lexicon | `helper/lexicon.yaml` with IPA; `make nouns` and `make review` build it from the game's text; every proper noun Rhapsodies of Vana'diel speaks is reviewed (64 entries) |
| Dialogue extraction from the client | dialogue tables and NPC lists decode; `tools/xievent.py` disassembles event bytecode for every zone (4 events of 84,914 still fail, all in an unused zone) and attributes each line to its speaker; `tools/ffxitext.py` cleans the text the way the addon does and expands the client's parameter codes; Rhapsodies resolves to 1,416 pack texts from 31 speakers, 25 lines live-only; Wings of the Goddess (`MISSIONS=wotg`) to 3,588 from 118 speakers, 63 live-only (`docs/Event-Decoding-Handoff.md`) |
| Pre-generated voice packs | `make pack VERSION=x` builds one from the decoder's corpus (Opus files, manifest, zip) and `make release` publishes it; the manifests and index under `packs/` are committed, the audio is not (`docs/Audio-Packs.md`); Rhapsodies 0.1 is built and installs by unzipping into `audio/` |
| Native playback module | `libs/spokenplay.dll`: winmm playback of Ogg Opus from a worker thread, with stop, state, and volume (`native/`, `make deps && make native`); plays, stops, and reports errors under the game's Wine prefix from a console (`make native-test`); first in-game run still to do |

## Related

- [Balloon](https://github.com/delphyne/Balloon) (our fork): the on-screen
  dialogue balloon this grew out of. Its native gamepad module is how a
  controller advance becomes visible to an addon.
- [Spoken for WoW](https://github.com/rusty-key/spoken-wow): the inspiration,
  including how it casts voices and ships pre-generated packs.
- [Lyre](https://www.curseforge.com/wow/addons/lyre): the live-generation
  alternative for WoW, closer to the helper design used here.

## Development

```
make install        # symlink this checkout into Windower's addons folder
make check          # luacheck plus the Lua and Python tests
make deps           # cross-build libogg, libopus, opusfile (32-bit, static) into native/deps/
make native         # build libs/spokenplay.dll with i686-w64-mingw32-gcc against Windower's LuaCore.dll
make native-test FILE="audio/rhapsodies/0.1/Iroha/<file>.opus" [STOP_MS=1500]   # play it under the game's Wine
make venv           # Python 3.12 venv with kokoro-onnx and piper-tts (needs uv)
make venv-cuda      # swap in the CUDA build so Kokoro runs on an NVIDIA GPU
make voices         # download the Kokoro model and three Piper voices (~500 MB)
make helper         # run the host helper against ./data; picks the best engine present
make helper BACKEND=piper   # or kokoro, espeak, none
make say TEXT="Welcome to Vana'diel." SPEAKER=Iroha   # hear one line: lexicon, casting, engine
```

In game: `//lua load spoken`, then `//spoken help`.

The engines need onnxruntime, which has no wheel for the system Python yet,
so `make venv` builds a Python 3.12 venv with
[uv](https://github.com/astral-sh/uv) (`python3 -m pip install --user uv`
if you don't have it). Models land in `helper/voices/<engine>/`, generated
audio in `helper/cache/`; both are gitignored. `helper/voices.json` maps a
speaker name to a voice per engine, with `default` as the fallback and `""`
for narration. It is generated by `helper/cast.py`, which casts every NPC in
Balloon's portrait set by race and sex into a voice pool per engine; fix a
miscast there and re-run it rather than editing the JSON. Kokoro voice
names start with language and sex (`am_adam`, `bf_emma`, `jf_alpha`); Piper
voices are fetched by name with `python -m piper.download_voices`.

Speakers not named in voices.json are cast from `helper/census.json`, which
`make census` builds from a [LandSandBoat](https://github.com/LandSandBoat/server)
checkout (`LSB=path`): the server's NPC data carries each humanoid NPC's race
and sex, and `helper/casting.py` maps race to the same voice pools cast.py
uses. Non-humanoid models (moogles, beasts, Iroha's unique model) have no
race and fall through to the default until they are cast by name. The
report `make census` prints takes the Rhapsodies of Vana'diel speakers from
the event decoder and says which are still uncast.
### Pronunciation

`helper/lexicon.yaml` fixes words the engines mispronounce. An entry is
either a respelling, or `ipa:` in espeak-style IPA with an optional hand
`spell:`. Piper and Kokoro are sent the IPA as phonemes (Piper reads
`[[ipa]]` natively; for Kokoro the helper phonemises the rest of the line
and splices the IPA in); engines that cannot take phonemes get the
respelling, derived from the IPA by `helper/respell.py` when no `spell:` is
given. It is applied before synthesis and before the cache key, so editing
an entry regenerates only the lines it touches, and the running helper
reloads the file within a second of a save. The lexicon is built from the game's own text rather than by waiting to be
surprised in play:

1. `make nouns` decodes every zone's dialogue table from the client, ranks
   the proper nouns by how often they are spoken with one storyline's text
   first (`MISSIONS=rov`, Rhapsodies of Vana'diel, by default; `wotg` for
   Wings of the Goddess), and writes `data/proper_nouns.tsv` with Kokoro's
   unaided IPA and an example line for each. `make nouns MISSIONS=wotg
   ONLY=1` keeps only the words that storyline's cutscenes actually speak,
   as decoded from the client's event files, so the review covers that
   pack's vocabulary and nothing else.
2. `make review` walks that list in a window, one word at a time: the
   name, the count, an example line, and the engine's IPA, which it speaks.
   `a` accepts the pronunciation into the lexicon, `r` repeats it, `s`
   skips a word the engine already says right, `e` plays the example line,
   `b` goes back to the previous word and undoes its accept or skip (it
   reaches into earlier runs too), `q` quits; each has a button too. To replace a reading, type IPA in the
   field (`j` jumps there) or click glyphs from the palette below it, which
   shows every symbol the lexicon accepts with a word that has that sound;
   Enter plays what you built, and `a` then accepts it. Without a display,
   or with `make review CLI=1`, the same keys work in the terminal.
   Progress is kept in `data/review_progress.json`, so the next run resumes
   where you stopped; words already in the lexicon are not asked again.
3. The running helper reloads the lexicon within a second of each accept,
   and `make say TEXT="..."` checks any line on demand. To hear an entry, `make say TEXT="..."` speaks one line
through the same lexicon, casting, and engine as live playback and prints
what the engine was given; add `RAW=1` to hear the unrespelled text for
comparison, `SPEAKER=<NPC name>` to hear it in that character's voice, and
`FRESH=1` to regenerate a line the cache already has.

`make venv-cuda` installs onnxruntime's CUDA 13 build and the NVIDIA runtime
libraries as pip packages, so nothing is needed system-wide beyond a driver
that supports CUDA 13. The helper prints which device Kokoro landed on at
start-up; without CUDA it falls back to the CPU silently.

Windower is found automatically under the Lumoria flatpak; see the Makefile
for overriding that. `CLAUDE.md` has the layout and conventions.
