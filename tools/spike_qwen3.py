#!/usr/bin/env python3
"""Listening spike: the same Rhapsodies lines through Kokoro and Qwen3-TTS,
side by side, to decide whether a Qwen3 backend is worth building.

    make venv-qwen                 a second venv with qwen-tts (torch, CUDA) and kokoro-onnx
    make spike-qwen [LIMIT=N] [RAW=1] [FRESH=1]
    .venv-qwen/bin/python -I tools/spike_qwen3.py --help

Picks a fixed list of lines from packs/rhapsodies/0.1/manifest.json: the
stutters ("Wh-what!?"), the interjections ("Hmph."), names the lexicon fixes,
and a few plain lines per speaker to hear whether a voice stays the same from
line to line. Each is rendered as

    kokoro          the pack's own path: the cast voice, the lexicon as IPA,
                    the helper's WAV cache (so this is the 0.1 pack audio)
    qwen-design     Qwen3-TTS VoiceDesign: a voice from a one-line description,
                    designed afresh for every line (shows drift)
    qwen-clone      the description rendered once on a calibration sentence,
                    then Qwen3-TTS Base clones that clip for every line
                    (the shape a real backend would take)
    qwen-clone-raw  --also-raw: the clone without the lexicon's respellings

Qwen3-TTS takes plain text only, so its lines get the lexicon's respellings
(`spell`, else helper/respell.py from the IPA). Output goes to data/spike/:
the WAVs, the reference clips, timings.json, and listen.html with an audio
player per variant and the Kokoro phonemes for each line. Open it in a browser.

The two Qwen models are loaded one after the other (about 3.4 GB each in
float16) with the speech codec on the CPU, so an 8 GB card that is also
running the helper and the desktop is enough (--codec-gpu keeps it all on the
card). qwen_tts and torch are imported only when a
Qwen variant is rendered, so the tests and --only-kokoro need neither.
"""

import argparse
import base64
import gc
import html
import json
import re
import shutil
import sys
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "helper"))
import spoken_helper as sh  # noqa: E402
from design_voices import BASE_REPO, DESIGN_REPO, Qwen  # noqa: E402,F401

MANIFEST = REPO / "packs" / "rhapsodies" / "0.1" / "manifest.json"
PLAYER = "<player>"

# (speaker, text) exactly as the manifest has them. Grouped by what they test;
# select_lines() fails if one is missing so the list cannot go stale quietly.
LINES = [
    # stutters: Kokoro spells the prefix letter ("double-u aitch what")
    ("Lilisette", "Wh-what!? That's an ill omen if I ever heard one!"),
    ("Tenzen", "Wh-what!?"),
    ("Iroha", "M-Master..."),
    ("Zeid", "I-impossible!"),
    ("Gilgamesh", "Wh-what's goin' on?"),
    # interjections: Hmph is in the lexicon, the rest are left to the engine
    ("Cait Sith", "Hmph."),
    ("Prishe", "Hmph. Long as I'm still in one piece I couldn't give a flying fig."),
    ("Tenzen", "Ugh. Do what you will!"),
    ("Tenzen", "Ahem. Our priestesses take vows of poverty, and I highly doubt that centuries-old tradition "
               "will have changed in the future."),
    ("Lilisette", "Hmmm... Then I have a stranger question: have you been having any odd dreams lately?"),
    ("Gilgamesh", "Oho! Would ya look who's decided ta join us!"),
    ("Ekokoko", "Something about setting sail and finding someone lost at sea? Bah, I never had much of a "
                "head for details."),
    # names: IPA on Kokoro, respellings on Qwen
    ("Iroha", "This time, I can sense the presence of another who featured prominently in tales of your "
              "adventures: Lilisette."),
    ("Tenzen", "Only in the most dire of circumstances is a priestess from Reisenjima Sanctorium permitted "
               "to leave the tiny island."),
    ("Tenzen", "What on Vana'diel..."),
    # plain lines: does the voice hold from line to line, and how is the register
    ("Iroha", "Many and more have supported you in your adventures throughout Vana'diel."),
    ("Iroha", "Behind every door you open is another meeting that could decide the fate of Vana'diel."),
    ("Alexander", "Oho. But 'twould seem thou art even more cacophonous than usual. Perhaps because now "
                  "there are two of thee."),
]

# One line per speaker in the attribute style Qwen's VoiceDesign examples use.
# Races and sexes follow helper/cast.py; the rest is how they read on the page.
VOICES = {
    "Iroha": "Gender: female. Age: young adult. A clear, warm, earnest voice with a soft Japanese accent; "
             "measured pace, formal and respectful, a little grave.",
    "Tenzen": "Gender: male. Age: middle-aged. A calm, low, dignified samurai's voice with a slight Japanese "
              "accent; unhurried, precise, formal.",
    "Gilgamesh": "Gender: male. Age: fifties. A booming, gravelly pirate captain's voice; hearty and brash, "
                 "rough working-class English accent, quick and loud.",
    "Lilisette": "Gender: female. Age: late teens. A bright, lively, energetic voice with a light French "
                 "accent; quick, expressive, easily excited.",
    "Cait Sith": "Gender: female. A prim, precise, feline voice with a refined British accent; crisp "
                 "consonants, slightly haughty, a hint of purr.",
    "Prishe": "Gender: female. Age: young adult. A brash, husky, confident voice with a working-class "
              "British accent; fast, blunt, amused.",
    "Zeid": "Gender: male. Age: mature. A very deep, grim, resonant voice; slow and heavy, few words, "
            "weary gravity.",
    "Alexander": "Gender: male. An immense, ancient, resonant voice like a cathedral organ; slow, archaic, "
                 "solemn and faintly amused.",
    "Ekokoko": "Gender: female. Age: young. A small, high, chirpy voice; quick and cheerful, a little "
               "flustered.",
}

# Rendered once per speaker to make the clip the Base model clones from. No
# names, two clauses, about four seconds. Neutral so the voice, not the line,
# is what the clone picks up.
CALIBRATION = ("Wait here while I speak with the guards. If anything goes wrong, follow the road back to "
               "town and do not look behind you.")

# the columns listen.html shows, when their files exist; the moss ones come from tools/spike_moss.py
VARIANTS = ("kokoro", "qwen-design", "qwen-clone", "qwen-clone-raw", "moss-ipa", "moss-ipa-plain", "moss-raw")


def log(*args):
    print(*args)


# ---------------------------------------------------------------------------
# lines

def slug(text, limit=32):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit].rstrip("-") or "line"


def select_lines(manifest, pairs=LINES, limit=0):
    """The manifest rows for `pairs`, in that order, numbered. Raises if any
    pair is not in the manifest, naming them all."""
    by_key = {(row["speaker"], row["text"]): row for row in manifest["lines"]}
    missing = [p for p in pairs if p not in by_key]
    if missing:
        raise KeyError("not in the manifest: " + "; ".join(f"{s}: {t!r}" for s, t in missing))
    rows = []
    for n, pair in enumerate(pairs, 1):
        row = dict(by_key[pair])
        row["n"] = n
        row["stem"] = f"{n:02d}-{slug(row['speaker'], 16)}-{slug(row['text'])}"
        rows.append(row)
    if limit:
        rows = rows[:limit]
    speakers = {r["speaker"] for r in rows}
    undescribed = sorted(speakers - set(VOICES))
    if undescribed:
        raise KeyError("no voice description for: " + ", ".join(undescribed))
    return rows


def texts_for(row, lexicon, player_word):
    """What each engine is handed. `kokoro` is the manifest's own `spoken`
    (IPA blocks, the pack's cache key); `qwen` is the same line with the
    lexicon as respellings; `raw` is the bare line."""
    raw = row["text"].replace(PLAYER, player_word)
    return {"kokoro": row["spoken"], "qwen": lexicon.apply(raw, phonemes=False), "raw": raw}


def wav_seconds(path):
    with wave.open(str(path), "rb") as w:
        return round(w.getnframes() / w.getframerate(), 3)


class Timings:
    """Per-variant render records and the summary listen.html shows. Loaded
    from the previous run's timings.json so files kept across reruns keep
    their numbers; a re-render replaces the record for that file."""

    def __init__(self):
        self.variants = {}

    @classmethod
    def load(cls, path):
        t = cls()
        path = Path(path)
        if path.exists():
            t.variants = json.loads(path.read_text()).get("variants", {})
        return t

    def record(self, variant, stem, seconds, audio):
        v = self.variants.setdefault(variant, {"renders": []})
        v["renders"] = [r for r in v["renders"] if r["file"] != stem]
        v["renders"].append({"file": stem, "seconds": round(seconds, 3), "audio": audio})

    def note(self, variant, **info):
        self.variants.setdefault(variant, {"renders": []}).update(info)

    def summary(self):
        out = {}
        for name, v in self.variants.items():
            if not v["renders"]:
                continue  # nothing rendered this run (all kept, or never asked for)
            spent = sum(r["seconds"] for r in v["renders"])
            audio = sum(r["audio"] for r in v["renders"])
            out[name] = dict({k: val for k, val in v.items() if k != "renders"},
                             lines=len(v["renders"]), seconds=round(spent, 1), audio=round(audio, 1),
                             rtf=round(spent / audio, 2) if audio else None)
        return out

    def dump(self, path):
        Path(path).write_text(json.dumps({"summary": self.summary(), "variants": self.variants}, indent=1))


# ---------------------------------------------------------------------------
# kokoro

def render_kokoro(rows, texts, synth, phonemize, cache_dir, out_dir, timings):
    """The pack's path: cache by speaker, spoken text, and voice; synthesise on
    a miss; copy into the spike folder. `phonemize(text, stem)` is recorded per
    line so the spelled-out tokens are visible on the page."""
    cache_dir, out_dir = Path(cache_dir), Path(out_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        spoken = texts[row["stem"]]["kokoro"]
        wav = cache_dir / f"{sh.cache_key(row['speaker'], spoken, row['voice'])}.wav"
        t0 = time.perf_counter()
        if not wav.exists():
            synth(spoken, row["voice"], wav)
            row["kokoro_cached"] = False
        else:
            row["kokoro_cached"] = True
        seconds = time.perf_counter() - t0
        dest = out_dir / f"{row['stem']}.kokoro.wav"
        shutil.copyfile(wav, dest)
        row["phonemes"] = phonemize(texts[row["stem"]]["raw"], row["stem"])
        timings.record("kokoro", row["stem"], seconds, wav_seconds(dest))
        log(f"kokoro  {row['stem']}  {'cache' if row['kokoro_cached'] else f'{seconds:.1f}s'}")
        log(f"        {row['phonemes']}")


def kokoro_backend(voices_dir, voice_map, census):
    backend = sh.KokoroBackend(voices_dir, sh.load_voice_map(voice_map, "kokoro"),
                               fallback=sh.census_fallback(census, "kokoro"))
    if not backend.available(voices_dir):
        raise FileNotFoundError(f"no Kokoro model under {voices_dir}/kokoro; run make voices")
    return backend


# ---------------------------------------------------------------------------
# qwen

# The Qwen engine lives in design_voices.py; imported at the top.


def render_qwen(rows, texts, engine, out_dir, timings, also_raw=False, fresh=False):
    """Design per line, then design once per speaker and clone the rest, one
    line at a time (a speaker's lines in one batch ran the 8 GB card out of
    memory in the codec decoder). Files already there are kept unless `fresh`,
    so a rerun after a crash costs only what is missing; timings cover what
    was rendered. `engine` has design/clone_prompt/clone/reseed/peak_vram_mb
    (Qwen above, or a fake in the tests)."""
    out_dir = Path(out_dir)
    ref_dir = out_dir / "ref"
    ref_dir.mkdir(parents=True, exist_ok=True)
    speakers = []
    for row in rows:
        if row["speaker"] not in speakers:
            speakers.append(row["speaker"])

    def render(variant, stem, dest, make):
        if dest.exists() and not fresh:
            log(f"{variant:7} {stem}  kept")
            return
        t0 = time.perf_counter()
        samples, rate = make()
        seconds = time.perf_counter() - t0
        sh.write_wav(dest, samples, rate)
        timings.record(variant, stem, seconds, wav_seconds(dest))
        log(f"{variant:7} {stem}  {seconds:.1f}s")

    # VoiceDesign: every line afresh, plus the calibration clip per speaker
    for row in rows:
        engine.reseed(row["speaker"])
        render("qwen-design", row["stem"], out_dir / f"{row['stem']}.qwen-design.wav",
               lambda row=row: engine.design(texts[row["stem"]]["qwen"], VOICES[row["speaker"]]))
    for speaker in speakers:
        engine.reseed(speaker)
        ref = ref_dir / f"{slug(speaker, 16)}.wav"
        render("qwen-design", f"ref/{ref.stem}", ref,
               lambda speaker=speaker: engine.design(CALIBRATION, VOICES[speaker]))
    timings.note("qwen-design", peak_vram_mb=engine.peak_vram_mb())

    # Base: one clone prompt per speaker, then that speaker's lines one by one
    variants = [("qwen-clone", "qwen")] + ([("qwen-clone-raw", "raw")] if also_raw else [])
    for speaker in speakers:
        mine = [r for r in rows if r["speaker"] == speaker]
        wanted = [(r, v, k) for r in mine for v, k in variants
                  if fresh or not (out_dir / f"{r['stem']}.{v}.wav").exists()]
        if not wanted:
            continue
        prompt = engine.clone_prompt(ref_dir / f"{slug(speaker, 16)}.wav", CALIBRATION)
        for r, variant, key in wanted:
            engine.reseed(speaker)
            render(variant, r["stem"], out_dir / f"{r['stem']}.{variant}.wav",
                   lambda r=r, key=key: engine.clone(texts[r["stem"]][key], prompt))
    timings.note("qwen-clone", peak_vram_mb=engine.peak_vram_mb())
    engine.free()


# ---------------------------------------------------------------------------
# the page

CHECKLIST = [
    "Does <b>qwen-design</b> keep one voice across Iroha's and Tenzen's lines, or does it drift?",
    "Does <b>qwen-clone</b> sound like its reference clip (below) and stay stable line to line?",
    "The stutters (Wh-what, M-Master, I-impossible) and Hmph on each engine. Kokoro's phonemes show "
    "what it was actually told to say.",
    "Lilisette, Reisenjima, Vana'diel: Kokoro has IPA, Qwen only the respelling shown in the "
    "<i>Qwen text</i> column.",
    "With RAW=1, does <b>qwen-clone-raw</b> already get the names right without the lexicon?",
]

STYLE = """
body{font:15px/1.4 system-ui,sans-serif;margin:24px;color:#222;background:#fafafa}
table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #ddd;padding:8px 6px;
vertical-align:top;text-align:left}th{background:#eee;position:sticky;top:0}
td.n{color:#888;width:2em}audio{width:230px;display:block}.ph{font-family:monospace;font-size:13px;
color:#555;white-space:pre-wrap}.txt{max-width:28em}.small{color:#666;font-size:13px}
.ref audio{display:inline-block;vertical-align:middle;margin-left:8px}
"""


def embedder(out_dir, encode=None):
    """Returns `src(path)`: a data: URI of `path` as Ogg Opus, so the page is
    one self-contained file. A browser installed as a flatpak gets the page
    through the document portal and cannot see the WAVs beside it, which is
    how this came up. The Opus files are kept in out_dir/.opus and re-encoded
    only when the WAV is newer. `encode(wav, opus)` defaults to the pack
    builder's encoder (opusenc or ffmpeg)."""
    out_dir = Path(out_dir)
    cache = out_dir / ".opus"

    def src(path):
        nonlocal encode
        path = Path(path)
        opus = cache / path.relative_to(out_dir).with_suffix(".opus")
        if not opus.exists() or opus.stat().st_mtime < path.stat().st_mtime:
            if encode is None:
                import build_pack  # beside this file
                encode = build_pack.Encoder()
            encode(path, opus)
        return "data:audio/ogg;base64," + base64.b64encode(opus.read_bytes()).decode("ascii")
    return src


def write_html(out_dir, rows, texts, timings, variants, src=None):
    """`src(path)` gives an <audio> element's src for a file in out_dir; the
    default embeds it (see embedder), `str` would link it."""
    out_dir = Path(out_dir)
    if src is None:
        src = embedder(out_dir)
    e = html.escape
    present = [v for v in variants if (out_dir / f"{rows[0]['stem']}.{v}.wav").exists()] if rows else []
    summary = timings.summary()
    parts = ["<!doctype html><meta charset=utf-8><title>Spoken: Kokoro vs Qwen3-TTS</title>",
             f"<style>{STYLE}</style><h1>Kokoro vs Qwen3-TTS, {len(rows)} lines</h1>",
             "<p>Rhapsodies of Vana'diel 0.1 lines. Listen row by row:</p><ol>"]
    parts += [f"<li>{c}</li>" for c in CHECKLIST]
    parts.append("</ol><table><tr><th>variant</th><th>lines</th><th>render s</th><th>audio s</th>"
                 "<th>RTF</th><th>peak VRAM MB</th></tr>")
    for v in present:
        s = summary.get(v, {})
        parts.append(f"<tr><td>{e(v)}</td><td>{s.get('lines', '')}</td><td>{s.get('seconds', '')}</td>"
                     f"<td>{s.get('audio', '')}</td><td>{s.get('rtf', '')}</td>"
                     f"<td>{s.get('peak_vram_mb') or ''}</td></tr>")
    parts.append("</table>")
    refs = sorted((out_dir / "ref").glob("*.wav")) if (out_dir / "ref").is_dir() else []
    if refs:
        parts.append("<h2>Reference clips (designed once, then cloned)</h2><p class=small>"
                     f"{e(CALIBRATION)}</p><ul class=ref>")
        parts += [f"<li>{e(r.stem)}<audio controls preload=none title='ref/{e(r.name)}' "
                  f"src='{src(r)}'></audio></li>" for r in refs]
        parts.append("</ul>")
    parts.append("<h2>Lines</h2><table><tr><th></th><th>speaker</th><th>text</th>")
    parts += [f"<th>{e(v)}</th>" for v in present]
    parts.append("</tr>")
    for row in rows:
        t = texts[row["stem"]]
        cells = [f"<td class=n>{row['n']}</td>",
                 f"<td>{e(row['speaker'])}<div class=small>{e(row['voice'])}</div></td>",
                 f"<td class=txt>{e(t['raw'])}"]
        if t["qwen"] != t["raw"]:
            cells.append(f"<div class=small><i>Qwen text:</i> {e(t['qwen'])}</div>")
        if t.get("moss") and t["moss"] != t["raw"]:
            cells.append(f"<div class=small><i>MOSS text:</i> {e(t['moss'])}</div>")
        if row.get("phonemes"):
            cells.append(f"<div class=ph>{e(row['phonemes'])}</div>")
        cells.append("</td>")
        for v in present:
            f = f"{row['stem']}.{v}.wav"
            cells.append(f"<td><audio controls preload=none title='{e(f)}' src='{src(out_dir / f)}'></audio></td>"
                         if (out_dir / f).exists() else "<td></td>")
        parts.append("<tr>" + "".join(cells) + "</tr>")
    parts.append("</table>")
    page = out_dir / "listen.html"
    page.write_text("\n".join(parts), encoding="utf-8")
    return page


# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(REPO / "data" / "spike"))
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--limit", type=int, default=0, help="only the first N lines")
    ap.add_argument("--only-kokoro", action="store_true")
    ap.add_argument("--only-qwen", action="store_true")
    ap.add_argument("--also-raw", action="store_true", help="add qwen-clone-raw: the clone without the lexicon")
    ap.add_argument("--fresh", action="store_true", help="re-render Qwen files that already exist")
    ap.add_argument("--cpu", action="store_true", help="run Qwen on the CPU (slow)")
    ap.add_argument("--link", action="store_true",
                    help="link the WAVs from listen.html instead of embedding them as Opus")
    ap.add_argument("--codec-gpu", action="store_true",
                    help="keep Qwen's speech codec on the GPU too (needs the card mostly free)")
    ap.add_argument("--voices", default=str(REPO / "helper" / "voices"))
    ap.add_argument("--voice-map", default=str(REPO / "helper" / "voices.json"))
    ap.add_argument("--census", default=str(REPO / "helper" / "census.json"))
    ap.add_argument("--cache", default=str(REPO / "helper" / "cache"), help="the helper's WAV cache")
    ap.add_argument("--lexicon", default=str(REPO / "helper" / "lexicon.yaml"))
    args = ap.parse_args(argv)

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    rows = select_lines(manifest, limit=args.limit)
    lexicon = sh.Lexicon.load(args.lexicon, backend="qwen")
    texts = {r["stem"]: texts_for(r, lexicon, manifest.get("player_word", "adventurer")) for r in rows}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    timings = Timings.load(out / "timings.json")

    if not args.only_qwen:
        backend = kokoro_backend(args.voices, args.voice_map, args.census)
        engine = backend.engine()
        # the phonemes shown on the page come from the same G2P and accent the line was rendered with
        voice_of = {r["stem"]: r["voice"] for r in rows}
        render_kokoro(rows, texts, backend.synthesise,
                      lambda t, stem=None: engine.tokenizer.phonemize(t, sh.kokoro_lang(voice_of.get(stem, "af_sarah"))),
                      args.cache, out, timings)
        timings.note("kokoro", device=backend.device)
        # Kokoro's onnxruntime session keeps its GPU arena until it is dropped,
        # and the 1.7B Qwen model needs the room (cuBLAS failed to allocate
        # with both resident on an 8 GB card)
        backend._engine = engine = None
        del backend
        gc.collect()
    if not args.only_kokoro:
        qwen = Qwen("cpu" if args.cpu else "cuda:0", codec_cpu=not args.codec_gpu)
        render_qwen(rows, texts, qwen, out, timings, also_raw=args.also_raw, fresh=args.fresh)
        for variant in ("qwen-design", "qwen-clone", "qwen-clone-raw"):
            if variant in timings.variants:
                timings.note(variant, device=qwen.device, codec="cpu" if qwen.codec_cpu else qwen.device)

    timings.dump(out / "timings.json")
    page = write_html(out, rows, texts, timings, VARIANTS,
                      src=(lambda path: str(Path(path).relative_to(out))) if args.link else None)
    for name, s in timings.summary().items():
        print(f"{name:15} {s['lines']:3} lines  {s['seconds']:7.1f}s for {s['audio']:6.1f}s audio  "
              f"RTF {s['rtf']}" + (f"  VRAM {s['peak_vram_mb']} MB" if s.get("peak_vram_mb") else ""))
    print(f"\nopen {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
