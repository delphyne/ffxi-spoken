#!/usr/bin/env python3
"""Write which storylines each speaker talks in, and which storylines and
speakers each word is spoken in, for the review tools' filters.

    make voice-stories
    python3 -I tools/voice_stories.py [--client path] [--lsb path]

Each storyline is one of tools/corpus.py's sources: a mission folder, a
quest region, or a companion, read the way the pack build reads it, so a
line counts only if a pack would voice it (dialogue under whoever
cast_as.yaml says speaks it, narration under the blank name) and what
helper/exclude.yaml drops is not counted. Three files come out:

    helper/voice_stories.json  speaker -> {storyline: lines} (committed)
    data/voice_lines.json      a few of each speaker's lines for the voice
                               review to hear a voice say (data/: it is the
                               game's text, so never committed)
    data/word_stories.json     capitalised word -> {"stories": {storyline:
                               lines}, "speakers": {speaker: lines}}, for
                               the lexicon review (data/, like the noun list)

A line counts once per word however many of its variants say it, the way
tools/proper_nouns.py counts. Generated, so never edit either; re-run after
a decoder or LandSandBoat update.
"""

import argparse
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import corpus  # noqa: E402
import xidat  # noqa: E402
import xievent  # noqa: E402

sys.path.insert(0, str(HERE.parent / "helper"))
import spoken_helper as sh  # noqa: E402

OUT = HERE.parent / "helper" / "voice_stories.json"
LINES_OUT = HERE.parent / "data" / "voice_lines.json"
WORDS_OUT = HERE.parent / "data" / "word_stories.json"

# LandSandBoat's folder -> the expansion or storyline's name
STORIES = {
    "sandoria": "San d'Oria", "bastok": "Bastok", "windurst": "Windurst",
    "rotz": "Rise of the Zilart", "cop": "Chains of Promathia", "toau": "Treasures of Aht Urhgan",
    "wotg": "Wings of the Goddess", "acp": "A Crystalline Prophecy", "amk": "A Moogle Kupo d'Etat",
    "asa": "A Shantotto Ascension", "soa": "Seekers of Adoulin", "rov": "Rhapsodies of Vana'diel",
    "tvr": "The Voracious Resurgence",
    "q-sandoria": "Quests: San d'Oria", "q-bastok": "Quests: Bastok", "q-windurst": "Quests: Windurst",
    "q-jeuno": "Quests: Jeuno", "q-outlands": "Quests: Outlands", "q-other": "Quests: other areas",
    "q-toau": "Quests: Aht Urhgan", "q-wotg": "Quests: Crystal War", "q-abyssea": "Quests: Abyssea",
    "q-soa": "Quests: Adoulin", "q-hidden": "Quests: trusts and other hidden quests", "q-misc": "Quests: unfiled",
    "mog-house": "Mog House", "mog-garden": "Mog Garden",
}
# storyline code -> tools/corpus.py source; a code not here is a mission folder
SOURCES = {
    "q-sandoria": "quests/sandoria", "q-bastok": "quests/bastok", "q-windurst": "quests/windurst",
    "q-jeuno": "quests/jeuno", "q-outlands": "quests/outlands", "q-other": "quests/otherAreas",
    "q-toau": "quests/ahtUrhgan", "q-wotg": "quests/crystalWar", "q-abyssea": "quests/abyssea",
    "q-soa": "quests/adoulin", "q-hidden": "quests/hiddenQuests", "q-misc": "quests/misc",
    "mog-house": "companions/mog-house", "mog-garden": "companions/mog-garden",
}
COUNTED = corpus.VOICED   # the classes of line a speaker is counted for


def source_of(code):
    return SOURCES.get(code, f"missions/{code}")


def speaker_of(line):
    """Who a row is counted under: whoever cast_as.yaml says speaks it,
    else the name shown. Rows with no class (not from corpus.classed) count."""
    return line.get("as") or line["speaker"]


def counted(line):
    return line.get("class", "dialogue") in COUNTED


SAMPLE_LINES = 4          # lines kept per speaker, for the voice review to hear a voice say
SAMPLE_LENGTH = (60, 200)  # characters: long enough to hear a voice, short enough to render quickly


def sample_lines(texts, count=SAMPLE_LINES, length=SAMPLE_LENGTH):
    """Up to `count` of a speaker's lines worth hearing a voice say: whole
    sentences of a middling length with no stage direction left in them,
    the longest first, each once; the first lines seen when none is."""
    lo, hi = length
    seen, good, fallback = set(), [], []
    for text in texts:
        if text in seen:
            continue
        seen.add(text)
        plain = " ".join(text.split())
        if plain and plain == " ".join(sh.strip_directions(plain).split()) and lo <= len(plain) <= hi \
                and plain[-1] in ".!?":
            good.append(plain)
        elif plain:
            fallback.append(plain)
    good.sort(key=len, reverse=True)
    return (good or fallback)[:count]


def build(lines_for, stories=STORIES):
    """{"stories": {code: name}, "speakers": {speaker: {code: lines}},
    "lines": {speaker: [sample lines]}}. `lines_for(code)` yields the
    decoder's rows for one storyline, classed (corpus.classed); only rows
    a pack would voice count, and storylines with none are left out."""
    speakers = collections.defaultdict(dict)
    texts = collections.defaultdict(list)
    found = {}
    for code, name in stories.items():
        counts, seen = collections.Counter(), set()
        for n, line in enumerate(lines_for(code)):
            if not counted(line):
                continue
            # a line said in several scenes or zones is one line, as it is one file in a pack
            key = (speaker_of(line), tuple(line.get("variants") or ()) or n)
            if key not in seen:
                seen.add(key)
                counts[speaker_of(line)] += 1
        if not counts:
            continue
        found[code] = name
        for speaker, n in counts.items():
            speakers[speaker][code] = n
        for line in lines_for(code):
            if line.get("kind", "say") == "say" and line.get("variants") and line.get("class", "dialogue") == "dialogue":
                texts[speaker_of(line)].append(line["variants"][0])
    return {"stories": found,
            "speakers": {s: dict(sorted(v.items())) for s, v in sorted(speakers.items())},
            "lines": {s: sample_lines(t) for s, t in sorted(texts.items()) if sample_lines(t)}}


def build_words(lines_for, stories=STORIES):
    """{word: {"stories": {code: lines}, "speakers": {speaker: lines}}} for
    every capitalised word the storylines' lines say, words split the way
    tools/proper_nouns.py splits them."""
    import proper_nouns as pn
    out = {}
    for code in stories:
        for line in lines_for(code):
            if line.get("kind", "say") != "say" or not counted(line):
                continue
            texts = line.get("variants") or ([pn.display_text(line["text"])] if line.get("text") else [])
            seen = set()
            for text in texts:
                for w in pn.words_in(text):
                    if w[0].isupper() and w not in seen:
                        seen.add(w)
                        e = out.setdefault(w, {"stories": {}, "speakers": {}})
                        e["stories"][code] = e["stories"].get(code, 0) + 1
                        speaker = speaker_of(line) or ""
                        e["speakers"][speaker] = e["speakers"].get(speaker, 0) + 1
    return {w: {"stories": dict(sorted(e["stories"].items())),
                "speakers": dict(sorted(e["speakers"].items(), key=lambda kv: -kv[1]))}
            for w, e in sorted(out.items())}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", default=str(xidat.CLIENT_DEFAULT))
    ap.add_argument("--lsb", default=str(xievent.LSB_DEFAULT))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--lines-out", default=str(LINES_OUT))
    ap.add_argument("--exclude", default=str(corpus.EXCLUDE))
    ap.add_argument("--companions", default=str(corpus.COMPANIONS))
    ap.add_argument("--cast-as", default=str(corpus.CAST_AS))
    ap.add_argument("--words-out", default=str(WORDS_OUT))
    args = ap.parse_args(argv)
    client = xidat.Client(args.client)
    cache = {}
    exclude, cast = corpus.Exclude.load(args.exclude), corpus.cast_table(args.cast_as)
    known = set(corpus.sources(args.lsb, args.companions))
    for missing in sorted(known - {source_of(code) for code in STORIES}):
        print(f"warning: {missing} is a source with no storyline code here (STORIES, SOURCES)", file=sys.stderr)

    def lines_for(code):   # each storyline decoded once for all three files
        if code not in cache:
            source = source_of(code)
            rows = corpus.rows_of(client, args.lsb, source, args.companions)[0] if source in known else []
            cache[code] = list(corpus.classed(rows, exclude, cast))
        return cache[code]

    data = build(lines_for)
    lines = data.pop("lines")
    Path(args.out).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {len(data['speakers'])} speakers in {len(data['stories'])} storylines")
    Path(args.lines_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.lines_out).write_text(json.dumps(lines, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.lines_out}: lines to hear for {len(lines)} speakers")
    words = build_words(lines_for)
    Path(args.words_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.words_out).write_text(json.dumps(words, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.words_out}: {len(words)} capitalised words")
    for code in data["stories"]:
        n = sum(1 for v in data["speakers"].values() if code in v)
        print(f"  {code:9} {n:4} speakers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
