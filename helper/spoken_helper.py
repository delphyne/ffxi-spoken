#!/usr/bin/env python3
"""Host-side helper for the Spoken FFXI addon. Work in progress.

Tails data/queue.jsonl, synthesises each dialogue line, plays it, and
reports progress in data/status.json. See docs/Protocol.md.

Standard library only. Backends:
  piper   local neural TTS; needs the `piper` binary and a voice .onnx
  espeak  espeak-ng, robotic but installed almost everywhere; for testing
  none    print what would be spoken and pretend it took a moment

    python3 -I helper/spoken_helper.py --data ./data [--backend piper]
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
from pathlib import Path


# ---------------------------------------------------------------------------
# queue

class QueueTail:
    """Yields new JSON entries appended to a file, surviving truncation."""

    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0

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


class Backend:
    name = "none"

    def __init__(self, voices_dir, voice_map):
        self.voices_dir = Path(voices_dir)
        self.voice_map = voice_map

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

    def synthesise(self, text, voice, out_path):
        args = ["espeak-ng", "-w", str(out_path)]
        if voice != "default":
            args += ["-v", voice]
        subprocess.run(args, input=text.encode("utf-8"), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_path


class PiperBackend(Backend):
    name = "piper"

    def synthesise(self, text, voice, out_path):
        model = self.voices_dir / f"{voice}.onnx"
        if not model.exists():
            raise FileNotFoundError(f"no piper voice at {model}")
        subprocess.run(["piper", "--model", str(model), "--output_file", str(out_path)],
                       input=text.encode("utf-8"), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_path


BACKENDS = {"none": NoneBackend, "espeak": EspeakBackend, "piper": PiperBackend}


def pick_backend(name):
    if name != "auto":
        return BACKENDS[name]
    if shutil.which("piper"):
        return PiperBackend
    if shutil.which("espeak-ng"):
        return EspeakBackend
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
        self.write_status(0, "idle")
        try:
            while True:
                self.tick()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            self.stop()


def load_voice_map(path):
    p = Path(path)
    if not p.exists():
        return {}
    with p.open() as f:
        return json.load(f)


def main(argv=None):
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(here.parent / "data"), help="the addon's data folder")
    ap.add_argument("--backend", default="auto", choices=["auto", *BACKENDS])
    ap.add_argument("--voices", default=str(here / "voices"), help="folder of piper .onnx voices")
    ap.add_argument("--voice-map", default=str(here / "voices.json"), help="speaker -> voice name")
    ap.add_argument("--cache", default=str(here / "cache"))
    args = ap.parse_args(argv)

    backend_cls = pick_backend(args.backend)
    backend = backend_cls(args.voices, load_voice_map(args.voice_map))
    Path(args.data).mkdir(parents=True, exist_ok=True)
    Helper(args.data, backend, args.cache, pick_player()).run()


if __name__ == "__main__":
    main()
