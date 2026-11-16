#!/usr/bin/env python3
"""Review the dialogue's proper nouns one by one and build the lexicon.

Reads data/proper_nouns.tsv (from tools/proper_nouns.py), speaks each word
with the engine's own pronunciation, and asks:

  a  accept: write the pronunciation being played to helper/lexicon.yaml
  r  repeat it
  j  reject: type the IPA to use instead; it plays, and the question repeats
  s  skip: the engine already says it right, no entry needed
  e  hear the word in an example line from the game
  q  quit

Progress is kept in data/review_progress.json, so quitting and starting
again resumes at the first word not yet covered. Words already in the
lexicon are skipped. Runs with the venv: `make review`.
"""

import argparse
import csv
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spoken_helper as sh  # noqa: E402
import respell  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"


def read_candidates(path):
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def load_progress(path):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_progress(path, progress):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(progress, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)


def append_entry(lexicon_path, word, ipa, note=""):
    """Append one stanza to lexicon.yaml, quoting the IPA so stress marks
    and commas are safe. The running helper hot-reloads the file."""
    p = Path(lexicon_path)
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    key = json.dumps(word, ensure_ascii=False) if ":" in word or word[:1] in "'\"[{,&*!|>%@`#" else word
    comment = f"   # {note}" if note else ""
    text += f"{key}:\n    ipa: {json.dumps(ipa, ensure_ascii=False)}{comment}\n"
    p.write_text(text, encoding="utf-8")


def covered_words(entries):
    """Words the lexicon already handles: every key, and every word of a
    multi-word key, so "Cait Sith" covers the candidates Cait and Sith."""
    words = set()
    for key in entries:
        words.add(key)
        words.update(key.split())
    return words


def validate_ipa(word, typed):
    """Normalise hand-typed IPA the way the lexicon does; raises ValueError."""
    return sh.Lexicon({word: {"ipa": typed}}).entries[word]["ipa"]


class Speaker:
    """Speaks `[[ipa]]` through a backend, caching in a temp folder."""

    def __init__(self, backend, player, cache_dir=None):
        self.backend = backend
        self.player = player
        self.cache = Path(cache_dir or tempfile.mkdtemp(prefix="spoken-review-"))
        self.voice = backend.voice_for("")

    def say(self, text):
        wav = self.cache / f"{sh.cache_key('', text, self.voice)}.wav"
        if not wav.exists():
            self.backend.synthesise(text, self.voice, wav)
        if self.player and wav.exists():
            subprocess.run(self.player + [str(wav)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def word(self, ipa):
        self.say(f"[[{ipa}]]")

    def in_context(self, word, ipa, sentence):
        self.say(sh.Lexicon({word: {"ipa": ipa}}).apply(sentence, phonemes=True))


def review(candidates, progress, lexicon_path, speaker, read=input, out=print, progress_path=None, ipa_for=None):
    """The loop. `read` and `speaker` are injectable for tests. Returns the
    number of words covered this run."""
    entries = sh.Lexicon.load(lexicon_path).entries
    existing = covered_words(entries)
    todo = [c for c in candidates if c["word"] not in progress and c["word"] not in existing]
    out(f"{len(todo)} to review, {len(progress)} covered before, {len(entries)} already in the lexicon")
    covered = 0
    for n, cand in enumerate(todo, 1):
        word = cand["word"]
        engine_ipa = cand.get("kokoro_ipa") or (ipa_for(word) if ipa_for else "")
        if not engine_ipa:
            out(f"{word}: no IPA available, skipping")
            continue
        current, mine = engine_ipa, False
        detail = (f"{cand.get('uses', '?')} uses, {cand.get('rov_uses', '0')} in Rhapsodies, "
                  f"{cand.get('zones', '?')} zones" + (", NPC name" if cand.get("npc") else ""))
        example = cand.get("example") or ""
        out(f"\n[{n}/{len(todo)}] {word}  ({detail})")
        if example:
            out(f'  "{example}"')
        out(f"  engine: {engine_ipa}  ({respell.respell(engine_ipa)})")
        speaker.word(current)
        while True:
            label = "yours" if mine else "engine"
            try:
                cmd = read(f"  {label}: {current}   [a]ccept [r]epeat [j]reject+IPA [s]kip [e]xample [q]uit > ").strip().lower()
            except EOFError:
                cmd = "q"
            if cmd == "r":
                speaker.word(current)
            elif cmd == "e":
                if example:
                    speaker.in_context(word, current, example)
                else:
                    out("  no example line for this word")
            elif cmd == "j":
                try:
                    typed = read("  ipa> ").strip()
                except EOFError:
                    typed = ""
                if not typed:
                    continue
                try:
                    current = validate_ipa(word, typed)
                except ValueError as e:
                    out(f"  {e}")
                    continue
                mine = True
                out(f"  now: {current}  ({respell.respell(current)})")
                speaker.word(current)
            elif cmd == "a":
                append_entry(lexicon_path, word, current, note=f"{cand.get('uses', '?')} uses")
                progress[word] = {"status": "accepted", "ipa": current, "engine_ipa": engine_ipa, "t": int(time.time())}
                out(f"  added {word}: {current}")
                break
            elif cmd == "s":
                progress[word] = {"status": "skipped", "engine_ipa": engine_ipa, "t": int(time.time())}
                out("  skipped; the engine's reading stands")
                break
            elif cmd == "q":
                out(f"\nstopped; {covered} covered this run. `make review` resumes here.")
                return covered
            else:
                out("  a, r, j, s, e or q")
        covered += 1
        if progress_path:
            save_progress(progress_path, progress)
    out(f"\nall {len(todo)} reviewed.")
    return covered


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", default=str(DATA / "proper_nouns.tsv"))
    ap.add_argument("--progress", default=str(DATA / "review_progress.json"))
    ap.add_argument("--lexicon", default=str(HERE / "lexicon.yaml"))
    ap.add_argument("--voices", default=str(HERE / "voices"))
    ap.add_argument("--voice-map", default=str(HERE / "voices.json"))
    ap.add_argument("--backend", default="auto", choices=["auto", *sh.BACKENDS])
    ap.add_argument("--top", type=int, default=0, help="review only the first N candidates (0 = all)")
    args = ap.parse_args(argv)

    if not Path(args.candidates).exists():
        sys.exit(f"{args.candidates} is missing; run `make nouns` first")
    backend_cls = sh.pick_backend(args.backend, args.voices)
    if not backend_cls.phonemes:
        sys.exit(f"the {backend_cls.name} backend cannot take phonemes; use kokoro or piper")
    backend = backend_cls(args.voices, sh.load_voice_map(args.voice_map, backend_cls.name))
    warm = getattr(backend, "warm_up", None)
    if warm:
        warm()
    ipa_for = None
    if backend_cls.name == "kokoro":
        ipa_for = lambda w: backend.engine().tokenizer.phonemize(w, "en-us")  # noqa: E731
    candidates = read_candidates(args.candidates)
    if args.top:
        candidates = candidates[:args.top]
    progress = load_progress(args.progress)
    try:
        review(candidates, progress, args.lexicon, Speaker(backend, sh.pick_player()),
               progress_path=args.progress, ipa_for=ipa_for)
    finally:
        save_progress(args.progress, progress)
    return 0


if __name__ == "__main__":
    sys.exit(main())
