#!/usr/bin/env python3
"""Host-side helper for the Spoken FFXI addon. Work in progress.

Tails data/queue.jsonl, synthesises each dialogue line, plays it, and
reports progress in data/status.json. See docs/Protocol.md.

The helper itself is standard library only; the neural backends need the
.venv that `make venv` builds (Python 3.12 with kokoro-onnx and piper-tts)
and the models that `make voices` downloads into helper/voices/. Backends,
in the order `auto` tries them:
  kokoro  Kokoro 82M, ~50 voices in one model; most natural; GPU via make venv-cuda
  piper   Piper, one .onnx per voice; fast, clear, flat
  espeak  espeak-ng, robotic but installed almost everywhere; for testing
  none    print what would be spoken and pretend it took a moment

    .venv/bin/python -I helper/spoken_helper.py --data ./data [--backend piper]
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path


# ---------------------------------------------------------------------------
# queue

class QueueTail:
    """Yields new JSON entries appended to a file, surviving truncation.

    Starts at the end of whatever is already there: lines written before the
    helper came up are old dialogue and must not be replayed."""

    def __init__(self, path, replay=False):
        self.path = Path(path)
        self.offset = 0
        if not replay and self.path.exists():
            self.offset = self.path.stat().st_size

    def poll(self):
        if not self.path.exists():
            return []
        size = self.path.stat().st_size
        if size < self.offset:
            self.offset = 0  # file was truncated or replaced
        if size == self.offset:
            return []
        with self.path.open("rb") as f:
            f.seek(self.offset)
            chunk = f.read()
        # only consume whole lines
        last_newline = chunk.rfind(b"\n")
        if last_newline < 0:
            return []
        self.offset += last_newline + 1
        entries = []
        for raw in chunk[:last_newline].split(b"\n"):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entries.append(json.loads(raw.decode("utf-8")))
            except (ValueError, UnicodeDecodeError) as e:
                print(f"skipping bad queue line: {e}", file=sys.stderr)
        return entries


# ---------------------------------------------------------------------------
# synthesis

def cache_key(speaker, text, voice):
    h = hashlib.sha256()
    h.update(voice.encode("utf-8"))
    h.update(b"\0")
    h.update(speaker.encode("utf-8"))
    h.update(b"\0")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def write_wav(path, samples, rate):
    """Write mono 16-bit PCM from float samples in [-1, 1]."""
    try:
        import numpy as np  # present wherever kokoro is; not needed otherwise
        pcm = (np.clip(np.asarray(samples, dtype="float32"), -1.0, 1.0) * 32767).astype("<i2").tobytes()
    except ImportError:
        import array
        pcm = array.array("h", (int(max(-1.0, min(1.0, s)) * 32767) for s in samples))
        if sys.byteorder != "little":
            pcm.byteswap()
        pcm = pcm.tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def find_tool(name):
    """A console script installed next to this interpreter wins over PATH,
    so running the helper from .venv finds .venv's piper."""
    beside = Path(sys.executable).parent / name
    if beside.is_file() and os.access(beside, os.X_OK):
        return str(beside)
    return shutil.which(name)


class Backend:
    """Turns text into a WAV. `voices_dir` is helper/voices; each backend
    keeps its models in a subfolder named after itself."""

    name = "none"

    def __init__(self, voices_dir, voice_map):
        self.voices_dir = Path(voices_dir)
        self.voice_map = voice_map

    @property
    def model_dir(self):
        return self.voices_dir / self.name

    @classmethod
    def available(cls, voices_dir):
        return True

    def voice_for(self, speaker):
        return self.voice_map.get(speaker) or self.voice_map.get("default") or "default"

    def synthesise(self, text, voice, out_path):
        raise NotImplementedError


class NoneBackend(Backend):
    name = "none"

    def synthesise(self, text, voice, out_path):
        print(f"[{voice}] {text}")
        return None  # nothing to play


class EspeakBackend(Backend):
    name = "espeak"

    @classmethod
    def available(cls, voices_dir):
        return shutil.which("espeak-ng") is not None

    def synthesise(self, text, voice, out_path):
        args = ["espeak-ng", "-w", str(out_path)]
        if voice != "default":
            args += ["-v", voice]
        subprocess.run(args, input=text.encode("utf-8"), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_path


class PiperBackend(Backend):
    """Piper (https://github.com/OHF-Voice/piper1-gpl). Voices are
    `<name>.onnx` plus `<name>.onnx.json` in voices/piper/, fetched with
    `python -m piper.download_voices <name>`. Fast; clear but flat."""

    name = "piper"

    @classmethod
    def available(cls, voices_dir):
        return find_tool("piper") is not None and any((Path(voices_dir) / cls.name).glob("*.onnx"))

    def synthesise(self, text, voice, out_path):
        if voice == "default":
            raise ValueError("voices.json needs a piper default voice")
        model = self.model_dir / f"{voice}.onnx"
        if not model.exists():
            raise FileNotFoundError(f"no piper voice at {model}")
        piper = find_tool("piper")
        if piper is None:
            raise FileNotFoundError("piper is not installed; run make venv")
        subprocess.run([piper, "-m", str(model), "-f", str(out_path)],
                       input=text.encode("utf-8"), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_path


# Kokoro voice names start with a language letter: af_heart is American
# English female, bm_george British male, jf_alpha Japanese female...
KOKORO_LANGS = {
    "a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
    "i": "it", "j": "ja", "p": "pt-br", "z": "cmn",
}


def kokoro_lang(voice):
    return KOKORO_LANGS.get(voice[:1], "en-us")


def kokoro_provider(available):
    """The onnxruntime provider to run Kokoro on. CUDA when the GPU build
    and its libraries are present, else CPU. TensorRT is skipped: it needs
    an engine build per session and fails over noisily without one."""
    if "CUDAExecutionProvider" in available:
        return "CUDAExecutionProvider"
    return "CPUExecutionProvider"


class KokoroBackend(Backend):
    """Kokoro 82M via kokoro-onnx. One model file and one voice pack in
    voices/kokoro/ give ~50 voices; the voice name selects one. More
    natural than Piper. About a third of realtime on CPU, six times faster
    on an NVIDIA GPU with `make venv-cuda`."""

    name = "kokoro"
    model_file = "kokoro-v1.0.onnx"
    voices_file = "voices-v1.0.bin"

    def __init__(self, voices_dir, voice_map):
        super().__init__(voices_dir, voice_map)
        self._engine = None
        self.device = None

    @classmethod
    def available(cls, voices_dir):
        d = Path(voices_dir) / cls.name
        if not ((d / cls.model_file).exists() and (d / cls.voices_file).exists()):
            return False
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError:
            return False
        return True

    def engine(self):
        if self._engine is None:
            try:
                import onnxruntime as ort
                from kokoro_onnx import Kokoro
            except ImportError as e:
                raise RuntimeError("kokoro-onnx is not installed; run make venv") from e
            if hasattr(ort, "preload_dlls"):
                ort.preload_dlls()  # finds the CUDA libraries installed by pip
            ort.set_default_logger_severity(3)  # errors only; CUDA placement warnings are noise
            provider = kokoro_provider(ort.get_available_providers())
            os.environ.setdefault("ONNX_PROVIDER", provider)  # kokoro-onnx reads this
            self._engine = Kokoro(str(self.model_dir / self.model_file),
                                  str(self.model_dir / self.voices_file))
            self.device = "cuda" if "CUDA" in self._engine.sess.get_providers()[0] else "cpu"
            print(f"kokoro: running on {self.device}")
        return self._engine

    def warm_up(self):
        """Load the model (and the GPU) before the first line arrives."""
        self.engine()

    def synthesise(self, text, voice, out_path):
        if voice == "default":
            raise ValueError("voices.json needs a kokoro default voice")
        samples, rate = self.engine().create(text, voice=voice, lang=kokoro_lang(voice))
        write_wav(out_path, samples, rate)
        return out_path


BACKENDS = {"none": NoneBackend, "espeak": EspeakBackend, "piper": PiperBackend, "kokoro": KokoroBackend}
AUTO_ORDER = (KokoroBackend, PiperBackend, EspeakBackend, NoneBackend)


def pick_backend(name, voices_dir):
    if name != "auto":
        return BACKENDS[name]
    for cls in AUTO_ORDER:
        if cls.available(voices_dir):
            return cls
    return NoneBackend


# ---------------------------------------------------------------------------
# playback

def pick_player():
    for candidate in (["paplay"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"], ["aplay", "-q"]):
        if shutil.which(candidate[0]):
            return candidate
    return None


# ---------------------------------------------------------------------------
# helper

class Helper:
    def __init__(self, data_dir, backend, cache_dir, player, poll_interval=0.1):
        self.data_dir = Path(data_dir)
        self.queue = QueueTail(self.data_dir / "queue.jsonl")
        self.status_path = self.data_dir / "status.json"
        self.backend = backend
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.player = player
        self.poll_interval = poll_interval
        self.playing = None      # (seq, Popen)
        self.fake_until = None   # (seq, deadline) for the none backend

    def write_status(self, seq, state, **extra):
        payload = {"seq": seq, "state": state, "backend": self.backend.name, "t": time.time()}
        payload.update(extra)
        fd, tmp = tempfile.mkstemp(dir=self.data_dir, prefix=".status-")
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, self.status_path)

    def stop(self):
        if self.playing:
            seq, proc = self.playing
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            self.playing = None
        self.fake_until = None

    def speak(self, entry):
        self.stop()
        seq = entry["seq"]
        speaker = entry.get("speaker", "")
        text = entry.get("text", "")
        voice = self.backend.voice_for(speaker)
        wav = self.cache_dir / f"{cache_key(speaker, text, voice)}.wav"
        cached = wav.exists()
        try:
            if not cached:
                self.write_status(seq, "generating", cached=False)
                result = self.backend.synthesise(text, voice, wav)
                if result is None:
                    # dry run: pretend it takes about as long as reading it would
                    self.fake_until = (seq, time.time() + 0.05 * max(1, len(text.split())))
                    self.write_status(seq, "speaking", cached=False)
                    return
        except Exception as e:  # noqa: BLE001 - report everything to the addon
            self.write_status(seq, "error", message=str(e))
            return
        if self.player is None:
            self.write_status(seq, "error", message="no audio player found")
            return
        proc = subprocess.Popen(self.player + [str(wav)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.playing = (seq, proc)
        self.write_status(seq, "speaking", cached=cached)

    def tick(self):
        for entry in self.queue.poll():
            if entry.get("stop"):
                self.stop()
                self.write_status(entry.get("seq", 0), "idle", reason=entry.get("reason"))
            elif "text" in entry:
                self.speak(entry)
        if self.playing:
            seq, proc = self.playing
            if proc.poll() is not None:
                self.playing = None
                self.write_status(seq, "done" if proc.returncode == 0 else "error",
                                  message=None if proc.returncode == 0 else f"player exited {proc.returncode}")
        if self.fake_until and time.time() >= self.fake_until[1]:
            seq = self.fake_until[0]
            self.fake_until = None
            self.write_status(seq, "done")

    def run(self):
        print(f"spoken helper: backend={self.backend.name} data={self.data_dir}")
        warm = getattr(self.backend, "warm_up", None)
        if warm:
            try:
                warm()
            except Exception as e:  # noqa: BLE001 - keep going; speak() will report it
                print(f"warm-up failed: {e}", file=sys.stderr)
        self.write_status(0, "idle")
        try:
            while True:
                self.tick()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            self.stop()


def load_voice_map(path, backend_name):
    """voices.json maps speaker -> voice, either flat or grouped by backend:
    {"kokoro": {"default": "am_adam", "Iroha": "jf_alpha"}, "piper": {...}}.
    Voice names differ per engine, so the grouped form is the useful one."""
    p = Path(path)
    if not p.exists():
        return {}
    with p.open() as f:
        data = json.load(f)
    if any(isinstance(v, dict) for v in data.values()):
        return data.get(backend_name, {})
    return data


def main(argv=None):
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(here.parent / "data"), help="the addon's data folder")
    ap.add_argument("--backend", default="auto", choices=["auto", *BACKENDS])
    ap.add_argument("--voices", default=str(here / "voices"), help="folder with a subfolder of models per backend")
    ap.add_argument("--voice-map", default=str(here / "voices.json"), help="speaker -> voice name, per backend")
    ap.add_argument("--cache", default=str(here / "cache"))
    args = ap.parse_args(argv)

    sys.stdout.reconfigure(line_buffering=True)  # progress shows up when logged to a file
    backend_cls = pick_backend(args.backend, args.voices)
    backend = backend_cls(args.voices, load_voice_map(args.voice_map, backend_cls.name))
    Path(args.data).mkdir(parents=True, exist_ok=True)
    Helper(args.data, backend, args.cache, pick_player()).run()


if __name__ == "__main__":
    main()
