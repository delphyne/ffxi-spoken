#!/usr/bin/env python3
"""Review the dialogue's proper nouns one by one and build the lexicon.

Reads data/proper_nouns.tsv (from tools/proper_nouns.py), speaks each word
with the engine's own pronunciation, and waits for one keypress:

  a  accept: write the pronunciation being played to helper/lexicon.yaml
  r  repeat it
  j  reject: type the IPA to use instead (a line); it plays, and the question repeats
  s  skip: the engine already says it right, no entry needed
  e  hear the word in an example line from the game
  b  back to the previous word, undoing its accept or skip
  q  quit

With a display and Tk (the venv has it) this opens a window instead: the
same keys, buttons for each of them, one word at a time, and a palette of
IPA glyphs with the sound each makes for building a replacement
(review_gui.py). --cli keeps the terminal loop, where only the IPA is a
line ending in Enter and the commands take effect on the key; when stdin
is not a terminal (a pipe, the tests) every read is a line.

Progress is kept in data/review_progress.json, so quitting and starting
again resumes at the first word not yet covered; `b` from there steps back
into the words covered in earlier runs. Words already in the lexicon are
skipped. Runs with the venv: `make review`.
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
    comment = f"   # {note}" if note else ""
    text += f"{lexicon_key(word)}:\n    ipa: {json.dumps(ipa, ensure_ascii=False)}{comment}\n"
    p.write_text(text, encoding="utf-8")


def lexicon_key(word):
    """The YAML key append_entry writes for `word`."""
    return json.dumps(word, ensure_ascii=False) if ":" in word or word[:1] in "'\"[{,&*!|>%@`#" else word


def remove_entry(lexicon_path, word):
    """Remove the stanza append_entry wrote for `word`: its key line and the
    indented lines under it. True when one was there."""
    p = Path(lexicon_path)
    if not p.exists():
        return False
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    key = lexicon_key(word) + ":"
    for i, line in enumerate(lines):
        if line.rstrip("\n") == key or line.startswith(key + " ") or line.startswith(key + "\t"):
            j = i + 1
            while j < len(lines) and (lines[j].startswith((" ", "\t")) or not lines[j].strip()):
                j += 1
            del lines[i:j]
            p.write_text("".join(lines), encoding="utf-8")
            return True
    return False


def covered_words(entries):
    """Words the lexicon already handles: every key, and every word of a
    multi-word key, so "Cait Sith" covers the candidates Cait and Sith."""
    words = set()
    for key in entries:
        words.add(key)
        words.update(key.split())
    return words


def read_key(prompt):
    """One keypress, without Enter, when stdin is a terminal; otherwise a
    line, so a piped script or a test drives the loop the same way. Ctrl-C
    and Ctrl-D end the run like `q` would, with progress saved."""
    if not sys.stdin.isatty():
        return input(prompt)
    import termios
    import tty
    sys.stdout.write(prompt)
    sys.stdout.flush()
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    if ch in ("", "\x04"):
        raise EOFError
    if ch == "\x03":
        raise KeyboardInterrupt
    print(ch if ch.isprintable() else "")
    return ch


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


class Session:
    """The review's state, shared by the terminal loop and the window: the
    words still to do, the current one with the engine's IPA and the IPA
    being tried, and the bookkeeping on accept, skip, and back. Playback
    goes through `speaker` (a Speaker, or a fake in tests).

    `todo` keeps the words covered in earlier runs (those in `progress`)
    so `back` can reach them; `start` is where this run begins."""

    def __init__(self, candidates, progress, lexicon_path, speaker, progress_path=None, ipa_for=None, out=print):
        self.progress, self.lexicon_path, self.speaker = progress, lexicon_path, speaker
        self.progress_path, self.ipa_for, self.out = progress_path, ipa_for, out
        entries = sh.Lexicon.load(lexicon_path).entries
        existing = covered_words(entries)
        self.todo = [c for c in candidates if c["word"] in progress or c["word"] not in existing]
        self.start = next((i for i, c in enumerate(self.todo) if c["word"] not in progress), len(self.todo))
        self.in_lexicon = len(entries)
        self.pos, self.covered = self.start - 1, 0
        self.cand = self.engine_ipa = self.current = None
        self.mine = False

    def intro(self):
        return (f"{len(self.todo) - self.start} to review, {self.start} covered before, "
                f"{self.in_lexicon} already in the lexicon")

    def candidate_ipa(self, cand):
        return cand.get("kokoro_ipa") or (self.ipa_for(cand["word"]) if self.ipa_for else "")

    @property
    def done(self):
        return self.cand is None

    @property
    def word(self):
        return self.cand["word"]

    @property
    def example(self):
        return self.cand.get("example") or ""

    def detail(self):
        c = self.cand
        return (f"{c.get('uses', '?')} uses, {c.get('pack_uses', c.get('rov_uses', '0'))} in the storyline, {c.get('zones', '?')} zones"
                + (", NPC name" if c.get("npc") else ""))

    def advance(self):
        """Move to the next word that has an IPA to play; False at the end."""
        while True:
            self.pos += 1
            if self.pos >= len(self.todo):
                self.cand = None
                return False
            cand = self.todo[self.pos]
            ipa = self.candidate_ipa(cand)
            if ipa:
                self.cand, self.engine_ipa, self.current, self.mine = cand, ipa, ipa, False
                return True
            self.out(f"{cand['word']}: no IPA available, skipping")

    def play(self):
        self.speaker.word(self.current)

    def play_example(self):
        """The current IPA inside the example line; False when there is none."""
        if not self.example:
            return False
        self.speaker.in_context(self.word, self.current, self.example)
        return True

    def set_ipa(self, typed):
        """Validate hand-typed IPA and make it current. Raises ValueError
        with the lexicon's explanation when it is not IPA."""
        self.current = validate_ipa(self.word, typed)
        self.mine = True
        return self.current

    def try_ipa(self, typed):
        """set_ipa, then play it."""
        self.set_ipa(typed)
        self.play()
        return self.current

    def accept(self):
        append_entry(self.lexicon_path, self.word, self.current, note=f"{self.cand.get('uses', '?')} uses")
        self.progress[self.word] = {"status": "accepted", "ipa": self.current, "engine_ipa": self.engine_ipa,
                                    "t": int(time.time())}
        self._covered()

    def skip(self):
        self.progress[self.word] = {"status": "skipped", "engine_ipa": self.engine_ipa, "t": int(time.time())}
        self._covered()

    def _covered(self):
        self.covered += 1
        if self.progress_path:
            save_progress(self.progress_path, self.progress)
        self.advance()

    def back(self):
        """Return to the word before this one and make its choice again: its
        progress entry is dropped and, if it was accepted, its lexicon
        stanza removed. The IPA it was given comes back as the one being
        tried. Works from the end of the list too. False at the first word."""
        pos = self.pos - 1
        while pos >= 0 and not self.candidate_ipa(self.todo[pos]):
            pos -= 1
        if pos < 0:
            return False
        cand = self.todo[pos]
        prev = self.progress.pop(cand["word"], None) or {}
        if prev.get("status") == "accepted":
            remove_entry(self.lexicon_path, cand["word"])
            self.in_lexicon -= 1
        if prev and pos >= self.start:
            self.covered -= 1
        self.start = min(self.start, pos)
        if self.progress_path:
            save_progress(self.progress_path, self.progress)
        self.pos, self.cand = pos, cand
        self.engine_ipa = prev.get("engine_ipa") or self.candidate_ipa(cand)
        self.current = prev.get("ipa") or self.engine_ipa
        self.mine = self.current != self.engine_ipa
        return True

    def farewell(self):
        if self.done:
            return f"all {len(self.todo) - self.start} reviewed."
        return f"stopped; {self.covered} covered this run. `make review` resumes here."


def review(candidates, progress, lexicon_path, speaker, read=input, out=print, progress_path=None, ipa_for=None,
           key=None):
    """The terminal loop over a Session. `key` reads a command (one keypress
    at a terminal), `read` a line of IPA; both default to line input and,
    with `speaker`, are injectable for tests. Returns the number of words
    covered this run."""
    key = key or read
    ses = Session(candidates, progress, lexicon_path, speaker, progress_path, ipa_for, out)
    out(ses.intro())
    ses.advance()
    while True:
        if ses.done:
            try:
                cmd = key(f"\n{ses.farewell()}   [b]ack to the last word, anything else quits > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                cmd = "q"
            if cmd == "b" and ses.back():
                out(f"  back to {ses.word}")
                continue
            break
        out(f"\n[{ses.pos + 1}/{len(ses.todo)}] {ses.word}  ({ses.detail()})")
        if ses.example:
            out(f'  "{ses.example}"')
        out(f"  engine: {ses.engine_ipa}  ({respell.respell(ses.engine_ipa)})")
        ses.play()
        while True:
            label = "yours" if ses.mine else "engine"
            try:
                cmd = key(f"  {label}: {ses.current}   [a]ccept [r]epeat [j]reject+IPA [s]kip [e]xample [b]ack [q]uit > ")
                cmd = cmd.strip().lower()
            except (EOFError, KeyboardInterrupt):
                cmd = "q"
            if not cmd:
                continue
            if cmd == "r":
                ses.play()
            elif cmd == "e":
                if not ses.play_example():
                    out("  no example line for this word")
            elif cmd == "j":
                try:
                    typed = read("  ipa> ").strip()
                except EOFError:
                    typed = ""
                if not typed:
                    continue
                try:
                    ses.try_ipa(typed)
                except ValueError as e:
                    out(f"  {e}")
                    continue
                out(f"  now: {ses.current}  ({respell.respell(ses.current)})")
            elif cmd == "a":
                out(f"  added {ses.word}: {ses.current}")
                ses.accept()
                break
            elif cmd == "s":
                out("  skipped; the engine's reading stands")
                ses.skip()
                break
            elif cmd == "b":
                if ses.back():
                    out("  back; its earlier choice is undone")
                    break
                out("  nothing before this word")
            elif cmd == "q":
                out("\n" + ses.farewell())
                return ses.covered
            else:
                out("  a, r, j, s, e, b or q")
    out("\n" + ses.farewell())
    return ses.covered


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", default=str(DATA / "proper_nouns.tsv"))
    ap.add_argument("--progress", default=str(DATA / "review_progress.json"))
    ap.add_argument("--lexicon", default=str(HERE / "lexicon.yaml"))
    ap.add_argument("--voices", default=str(HERE / "voices"))
    ap.add_argument("--voice-map", default=str(HERE / "voices.json"))
    ap.add_argument("--backend", default="auto", choices=["auto", *sh.BACKENDS])
    ap.add_argument("--top", type=int, default=0, help="review only the first N candidates (0 = all)")
    ap.add_argument("--cli", action="store_true", help="the terminal loop even when a window is possible")
    args = ap.parse_args(argv)
    import review_gui
    gui = not args.cli and review_gui.available()

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
    speaker = Speaker(backend, sh.pick_player())
    try:
        if gui:
            review_gui.run(Session(candidates, progress, args.lexicon, speaker, args.progress, ipa_for))
        else:
            review(candidates, progress, args.lexicon, speaker, progress_path=args.progress, ipa_for=ipa_for,
                   key=read_key)
    finally:
        save_progress(args.progress, progress)
    return 0


if __name__ == "__main__":
    sys.exit(main())
