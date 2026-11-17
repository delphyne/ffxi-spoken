#!/usr/bin/env python3
"""Write helper/voice_stories.json: which storylines each speaker talks in,
and how many lines, for the voice review's storyline filter.

    make voice-stories
    python3 -I tools/voice_stories.py [--client path] [--lsb path]

Each storyline is a folder under LandSandBoat's scripts/missions; its
cutscenes go through the event decoder (tools/xievent.py) the way the pack
build does, and every attributed line counts for its speaker. Generated, so
never edit the JSON; re-run after a decoder or LandSandBoat update.
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", default=str(xidat.CLIENT_DEFAULT))
    ap.add_argument("--lsb", default=str(xievent.LSB_DEFAULT))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    client = xidat.Client(args.client)
    data = build(lambda code: xievent.rov_lines(client, args.lsb, code))
    Path(args.out).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {len(data['speakers'])} speakers in {len(data['stories'])} storylines")
    for code in data["stories"]:
        n = sum(1 for v in data["speakers"].values() if code in v)
        print(f"  {code:9} {n:4} speakers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
