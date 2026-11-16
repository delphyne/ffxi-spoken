# Audio packs: strategy

Decided 2026-11-15. This is the shape of the product; the other documents
describe how to produce the pieces.

## The assumption everything rests on

Anyone not developing the addon uses pre-rendered audio. Live text-to-speech
needs a Python 3.12 venv, a 300 MB model, and ideally CUDA; that is a
developer's setup, not a player's. So:

- The addon plus downloaded packs is the product. A line with no downloaded
  audio is silent. Nothing is generated on the player's machine.
- The helper, the venv, the engines, the lexicon, the census, and the review
  loop are the generation pipeline. They run on the developer's machine to
  produce packs. Their protocol (`docs/Protocol.md`) is interim and does not
  need to be polished for strangers.
- Playback on the player's side is a native Lua C module, the route
  Balloon's gamepad module already uses: `libs/spokenplay.dll` (built from
  `native/`) is loaded by the addon, decodes Opus with opusfile, plays
  through winmm from its own thread, and stops on the next line, a
  release, or zoning. The addon holds the manifest index in memory
  (`packs.lua`). No file polling, no "is the helper running" failure mode.
  The Python helper is the developer's live-synthesis path (`//spoken mode
  helper`). `windower.play_sound` is not an option: it cannot stop a sound
  and takes only WAV.

## What the client's layout offers, and what it does not

- Text is organised per zone: one English string table, one event bytecode
  file, one entity list, for 294 zones. The ROM folder a table sits in is
  where its latest copy landed, not a grouping: launch-era zones have tables
  in ROM, ROM2, ROM3, ROM4, and ROM9. Do not group by ROM.
- Within a zone's table, ids are assigned in blocks as content was added,
  so a cutscene's lines are contiguous (Norg's Rhapsodies scene 286 is 16
  ids in a span of 16). There is no header saying which quest a block is;
  that mapping comes only from the event bytecode plus LandSandBoat's
  mission scripts, which is what `docs/Event-Decoding-Handoff.md` produces.
- Zone and storyline are orthogonal: a mission spans twenty zones, a city
  holds a hundred quests. Packs follow storylines, not zones.
- Size: 1,276,613 prompted dialogue lines across all zones, 129,188 unique
  by exact text, about 212 hours of speech, about 4.6 GB as Opus at 48 kbps.
  Ten-to-one duplication because moogles, guards, and shared quest text
  recur in every zone. Rhapsodies of Vana'diel is 59 cutscenes and a few
  hundred lines: tens of megabytes, the right first pack.

## What a line's key is

A pack line is found by speaker plus cleaned text, where cleaned means what
`text.clean` in the addon produces and `tools/ffxitext.py` reproduces from
the DAT. Two consequences of the client's parameter codes
(`docs/Event-Decoding-Handoff.md`, step 5):

- Lines with a gender or conditional choice (`[his/her]`,
  `[in the end/when Ragnarok cometh]`) exist once per alternative; the pack
  holds every variant as its own file.
- Lines that name the player hold the token `<player>` in their key. The
  addon replaces the player's own name with that token before lookup, and
  the audio is rendered with a neutral word in that slot.
- Lines that embed an item name or a number cannot be enumerated and are
  not in any pack; they stay silent. Rhapsodies has 25 of 1,392.

## Layout: ownership by directory

A content-addressed store knows what a file is but not who owns it, so
deleting anything needs reference counting across every manifest and
orphans are invisible until someone writes a garbage collector. Audio here
is curated and shipped, not accumulated, so the pack is the unit of
ownership and the filesystem expresses it:

```
audio/
  rhapsodies/
    1.2/                 the installed version, self-contained
      manifest.json
      Iroha/i-bid-you-finish-your-tasks.7f3a.opus
      Gilgamesh/yar-irohas-already-given-me.c91e.opus
    1.3.part/            a download in progress, ignored until renamed
  core/
    0.4/
```

- Uninstall: remove the pack directory.
- Upgrade: download the new version beside the old, verify every file
  against the manifest's sha256, rename into place, delete the old version.
  Versions are replaced whole; storyline packs are small enough that delta
  updates are not worth their complexity yet.
- Crash recovery: a `.part` directory is removed at the next start.
- Orphan sweep: anything under `audio/` that is not `<pack>/<version>/`
  with a valid manifest is garbage by definition.
- Hashes appear only as a short suffix keeping readable filenames unique
  and as per-file sha256 in the manifest for download verification. They
  never decide what to delete.
- The developer's live cache, if any, lives apart from `audio/` and is
  size-capped separately. Nothing from it ships without going through a
  pack build.

## Manifest and lookup

The addon knows only the speaker and the cleaned line text at runtime, so
that pair is the lookup key for shipped audio: not the lexicon-substituted
text, not a voice name, so that a lexicon fix or a recast changes nothing
on the player's side until a new pack version ships. The index is built in
memory from every installed manifest at start-up.

A manifest row carries: lookup key (speaker, normalised text), file path,
sha256, duration, and provenance: the zones and text ids where the line
occurs, the voice and lexicon version that produced it, and the hash of the
game text at build time so a line the game has since changed is detected
when the pack is rebuilt. One file per unique speaker and text within a
pack collapses the cross-zone duplication; duplication between packs is
accepted, and the core pack keeps it small.

Normalisation must match what the addon writes to its queue byte for
byte, which is why a Python port of `text.clean` with the same tests is a
prerequisite (handoff step 5).

## Pack boundaries

- Storyline packs from mission scripts: Rhapsodies of Vana'diel first,
  then other mission lines. Line sets come from the decoder over the events
  LandSandBoat's scripts name.
- A core pack of lines that appear in many zones, which the dedup data
  identifies directly and which covers most of what a player hears while
  wandering.
- City and zone packs later, for completeness.
- Lines carrying a gender or conditional choice are rendered once per
  alternative, and lines naming the player are rendered with a neutral
  word in the slot ("adventurer"; `--player-word` to change it) and keyed
  on the `<player>` token. Decided 2026-11-16 with the first build; see
  "What a line's key is".
- Narration lines (no speaker) are in: the first Rhapsodies pack has six.
  The addon only looks them up when narration is enabled.

## Distribution and what is version-controlled

Audio is a build product of inputs that are already in git (the decoder,
the lexicon, the casting, the engine's weights), so it is never committed
and not put in Git LFS either: a recast rewrites every file of that
speaker, LFS keeps every rebuild forever against a 1 GB quota, and player
downloads would count against the repository's bandwidth. What is
committed is the record of each build:

- `packs/<pack>/<version>/manifest.json`, a copy of the shipped manifest.
  Two versions diff into exactly which lines changed and why, because
  each row carries the voice, the lexicon-applied text the engine was
  given, and a hash of the game's bytes per source, and the manifest
  carries the engine's model-file hashes, the repo commit it was built
  from (and whether the tree was dirty), and the lexicon's hash.
- `packs/index.json`, the current version of each pack with its archive's
  download URL, sha256, and size. This is the update channel: the addon or
  helper reads it to offer updates.

The archive itself, `<pack>-<version>.zip` unpacking to
`<pack>/<version>/...`, goes on a GitHub Release tagged
`<pack>-<version>` (`make release`); assets have no bandwidth quota. Opus
at 48 kbps mono. A rebuild is not bit-for-bit (GPU inference can differ
in the last bits), so the archive is the record of what shipped and the
manifest the record of how.

## Building

`make pack VERSION=0.1` runs `tools/build_pack.py`: the decoder's corpus
for the storyline (`MISSIONS=rov`), deduplicated on (speaker, cleaned
text), each line rendered with its cast voice through the helper's
backend into the helper's WAV cache (so a rebuild only renders what the
lexicon, casting, or game text changed), encoded with opusenc or ffmpeg,
and written to `audio/<pack>/<version>.part/` then renamed into place.
It then writes the zip beside the version directory, copies the manifest
to `packs/<pack>/<version>/`, and updates `packs/index.json`; those two
are committed. `DRY=1` lists the lines and voices without rendering;
`LIMIT=N` renders a test slice and writes no archive, copy, or index
entry; `FORCE=1` replaces an existing version. The manifest row is as
described above; `spoken` records the exact text the engine was given,
and each source carries a short hash of the DAT bytes. The first
Rhapsodies build: 1,193 files from 32 speakers, 81 naming the player;
skipped 26 live-only lines (item or number slots), 15 whose text is empty
after cleaning, and 38 menus; 75 minutes of speech, 29 MB, six minutes on
a GPU. No agent is involved: `make pack` and `make release` are the whole
procedure.

## Playing (built 2026-11-16)

- `packs.lua` scans `audio/` at load and on `//spoken reload`: every
  `<pack>/<version>/manifest.json`, skipping `.part`, keeping the highest
  version of a pack and warning about the rest. The index is three tables:
  speaker plus text to file, text to the speakers that say it, and a folded
  form (letters and digits only) of speaker and text to the lines that fold
  the same. The first pack by name wins a line two packs share. Decoding
  the 670 KB Rhapsodies manifest takes about 25 ms. Windower's `json.lua`
  is not used: it mis-parses escaped quotes, which dialogue is full of.
- The native module is `native/spokenplay_core.c` (no Lua: one worker
  thread, a mailbox, a generation counter; reads the file, `op_open_memory`,
  `op_read_stereo` into four 100 ms waveOut buffers at 48 kHz stereo, with
  software gain; a terminal state is only written when the generation still
  matches), `native/spokenplay_lua.c` (the `play`, `stop`, `state`,
  `set_volume`, `version`, `shutdown` bindings and a sentinel userdata
  whose finaliser shuts the thread down before Lua frees the DLL), and
  `native/spokenplay_test.c`, a console driver `make native-test` runs
  under the game's Wine prefix. winmm over DirectSound or XAudio2: plain C,
  no COM or window handle, and under Wine it shares the mixer the game's
  own sound uses. Deps are static, cross-built by `native/build-deps.sh`.
- The addon's `prerender` polls `state()` every `PollFrames` frames;
  `done` on a prompted line drives auto-advance exactly as the helper's
  status file does in helper mode.
- Misses: with `//spoken log` (or debug) on, every line no pack has is one
  JSON row in `data/misses.jsonl`: reason (`unknown`, `file_missing`,
  `no_player`, `play_failed`), speaker and text as looked up, the raw chat
  bytes with everything outside printable ASCII escaped, the player's
  name, zone, position, current target, the other speakers of the same
  text, the nearest folded matches, and the installed packs. That is what
  it takes to tell a cleaning mismatch from a missing line from a bad
  speaker attribution.

## Open

- The update channel's index format, and installing or upgrading a pack
  from inside the game or the addon rather than by unzipping into `audio/`.
- First in-game run of the native module; latency and device behaviour
  inside the FFXI process.
