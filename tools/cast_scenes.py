#!/usr/bin/env python3
"""Keep two speakers of one scene off the same pool voice.

    make cast-scenes            # report, write helper/cast_scenes.json, regenerate voices.json
    python3 -I tools/cast_scenes.py [--dry-run]

A pool voice is shared: cast.py spreads a casting group's speakers over
its pool by their place in a list, and the census's minor NPCs by a hash of
the name, neither of which knows who talks to whom. This reads the scenes
out of the extracted corpus (data/corpus/, make corpus; a scene is one
event of one zone), finds the scenes where two pooled speakers of one
group share a voice, and moves as few speakers as it can to another voice
of the same pool until no more scenes can be cleared. The speakers it
moved go to helper/cast_scenes.json (speaker -> pool voice), which cast.py
reads; the rest stay where they were.

What it cannot clear it reports: a pool of one voice has nowhere to move
anyone, and a scene with more speakers of a group than the pool has voices
must double up. The report's distribution is the case for a bigger pool.
"""

import argparse
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "helper"))
import cast  # noqa: E402
import casting  # noqa: E402
import designs as designs_module  # noqa: E402

CORPUS = REPO / "data" / "corpus"
CENSUS = REPO / "helper" / "census.json"
VOICED = ("dialogue",)   # narration has no speaker to collide
SKIP = {"narrator"}      # the ??? voices: one storyteller by design
CLEARABLE = 1000


def scenes_of(corpus_dir=CORPUS):
    """(zone, event) -> the speakers voiced in it, from every extracted
    source. A line cast_as.yaml gives to someone counts as theirs."""
    scenes = collections.defaultdict(set)
    for path in sorted(Path(corpus_dir).rglob("*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                who = row.get("as") or row.get("speaker")
                if row.get("class") not in VOICED or not who:
                    continue
                for src in row.get("sources", ()):
                    scenes[(src.get("zone"), src.get("event"))].add(who)
    return scenes


def pooled(names, designs, moves, census):
    """speaker -> (character, group) for the speakers on a pool voice. A
    character is cast.py's first name for someone listed under several,
    since those share a voice whatever happens."""
    listed, first = {}, {}
    for group, characters in cast.CAST.items():
        for entry in characters:
            entry = [entry] if isinstance(entry, str) else entry
            for name in entry:
                listed[name], first[name] = group, entry[0]
    out = {}
    for name in names:
        group = moves.get(name) or listed.get(name)
        if name in moves:
            who = name   # a moved name stands alone in its new group
        else:
            who = first.get(name, name)
        if group is None:
            group = casting.RACE_GROUPS.get((census.get(name) or {}).get("race"))
        if group is None or group == cast.SILENT or group in SKIP or group not in designs.pools:
            continue
        if designs.voice_for_speaker(name):
            continue   # a voice of their own
        out[name] = (who, group)
    return out


def colour(nodes, weight, start, k):
    """Move as few of `nodes` as possible off their `start` voice (0..k-1)
    so that the summed `weight` of pairs sharing a voice is least: the
    worst-placed first, each to its best voice, staying put on a tie."""
    now = dict(start)

    def cost(n, c):
        return sum(w for m, w in weight[n].items() if now[m] == c)

    for _ in range(50):
        changed = False
        for n in sorted(nodes, key=lambda n: (-cost(n, now[n]), n)):
            here = cost(n, now[n])
            if not here:
                continue
            best = min(range(k), key=lambda c: (cost(n, c), c != start[n], c))
            if cost(n, best) < here:
                now[n], changed = best, True
        if not changed:
            break
    return now


def solve(scenes, designs, moves, census, base):
    """The pins and what is left. `base` is voices.json's moss section built
    with no pins. Returns (pins, before, after): pins is speaker -> (group,
    index); before and after list (scene, group, {voice index: characters})
    for every scene where a voice is shared."""
    names = set().union(*scenes.values()) if scenes else set()
    info = pooled(names, designs, moves, census)
    start, members = {}, collections.defaultdict(set)
    for name, (who, group) in info.items():
        pool = designs.pools[group]
        voice = base.get(name) or casting.pick(pool, name)
        if voice not in pool:
            continue
        start.setdefault((group, who), pool.index(voice))
        members[(group, who)].add(name)
    weight = collections.defaultdict(lambda: collections.defaultdict(int))
    cast_of = {}   # scene -> group -> characters
    for scene, speakers in scenes.items():
        by_group = collections.defaultdict(set)
        for name in speakers:
            if name in info and (info[name][1], info[name][0]) in start:
                by_group[info[name][1]].add((info[name][1], info[name][0]))
        cast_of[scene] = by_group
        for group, chars in by_group.items():
            # a scene the pool can cover outweighs any number that must double up whatever is done
            w = CLEARABLE if len(chars) <= len(designs.pools[group]) else 1
            for a in chars:
                for b in chars:
                    if a != b:
                        weight[a][b] += w
    final = dict(start)
    for group in sorted({g for g, _ in start}):
        nodes = [n for n in start if n[0] == group]
        final.update(colour(nodes, weight, {n: start[n] for n in nodes}, len(designs.pools[group])))

    def shared(voices):
        out = []
        for scene, by_group in sorted(cast_of.items(), key=str):
            for group, chars in sorted(by_group.items()):
                on = collections.defaultdict(list)
                for c in sorted(chars):
                    on[voices[c]].append(c[1])
                if any(len(v) > 1 for v in on.values()):
                    out.append((scene, group, dict(on)))
        return out

    pins = {name: (node[0], final[node]) for node in start if final[node] != start[node] for name in members[node]}
    return pins, shared(start), shared(final)


def report(before, after, designs, out=print):
    out(f"scenes where two speakers share a pool voice: {len(before)} before, {len(after)} after")
    by_group = collections.Counter(group for _, group, _ in after)
    sizes = collections.defaultdict(collections.Counter)
    for _, group, on in after:
        sizes[group][sum(len(v) for v in on.values())] += 1
    if after:
        out("what is left, by group (pool size; scenes; speakers of the group in the scene: scenes):")
    for group, n in by_group.most_common():
        dist = ", ".join(f"{k}: {v}" for k, v in sorted(sizes[group].items()))
        out(f"  {group:<18} pool {len(designs.pools[group])}  {n:>4} scenes   {dist}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--census", default=str(CENSUS))
    ap.add_argument("--out", default=str(cast.PINS))
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = ap.parse_args(argv)
    designs = designs_module.load_default(groups=set(casting.POOLS))
    moves = cast.load_moves()
    census = json.loads(Path(args.census).read_text(encoding="utf-8")) if Path(args.census).exists() else {}
    scenes = scenes_of(args.corpus)
    if not scenes:
        raise SystemExit(f"no extracted corpus under {args.corpus}; run make corpus")
    base = cast.build(designs, moves, pins={})["moss"]
    pins, before, after = solve(scenes, designs, moves, census, base)
    report(before, after, designs)
    print(f"{len(pins)} speakers moved to another voice of their pool")
    if args.dry_run:
        return 0
    cast.write_pins(pins, args.out)
    cast.VOICES.write_text(cast.render(), encoding="utf-8")
    print(f"wrote {Path(args.out).name} and {cast.VOICES.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
