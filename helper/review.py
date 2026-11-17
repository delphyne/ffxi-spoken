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
skipped. Runs with the venv: `make review-lexicon`.

Existing entries can be reviewed too, with the same keys: accept writes the
IPA into the entry where it is (its comment kept) and moves on, skip leaves
it, back goes to the previous entry, and d deletes it (the engine already
says it right). The window shows every word, incoming and in the lexicon,
in a table with a search and filters for status, storyline, and speaker
(review_gui.py); in the terminal, `--entries` (make review-lexicon
ENTRIES=1) steps through the entries instead of the incoming words, and
`--entry WORD` starts at one (in the window, opens it).
"""

import argparse
import csv
import json
import re
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
    """Remove `word`'s stanza: its key line and the indented lines under it.
    True when one was there."""
    p = Path(lexicon_path)
    if not p.exists():
        return False
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    span = find_stanza(lines, word)
    if span is None:
        return False
    del lines[span[0]:span[1]]
    p.write_text("".join(lines), encoding="utf-8")
    return True


def find_stanza(lines, word):
    """(start, end) of `word`'s stanza: its key line and the indented or
    blank lines under it. Keys are matched by their YAML value, so a hand
    -written key quoted differently from lexicon_key still matches."""
    import yaml
    for i, line in enumerate(lines):
        if not line.strip() or line[0] in " \t#":
            continue
        try:
            parsed = yaml.safe_load(line)
        except yaml.YAMLError:
            continue
        if isinstance(parsed, dict) and word in parsed:
            j = i + 1
            while j < len(lines) and (lines[j].startswith((" ", "\t")) or not lines[j].strip()):
                j += 1
            while j > i + 1 and not lines[j - 1].strip():   # trailing blank lines belong to the file
                j -= 1
            return i, j
    return None


IPA_LINE = re.compile(r"^(?P<lead>\s+ipa:\s*)(?P<value>.*?)(?P<comment>\s+#.*)?$")
INLINE = re.compile(r"^(?P<key>[^#]*?:)\s*(?P<value>.*?)(?P<comment>\s+#.*)?$")


def replace_entry(lexicon_path, word, ipa):
    """Give an existing entry new IPA in place, keeping its position, its
    comment, and any `spell:` line. A respelling-only entry ("Word: Spell")
    becomes ipa plus that spell. False when the word has no entry."""
    p = Path(lexicon_path)
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    span = find_stanza(lines, word)
    if span is None:
        return False
    i, j = span
    value = json.dumps(ipa, ensure_ascii=False)
    for k in range(i + 1, j):
        m = IPA_LINE.match(lines[k].rstrip("\n"))
        if m:
            lines[k] = f"{m['lead']}{value}{m['comment'] or ''}\n"
            break
    else:
        m = INLINE.match(lines[i].rstrip("\n"))
        spell = m["value"].strip() if m and m["value"].strip() else None
        stanza = [f"{lexicon_key(word)}:\n", f"    ipa: {value}{(m and m['comment']) or ''}\n"]
        if spell:
            stanza.append(f"    spell: {spell}\n")
        lines[i:i + 1] = stanza
    p.write_text("".join(lines), encoding="utf-8")
    return True


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


def validate_ipa(word, typed, keep_dots=False):
    """Normalise hand-typed IPA the way the lexicon does; raises ValueError.
    New entries drop syllable dots; an existing entry being edited keeps
    them (the respeller uses them; playback and the lexicon strip them
    before the engine hears the word)."""
    e = sh.Lexicon({word: {"ipa": typed}}).entries[word]
    return e["ipa_marked"] if keep_dots else e["ipa"]


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
        self.say(f"[[{ipa.replace('.', '')}]]")   # Kokoro pauses at syllable dots

    def in_context(self, word, ipa, sentence):
        self.say(sh.Lexicon({word: {"ipa": ipa}}).apply(sentence, phonemes=True))


class Session:
    """The review's state, shared by the terminal loop and the window: the
    words still to do, the current one with the engine's IPA and the IPA
    being tried, and the bookkeeping on accept, skip, and back. Playback
    goes through `speaker` (a Speaker, or a fake in tests).

    `todo` keeps the words covered in earlier runs (those in `progress`)
    so `back` can reach them; `start` is where this run begins.

    `open_entries` switches to the lexicon's existing entries (mode
    "entries"): accept rewrites the entry in place, skip leaves it, back
    goes to the previous one, delete removes it, and `close_entries` (or
    running off the end) returns to the incoming word left off at."""

    def __init__(self, candidates, progress, lexicon_path, speaker, progress_path=None, ipa_for=None, out=print):
        self.progress, self.lexicon_path, self.speaker = progress, lexicon_path, speaker
        self.progress_path, self.ipa_for, self.out = progress_path, ipa_for, out
        self.by_word = {c["word"]: c for c in candidates}
        self.mode, self.queue_state, self.entry_words, self.entry_pos, self.saved_ipa = "queue", None, [], -1, None
        self.in_entry = False
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
        if self.mode == "entries" and "uses" not in c:
            return "lexicon entry (not among the incoming words, so no counts)"
        return (f"{c.get('uses', '?')} uses, {c.get('pack_uses', c.get('rov_uses', '0'))} in the storyline, {c.get('zones', '?')} zones"
                + (", NPC name" if c.get("npc") else ""))

    def position(self):
        """(n, of) for the counter: the incoming word's, or the entry's."""
        if self.mode == "entries":
            return self.entry_pos + 1, len(self.entry_words)
        return self.pos + 1, len(self.todo)

    # -- existing entries ------------------------------------------------

    def entries(self):
        """Every word in the lexicon, in file order."""
        return list(sh.Lexicon.load(self.lexicon_path).entries)

    def open_entries(self, word=None):
        """Review existing entries, from `word` (else the first). The
        incoming word in hand is kept for close_entries."""
        if self.mode == "queue":
            self.queue_state = (self.pos, self.cand, self.engine_ipa, self.current, self.mine)
        self.mode = "entries"
        self.entry_words = self.entries()
        if not self.entry_words:
            return self.close_entries()
        self.entry_pos = self.entry_words.index(word) if word in self.entry_words else 0
        return self.load_entry()

    def load_entry(self):
        word = self.entry_words[self.entry_pos]
        e = sh.Lexicon.load(self.lexicon_path).entries[word]
        self.cand = dict(self.by_word.get(word, {}), word=word)
        engine = self.candidate_ipa(self.cand) if self.cand.get("kokoro_ipa") or self.ipa_for else ""
        self.saved_ipa = e["ipa_marked"]
        self.engine_ipa = engine or self.saved_ipa or ""
        self.current = self.saved_ipa or self.engine_ipa
        self.mine = self.current != self.engine_ipa
        return True

    # -- any word, opened from the window's table ------------------------

    def open_word(self, word):
        """Put any word in hand, in the lexicon or not (mode "word"). Accept
        writes or updates its entry, skip records it as skipped, delete
        removes its entry; none of them moves on, the window does that."""
        if self.mode == "queue" and self.queue_state is None:
            self.queue_state = (self.pos, self.cand, self.engine_ipa, self.current, self.mine)
        self.mode = "word"
        e = sh.Lexicon.load(self.lexicon_path).entries.get(word)
        self.cand = dict(self.by_word.get(word, {}), word=word)
        engine = self.candidate_ipa(self.cand) if self.cand.get("kokoro_ipa") or self.ipa_for else ""
        self.saved_ipa = e["ipa_marked"] if e else None
        self.in_entry = e is not None
        self.engine_ipa = engine or self.saved_ipa or ""
        self.current = self.saved_ipa or self.engine_ipa
        self.mine = self.current != self.engine_ipa
        return True

    def status_of(self, word, entries=None, covered=None):
        """new, lexicon, skipped, deleted, or covered (part of a longer
        entry such as Cait in Cait Sith), for the table."""
        entries = entries if entries is not None else sh.Lexicon.load(self.lexicon_path).entries
        if word in entries:
            return "lexicon"
        rec = self.progress.get(word) or {}
        if rec.get("deleted"):
            return "deleted"
        if rec.get("status") == "skipped":
            return "skipped"
        if word in (covered if covered is not None else covered_words(entries)):
            return "covered"
        return "new"

    def saved_spelling(self):
        """The entry's respelling: its hand `spell`, else derived from its IPA."""
        return sh.Lexicon.load(self.lexicon_path).spelling(self.word)

    def close_entries(self):
        """Back to the incoming word left off at."""
        if self.mode != "entries":
            return False
        self.mode = "queue"
        if self.queue_state:
            self.pos, self.cand, self.engine_ipa, self.current, self.mine = self.queue_state
        self.queue_state, self.saved_ipa = None, None
        self.in_lexicon = len(self.entries())
        return True

    def next_entry(self, step=1):
        """Move within the entries; running off either end closes them.
        False when that happened."""
        self.entry_pos += step
        if 0 <= self.entry_pos < len(self.entry_words):
            self.load_entry()
            return True
        self.close_entries()
        return False

    def _save(self):
        if self.progress_path:
            save_progress(self.progress_path, self.progress)

    def delete(self):
        """Remove the entry in hand. Its word counts as skipped for the
        incoming queue, so it is not asked again."""
        if self.mode == "word":
            if not self.in_entry:
                return False
            remove_entry(self.lexicon_path, self.word)
            self.progress[self.word] = {"status": "skipped", "deleted": True, "engine_ipa": self.engine_ipa,
                                        "t": int(time.time())}
            self.in_lexicon -= 1
            self._save()
            return self.open_word(self.word)
        if self.mode != "entries":
            return False
        word = self.word
        remove_entry(self.lexicon_path, word)
        self.progress[word] = {"status": "skipped", "deleted": True, "engine_ipa": self.engine_ipa,
                               "t": int(time.time())}
        if self.progress_path:
            save_progress(self.progress_path, self.progress)
        del self.entry_words[self.entry_pos]
        self.entry_pos -= 1
        self.next_entry()
        return True

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
        keep = self.mode == "entries" or (self.mode == "word" and self.in_entry)
        self.current = validate_ipa(self.word, typed, keep_dots=keep)
        self.mine = True
        return self.current

    def try_ipa(self, typed):
        """set_ipa, then play it."""
        self.set_ipa(typed)
        self.play()
        return self.current

    def accept(self):
        if self.mode == "word":
            if self.in_entry:
                if self.current != self.saved_ipa:
                    replace_entry(self.lexicon_path, self.word, self.current)
            else:
                append_entry(self.lexicon_path, self.word, self.current, note=f"{self.cand.get('uses', '?')} uses")
                self.progress[self.word] = {"status": "accepted", "ipa": self.current,
                                            "engine_ipa": self.engine_ipa, "t": int(time.time())}
                self.in_lexicon += 1
            self.covered += 1
            self._save()
            return self.open_word(self.word)
        if self.mode == "entries":
            if self.current != self.saved_ipa:
                replace_entry(self.lexicon_path, self.word, self.current)
            self.covered += 1
            return self.next_entry()
        append_entry(self.lexicon_path, self.word, self.current, note=f"{self.cand.get('uses', '?')} uses")
        self.progress[self.word] = {"status": "accepted", "ipa": self.current, "engine_ipa": self.engine_ipa,
                                    "t": int(time.time())}
        self._covered()

    def skip(self):
        if self.mode == "word":
            if not self.in_entry:
                self.progress[self.word] = {"status": "skipped", "engine_ipa": self.engine_ipa,
                                            "t": int(time.time())}
                self.covered += 1
                self._save()
            return True
        if self.mode == "entries":
            return self.next_entry()
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
        tried. Works from the end of the list too. False at the first word.
        Among the entries, the previous entry, nothing undone."""
        if self.mode == "entries":
            if self.entry_pos == 0:
                return False
            return self.next_entry(-1)
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
        return f"stopped; {self.covered} covered this run. `make review-lexicon` resumes here."


def review(candidates, progress, lexicon_path, speaker, read=input, out=print, progress_path=None, ipa_for=None,
           key=None, entries=None):
    """The terminal loop over a Session. `key` reads a command (one keypress
    at a terminal), `read` a line of IPA; both default to line input and,
    with `speaker`, are injectable for tests. `entries` (True, or a word to
    start at) reviews the existing lexicon entries instead of the incoming
    words. Returns the number of words covered this run."""
    key = key or read
    ses = Session(candidates, progress, lexicon_path, speaker, progress_path, ipa_for, out)
    if entries:
        ses.open_entries(entries if isinstance(entries, str) else None)
        out(f"{len(ses.entry_words)} entries in the lexicon")
    else:
        out(ses.intro())
        ses.advance()
    while True:
        if entries and ses.mode != "entries":
            out("\nend of the lexicon's entries.")
            return ses.covered
        if ses.done:
            try:
                cmd = key(f"\n{ses.farewell()}   [b]ack to the last word, anything else quits > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                cmd = "q"
            if cmd == "b" and ses.back():
                out(f"  back to {ses.word}")
                continue
            break
        n, of = ses.position()
        out(f"\n[{n}/{of}] {ses.word}  ({ses.detail()})")
        if ses.example:
            out(f'  "{ses.example}"')
        out(f"  engine: {ses.engine_ipa}  ({respell.respell(ses.engine_ipa)})")
        if ses.mode == "entries":
            out(f"  in the lexicon: {ses.saved_ipa}  ({respell.respell(ses.saved_ipa)})" if ses.saved_ipa
                else f"  in the lexicon: a respelling, {ses.saved_spelling()!r}; accept turns it into IPA")
        ses.play()
        while True:
            label = "yours" if ses.mine else "engine"
            choices = ("[a]ccept [r]epeat [j]reject+IPA [s]kip [e]xample [b]ack [q]uit" if ses.mode == "queue"
                       else "[a]ccept [r]epeat [j]edit IPA [s]kip [d]elete [e]xample [b]ack [q]uit")
            try:
                cmd = key(f"  {label}: {ses.current}   {choices} > ")
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
                if ses.mode == "entries":
                    out(f"  kept {ses.word}: {ses.current}" if ses.current == ses.saved_ipa
                        else f"  updated {ses.word}: {ses.current}")
                else:
                    out(f"  added {ses.word}: {ses.current}")
                ses.accept()
                break
            elif cmd == "s":
                out("  left as it is" if ses.mode == "entries" else "  skipped; the engine's reading stands")
                ses.skip()
                break
            elif cmd == "d" and ses.mode == "entries":
                out(f"  deleted {ses.word}; the engine's reading stands")
                ses.delete()
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
                out("  a, r, j, s, e, b or q" + (", or d" if ses.mode == "entries" else ""))
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
    ap.add_argument("--word-stories", default=str(DATA / "word_stories.json"),
                    help="storylines and speakers per word, for the window's filters (make voice-stories)")
    ap.add_argument("--entries", action="store_true", help="review the lexicon's existing entries")
    ap.add_argument("--entry", metavar="WORD", help="review existing entries starting at WORD")
    args = ap.parse_args(argv)
    import review_gui
    gui = not args.cli and review_gui.available()

    entries = args.entry or args.entries
    if not Path(args.candidates).exists():
        if not entries:
            sys.exit(f"{args.candidates} is missing; run `make nouns` first")
        args.candidates = None   # existing entries need no candidates; they only lend counts and examples
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
    candidates = read_candidates(args.candidates) if args.candidates else []
    if args.top:
        candidates = candidates[:args.top]
    progress = load_progress(args.progress)
    speaker = Speaker(backend, sh.pick_player())
    try:
        if gui:
            review_gui.run(Session(candidates, progress, args.lexicon, speaker, args.progress, ipa_for),
                           entries=entries, word_stories=review_gui.load_word_stories(args.word_stories))
        else:
            review(candidates, progress, args.lexicon, speaker, progress_path=args.progress, ipa_for=ipa_for,
                   key=read_key, entries=entries)
    finally:
        save_progress(args.progress, progress)
    return 0


if __name__ == "__main__":
    sys.exit(main())
