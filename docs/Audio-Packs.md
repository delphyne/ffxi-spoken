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
- Playback on the player's side will be a native Lua C module, the route
  Balloon's gamepad module already uses: loaded by the addon, decodes Opus,
  plays through a Windows audio API under Wine, stops on advance or release,
  and holds the manifest index in-process. No file polling, no "is the helper
  running" failure mode. Deferred until close to a first viable release; the
  Python helper is the interim player. `windower.play_sound` is not an
  option: it cannot stop a sound and takes only WAV.

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
- Lines carrying the player's name or a gender choice are left out of the
  first packs; they stay silent until a policy is chosen (generate both
  gender variants with the name dropped, or skip).

## Distribution

GitHub Releases, one archive per pack version, plus a small index file
naming the current version of each pack so the addon or helper can offer
updates. Opus at 48 kbps mono; confirm Opus playback in the interim helper
(paplay through libsndfile, or ffplay) before the first build.

## Open

- The native player module: audio API choice under Wine, Opus decoding
  (libopus and opusfile are small and static-linkable), and how the addon
  hands it speaker and text.
- Whether the first Rhapsodies pack ships narration (empty speaker) lines.
- The update channel's index format.
