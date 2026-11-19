#!/usr/bin/env python3
"""A MOSS worker for the voice review: lines rendered the way a pack would
render them, continuing a reference clip, one request per line.

    .venv-moss/bin/python -I helper/moss_say.py

Reads one JSON object per line on stdin and answers one per line on stdout:

    {"ref": "refs/iroha.wav", "ref_text": "Wait here...", "seed": 7,
     "text": "Master, [[vɑnədiˈɛl]] needs you.", "out": "hear/iroha.1.wav"}
    -> {"ok": true, "out": "hear/iroha.1.wav", "seconds": 12.3}
    -> {"ok": false, "error": "..."}

`text` is the line as the pack would send it: directions dropped, the
player named, the lexicon applied with its [[IPA]] blocks, which become
MOSS's /.../ spans here. The model stays loaded between requests and the
codec on the CPU (MOSS_CODEC=cpu32, the one-off path), so the first line
pays the load and the rest do not. The clip's codes are kept per file.
The window starts this under .venv-moss once Qwen has let go of the card,
and ends it before the next Qwen take: the two do not fit together.
"""

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import moss  # noqa: E402
import spoken_helper as sh  # noqa: E402


class Sayer:
    """Renders lines through an engine (moss.Engine or a fake with the same
    encode/generate/decode/rate), keeping each clip's codes."""

    def __init__(self, engine, write_wav=sh.write_wav):
        self.engine, self.write_wav = engine, write_wav
        self.codes = {}   # (clip path, mtime) -> codes

    def reference(self, clip):
        clip = Path(clip)
        key = (str(clip), clip.stat().st_mtime_ns)
        if key not in self.codes:
            self.codes.clear()   # one clip at a time is all the window needs
            self.codes[key] = self.engine.encode(clip)
        return self.codes[key]

    def say(self, req):
        for k in ("ref", "ref_text", "seed", "text", "out"):
            if k not in req:
                raise ValueError(f"request lacks {k!r}")
        text = moss.ipa_spans(req["text"], sh.PHONEME_BLOCK)
        generated = self.engine.generate(text, self.reference(req["ref"]), req["ref_text"], int(req["seed"]))
        out = Path(req["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        self.write_wav(out, self.engine.decode(generated), self.engine.rate)
        return str(out)


def serve(inp, out, sayer):
    """Answer each request line until stdin closes."""
    for line in inp:
        line = line.strip()
        if not line:
            continue
        started = time.monotonic()
        try:
            path = sayer.say(json.loads(line))
            reply = {"ok": True, "out": path, "seconds": round(time.monotonic() - started, 1)}
        except Exception as e:  # noqa: BLE001 - every failure goes back as a reply
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        out.write(json.dumps(reply, ensure_ascii=False) + "\n")
        out.flush()


def main(argv=None):
    settings = moss.Settings.from_env()
    if settings.codec != "cpu32":
        sys.exit("moss_say decodes on the CPU; unset MOSS_CODEC")
    # replies alone go up the pipe; the engine's and the libraries' chatter goes to stderr
    replies = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    sys.stdout = sys.stderr
    sayer = Sayer(moss.Engine(settings, "cuda"))
    sayer.engine.load_model()
    replies.write(json.dumps({"ready": True, "device": sayer.engine.device}) + "\n")
    replies.flush()
    serve(sys.stdin, replies, sayer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
