# ElevenLabs backend: handoff

Status: **agreed in outline on 2026-11-19; built so far: the key, the client, and the account check (build
order step 1), the extraction (step 2), and the voice prompts.** This
records the decision to move voice creation and pack synthesis to ElevenLabs,
what was decided, how each piece is meant to work, and what is still open.
Facts about ElevenLabs marked *unverified* come from their docs and help
centre and have not been confirmed by a real call.

## Why

Qwen3 VoiceDesign ignores accent hints, so the designed voices cannot be
steered where the cast needs them, and the 8 GB card makes every audition a
model swap (see `docs/MOSS-Backend-Handoff.md`). ElevenLabs gives voice design,
a UI to audition in, and synthesis from one service. The cost is money per
character, rate limits, and a secret to keep.

The player-side direction does not change: players use pre-rendered packs
only (`docs/Audio-Packs.md`). ElevenLabs is developer-side generation.

## Decisions

| Topic | Decision |
|---|---|
| Model | `eleven_v4`; the model is a setting. `eleven_v4_turbo` bills at half the rate and is worth hearing beside it |
| Auditions | Mostly in the ElevenLabs UI and app, not in our tools |
| Voice matching | Voices named `ffxi/<voice key>`; a sync command resolves names into a committed lock file |
| Voice creation | Named characters are made by hand in the UI from `voice_designs.yaml`'s prompts; pools are stock voices |
| Voice ceiling | Pro's 160 custom slots (confirmed by the account check); Voice Library voices for pools where they work; fewer individual voices |
| Individual voices | By importance across all storylines, not per storyline (Shantotto keeps one voice everywhere) |
| Missing voice in a slice | The run stops before any request is sent |
| Releases | Complete storylines only; no partial packs |
| Stage directions | Mapped to audio tags; interjections too, where the tags are expressive enough |
| Re-takes | One take per line; a hand-kept file flags lines to redo |
| API key | Desktop keyring (macOS Keychain or `secret-tool`); `ELEVENLABS_API_KEY` overrides |
| Plan tier | Pro: $99 a month. The account read 639,758 characters for the month on 2026-11-19, not the 1.24 M first assumed. 10 concurrent requests *(unverified for v4)* |
| Pronunciation | Our lexicon, applied before sending, as inline slash IPA; ElevenLabs' pronunciation dictionaries only as a file generated from it, if at all |
| MOSS, Qwen, review window | Kept; `voice_designs.yaml` stays the casting record and holds each named character's prompt |
| Scope | Storylines, quests, and constant companions only |
| Story narration | Voiced by the narrator voice |
| System text | Never voiced |
| Dialogue with no known speaker | Blocks its pack until attributed |
| New sources | Scripted quests, the Mog House moogle, the Mog Garden cast |
| Boilerplate | Dropped by a deny-list of patterns, inside those sources |

## Scope: what gets voiced

The corpus today is the twelve mission storylines, decoded from event
bytecode by `tools/xievent.py`. Menus are already skipped and most item
messages have no fixed text, so little system text is in it. Quests and
companions are not decoded at all yet.

### Sources

A pack is defined by a list of sources:

- **A mission folder** under LandSandBoat's `scripts/missions` (as now).
- **A quest region** under `scripts/quests` (about 587 scripts, filed by
  region: `sandoria`, `bastok`, `windurst`, `jeuno`, `outlands`, `ahtUrhgan`,
  `crystalWar`, `abyssea`, `adoulin`, `otherAreas`, `hiddenQuests`). Each
  region is its own pack. Only lines inside a quest's events count; an NPC's
  idle line outside the quest is not in the quest script and is out by
  construction.
- **A companion**: a hand-kept list of zones and entity names, starting with
  the Mog House moogle and the Mog Garden characters.

The deny-list applies inside these sources. Decoding every NPC in the game
and filtering down is not the plan.

### Line classes

Every decoded line gets one class: `dialogue`, `narration` (story prose in a
cutscene with no speaker), `system` (item, key item, gil, status, tutorial,
prompt), `menu`, or `boilerplate`. Only dialogue and narration render. The
others are counted, with the reason, in the manifest's `skipped`.

### The deny-list

`helper/exclude.yaml`, hand-kept: patterns by text, speaker, zone, or event,
each with a reason, plus single lines. Starter patterns: shops, guards,
conquest, teleports, home points, repeated greetings. An audit command lists
what each pattern dropped and samples what survived, so leaks are caught
before money is spent. A pattern that matches nothing is reported as stale,
as `cast_as.yaml` entries are.

### What is built

`tools/corpus.py` (`make corpus`, `make audit-corpus`) does the extraction
for every source: `missions/<folder>`, `quests/<region>`, and
`companions/<name>` from `helper/companions.yaml`. The lines go to
`data/corpus/<source>.jsonl`, which is never committed because it is the
game's text; `helper/corpus/<source>.json` holds the counts and is. The
class a pack cannot hold is called `unvoiceable` there: no fixed text, a
stage direction alone, or punctuation alone. Found on the way:

- `helper/exclude.yaml` holds starter rules only. The system rules are
  written from the shapes of the speakerless lines; what they leave as
  narration still mixes story prose with description, and each source wants
  a read through `make audit-corpus SOURCE=... SHOW=narration` before it is
  rendered. The only boilerplate rules so far drop the three tutorial NPCs.
- The Mog House has no zone of its own in the client's data: its moogle's
  events sit in each city's zone, so that companion is "what the name
  Moogle says in the cities with a residential area". Nearly half of what
  it says has an item or a number filled in and cannot be in a pack.
- `missions/tvr` is empty: LandSandBoat's scripts for it are stubs.
  `quests/misc`, the two quest scripts filed under no region, is empty too:
  they start no events the lister can see.
- A few lines come out of the cleaner with their first letter or a control
  code wrong (`tools/ffxitext.py`); the first exclude rule holds them back.
- `tools/build_pack.py` and `tools/voice_stories.py` read the same
  extraction. A pack names its sources (`make pack SOURCE="quests/jeuno
  ..."`, a mission folder by default), skips what the classes say is never
  voiced, and stops on unattributed dialogue, telling where it is without
  its text; a dry run lists it and goes on. `helper/voice_stories.json`
  counts, per speaker and storyline, the unique lines a pack would voice,
  under whoever `cast_as.yaml` says speaks them, over missions, quests, and
  companions; the sample lines the review window hears moved to
  `data/voice_lines.json`, out of the repo. Lines of punctuation alone
  ("...") are no longer rendered: in game they are a miss, which stops
  whatever was playing.
- `helper/census.py` and `tools/proper_nouns.py` still read mission folders
  through `xievent.rov_lines`.
- Counted this way, 480 names have twenty lines or more, 223 have fifty,
  103 have a hundred, and 40 have two hundred. The threshold for a voice of
  one's own has to rise well above twenty to fit 160 slots.

### Attribution gate

A dialogue line with no speaker fails its pack's render preflight, listing
zone, event, and text. The fix is the decoder or an entry in
`helper/cast_as.yaml`. Counted on 2026-11-19:

| Storyline | Dialogue lines with no speaker |
|---|---|
| Seekers of Adoulin (`soa`) | about 1,600 (about 140,000 characters) |
| San d'Oria | about 150 |
| Rhapsodies (`rov`) | none |

Attribution for Adoulin is its own work item and comes before that pack.

## Casting and voices

### Who gets a voice of their own

Importance is counted over the whole corpus (missions, quests, companions).
The current rule, twenty lines or more, yields 178 named characters plus 94
pool voices, 272 in all, which does not fit Pro. So:

1. A **cast audit** prints how many individual voices each threshold yields
   over the full corpus. The threshold is then chosen to fit 160 slots
   together with the designed pool voices.
2. Pool voices come from the **Voice Library** where a library voice works;
   the help centre says those do not use custom slots *(unverified, and an
   owner can withdraw a library voice)*.
3. The moogle and the garden characters get individual voices whatever
   their line count, because the player hears them constantly.

### Files

- `helper/elevenlabs.yaml`, hand-kept: model, voice settings (stability,
  similarity, style, speed), the threshold, the direction and interjection
  tag table, and pool voices assigned to Voice Library ids.
- `helper/elevenlabs_voices.json`, generated and committed: the lock file,
  voice key to `voice_id`, name, and category. Voice ids are not secrets.
- `helper/voices.json` gains an `elevenlabs` section, written by `cast.py`
  as the `moss` section is.

### Sync

`make elevenlabs-sync` lists the account (`GET /v2/voices`, paged), matches
`ffxi/<key>` names to voice keys, and writes the lock file. It reports keys
with no voice, duplicate names, `ffxi/` voices no key wants, and slots used
against the ceiling. Builds read only the lock file and never list the
account. A voice replaced in the UI gets a new id, so the lock changes and
only that voice's lines re-render.

### Voice prompts

Built 2026-11-19. A named character's design in `helper/voice_designs.yaml`
composes (`designs.compose`) into the form ElevenLabs' voice design asks
for:

```
Native <accent>. <Gender>, <age>. <Quality> quality.
Persona: <persona>. Emotion: <emotion>.
<description: one or two sentences about timbre, pacing, delivery>
```

`accent`, `persona` (two to five words), and `emotion` (two or three
adjectives) are fields of a cast entry beside gender and age; a blank one
leaves its part out. The quality level is the run's, `QUALITY=` on the
Makefile, high by default. `make voice-prompts [ONLY=Name] [QUALITY=high]`
prints the prompts to paste into the UI, and the review window's Show
prompt has a Copy button.

Race is not in the prompt: the per-race phrases written for Qwen are gone.
A design keeps its race because it helps whoever is designing the voice.

Most cast entries still carry their accent inside the description, as they
were written for Qwen; moving it into `accent` and filling persona and
emotion is done by hand, a character at a time.

### Pool voices

The pools are stock voices, so a pool voice has no prompt fields: its
description stays as a note on what to pick. The earlier plan, drafting
them through `POST /v1/text-to-voice/design`, is dropped.

## The backend

`helper/elevenlabs.py` is a client on the standard library (`urllib`), so
the helper stays free of dependencies and runs under the system Python.
`ElevenLabsBackend` in `spoken_helper.py` joins the other backends, which
also gives `make say BACKEND=elevenlabs`.

**Text sent.** Stage directions and chosen interjections become audio tags
through the table (`<Sigh>` becomes `[sighs]`); unknown directions are
dropped and listed by the audit. The lexicon is loaded for backend
`elevenlabs` and its IPA goes inline in slashes, the form v4 is documented
to take, reusing `moss.ipa_spans`. `<player>` becomes the player word.
Which interjections move from lexicon entries to tags is decided by a
listening pass of about thirty lines.

**Request.** `POST /v1/text-to-speech/{voice_id}` with `model_id`,
`voice_settings`, a stable per-line `seed`, and a lossless 48 kHz
`output_format` (`pcm_48000` or `wav_48000`). No request stitching: lines
are independent and cached singly. `seed` is best effort; output is not
guaranteed to repeat.

**Masters.** The returned audio is the master and is kept in its own
gitignored folder, apart from the disposable `helper/cache/`, because
losing it costs money. It needs backing up. The existing encoder makes the
pack's Opus from it.

**Cache key.** Voice id, model, settings, seed, the tag table's effect, and
the text actually sent. Changing any of them re-renders only the lines
affected.

**Ledger.** `data/elevenlabs_ledger.jsonl`: per request the time, line key,
characters billed (from the response header), and request id.

**Manifest.** The engine record names the model, settings, and voice ids.
Never the key.

## Rendering in slices

Rate limits and the monthly allowance mean a pack is rendered in pieces:

```
make render MISSIONS=rov SPEAKER=Tenzen [BUDGET=20000] [DRY=1]
make coverage MISSIONS=rov
```

- **Preflight**, before the first request: count the slice's uncached lines
  and characters, price them, read the remaining allowance
  (`GET /v1/user/subscription`). Stop on any speaker in the slice with no
  locked voice, any unattributed dialogue line, or a `BUDGET` or allowance
  overrun. `DRY=1` stops here and prints the estimate.
- **Render** with a small worker pool below the plan's concurrency, back off
  with jitter on rate-limit replies, and write each master atomically, so a
  stopped run resumes where it left off. An exhausted allowance ends the run
  cleanly with a report.
- **Coverage** shows, per speaker: voice key, whether it is locked, lines,
  characters, rendered, and the cost of what remains.

### Pack assembly

`make pack BACKEND=elevenlabs` makes no API calls and so can never spend.
It assembles from the masters and refuses if any line of the pack is
missing: complete storylines only.

### Re-takes

`helper/retakes.yaml` lists a line (speaker and text) with a new seed, an
optional override of the text or tags sent, and a note. The entry changes
that line's cache key, so the next render fetches a new take, the old one
stays on disk, and deleting the entry reverts to it.

## The API key

- `make elevenlabs-key` stores the key in the desktop keyring, encrypted at
  rest and unlocked with the login: the login Keychain on macOS
  (`security`), the Secret Service on Linux (`secret-tool`), as service
  `ffxi-spoken-elevenlabs`, account `api-key`, on both. The keyring's own
  tool prompts for it without echo, so the key never passes through our
  code on the way in. `ELEVENLABS_API_KEY` overrides it. Any process
  running as the user can read the keyring entry, which is why the key is
  a restricted one.
- `make elevenlabs-check` reads the account and spends nothing: plan,
  characters left, voice slots, the voices and how many are `ffxi/`, and
  the speech models. A read the key has no permission for is reported in
  its place. Built 2026-11-19 (`helper/elevenlabs.py`); the first run
  showed restricted keys do have per-endpoint permissions: the check
  needs `user_read`, `voices_read`, and `models_read`.
- The key is sent only in the `xi-api-key` header. It never appears in
  command arguments, logs, error text, the manifest, cache keys, the ledger,
  or test fixtures.
- A test scans the tree and a built manifest for key-shaped strings; `make
  check` runs it.
- Use a restricted key scoped to synthesis and reading voices, with a credit
  cap set in the ElevenLabs dashboard *(scopes and caps unverified)*.
  Nothing we run creates voices, so no key needs voice-write rights.

## Cost

Characters are measured from the committed Rhapsodies manifest and
`voice_stories.json`. The allowance is what `make elevenlabs-check` read on
2026-11-19: 639,758 characters for the month, 146 used, resetting on
2026-12-19. Why it is that figure and not the 1,238,000 this plan first
assumed is not known (the plan page, a rollover, or a change of terms);
the check is the number to trust.

| Scope | Characters | Share of a month's allowance |
|---|---|---|
| Rhapsodies (1,193 lines, 32 speakers) | 80,000 | about an eighth |
| The twelve mission storylines (about 27,000 lines) | about 1.8 M | about 2.8 months |
| One month on Pro ($99) | 639,758 | |

At $99 for that allowance a thousand characters cost about $0.155, so the
twelve storylines are about $280 on `eleven_v4`, over three months unless
more characters are bought. The models' own figures, from the same check:

| Model | Characters a request | Cost multiplier |
|---|---|---|
| `eleven_v4` | 10,000 | 1.0 |
| `eleven_v4_turbo` | 10,000 | 0.5 |
| `eleven_v3` | 5,000 | 1.0 |
| `eleven_multilingual_v2` | 10,000 | 1.0 |
| `eleven_flash_v2_5` | 40,000 | 0.5 |

`eleven_v4_turbo` would halve all of the above if it sounds good enough;
it goes into the listening pass.

Quests and companions are not in these numbers and cannot be sized until
they are decoded. Re-takes are extra.

## Pronunciation dictionaries

ElevenLabs keeps pronunciation dictionaries that a request can attach
(`pronunciation_dictionary_locators`, at most three, each with an optional
`version_id`; made with `POST /v1/pronunciation-dictionaries/add-from-rules`).
A rule is an alias (a respelling) or a phoneme (IPA or CMU Arpabet);
matching is case-sensitive and whole-word by default, and only the first
matching rule is used. Looked at on 2026-11-19 and not adopted as the main
mechanism:

- v4 is documented to read IPA inline between slashes, which is what the
  backend already plans to send from `helper/lexicon.yaml`.
- The docs do not say which models honour a dictionary's phoneme rules;
  the only model they name for phoneme tags is `eleven_flash_v2`, and
  alias rules are what they suggest elsewhere.
- A dictionary applied on their side is not in the text we send, so the
  cache key would have to carry its version as well.
- Ours does more: the longest phrase wins, entries can be limited to
  engines, and `<player>` and stage directions are handled in the same pass.

It stays in reserve for two uses, in both as a file generated from our
lexicon and never edited there: a fallback of alias rules if v4's inline
IPA proves unreliable, and hand auditions in the ElevenLabs UI, if the UI
applies a dictionary *(unverified)*. The listening pass settles it: a few
names said three ways, inline IPA, a respelling, and a dictionary phoneme
rule. That needs a key allowed to write dictionaries; the docs name no
permission for it.

## Tests

All tests use a fake transport and never touch the network. They run under
the system Python and the venvs like the rest (`CLAUDE.md`, Conventions).
Cover: the client's paging, backoff, and redaction; sync's matching and
reports; the backend's text preparation and cache key; preflight's stops;
offline assembly's refusal; the deny-list and line classes; the key scan.

## Build order

1. Key storage, the client, and a no-spend account check. *(built)*
2. Quest and companion decoding, line classes, the deny-list and its audit.
3. Attribution fixes for the storylines wanted first.
4. Cast audit over the full corpus, then the threshold decision.
5. Sync and the lock file.
6. The backend and `make say`.
7. Slice render, ledger, coverage.
8. Offline pack assembly.
9. The tag table, re-takes.

Rhapsodies has no unattributed lines and needs no new decoding, so it can go
through steps 5 to 8 while 2 to 4 are in progress. Along the way: update
`CLAUDE.md`, the Makefile help, and `README.md`'s status table.

## To verify on the first real calls

- Whether v4 wants stress marks in slash IPA (MOSS mispronounces with
  them), and which audio tags it honours on designed voices.
- Custom voice slots on the other plans (Creator 30, Scale 660 comes from
  an old help article). Pro's 160 is confirmed.
- Why the month's allowance read 639,758 characters.
- Whether `eleven_v4_turbo` reads slash IPA and audio tags as v4 does; the
  docs speak only of v4.
- Whether a dictionary's phoneme rules work on v4 at all.
- That library voices can be called by the API without using slots, whether
  they must first be added to the account, and what happens to a locked
  voice when its owner withdraws it.
- Lossless 48 kHz output on Pro (the docs say 44.1 kHz PCM and WAV need Pro).
- Concurrency for v4 on Pro (the docs give 10 for Multilingual v2, 20 for
  Flash).
- The billed-characters header: the docs show both `character-cost` and
  `x-character-count`.
- That restricted keys support a credit cap. (Scopes they do: a key
  without `user_read` is refused the subscription read by name.)
- That designed voices (Voice Design) behave well on v4.

## Open

- The line threshold for an individual voice, once the audit exists.
- Companion lines with item names or numbers filled in at run time have no
  fixed text and stay silent under the pack design. Accept, or find the
  fixed subset worth voicing.
- `eleven_v4` or `eleven_v4_turbo`: half the cost against whatever is lost
  in the voice. Decided by ear in the listening pass.
- Whether to buy characters beyond the month's allowance, or let the
  storylines take the months they take.
- Redistribution: packs ship ElevenLabs output. Paid plans license
  generated audio, but Voice Library voices carry their own terms; read
  them before a pool voice goes into a released pack.

## Sources

- Create speech: https://elevenlabs.io/docs/api-reference/text-to-speech/convert
- Models and concurrency: https://elevenlabs.io/docs/overview/models
- API pricing: https://elevenlabs.io/pricing/api
- Voice slots: https://help.elevenlabs.io/hc/en-us/articles/24351056337937-How-many-voice-slots-do-I-get-per-tier-and-how-can-I-increase-it
- List voices: https://elevenlabs.io/docs/api-reference/voices/search
- Design a voice: https://elevenlabs.io/docs/api-reference/text-to-voice/design
- Pronunciation dictionaries: https://elevenlabs.io/docs/api-reference/pronunciation-dictionaries/create-from-rules
- Best practices (tags, phonemes, dictionaries): https://elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices
- API introduction (response headers): https://elevenlabs.io/docs/api-reference/introduction
