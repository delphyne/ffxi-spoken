#!/usr/bin/env python3
"""Extract what a source says: every line the packs may voice, classed, and
what was left out and why (docs/ElevenLabs-Handoff.md, Scope).

    python3 tools/corpus.py list                     the sources there are
    python3 tools/corpus.py extract missions/rov     one source
    python3 tools/corpus.py extract --all [--stale]
    python3 tools/corpus.py audit quests/jeuno       what each exclude rule dropped, and samples of what stayed

A source is one of

    missions/<folder>    the scenes LandSandBoat's scripts/missions/<folder> runs
    quests/<region>      the same for scripts/quests/<region>; "misc" is the
                         scripts not filed under a region
    companions/<name>    helper/companions.yaml: zones whose events belong
                         to a character the player lives with, every
                         event or only what the named speakers say

and each is a pack's worth of lines. A line gets one class:

    dialogue      a named character says it                     voiced
    narration     story prose with no speaker                   voiced, by the narrator
    unattributed  said, but the decoder cannot name by whom     blocks its pack
    system        the game talking: items, key items, prompts   never voiced
    boilerplate   stock NPC speech: shops, guards, greetings    never voiced
    menu          a choice the player is offered                never voiced
    unvoiceable   no fixed text (filled in at run time), or     cannot be in a pack
                  nothing to say ("...", a stage direction)

system and boilerplate come from helper/exclude.yaml, a hand-kept list of
rules; everything else from the decoder. Two files come out per source:

    data/corpus/<kind>/<name>.jsonl    the lines themselves, one unique
                                       (speaker, text) each. data/ is not
                                       committed: this is the game's text
    helper/corpus/<kind>/<name>.json   counts only: per class, per speaker,
                                       per rule, and where the unattributed
                                       lines are. Committed
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "helper"))
import rov_events  # noqa: E402
import xidat  # noqa: E402
import xievent  # noqa: E402
import spoken_helper as sh  # noqa: E402

LSB_DEFAULT = xievent.LSB_DEFAULT
EXCLUDE = REPO / "helper" / "exclude.yaml"
COMPANIONS = REPO / "helper" / "companions.yaml"
CAST_AS = REPO / "helper" / "cast_as.yaml"
TEXT_OUT = REPO / "data" / "corpus"
COUNT_OUT = REPO / "helper" / "corpus"
MISC = "misc"                      # quest scripts filed under no region
VOICED = ("dialogue", "narration")
CLASSES = (*VOICED, "unattributed", "system", "boilerplate", "menu", "unvoiceable")
EXCLUDED = ("system", "boilerplate")   # the classes a rule may give
NAMELESS = ("", "???")             # shown names that say nothing about who speaks


def load_yaml(path):
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- sources

def companions(path=COMPANIONS):
    """name -> {"zones": [ids], "speakers": [names] or None, "note": ...}
    from helper/companions.yaml."""
    path = Path(path)
    if not path.exists():
        return {}
    out = {}
    for name, entry in (load_yaml(path) or {}).items():
        zones = entry.get("zones") if isinstance(entry, dict) else None
        if not zones or not all(isinstance(z, int) for z in zones):
            raise ValueError(f"{path.name}: {name!r} needs zones, a list of zone ids")
        speakers = entry.get("speakers")
        if speakers is not None and (not speakers or not all(isinstance(s, str) for s in speakers)):
            raise ValueError(f"{path.name}: {name!r} speakers must be a list of names")
        out[str(name)] = {"zones": list(zones), "speakers": speakers, "note": entry.get("note", "")}
    return out


def sources(lsb=LSB_DEFAULT, companions_path=COMPANIONS):
    """Every source id there is, missions first."""
    lsb = Path(lsb)
    out = [f"missions/{p.name}" for p in sorted((lsb / "scripts/missions").iterdir()) if p.is_dir()]
    quests = lsb / "scripts/quests"
    out += [f"quests/{p.name}" for p in sorted(quests.iterdir()) if p.is_dir()]
    if any(quests.glob("*.lua")):
        out.append(f"quests/{MISC}")
    out += [f"companions/{name}" for name in companions(companions_path)]
    return out


def scripts_of(lsb, source):
    """The LandSandBoat scripts a missions/ or quests/ source is made of."""
    kind, _, name = source.partition("/")
    root = Path(lsb) / "scripts" / kind
    if kind not in ("missions", "quests") or not name:
        raise ValueError(f"{source!r} is not missions/<folder> or quests/<region>")
    if kind == "quests" and name == MISC:
        return sorted(root.glob("*.lua"))
    if not (root / name).is_dir():
        raise FileNotFoundError(f"{root / name} is not a LandSandBoat folder")
    return sorted((root / name).rglob("*.lua"))


def targets(client, lsb, source, companions_path=COMPANIONS):
    """(zone, event) for every scene of a source, sorted, and how many
    scripts they came from (None for a companion)."""
    kind, _, name = source.partition("/")
    if kind == "companions":
        entry = companions(companions_path).get(name)
        if entry is None:
            raise ValueError(f"{name!r} is not in {Path(companions_path).name}")
        found = set()
        for zone in entry["zones"]:
            for block in client.event_blocks(zone):
                found.update((zone, cs) for cs in block["events"])
        return sorted(found), None
    ids = rov_events.zone_ids(Path(lsb))
    scripts = scripts_of(lsb, source)
    found = set()
    for lua in scripts:
        found.update((r["zone_id"], r["event"]) for r in rov_events.events_in(lua, ids) if r["zone_id"] is not None)
    return sorted(found), len(scripts)


# ---------------------------------------------------------------- the deny-list

class Rule:
    """One entry of helper/exclude.yaml: a class, a reason, and what it
    matches. `text` is a regular expression searched in the cleaned line;
    `speaker`, `zone`, and `event` must equal the line's; `kind` is the
    decoder's ("say" or "narration"). All that are given must hold, and
    at least one of text, speaker, zone must be given."""

    FIELDS = {"class", "reason", "text", "speaker", "zone", "event", "kind"}

    def __init__(self, data, where):
        self.where = where
        extra = set(data) - self.FIELDS
        if extra:
            raise ValueError(f"{where}: unknown field {sorted(extra)[0]!r}")
        self.cls, self.reason = data.get("class"), data.get("reason")
        if self.cls not in EXCLUDED:
            raise ValueError(f"{where}: class must be one of {', '.join(EXCLUDED)}")
        if not self.reason:
            raise ValueError(f"{where}: a rule needs a reason")
        if not any(k in data for k in ("text", "speaker", "zone")):
            raise ValueError(f"{where}: a rule needs text, speaker, or zone")
        self.text = re.compile(data["text"]) if "text" in data else None
        self.speaker, self.zone, self.event, self.kind = (data.get(k) for k in ("speaker", "zone", "event", "kind"))
        self.hits = 0

    def matches(self, row, text):
        if self.kind is not None and row["kind"] != self.kind:
            return False
        if self.speaker is not None and row["speaker"] != self.speaker:
            return False
        if self.zone is not None and row["zone"] != self.zone:
            return False
        if self.event is not None and row["event"] != self.event:
            return False
        return self.text is None or bool(self.text.search(text))


class Exclude:
    def __init__(self, rules=()):
        self.rules = list(rules)

    @classmethod
    def load(cls, path=EXCLUDE):
        path = Path(path)
        if not path.exists():
            return cls()
        data = load_yaml(path) or []
        return cls(Rule(entry, f"{path.name} rule {n}") for n, entry in enumerate(data, 1))

    def rule_for(self, row, text):
        """The first rule that drops this line, counted, or None."""
        for rule in self.rules:
            if rule.matches(row, text):
                rule.hits += 1
                return rule
        return None

    def stale(self):
        return [r for r in self.rules if not r.hits]


# ---------------------------------------------------------------- classing

def spoken_by(row, cast_as):
    """The character cast_as.yaml gives a line whose shown name says
    nothing, or None."""
    return cast_as.who(row) if cast_as else None


def unnamed(speaker):
    return speaker in NAMELESS or speaker.startswith("<0x")


def classify(row, text, exclude, who=None):
    """(class, reason) for one decoded row and one of its cleaned texts
    (None when it has none)."""
    if row["kind"] == "menu":
        return "menu", "menu"
    if text is None:
        if row.get("text_id") is None:
            return "unvoiceable", "variable text id"
        live = sorted(set(row.get("live_only") or []))
        return "unvoiceable", ("live-only: " + ", ".join(live)) if live else "empty text"
    said = sh.strip_directions(text)
    if not said:
        return "unvoiceable", "stage direction only"
    if not any(ch.isalnum() for ch in said):
        return "unvoiceable", "punctuation only"
    rule = exclude.rule_for(row, text) if exclude else None
    if rule:
        return rule.cls, rule.reason
    if row["kind"] == "narration":
        return "narration", "narration"
    if unnamed(row["speaker"]) and not who:
        return "unattributed", "no speaker" if row["speaker"] in NAMELESS else "unnamed entity"
    return "dialogue", "dialogue"


def classed(rows, exclude=None, cast_as=None):
    """The rows again, each with `class` (that of its first variant to be
    voiced, else of its first variant) and `as` (who cast_as.yaml says
    speaks it, or None): what the counters of whole rows go by."""
    for row in rows:
        who = spoken_by(row, cast_as)
        found = [classify(row, text, exclude, who)[0] for text in (row.get("variants") or [None])]
        yield {**row, "class": next((c for c in found if c in VOICED), found[0]), "as": who}


def extract(rows, exclude=None, cast_as=None):
    """Decoder rows to (lines, counts). `lines` holds one record per unique
    (class, speaker, text) in first-seen order; `counts` is what gets
    committed and holds no text. One (speaker, text) cast two ways is an
    error, because a pack holds one file for it."""
    lines, order = {}, []
    cast = {}        # (speaker, text) -> (who, its first source), for lines a pack could hold
    reasons = {c: {} for c in CLASSES}
    where = {}       # (zone, event) -> unattributed lines
    for row in rows:
        who = spoken_by(row, cast_as)
        for text in (row.get("variants") or [None]):
            cls, reason = classify(row, text, exclude, who)
            source = {"zone": row["zone"], "event": row["event"], "text_id": row.get("text_id")}
            key = (cls, row["speaker"], text if text is not None
                   else (row["zone"], row["event"], row.get("offset"), row.get("text_id")))
            if cls in VOICED or cls == "unattributed":
                was, first = cast.setdefault((row["speaker"], text), (who, source))
                if was != who:
                    raise ValueError(
                        f"{row['speaker'] or '(no name)'!r} {text!r} is cast as {was or 'nobody'} "
                        f"(zone {first['zone']} event {first['event']} text {first['text_id']}) and as {who or 'nobody'} "
                        f"(zone {row['zone']} event {row['event']} text {row['text_id']}); cast_as.yaml must cover both")
            if key not in lines:
                lines[key] = {"class": cls, "reason": reason, "speaker": row["speaker"], "text": text, "sources": []}
                if who:
                    lines[key]["as"] = who
                order.append(key)
                reasons[cls][reason] = reasons[cls].get(reason, 0) + 1
                if cls == "unattributed":
                    at = (row["zone"], row["event"])
                    where[at] = where.get(at, 0) + 1
            if source not in lines[key]["sources"]:
                lines[key]["sources"].append(source)
    out = [lines[k] for k in order]
    classes = {}
    for cls in CLASSES:
        mine = [r for r in out if r["class"] == cls]
        classes[cls] = {"lines": len(mine), "chars": sum(len(r["text"] or "") for r in mine)}
    speakers = {}
    for r in out:
        if r["class"] == "dialogue":
            name = r.get("as") or r["speaker"]
            s = speakers.setdefault(name, {"lines": 0, "chars": 0})
            s["lines"] += 1
            s["chars"] += len(r["text"])
    counts = {
        "classes": classes,
        "voiced": {"lines": sum(classes[c]["lines"] for c in VOICED), "chars": sum(classes[c]["chars"] for c in VOICED)},
        "reasons": {c: dict(sorted(v.items())) for c, v in reasons.items() if v and c not in VOICED},
        "speakers": dict(sorted(speakers.items(), key=lambda kv: (-kv[1]["lines"], kv[0]))),
        "unattributed": [{"zone": z, "event": e, "lines": n} for (z, e), n in sorted(where.items())],
    }
    return out, counts


def decoded(client, found, undecoded):
    """The decoder's rows for each (zone, event), every block included. An
    event the decoder cannot walk is skipped and appended to `undecoded`:
    a whole zone always holds a few, and one must not cost the rest."""
    cache = {}
    for zone, cs in found:
        if zone not in cache:
            cache[zone] = (client.npcs(zone), client.dialog_bytes(zone), list(client.event_blocks(zone)))
        names, strs, blocks = cache[zone]
        for block in blocks:
            if cs in block["events"]:
                try:
                    yield from xievent.lines(block, cs, names, strs, zone)
                except xievent.DecodeError:
                    undecoded.append((zone, cs))


def rows_of(client, lsb, source, companions_path=COMPANIONS):
    """The decoder's rows for one source, and its counts' head: what
    extract and classed are given, and what the pack builder and the story
    counter read."""
    found, scripts = targets(client, lsb, source, companions_path)
    kind, _, name = source.partition("/")
    only = companions(companions_path)[name]["speakers"] if kind == "companions" else None
    present = set()
    rows = []
    undecoded = []
    for row in decoded(client, found, undecoded):
        if only is not None and not (row["kind"] == "say" and row["speaker"] in only):
            continue   # a companion kept by name: the zone's other events are someone else's
        present.add((row["zone"], row["event"]))
        rows.append(row)
    head = {"source": source}
    if scripts is not None:
        head["scripts"] = scripts
    head["scenes"] = {"named": len(found), "with_lines": len(present), "undecoded": len(set(undecoded))}
    return rows, head


def run(client, lsb, source, exclude, cast_as, companions_path=COMPANIONS):
    """Extract one source from the client: (lines, counts)."""
    rows, head = rows_of(client, lsb, source, companions_path)
    lines, counts = extract(rows, exclude, cast_as)
    return lines, {**head, **counts}


def paths(source, text_out=TEXT_OUT, count_out=COUNT_OUT):
    return Path(text_out) / f"{source}.jsonl", Path(count_out) / f"{source}.json"


def write(source, lines, counts, text_out=TEXT_OUT, count_out=COUNT_OUT):
    text_path, count_path = paths(source, text_out, count_out)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines), encoding="utf-8")
    count_path.parent.mkdir(parents=True, exist_ok=True)
    count_path.write_text(json.dumps(counts, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return text_path, count_path


def summary(counts):
    c = counts["classes"]
    parts = [f"{c[k]['lines']} {k}" for k in CLASSES if c[k]["lines"]]
    return (f"{counts['source']}: {counts['voiced']['lines']} lines to voice, {counts['voiced']['chars']} characters, "
            f"{len(counts['speakers'])} speakers; " + ", ".join(parts))


# ---------------------------------------------------------------- commands

def cast_table(path):
    if not Path(path).exists():
        return None
    import cast_as
    return cast_as.CastAs.load(path)


def cmd_list(args, client):
    for s in sources(args.lsb, args.companions):
        print(s)
    return 0


def cmd_extract(args, client):
    names = sources(args.lsb, args.companions) if args.all else args.source
    if not names:
        raise SystemExit("extract needs a source, or --all")
    exclude = Exclude.load(args.exclude)
    cast = cast_table(args.cast_as)
    for source in names:
        lines, counts = run(client, args.lsb, source, exclude, cast, args.companions)
        write(source, lines, counts, args.text_out, args.count_out)
        print(summary(counts))
    if args.stale:
        for rule in exclude.stale():
            print(f"exclude rule matched no line: {rule.reason} ({rule.where})")
    return 0


def cmd_audit(args, client):
    import random
    exclude = Exclude.load(args.exclude)
    lines, counts = run(client, args.lsb, args.source[0], exclude, cast_table(args.cast_as), args.companions)
    print(summary(counts))
    for cls in EXCLUDED + ("unvoiceable",):
        for reason, n in (counts["reasons"].get(cls) or {}).items():
            print(f"  {n:5} {cls:12} {reason}")
    rng = random.Random(args.seed)
    for cls in args.show or VOICED:
        mine = [r for r in lines if r["class"] == cls and (not args.speaker or r["speaker"] == args.speaker)]
        print(f"-- {cls}: {len(mine)} lines, {min(args.samples, len(mine))} shown")
        for r in rng.sample(mine, min(args.samples, len(mine))):
            at = r["sources"][0]
            print(f"   {at['zone']:>3}/{at['event']:<5} [{r['speaker']}] {(r['text'] or r['reason'])[:args.width]!r}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", default=str(xidat.CLIENT_DEFAULT))
    ap.add_argument("--lsb", default=str(LSB_DEFAULT))
    ap.add_argument("--exclude", default=str(EXCLUDE))
    ap.add_argument("--companions", default=str(COMPANIONS))
    ap.add_argument("--cast-as", default=str(CAST_AS))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    e = sub.add_parser("extract")
    e.add_argument("source", nargs="*")
    e.add_argument("--all", action="store_true")
    e.add_argument("--stale", action="store_true", help="name the exclude rules that matched nothing (use with --all)")
    e.add_argument("--text-out", default=str(TEXT_OUT))
    e.add_argument("--count-out", default=str(COUNT_OUT))
    a = sub.add_parser("audit")
    a.add_argument("source", nargs=1)
    a.add_argument("--show", action="append", choices=CLASSES, help="classes to sample (default: the voiced ones)")
    a.add_argument("--speaker", help="sample only this shown name")
    a.add_argument("--samples", type=int, default=12)
    a.add_argument("--seed", type=int, default=1)
    a.add_argument("--width", type=int, default=100)
    args = ap.parse_args(argv)
    client = None if args.cmd == "list" else xidat.Client(args.client)
    return {"list": cmd_list, "extract": cmd_extract, "audit": cmd_audit}[args.cmd](args, client)


if __name__ == "__main__":
    sys.exit(main())
