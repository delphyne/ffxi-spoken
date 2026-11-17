#!/usr/bin/env python3
"""Write which storylines each speaker talks in, and which storylines and
speakers each word is spoken in, for the review tools' filters.

    make voice-stories
    python3 -I tools/voice_stories.py [--client path] [--lsb path]

Each storyline is a folder under LandSandBoat's scripts/missions; its
cutscenes go through the event decoder (tools/xievent.py) the way the pack
build does. Two files come out:

    helper/voice_stories.json  speaker -> {storyline: lines}, for the voice
                               review (committed; small)
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
import xidat  # noqa: E402
import xievent  # noqa: E402

OUT = HERE.parent / "helper" / "voice_stories.json"
WORDS_OUT = HERE.parent / "data" / "word_stories.json"

# LandSandBoat's folder -> the expansion or storyline's name
STORIES = {
    "sandoria": "San d'Oria", "bastok": "Bastok", "windurst": "Windurst",
    "rotz": "Rise of the Zilart", "cop": "Chains of Promathia", "toau": "Treasures of Aht Urhgan",
    "wotg": "Wings of the Goddess", "acp": "A Crystalline Prophecy", "amk": "A Moogle Kupo d'Etat",
    "asa": "A Shantotto Ascension", "soa": "Seekers of Adoulin", "rov": "Rhapsodies of Vana'diel",
    "tvr": "The Voracious Resurgence",
}


def build(lines_for, stories=STORIES):
    """{"stories": {code: name}, "speakers": {speaker: {code: lines}}}.
    `lines_for(code)` yields the decoder's line dicts for one storyline;
    storylines with no lines are left out."""
    speakers = collections.defaultdict(dict)
    found = {}
    for code, name in stories.items():
        counts = collections.Counter(line["speaker"] for line in lines_for(code))
        if not counts:
            continue
        found[code] = name
        for speaker, n in counts.items():
            speakers[speaker][code] = n
    return {"stories": found,
            "speakers": {s: dict(sorted(v.items())) for s, v in sorted(speakers.items())}}


def build_words(lines_for, stories=STORIES):
    """{word: {"stories": {code: lines}, "speakers": {speaker: lines}}} for
    every capitalised word the storylines' lines say, words split the way
    tools/proper_nouns.py splits them."""
    import proper_nouns as pn
    out = {}
    for code in stories:
        for line in lines_for(code):
            if line.get("kind", "say") != "say":
                continue
            texts = line.get("variants") or ([pn.display_text(line["text"])] if line.get("text") else [])
            seen = set()
            for text in texts:
                for w in pn.words_in(text):
                    if w[0].isupper() and w not in seen:
                        seen.add(w)
                        e = out.setdefault(w, {"stories": {}, "speakers": {}})
                        e["stories"][code] = e["stories"].get(code, 0) + 1
                        speaker = line.get("speaker") or ""
                        e["speakers"][speaker] = e["speakers"].get(speaker, 0) + 1
    return {w: {"stories": dict(sorted(e["stories"].items())),
                "speakers": dict(sorted(e["speakers"].items(), key=lambda kv: -kv[1]))}
            for w, e in sorted(out.items())}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", default=str(xidat.CLIENT_DEFAULT))
    ap.add_argument("--lsb", default=str(xievent.LSB_DEFAULT))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--words-out", default=str(WORDS_OUT))
    args = ap.parse_args(argv)
    client = xidat.Client(args.client)
    cache = {}

    def lines_for(code):   # each storyline decoded once for both files
        if code not in cache:
            cache[code] = list(xievent.rov_lines(client, args.lsb, code))
        return cache[code]

    data = build(lines_for)
    Path(args.out).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {len(data['speakers'])} speakers in {len(data['stories'])} storylines")
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
