#!/usr/bin/env python3
"""Find the proper nouns in FFXI's dialogue, ranked for the lexicon.

Reads every zone's NA dialogue table from the client, counts capitalised
words that are not ordinary English, and ranks them by how often they are
spoken, with words from the Rhapsodies of Vana'diel cutscenes first. The
Rhapsodies counts come from the event decoder (tools/xievent.py): the
cleaned pack texts of every line its scenes can show, so with --rov the
list is exactly the vocabulary the Rhapsodies pack will render. Each word
comes with Kokoro's unaided IPA so an entry for helper/lexicon.yaml starts
from what the engine would say anyway.

    .venv/bin/python -I tools/proper_nouns.py [--top 300] [--no-ipa] [--rov]

Writes data/proper_nouns.tsv (everything, or the Rhapsodies words with
--rov) and data/lexicon-candidates.yaml (the top entries as commented-out
lexicon stanzas to uncomment and edit). Needs the client install, a
LandSandBoat checkout for the Rhapsodies list, and /usr/share/dict/words.
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import xidat  # noqa: E402
import xievent  # noqa: E402

WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*[A-Za-z]|[A-Za-z]")
JUNK = re.compile(r"[^\x20-\x7e]+")


PROMPT = re.compile(r"\x7f[0-9]")  # prompt / auto-prompt markers


def display_text(s):
    """A dialogue string stripped of control and parameter bytes, for humans."""
    return re.sub(r"\s+", " ", JUNK.sub(" ", PROMPT.sub("", s))).strip()
DICT = Path("/usr/share/dict/words")


def english_words():
    if not DICT.exists():
        print(f"warning: {DICT} missing; nothing will be filtered as English", file=sys.stderr)
        return set()
    return {w.strip().lower() for w in DICT.read_text(encoding="utf-8", errors="replace").splitlines()}


STUTTER = re.compile(r"^[A-Za-z]{1,2}-[A-Za-z]")  # "Wh-what", "S-sorry": the word is the rest


def words_in(text):
    for m in WORD.finditer(text.replace("--", " ")):
        yield m.group(0)


def rov_words(rows):
    """Capitalised words in the Rhapsodies corpus: a Counter over the
    cleaned pack texts, each line's variants counted once, plus the set of
    speaker names (a name is a proper noun whatever the dictionary says).
    Live-only lines (an item or number slot) count their fixed text."""
    words, speakers = Counter(), set()
    for row in rows:
        if row["kind"] != "say":
            continue
        if row["speaker"] and not row["speaker"].startswith("<"):
            speakers.add(row["speaker"])
        texts = row.get("variants") or ([display_text(row["text"])] if row.get("text") else [])
        seen = set()
        for text in texts:
            for w in words_in(text):
                if w[0].isupper() and w not in seen:
                    seen.add(w)
                    words[w] += 1
    return words, speakers


def is_proper(word, cap_count, low_count, english, npc_names, speaker_names=frozenset()):
    """Capitalised, not an English word (an NPC called Lion needs no
    lexicon entry), and not a sentence-initial common word, which shows up
    in lower case elsewhere. Being part of an NPC name rescues a word the
    lower-case test would otherwise reject. A word in a decoded speaker's
    name skips the dictionary test too: the system word list knows Iroha,
    Gilgamesh, and Aldo, and the engine still has to say them right."""
    base = word.strip("'-")
    if len(base) < 3 or not base[0].isupper() or base.isupper() or STUTTER.match(base):
        return False
    if base in speaker_names:
        return True
    stem = base[:-2] if base.endswith("'s") else base
    if stem.lower() in english or stem.lower().rstrip("s") in english:
        return False
    if base in npc_names:
        return True
    return low_count <= cap_count * 0.2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", default=str(xidat.CLIENT_DEFAULT))
    ap.add_argument("--lsb", default=str(xievent.LSB_DEFAULT))
    ap.add_argument("--top", type=int, default=300, help="entries in the YAML candidates file")
    ap.add_argument("--no-ipa", action="store_true", help="skip Kokoro IPA (no venv needed)")
    ap.add_argument("--rov", action="store_true", help="only words the Rhapsodies of Vana'diel scenes speak")
    ap.add_argument("--out", default=str(HERE.parent / "data"))
    args = ap.parse_args(argv)

    client = xidat.Client(args.client)
    english = english_words()
    census_path = HERE.parent / "helper/census.json"
    npc_names = set()
    if census_path.exists():
        for name in json.loads(census_path.read_text(encoding="utf-8")):
            npc_names.update(w for w in name.split() if w[:1].isupper())

    cap = Counter()
    low = Counter()
    zones_of = defaultdict(set)
    example = {}
    for zone, info in client.zones.items():
        if not info.get("strings_na") or not (client.root / info["strings_na"]).exists():
            continue
        if (client.root / info["strings_na"]).stat().st_size < 16:
            continue  # a stub table (a few zones have one)
        try:
            strings = client.dialog(int(zone))
        except Exception as e:  # noqa: BLE001
            print(f"zone {zone}: {e}", file=sys.stderr)
            continue
        for s in strings:
            for w in words_in(s):
                if w[0].isupper():
                    cap[w] += 1
                    zones_of[w].add(info["name"])
                    if w not in example and 20 <= len(s) <= 140 and "\x0c" not in s:
                        example[w] = display_text(s)
                else:
                    low[w.lower()] += 1

    try:
        rov, speakers = rov_words(xievent.rov_lines(client, args.lsb))
    except FileNotFoundError as e:
        if args.rov:
            sys.exit(f"--rov needs the Rhapsodies corpus: {e}")
        print(f"no Rhapsodies weighting: {e}", file=sys.stderr)
        rov, speakers = Counter(), set()
    speaker_names = {w for name in speakers for w in name.split() if w[:1].isupper()}
    npc_names |= speaker_names
    for w in rov:
        cap[w] += 0  # a word only the decoder saw (a cleaned variant) still ranks

    proper = [w for w in cap if is_proper(w, cap[w], low[w.lower()], english, npc_names, speaker_names)]
    if args.rov:
        proper = [w for w in proper if rov[w]]
    proper.sort(key=lambda w: (-rov[w], -cap[w], w))

    ipa = {}
    if not args.no_ipa:
        try:
            from kokoro_onnx.tokenizer import Tokenizer
            tok = Tokenizer()
            for w in proper[:args.top]:
                ipa[w] = tok.phonemize(w)
        except ImportError:
            print("kokoro-onnx not importable; run with .venv/bin/python for IPA", file=sys.stderr)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "proper_nouns.tsv").open("w", encoding="utf-8") as f:
        f.write("word\tuses\trov_uses\tzones\tnpc\tkokoro_ipa\texample\n")
        for w in proper:
            f.write(f"{w}\t{cap[w]}\t{rov[w]}\t{len(zones_of[w])}\t{'y' if w in npc_names else ''}\t{ipa.get(w, '')}"
                    f"\t{example.get(w, '')}\n")
    with (out / "lexicon-candidates.yaml").open("w", encoding="utf-8") as f:
        f.write("# Lexicon candidates from tools/proper_nouns.py: the most-spoken proper nouns,\n"
                "# Rhapsodies of Vana'diel first, each with Kokoro's unaided IPA. Uncomment a\n"
                "# stanza, fix the IPA, and move it to helper/lexicon.yaml.\n\n")
        for w in proper[:args.top]:
            f.write(f"# {w}: {cap[w]} uses, {rov[w]} in Rhapsodies, {len(zones_of[w])} zones"
                    f"{', NPC name' if w in npc_names else ''}\n")
            f.write(f"# {w}:\n#   ipa: \"{ipa.get(w, '')}\"\n\n")
    scope = f"{len(proper)} Rhapsodies proper nouns" if args.rov else f"{len(proper)} proper nouns"
    print(f"{sum(cap.values())} capitalised words, {sum(rov.values())} in Rhapsodies; {scope} -> {out / 'proper_nouns.tsv'}")
    print(f"top {args.top} with IPA -> {out / 'lexicon-candidates.yaml'}")
    print(f"{'word':18} {'uses':>6} {'rov':>4}  kokoro says")
    for w in proper[:30]:
        print(f"{w:18} {cap[w]:6} {rov[w]:4}  {ipa.get(w, '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
