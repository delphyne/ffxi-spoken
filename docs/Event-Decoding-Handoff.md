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
  | `0x48 m m`, `0x49 m m` | 3 | message `m` | none (narration, system text) |
  | `0x24` | 7 | a choice menu | with `0x25`/`0x7F` waiting for the pick |
  | `0x23` | 1 | waits for the player to dismiss the message | |

  A 16-bit operand with bit 15 set indexes the block's immediates
  (`getworkofs`: `References[val & 0x7FFF]`); otherwise it names an event
  variable, which only a VM can resolve. Actor constants: `0x7FFFFFF0`,
  `0x7FFFFFC0`, `0x7FFFFFF9` are the player; `0x7FFFFFF8` is the block's
  own entity; values with a non-zero top byte are server ids. Opcodes
  `0x1F`, `0x31`, `0x36`, `0x37`, `0x7A` write `EntityTargetIndex[1]`, so
  they change who `0x1D` attributes to.

- **Proof on Rhapsodies text.** `tools/xidat.py cutscene 249 368` (Mhaura,
  mission 1-2) returns Marilionne's four lines; `252 286` (Norg, mission
  2-23) returns a conversation among Gilgamesh, Tenzen, and Iroha with the
  right speaker on each line. Both from the pattern-matching heuristic in
  the tool, not a real decoder, so branches interleave and the order is
  approximate.

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
  `cutscene` reader. Start the disassembler as a new module beside it.
- `tools/rov_events.py`: the Rhapsodies cutscene list.
- `~/scm/github/atom0s/XiEvents`: structures, VM functions, one page per
  opcode. `Event VM Functions.md` has `getworkofs` and `GetActorIndex`.
- `~/scm/github/LandSandBoat/server`: `scripts/missions/rov/` for events,
  `scripts/zones/<Zone>/IDs.lua` to cross-check text ids,
  `data/zones/<zone>/npcs.yaml` for looks.
- `docs/Voice-Handoff.md`: the original design record and why the addon
  cannot see dialogue advances on the wire.
