#!/usr/bin/env python3
"""Design the MOSS backend's voices: each description in
helper/voice_designs.yaml becomes a reference clip, Qwen3-TTS VoiceDesign
speaking the calibration sentence in that voice.

    make design-voices [ONLY=Name] [FRESH=1]     render missing or changed clips
    make review-voices                           a page with every clip to listen to
    .venv-qwen/bin/python -I tools/design_voices.py --help

Clips go to helper/voices/moss/refs/<voice>.wav (gitignored; audio is never
committed), with refs/index.json recording each voice's description, seed,
and sha256. A clip is redesigned when it is missing, when its description or
seed in the YAML no longer matches the index, or with --fresh. To reroll a
voice you dislike, change its seed and run again.

--adopt DIR takes clips already designed elsewhere (the listening spike's
data/spike/ref/) for voices whose clip is missing, so voices that were heard
and liked are not redrawn. The index records them as adopted.

Runs under .venv-qwen (torch, qwen-tts). The Qwen3 model loads in float16
with SDPA; Turing cards have neither bfloat16 nor FlashAttention 2.
"""

import argparse
import hashlib
import html
import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "helper"))
import casting  # noqa: E402
import designs as designs_module  # noqa: E402
import spoken_helper as sh  # noqa: E402

DESIGN_REPO = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
BASE_REPO = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
REFS = REPO / "helper" / "voices" / "moss" / "refs"


def log(*args):
    print(*args, flush=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Qwen:
    """Qwen3-TTS through the qwen_tts package. One model at a time; `load`
    swaps. Imports torch lazily so the rest of the script runs without it."""

    def __init__(self, device="cuda:0", seed=1234, codec_cpu=True):
        self.device = device
        self.seed = seed
        self.codec_cpu = codec_cpu and device.startswith("cuda")
        self.model = None
        self.repo = None

    def _torch(self):
        import torch
        return torch

    def load(self, repo):
        if self.repo == repo:
            return
        self.free()
        torch = self._torch()
        from qwen_tts import Qwen3TTSModel
        log(f"qwen    loading {repo} on {self.device}")
        t0 = time.perf_counter()
        # Turing has no bfloat16 and no FlashAttention 2: float16 and SDPA.
        dtype = torch.float16 if self.device.startswith("cuda") else torch.float32
        self.model = Qwen3TTSModel.from_pretrained(repo, device_map=self.device, dtype=dtype,
                                                   attn_implementation="sdpa")
        if self.codec_cpu:
            # The talker (1.7B) stays on the card; the speech codec that turns
            # its codes into audio moves to the CPU in float32. With other
            # processes on an 8 GB card the two together ran out of memory on
            # lines over about five seconds. The wrapper moves its inputs to
            # `device`, so changing that is all it takes.
            codec = self.model.model.speech_tokenizer
            codec.model.float().to("cpu")
            codec.device = torch.device("cpu")
            torch.cuda.empty_cache()
        self.repo = repo
        log(f"qwen    loaded in {time.perf_counter() - t0:.0f}s"
            + (" (codec on cpu)" if self.codec_cpu else ""))

    def free(self):
        if self.model is not None:
            self.model = None
            self.repo = None
            torch = self._torch()
            if self.device.startswith("cuda"):
                torch.cuda.empty_cache()

    def manual_seed(self, seed):
        self._torch().manual_seed(seed)

    def reseed(self, speaker):
        """The listening spike's per-speaker seed: base seed plus the name's
        code points. voice_designs.yaml records the result for its voices."""
        self.manual_seed(self.seed + sum(map(ord, speaker)))

    def peak_vram_mb(self):
        torch = self._torch()
        if not self.device.startswith("cuda"):
            return None
        mb = torch.cuda.max_memory_allocated() / 2**20
        torch.cuda.reset_peak_memory_stats()
        return round(mb)

    def design(self, text, instruct):
        self.load(DESIGN_REPO)
        wavs, sr = self.model.generate_voice_design(text=text, language="English", instruct=instruct)
        return wavs[0], sr

    def clone_prompt(self, ref_audio, ref_text):
        self.load(BASE_REPO)
        return self.model.create_voice_clone_prompt(ref_audio=str(ref_audio), ref_text=ref_text,
                                                    x_vector_only_mode=False)

    def clone(self, text, prompt):
        self.load(BASE_REPO)
        wavs, sr = self.model.generate_voice_clone(text=text, language="English", voice_clone_prompt=prompt)
        return wavs[0], sr


# ---------------------------------------------------------------------------

def load_index(refs):
    path = Path(refs) / "index.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("voices", {})


def write_index(refs, index, calibration):
    Path(refs, "index.json").write_text(json.dumps(
        {"calibration": calibration, "model": DESIGN_REPO, "voices": dict(sorted(index.items()))},
        indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def selected(d, only):
    """Voice names for --only, which takes voice names or speaker names."""
    if not only:
        return list(d.voices)
    out = []
    for name in only:
        voice = name if name in d.voices else d.voice_for_speaker(name)
        if voice is None:
            raise SystemExit(f"--only {name!r}: no such voice or designed speaker")
        out.append(voice)
    return out


def plan(d, index, refs, voices, fresh=False):
    """(voice, reason) for each clip to (re)design; reason is missing,
    changed, or fresh."""
    todo = []
    for voice in voices:
        v, rec = d.voices[voice], index.get(voice)
        clip = Path(refs) / f"{voice}.wav"
        if fresh:
            todo.append((voice, "fresh"))
        elif not clip.exists() or rec is None:
            todo.append((voice, "missing"))
        elif rec.get("description") != v["description"] or rec.get("seed") != v["seed"] \
                or rec.get("calibration") != d.calibration:
            todo.append((voice, "changed"))
    return todo


def record(d, voice, clip, how):
    v = d.voices[voice]
    rec = {k: v[k] for k in ("kind", "description", "seed") if k in v}
    rec.update({k: v[k] for k in ("group", "names") if k in v})
    rec.update(calibration=d.calibration, sha256=sha256_file(clip), how=how)
    return rec


def design_all(d, todo, refs, engine, index, adopt=None):
    """Render each planned clip, or copy it from `adopt` when the reason is
    `missing` and a clip by that name is there. Updates `index` in place."""
    refs = Path(refs)
    refs.mkdir(parents=True, exist_ok=True)
    for voice, reason in todo:
        clip = refs / f"{voice}.wav"
        source = Path(adopt) / f"{voice}.wav" if adopt else None
        if reason == "missing" and source is not None and source.exists():
            shutil.copyfile(source, clip)
            index[voice] = record(d, voice, clip, f"adopted from {source}")
            log(f"adopt   {voice}")
            continue
        v = d.voices[voice]
        engine.manual_seed(v["seed"])
        t0 = time.perf_counter()
        samples, rate = engine.design(d.calibration, v["description"])
        sh.write_wav(clip, samples, rate)
        index[voice] = record(d, voice, clip, "designed")
        log(f"design  {voice:16} {reason:8} {time.perf_counter() - t0:5.1f}s")
        write_index(refs, index, d.calibration)  # after every clip, so a crash loses one
    write_index(refs, index, d.calibration)


def write_review(d, refs, index):
    """refs/review.html: every voice, its description, and its clip,
    embedded as Opus so a sandboxed browser can play it."""
    import spike_qwen3 as sp  # beside this file; the embedder and page style
    refs = Path(refs)
    src = sp.embedder(refs)
    e = html.escape
    parts = ["<!doctype html><meta charset=utf-8><title>Spoken: designed voices</title>",
             f"<style>{sp.STYLE}</style><h1>Designed voices</h1>",
             f"<p>Each clip says: <i>{e(d.calibration)}</i></p>",
             "<table><tr><th>voice</th><th>speakers</th><th>description</th><th>seed</th><th>clip</th></tr>"]
    for voice, v in d.voices.items():
        clip = refs / f"{voice}.wav"
        who = ", ".join(v.get("names", [])) or f"pool: {v['group']}"
        cell = (f"<audio controls preload=none src='{src(clip)}'></audio>" if clip.exists()
                else "<span class=small>not designed yet</span>")
        how = index.get(voice, {}).get("how", "")
        parts.append(f"<tr><td>{e(voice)}<div class=small>{e(how)}</div></td><td>{e(who)}</td>"
                     f"<td class=txt>{e(v['description'])}</td><td>{v['seed']}</td><td>{cell}</td></tr>")
    parts.append("</table>")
    page = refs / "review.html"
    page.write_text("\n".join(parts), encoding="utf-8")
    return page


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--designs", default=str(designs_module.DEFAULT_PATH))
    ap.add_argument("--refs", default=str(REFS))
    ap.add_argument("--only", action="append", help="a voice or speaker name; repeatable")
    ap.add_argument("--fresh", action="store_true", help="redesign even if the clip is current")
    ap.add_argument("--adopt", metavar="DIR", help="copy missing clips from DIR/<voice>.wav instead of designing")
    ap.add_argument("--dry-run", action="store_true", help="list what would be designed")
    ap.add_argument("--review", action="store_true", help="only write refs/review.html")
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--codec-gpu", action="store_true", help="keep Qwen's speech codec on the GPU")
    args = ap.parse_args(argv)

    d = designs_module.Designs.load(args.designs, groups=set(casting.POOLS))
    index = load_index(args.refs)
    if not args.review:
        todo = plan(d, index, args.refs, selected(d, args.only), fresh=args.fresh)
        log(f"{len(todo)} of {len(d.voices)} voices to design")
        if args.dry_run:
            for voice, reason in todo:
                log(f"  {voice:16} {reason}")
            return 0
        if todo:
            engine = Qwen("cpu" if args.cpu else "cuda:0", codec_cpu=not args.codec_gpu)
            design_all(d, todo, args.refs, engine, index, adopt=args.adopt)
            engine.free()
        orphans = sorted(p.stem for p in Path(args.refs).glob("*.wav") if p.stem not in d.voices)
        if orphans:
            log("clips with no design (left in place): " + ", ".join(orphans))
    page = write_review(d, args.refs, load_index(args.refs))
    log(f"open {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
