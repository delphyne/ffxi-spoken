# Rendering packs with MOSS-TTS from designed voices: handoff

Written 2026-11-17. The goal is to render audio packs with individual,
natural voices that still say the game's names right. Kokoro, the engine
behind Rhapsodies 0.1, says names right through the lexicon's IPA, but it
has about fifty stock voices, flat delivery, and spells out what it does not
know ("Hmph" comes out as "aitch em pee aitch", "Wh-what" as "double-u aitch
what"). This records what the listening tests established, the design that
follows from them, and the build order.

Nothing here touches the addon, the pack format, or the native player. It
is all developer tooling on the host, as `CLAUDE.md` describes.

## What the listening established

Two spike scripts render the same eighteen Rhapsodies lines through each
candidate and put them side by side on one page: `tools/spike_qwen3.py`
(`make spike-qwen`) and `tools/spike_moss.py` (`make spike-moss`). The
lines cover stutters, interjections, the lexicon's names, and several plain
lines per speaker. These are the user's verdicts, by ear:

| | Kokoro | Qwen3-TTS clone | MOSS-TTS-Local |
| --- | --- | --- | --- |
| Names from the lexicon | right (IPA) | wrong, no fix found | right, IPA with stress marks removed |
| Stutters and interjections | spelled out | much better | as good as or better than Qwen |
| Individual voices | fifty presets | designed, stable when cloned | matches a designed clip, drifts somewhat |

- **Qwen3-TTS** (1.7B, `qwen-tts` on PyPI) designs a voice from a sentence
  (VoiceDesign) or clones one from a clip (Base). A description alone
  drifts from line to line; a clip designed once and cloned holds steady.
  It takes plain text only. Respellings derived from the lexicon's IPA,
  hand respellings in four styles, extra seeds, and in-context priming with
  verified takes all failed on Vana'diel, and the two spellings that worked
  in Tenzen's voice failed in other voices. Fine-tuning is single-speaker
  and full-parameter, beyond an 8 GB card. It stays useful for one thing:
  designing a voice from a description.
- **MOSS-TTS-Local** (`OpenMOSS-Team/MOSS-TTS-Local-Transformer`, 3.06B
  parameters despite the 1.7B in its documentation, Apache-2.0, 24 kHz)
  clones from a reference and accepts IPA wrapped in slashes inside
  ordinary text. With the stress marks left in, names come out wrong; with
  them removed, right. In generation mode (clip as a reference) the voice
  matched the clip for some speakers and not others; continuation mode (the
  clip is the start of the answer, its transcript the start of the text)
  matched with short and long references. It drifts somewhat between lines.
- **Ruled out**: GLM-TTS (its phoneme input is pinyin only; no English
  path), the 8B MOSS-TTS (does not fit), ZONOS3-Mini (its pronunciation
  lexicon is private).

## The design

Qwen3 VoiceDesign makes one reference clip per voice from a hand-written
description; MOSS-TTS-Local renders every line by continuing that clip, with
the lexicon applied as IPA. Kokoro stays as the backend for live play and
quick checks.

### Voices

- **`helper/voice_designs.yaml`**, hand-edited like the lexicon:
  - `calibration`: the neutral sentence every clip says, and so the
    transcript MOSS continues from.
  - `cast`: a description and seed per named character. It starts with the
    Rhapsodies speakers. Variant names share a voice the way `cast.py` pins
    them (Volto Oscuro is Iroha; the twelve Cait Siths are one).
  - `pools`: four descriptions per casting group (the keys of
    `casting.POOLS`), for the thousands of NPCs cast by race.
- **`tools/design_voices.py`** runs under `.venv-qwen` and renders each
  description into `helper/voices/moss/refs/<voice>.wav`, then encodes the
  clip's MOSS codes beside it as `<voice>.codes.pt` under `.venv-moss`. A
  character's voice name is its slug; a pool voice is `<group>-<n>`.
  `refs/index.json` records description, seed, and sha256 per voice.
- **Audio is never committed.** The clips sit in the gitignored
  `helper/voices/`. Every pack manifest records the sha256 of each clip it
  used, and `make release` uploads the clip set beside the pack zip, so a
  pack can be rebuilt with the same voices.
- `make design-voices [ONLY=Name] [FRESH=1]`, and `make review-voices` for a
  page with every clip and its description.

### Casting

`helper/casting.py` gains `MOSS_POOLS`, four voices per group, picked by the
same stable hash as Kokoro's pools. `helper/cast.py` writes a `moss` section
into `voices.json`: a named character with a design gets their own voice,
everyone else in `CAST` their group's pool voice. NPCs outside `voices.json`
fall back through the census by race as now.

### The backend

`MossBackend` in `helper/spoken_helper.py`, `name = "moss"`,
`phonemes = True`:

- `[[ipa]]` blocks become `/ipa/` with `ˈ` and `ˌ` removed.
- Continuation mode: the user turn is the calibration sentence followed by
  the line, the assistant turn the clip's codes.
- The voice string it hands the cache is
  `moss:<voice>:<clip sha8>:t<temperature>:<fp16|int8>`, so redesigning a
  voice or changing a setting re-renders only the lines it touches, and
  Kokoro's cache entries stay distinct.
- The seed from `voice_designs.yaml` is set before every line.
- Settings, recorded in every manifest: `MOSS_TEMPERATURE` (1.0),
  `MOSS_N_VQ` (32), `MOSS_INT8` (off).
- Not in `AUTO_ORDER`: at about 2.4 times real time it is for packs and
  `make say`, not live play.
- Reference WAVs are read with `wave`, never `torchaudio.load`: this
  machine's FFmpeg 9 is newer than torchcodec supports.

### Two GPU passes for packs

The model in float16 is 6.1 GB; the codec ships in float32 at 7.1 GB; the
desktop holds about 1.7 GB of the 8 GB card. They cannot be resident
together, but they never need to be:

1. **Codes.** Only the model on the GPU. Every line missing from the WAV
   cache is generated to codes, saved under `helper/cache/moss-codes/`.
2. **Audio.** The model unloaded, the codec on the GPU in float32 as
   trained, every saved code file decoded into the WAV cache.

An interrupted build resumes in either pass. Reference codes are encoded
once at design time, so builds never load the encoder. One-off lines
(`make say`, the helper) keep a single-pass path with the codec on the CPU.

### Pack build

- `tools/build_pack.py` drives the two passes when the backend offers them,
  and asks the backend for its engine record: model repo and snapshot
  revision, the settings, and every reference clip's hash.
- `make pack BACKEND=moss` runs under `.venv-moss/bin/python`; the Makefile
  picks the interpreter by backend.
- Rhapsodies 0.2 is the first MOSS pack. 75 minutes of audio at about 2.4
  times real time is roughly three hours, less if the GPU codec pass is
  faster than the CPU one.

## Environment

| venv | built by | holds | why separate |
| --- | --- | --- | --- |
| `.venv` | `make venv` | Kokoro, Piper | onnxruntime pins |
| `.venv-qwen` | `make venv-qwen` | qwen-tts, torch 2.10, Kokoro | torch and torchaudio must match; qwen-tts pins neither |
| `.venv-moss` | `make venv-moss` | MOSS-TTS from `~/scm/github/OpenMOSS/MOSS-TTS`, torch 2.9.1, transformers 5, bitsandbytes | MOSS pins torch 2.9.1 and transformers 5.0 |

Models download into `helper/voices/<engine>/hf` through `HF_HOME`. The
helper and the pack builder need only PyYAML and numpy beyond the engine, so
`.venv-moss` carries those too.

## Build order

1. Spike tooling committed (`07efa75`).
2. `voice_designs.yaml` with descriptions for the Rhapsodies speakers and the
   pools; its loader and validation; `design_voices.py`; Make targets.
3. `MOSS_POOLS` and the `moss` section of `voices.json`.
4. `MossBackend`, single-pass, with `make say BACKEND=moss`.
5. The two-pass interface and `build_pack.py` support; the engine record.
6. Docs: `CLAUDE.md` architecture, `docs/Audio-Packs.md` engine fields,
   `README.md` status table. No `_addon.version` bump.
7. Design the voices, listen, build a 20-line Rhapsodies 0.2, listen, time
   both passes, then the full pack.

Each step lands with its tests in `helper/test_helper.py` or `tools/`, run by
`make check` under the system python3 and under `.venv-moss`.

## Open points

- **Drift.** Left at temperature 1.0 until a first pack has been heard.
  Lower audio temperature is the usual way to steady a cloned voice; the
  setting exists so this needs no code.
- **Pool size.** Four voices per group is a guess at enough variety for
  crowds.
- **8-bit quality.** Untested by ear against float16; only the single-pass
  path uses it.
- **Generation versus continuation fidelity** was judged on three speakers.
