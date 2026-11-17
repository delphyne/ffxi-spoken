#!/usr/bin/env python3
"""Listening spike, second engine: the same Rhapsodies lines through
MOSS-TTS-Local, cloned from the clips Qwen3-TTS designed, with the lexicon
as IPA. Adds columns to the page tools/spike_qwen3.py writes.

    make venv-moss                  .venv-moss from a MOSS-TTS checkout (torch 2.9.1 cu128)
    make spike-moss [LIMIT=N] [FRESH=1]
    .venv-moss/bin/python -I tools/spike_moss.py --help

Why: Qwen3-TTS gave individual, natural voices but takes no phonemes, and no
respelling or in-context trick got the game's names right reliably. MOSS-TTS
accepts IPA inside slashes mixed with text, and clones a voice from a
reference clip, so the lexicon can keep its IPA and Qwen's VoiceDesign can
keep designing the voices. Variants:

    moss-ipa        the lexicon's IPA (stress marks and all) as /.../ spans
    moss-ipa-plain  the same without the stress marks, the way MOSS's own
                    phonemizer writes IPA
    moss-raw        the bare line, no lexicon

Each speaker is cloned from data/spike/ref/<speaker>.wav, the clip the Qwen
spike designed; run `make spike-qwen` first if they are missing. Files that
exist are kept unless --fresh, and timings merge into data/spike/timings.json.
"""

import argparse
import json
import re
import sys
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "helper"))
import spike_qwen3 as sp  # noqa: E402
import spoken_helper as sh  # noqa: E402

MODEL_REPO = "OpenMOSS-Team/MOSS-TTS-Local-Transformer"
STRESS = "ˈˌ"


def ipa_spans(text, plain=False):
    """`[[ipa]]` blocks (the helper's neutral phoneme markup) as MOSS's
    `/ipa/` spans; `plain` drops the stress marks, which MOSS's own
    phonemizer never writes."""
    def span(m):
        ipa = m.group(1)
        if plain:
            ipa = "".join(c for c in ipa if c not in STRESS)
        return f"/{ipa}/"
    return sh.PHONEME_BLOCK.sub(span, text)


def moss_texts(row, lexicon, player_word):
    raw = row["text"].replace(sp.PLAYER, player_word)
    marked = lexicon.apply(raw, phonemes=True)
    return {"moss-ipa": ipa_spans(marked), "moss-ipa-plain": ipa_spans(marked, plain=True), "moss-raw": raw}


class Moss:
    """MOSS-TTS-Local through transformers' remote code. Imports torch lazily
    so the tests and --help run without it."""

    def __init__(self, device="cuda", seed=1234, n_vq=32, max_new_tokens=1024, codec_cpu=True, int8=False):
        self.device = device
        self.codec_cpu = codec_cpu and device.startswith("cuda")
        self.int8 = int8 and device.startswith("cuda")
        self.seed = seed
        self.n_vq = n_vq
        self.max_new_tokens = max_new_tokens
        self.model = self.processor = None
        self.rate = None
        self._codes = {}

    def load(self):
        if self.model is not None:
            return
        import torch
        from transformers import AutoModel, AutoProcessor
        torch.backends.cuda.enable_cudnn_sdp(False)  # the model card calls this backend broken
        t0 = time.perf_counter()
        sp.log(f"moss    loading {MODEL_REPO} on {self.device}")
        self.processor = AutoProcessor.from_pretrained(MODEL_REPO, trust_remote_code=True)
        codec = self.processor.audio_tokenizer
        codec_params = sum(p.numel() for p in codec.parameters()) / 1e9
        if not self.codec_cpu:
            # the codec loads in float32 and alone filled the 8 GB card before
            # the model arrived; on the GPU it goes in float16
            self.processor.audio_tokenizer = codec.to(self.device, dtype=torch.float16)
        # Turing has no bfloat16; float16 keeps the 6 GB of weights at half size
        dtype = torch.float16 if self.device.startswith("cuda") else torch.float32
        if self.int8:
            # 6 GB of float16 weights leave no room on an 8 GB card once the
            # desktop has its share; 8-bit weights (bitsandbytes) take half
            from transformers import BitsAndBytesConfig
            self.model = AutoModel.from_pretrained(MODEL_REPO, trust_remote_code=True, attn_implementation="sdpa",
                                                   torch_dtype=dtype, device_map={"": self.device},
                                                   quantization_config=BitsAndBytesConfig(load_in_8bit=True))
        else:
            self.model = AutoModel.from_pretrained(MODEL_REPO, trust_remote_code=True, attn_implementation="sdpa",
                                                   torch_dtype=dtype).to(self.device)
        self.model.eval()
        model_params = sum(p.numel() for p in self.model.parameters()) / 1e9
        self.rate = self.processor.model_config.sampling_rate
        sp.log(f"moss    loaded in {time.perf_counter() - t0:.0f}s: model {model_params:.2f}B "
               f"{'int8' if self.int8 else str(dtype).split('.')[-1]} on {self.device}, "
               f"codec {codec_params:.2f}B on {'cpu' if self.codec_cpu else self.device}, {self.rate} Hz")

    def free(self):
        if self.model is not None:
            import torch
            self.model = self.processor = None
            if self.device.startswith("cuda"):
                torch.cuda.empty_cache()

    def reseed(self, speaker):
        import torch
        torch.manual_seed(self.seed + sum(map(ord, speaker)))

    def peak_vram_mb(self):
        import torch
        if not self.device.startswith("cuda"):
            return None
        mb = torch.cuda.max_memory_allocated() / 2**20
        torch.cuda.reset_peak_memory_stats()
        return round(mb)

    def reference_codes(self, ref_wav):
        """The reference clip as codec tokens, encoded once per clip. The
        processor would load a path through torchaudio, which needs torchcodec
        and an FFmpeg no newer than 8; this machine has 9, so the WAV is read
        here and handed over as a waveform instead."""
        import torch
        ref_wav = Path(ref_wav)
        if ref_wav not in self._codes:
            with wave.open(str(ref_wav), "rb") as w:
                rate = w.getframerate()
                pcm = w.readframes(w.getnframes())
            samples = torch.frombuffer(bytearray(pcm), dtype=torch.int16).float() / 32768.0
            codes = self.processor.encode_audios_from_wav([samples.unsqueeze(0)], sampling_rate=rate)
            self._codes[ref_wav] = codes[0]
        return self._codes[ref_wav]

    def clone(self, text, ref_wav, ref_text=None):
        """One line in the voice of `ref_wav`; returns (samples, rate). With
        `ref_text` (the clip's transcript) MOSS runs in continuation mode: the
        clip becomes the start of the answer and the line continues it, the
        way Qwen's in-context cloning worked. Without it, generation mode with
        the clip as a reference."""
        import torch
        self.load()
        codes = self.reference_codes(ref_wav)
        if ref_text:
            conversation = [self.processor.build_user_message(text=ref_text + " " + text),
                            self.processor.build_assistant_message(audio_codes_list=[codes])]
            batch = self.processor([conversation], mode="continuation")
        else:
            conversation = [self.processor.build_user_message(text=text, reference=[codes])]
            batch = self.processor([conversation], mode="generation")
        with torch.no_grad():
            outputs = self.model.generate(input_ids=batch["input_ids"].to(self.device),
                                          attention_mask=batch["attention_mask"].to(self.device),
                                          max_new_tokens=self.max_new_tokens, n_vq_for_inference=self.n_vq)
        message = self.processor.decode(outputs)[0]
        audio = message.audio_codes_list[0]
        return audio.float().cpu().numpy(), self.rate


def render_moss(rows, texts, engine, out_dir, ref_dir, timings, fresh=False):
    """Every line in each variant, cloned from the speaker's reference clip.
    `engine` has clone/reseed/peak_vram_mb/free (Moss, or a fake in the tests)."""
    out_dir, ref_dir = Path(out_dir), Path(ref_dir)
    missing = sorted({r["speaker"] for r in rows if not (ref_dir / f"{sp.slug(r['speaker'], 16)}.wav").exists()})
    if missing:
        raise FileNotFoundError("no reference clip for " + ", ".join(missing) + f" under {ref_dir}; run make spike-qwen")
    for row in rows:
        ref = ref_dir / f"{sp.slug(row['speaker'], 16)}.wav"
        for variant, text in texts[row["stem"]].items():
            dest = out_dir / f"{row['stem']}.{variant}.wav"
            if dest.exists() and not fresh:
                sp.log(f"{variant:14} {row['stem']}  kept")
                continue
            engine.reseed(row["speaker"])
            t0 = time.perf_counter()
            samples, rate = engine.clone(text, ref)
            seconds = time.perf_counter() - t0
            sh.write_wav(dest, samples, rate)
            timings.record(variant, row["stem"], seconds, sp.wav_seconds(dest))
            sp.log(f"{variant:14} {row['stem']}  {seconds:.1f}s")
    peak = engine.peak_vram_mb()
    for variant in ("moss-ipa", "moss-ipa-plain", "moss-raw"):
        if variant in timings.variants:
            timings.note(variant, device=engine.device, peak_vram_mb=peak,
                         codec="cpu" if getattr(engine, "codec_cpu", False) else engine.device,
                         weights="int8" if getattr(engine, "int8", False) else "float16")
    engine.free()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(REPO / "data" / "spike"))
    ap.add_argument("--manifest", default=str(sp.MANIFEST))
    ap.add_argument("--limit", type=int, default=0, help="only the first N lines")
    ap.add_argument("--fresh", action="store_true", help="re-render files that already exist")
    ap.add_argument("--cpu", action="store_true", help="run on the CPU (very slow)")
    ap.add_argument("--n-vq", type=int, default=32, help="codec depth: 4 fast and rough, 32 full fidelity")
    ap.add_argument("--codec-gpu", action="store_true", help="put the audio codec on the GPU too (needs more than 8 GB)")
    ap.add_argument("--int8", action="store_true", help="load the model in 8-bit (bitsandbytes) to fit beside a busy desktop")
    ap.add_argument("--lexicon", default=str(REPO / "helper" / "lexicon.yaml"))
    args = ap.parse_args(argv)

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    rows = sp.select_lines(manifest, limit=args.limit)
    lexicon = sh.Lexicon.load(args.lexicon)
    player_word = manifest.get("player_word", "adventurer")
    moss = {r["stem"]: moss_texts(r, lexicon, player_word) for r in rows}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    timings = sp.Timings.load(out / "timings.json")

    engine = Moss("cpu" if args.cpu else "cuda", n_vq=args.n_vq, codec_cpu=not args.codec_gpu, int8=args.int8)
    render_moss(rows, moss, engine, out, out / "ref", timings, fresh=args.fresh)
    timings.dump(out / "timings.json")

    # the shared page: Kokoro and Qwen columns from their files, ours beside them
    page_texts = {r["stem"]: sp.texts_for(r, lexicon, player_word) for r in rows}
    for r in rows:
        page_texts[r["stem"]]["moss"] = moss[r["stem"]]["moss-ipa"]
    page = sp.write_html(out, rows, page_texts, timings, sp.VARIANTS)
    for name, s in timings.summary().items():
        if name.startswith("moss"):
            print(f"{name:15} {s['lines']:3} lines  {s['seconds']:7.1f}s for {s['audio']:6.1f}s audio  RTF {s['rtf']}"
                  + (f"  VRAM {s['peak_vram_mb']} MB" if s.get("peak_vram_mb") else ""))
    print(f"\nopen {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
