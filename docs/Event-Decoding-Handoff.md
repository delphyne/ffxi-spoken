# Attributing every dialogue line to its speaker: handoff

Written 2026-11-15. The goal is a corpus of `(zone, event, order, speaker,
text)` for all of FFXI's NPC dialogue, starting with Rhapsodies of
Vana'diel, so that voices can be cast and audio pre-generated before the
player ever meets the line. This records what is already verified, the
plan, and how the result joins the NPC census that casts voices today.

## Where things stand

Verified on this machine against the retail client under the Lumoria
prefix, with `tools/xidat.py`:

- **Which DAT is which.** atom0s/XiEvents' "Event DAT Files.md" is a table
  of 300 zones giving the entity list, event bytecode, and JP and NA string
  DATs for each. It is copied into `tools/zone_dats.json`; 295 zones have
  all three files present in this client. Do not derive ids by formula; the
  offsets change between folder blocks.
- **Dialogue tables decode.** XOR every byte with 0x80, read the uint32
  offset table at byte 4, split NUL-terminated Shift-JIS strings. Index N is
  the game's text id N: checked against LandSandBoat's `IDs.lua` for Mhaura
  (29 ids, all match). Strings still carry prompt bytes (`0x7F 0x31`), line
  breaks (`0x07`), and parameter codes (`0x01 0x05 ...` item, `0x7F ... 0x08`
  player name, `[Lord/Lady]` gender choice) that `text.lua` strips in game.
- **Entity lists decode.** 32-byte records, 28-byte name plus uint32 id.
  Actor ids in event bytecode are these ids; bits 12-23 are the zone.
- **Event bytecode layout** matches XiEvents' `eventblock_t`: actor,
  tag count, `TagOffset[]`, `EventExecNum[]` (parallel arrays: event id to
  start offset), immediates, bytecode. Event ids are the `csid` values
  LandSandBoat passes to `startEvent`; `onZoneIn` cutscenes live in a block
  whose actor is a nameless entity, so the block actor is not the speaker.
- **Message opcodes**, from `~/scm/github/atom0s/XiEvents/OpCodes/`:

  | opcode | size | prints | speaker |
  | --- | --- | --- | --- |
  | `0x1D m m` | 3 | message `m` | the current event entity (`EntityTargetIndex[1]`) |
  | `0x2B a a a a m m` | 7 | message `m` | actor `a` (uint32, resolved by `GetActorIndex`) |
  | `0xB0 ? a a a a b b b b m m` | 12 | message `m` | actor `a`, listener `b` |
  | `0x48 m m` | 3 | message `m` | none (system text) |
  | `0x49 a a a a m m` | 7 | message `m` | none; `a` only positions the text |
  | `0x24` | 7 | a choice menu | with `0x25`/`0x7F` waiting for the pick |
  | `0x23` | 1 | waits for the player to dismiss the message | |

  A 16-bit operand with bit 15 set indexes the block's immediates
  (`getworkofs`: `References[val & 0x7FFF]`); otherwise it names an event
  variable, which only a VM can resolve. Actor constants: `0x7FFFFFF0`,
  `0x7FFFFFC0`, `0x7FFFFFF9` are the player; `0x7FFFFFF8` is the block's
  own entity; values with a non-zero top byte are server ids. Only `0x7A`
  writes `EntityTargetIndex[1]` (case 2 retargets the VM at another entity,
  case 3 restores the block's own), so only it changes who `0x1D` attributes
  to. (An earlier draft also listed `0x1F`, `0x31`, `0x36`, `0x37`; those
  move the entity without changing it.)

- **Proof on Rhapsodies text.** `tools/xidat.py cutscene 249 368` (Mhaura,
  mission 1-2) returns Marilionne's four lines; `252 286` (Norg, mission
  2-23) returns a conversation among Gilgamesh, Tenzen, and Iroha with the
  right speaker on each line. Both from the pattern-matching heuristic in
  the tool, not a real decoder, so branches interleave and the order is
  approximate.

## Step 1 done: the disassembler (2026-11-15)

`tools/xievent.py` walks event bytecode. `tools/opcodes.json` is generated
from the XiEvents pages (`xievent.py regen`); the forty-odd opcodes whose
size depends on the following byte are spelled out in `VARIABLE`, and the
tests in `tools/test_xievent.py` check the two agree. Results:

- `check --rov`: all 250 block/event entries for the Rhapsodies cutscenes
  (50 distinct zone/event ids) walk to their end, in both modes.
- `check --all`: 84,914 entries across 295 zones, 4 failures, all in zone
  210 (an unused zone; opcode `0x5C` sub-case `0xB0` is undocumented and the
  bytes are ambiguous). 13 entries reach a `0x9D` sub-case 7 jump table
  whose targets depend on an event variable and are not followed; none are
  Rhapsodies.

What the walk taught us, beyond the XiEvents pages:

- **Event ids are per block, not per zone.** The same id recurs in several
  blocks of one zone (Norg event 286 exists in Gilgamesh's and Kamui's
  blocks as a bare END and in the nameless entity's block as the real
  scene). LandSandBoat's `startEvent(csid)` runs against the entity that
  triggered it, so attribution needs (zone, actor, event), and `check`
  walks every block that has the id.
- **Data lives inside the bytecode.** `0x9D` parks 16-bit tables (value
  tables and jump tables) after a RET or END, so a linear walk reads them
  as opcodes and derails. The default walk is therefore reachability:
  follow fall-through, jumps and calls from the entry over the whole
  block, record the table offsets, and treat an instruction landing inside
  another as an error. `--linear` keeps the old segment walk.
- **Segments are not self-contained.** CALL (`0x1A`) targets shared
  subroutines outside the event's own range, so bounds are the block.
- **`0x02` IF's else offset is absolute**, not relative as the page says
  (`02 .. 02 4b` at Norg 0x4AD5 lands on the IF at 0x4B02). `0x3E`,
  `0x44`, `0x82` are also absolute.
- **`0x21` ends the event** (sets `EventExecEnd`, never advances); code
  after it is data or unreachable. Falling or jumping exactly off the end
  of a block also ends it (115 events do this; many blocks end without an
  END).
- **Pages that disagree with the client** (Feb 2022 pages, 2026 client):
  `0xC4` is 12 bytes, not 11; `0xAE` sub-case 8 is 6, not 10; `0x5B`/`0x66`
  are always 15. Undocumented sub-cases sized by inspection: `0xB4` 0x12
  (6), 0x13 (20), 0x14 (12), 0x15 (2); `0xB6` 0x16 (22), 0x17 (4); `0xB1` 3
  (4) and 4 (6); `0x46` 3 (8); opcode `0xDA` (3, one instance). Each is
  marked in `VARIABLE`/`INFERRED` with where it was seen.
- `0x9D` sub-case 0x0E is 8 or 10 bytes depending on a zone table at run
  time and is left as an error; it has not appeared in a reachable walk.

## Step 2 done: speaker tracking (2026-11-15)

`xievent.py lines ZONE CS` walks an event carrying the VM's current entity
as state (so a `0x7A` retarget changes later `0x1D` lines, and a line
reached with two entities yields two rows), resolves `0x2B`/`0xB0` actors
by the `GetActorIndex` rules, and emits
`(zone, event, block_actor, offset, kind, speaker_id, speaker, text_id, text)`.
Acceptance held: Norg 286 is 16 lines, all Gilgamesh, Tenzen, or Iroha.

`lines --rov` over the 50 Rhapsodies scene ids: 1,392 spoken lines, 22
narration (`0x48`), 38 menus, 31 speakers, nothing unresolved. Every
message id is an immediate, so step 4 (event variables) has no work in
Rhapsodies. 1,364 lines come through `0x2B` with a named actor; 28 through
`0x1D` in the blocks of the NPCs who trigger the scene (Numi Adaligo,
Naillina, Heillal), so the block actor is the speaker there. 18 lines
belong to entities literally named `???` (disembodied voices), which is
also what the client shows. No `0x7A` case 2 or 3 appears. This speaker list is the real Rhapsodies cast for
`cast.py` and should replace the census report's 18.

Step 4 is therefore deferred until a storyline needs it. Step 3's walker
exists; what remains of it is grouping lines by path and marking
choice-only lines, which matters for playback order rather than coverage.

## Step 5 done: the text.clean port and parameter codes (2026-11-15)

`tools/ffxitext.py` is `text.clean` in Python, step for step, with
`tools/test_ffxitext.py` mirroring the Lua cases one for one (keep them in
step). It also decodes the parameter codes a DAT entry still carries,
which the chat log has already rendered by the time the addon sees it:

| bytes | meaning | handling |
| --- | --- | --- |
| `0C n [a/b/...]` | alternative chosen by event parameter n | one variant per alternative |
| `7F 85 [his/her]` | player's gender | one variant each |
| `7F 92 n [has/have]` | plural by number n | one variant each |
| `08` | the player's name | the token `<player>` |
| `0A n` | a number | live-only |
| `01 len ...` | an item or key item name | live-only |

`expand(raw)` returns every byte rendering (or none with the reasons it
cannot), `variants(raw)` the cleaned text of each. `xievent.py lines` now
carries both per row, and `lines --rov` reports: 1,416 pack texts from
1,367 lines; 25 lines live-only (25 mention items, 6 numbers), mostly
Iroha describing training foods. Those stay silent in the pack.

Decisions this implies, recorded in `docs/Audio-Packs.md`: a pack holds one
file per variant, and the addon must replace the player's own name with
`<player>` before looking a line up (the audio for such a line is rendered
with a neutral word in that slot; which word is a casting choice).

Two things found on the way:

- `xidat.dialog()` used to cut each entry at its first NUL. A NUL is also
  parameter 0 (`0C 00 [...]`, `0A 00`), so 1.4 million of the 2.7 million
  entries were truncated, including every line that picks alternative 0.
  Entries actually end in NUL 0x07; `dialog_bytes()` strips only that.
  `tools/proper_nouns.py` reads the fixed decoder and will see more text.
- `text.lua` maps `0x88 0x69` to a UTF-8 "é" before calling
  `windower.from_shift_jis`, which may mangle it. The Python side parks the
  code in cp932's user area and fixes it after decoding. Check what the
  chat log shows for a line with that glyph in game.

Next on the critical path: casting the 31 Rhapsodies speakers, lexicon
review of their proper nouns, then the pack build tool.

## What the heuristic gets wrong, and why a decoder is needed

The current `cutscene` command scans for message opcode bytes and trusts
whatever follows. It does not walk the bytecode, so it can misread a data
byte as an opcode, it loses jump structure (branches appear as one flat
list), it cannot see messages whose id lives in an event variable, and it
attributes `0x1D` to the block actor even after an opcode has changed the
event entity. For casting that is fine; for a pre-generated pack, where the
addon must find the line it is hearing, we need every reachable line with
the right speaker.

## Plan

Steps are ordered so each produces something usable. Rhapsodies first in
every step: `tools/rov_events.py` lists its 59 cutscenes across 22 zones
from LandSandBoat's `scripts/missions/rov/`, and those rows are the test
set.

1. **Linear disassembler.** Build an opcode size table from the 218 files
   in `XiEvents/OpCodes/` (each has an `OpCode Size` row; about ten are
   variable-length and need the sub-case rule from their page). Walk each
   event's bytes from its `TagOffset` to the next, emitting
   `(offset, opcode, operands)`. Stop at `0x00` (END) and treat unknown
   opcodes as a hard error so gaps surface. Acceptance: every Rhapsodies
   event walks to its end without error.
2. **Speaker tracking.** Keep `EntityTargetIndex[1]` as state: it starts as
   the block actor and changes at the opcodes listed above (read each one's
   page for which operand carries the new entity). Resolve `0x2B`/`0xB0`
   actors through the `GetActorIndex` rules. Output lines as
   `(zone, event, offset, speaker_id, speaker_name, text_id, text)`.
   Acceptance: Norg 286 attributes every line to Gilgamesh, Tenzen, Iroha,
   or the player with no `<0x...>` placeholders.
3. **Control flow.** Follow jumps (`0x01` and the conditional branch
   family; XiEvents documents them) to mark each line with its path so
   alternatives are grouped, and mark lines only reachable through a
   choice (`0x24`). Keep the linear output as the superset; pre-generation
   wants every line, playback wants the order.
4. **Event variables.** Where a message id is not an immediate, record
   the variable instead of dropping the line, and note how often it happens
   in Rhapsodies. If rare, hand-fill; if common, a partial VM for
   `WorkLocal` assignments is the next step.
5. **Text normalisation.** Port `text.clean` from `text.lua` to Python,
   with the same tests, so extracted text hashes identically to what the
   addon writes to the queue. This is what lets a pre-generated WAV be
   found by the live cache key. Parameter codes need a decision: the
   player name and gender choice vary per player, so either generate both
   variants or treat such lines as live-only.
6. **Corpus build.** Run over all 295 zones, write `data/corpus/<zone>.jsonl`,
   gitignored, and a summary of lines, speakers, and unresolved items.
   Then the Rhapsodies subset: lines from the 59 cutscenes plus the quest
   lines of their speakers.

## Why this is the critical path (added 2026-11-15)

Players only ever hear pre-rendered packs (`docs/Audio-Packs.md`), and a
line with no pack audio is silent. Coverage of a storyline is therefore the
whole feature: the Rhapsodies pack must contain every line its missions can
show, and only this decoder can enumerate that. The order of work is now:
steps 1 to 3 above for Rhapsodies; step 5 (the `text.clean` port) because
pack lookup keys must match what the addon captures; casting for the
Rhapsodies speakers; lexicon review of the Rhapsodies proper nouns
(`make nouns`, `make review`); then a pack build tool that writes the
version directory and manifest. Runtime niceties of the helper (census
fallback casting, speed per voice, threaded synthesis) matter only insofar
as they shape generated audio.

## Marrying the two paths

The census (`helper/census.py`, from LandSandBoat) answers "who exists and
what race are they" for 13,845 names (9,859 from LandSandBoat with looks, the rest from the client's entity lists), 1,853 of whom LandSandBoat knows to
speak, but it cannot see cutscene-only speakers or which lines they say.
The decoder answers "who says what" but knows speakers only as entity ids
and names. They join on the display name, which is the same string in the
entity list DAT, in LandSandBoat's `npcs.yaml`, and in the chat log the
addon reads:

- **Casting.** Every speaker the decoder finds is looked up in the census
  for race and sex, then in `voices.json` for a hand-picked voice,
  otherwise cast from the race pool by `casting.py`. Speakers the census
  lacks (nameless cutscene actors, unique models such as Iroha and
  Gilgamesh) get listed by the decoder's summary and go into `cast.py` by
  hand; Rhapsodies' are few enough to do in one sitting.
- **Audit.** `census.py --report` currently lists 1,798 speakers with no
  hand-picked voice and 18 for Rhapsodies, but that 18 is only the NPCs
  LandSandBoat triggers; the decoder's speaker list for the 59 cutscenes
  is the real Rhapsodies cast and should replace it in the report.
- **Pre-generation.** Each decoded line becomes a `(speaker, cleaned text,
  voice)` triple, hashed exactly as `spoken_helper.cache_key` does, and the
  helper's cache is filled ahead of time. The live path is unchanged: a
  line that was pre-generated is a cache hit, anything else is generated on
  the spot. Pronunciation goes through `lexicon.yaml` in both paths, so the
  corpus is also the input for finding every proper noun to respell.

## Pointers

- `tools/xidat.py`: dialogue, entities, events, and the heuristic
  `cutscene` reader, superseded by `tools/xievent.py`: the disassembler
  (`disasm`, `check`, `regen`) and speaker tracker (`lines`), with
  `tools/opcodes.json` and `tools/test_xievent.py`.
- `tools/rov_events.py`: the Rhapsodies cutscene list.
- `~/scm/github/atom0s/XiEvents`: structures, VM functions, one page per
  opcode. `Event VM Functions.md` has `getworkofs` and `GetActorIndex`.
- `~/scm/github/LandSandBoat/server`: `scripts/missions/rov/` for events,
  `scripts/zones/<Zone>/IDs.lua` to cross-check text ids,
  `data/zones/<zone>/npcs.yaml` for looks.
- `docs/Voice-Handoff.md`: the original design record and why the addon
  cannot see dialogue advances on the wire.
