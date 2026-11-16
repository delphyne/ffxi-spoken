#!/usr/bin/env python3
"""Build helper/census.json: every NPC name the game can show, with race.

Sources, in order of trust:
  LandSandBoat (open-source server): data/zones/*/npcs.yaml gives each
    NPC's display name and model look, whose `race` byte identifies the
    humanoid race and sex; scripts/zones/*/npcs/*.lua and the mission and
    quest scripts say which NPCs ever start an event or print a message.
  The client's own per-zone NPC list DATs (optional, --client): names the
    server data lacks, flagged so they can be chased.

The census is the fallback casting source: a speaker absent from voices.json
is cast from the pool for its race (see casting.py). It is also the audit
list: --report prints speaking NPCs with no voice, Rhapsodies of Vana'diel
first.

    python3 helper/census.py --lsb ~/scm/github/LandSandBoat/server [--client <FFXI dir>] [--report]

Needs PyYAML (python3-pyyaml), so it runs with the system python3, not .venv.
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

EVENT_API = re.compile(r"\b(startEvent|progressEvent|startCutscene|startOptionalCutscene|messageSpecial|"
                       r"messageText|showText|messageName|startEventString)\s*\(")
TRIGGER_KEY = re.compile(r"^\s*\['([^']+)'\]\s*=", re.M)
CLIENT_DEFAULT = (Path.home() / ".var/app/net.windower.Lumoria/data/prefixes/prefix-1/pfx/drive_c"
                  "/Program Files (x86)/PlayOnline/SquareEnix/FINAL FANTASY XI")


def norm(zone_name):
    return re.sub(r"[^a-z0-9]", "", zone_name.lower())


def zone_of(npc_id):
    return (npc_id >> 12) & 0xFFF


def read_lsb(lsb):
    lsb = Path(lsb)
    npcs = {}  # (zone_norm, script) -> dict
    for yml in sorted((lsb / "data/zones").glob("*/npcs.yaml")):
        zone = yml.parent.name
        with yml.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        for npc_id, npc in (doc.get("npcs") or {}).items():
            if not isinstance(npc, dict):
                continue
            name = npc.get("display_name") or ""
            if not name or name.startswith("NPC[") or name == "none":
                continue
            look = ((npc.get("render") or {}).get("look") or {})
            npcs[(norm(zone), npc.get("script") or "", int(npc_id))] = {
                "name": name, "zone": zone, "zone_id": zone_of(int(npc_id)),
                "race": look.get("race") if look.get("type") == "equipped" else None,
                "look": look.get("type"), "model": look.get("model"),
            }
    talkers = set()  # (zone_norm, script); zone_norm None = any zone
    for lua in (lsb / "scripts/zones").glob("*/npcs/*.lua"):
        if EVENT_API.search(lua.read_text(encoding="utf-8", errors="replace")):
            talkers.add((norm(lua.parent.parent.name), lua.stem))
    rov = set()
    for folder in ("missions", "quests"):
        for lua in (lsb / "scripts" / folder).rglob("*.lua"):
            keys = set(TRIGGER_KEY.findall(lua.read_text(encoding="utf-8", errors="replace")))
            for k in keys:
                talkers.add((None, k))
            if "/missions/rov/" in lua.as_posix():
                rov.update(keys)
    return npcs, talkers, rov


def read_client(client):
    """Names from the client's per-zone entity list DATs, located through
    tools/zone_dats.json (the XiEvents per-zone table)."""
    client = Path(client)
    zones = json.loads((Path(__file__).resolve().parent.parent / "tools/zone_dats.json").read_text())["zones"]
    out = defaultdict(set)
    for zone, info in zones.items():
        if not info.get("entities"):
            continue
        p = client / info["entities"]
        if not p.exists():
            continue
        d = p.read_bytes()
        for i in range(0, len(d) - 31, 32):
            name = d[i:i + 28].split(b"\0")[0].decode("shift_jis", "replace")
            if name and name != "none" and not name.startswith("NPC[") and name[0].isalpha():
                out[name].add(int(zone))
    return out


def build(npcs, talkers, rov, client_names=None):
    by_name = defaultdict(lambda: {"race": Counter(), "zones": set(), "scripts": set(), "talks": False, "rov": False,
                                   "look": Counter(), "model": Counter()})
    for (zone_norm, script, _), npc in npcs.items():
        e = by_name[npc["name"]]
        e["zones"].add(npc["zone"])
        if script:
            e["scripts"].add(script)
        if npc["race"] is not None:
            e["race"][npc["race"]] += 1
        if npc["look"]:
            e["look"][npc["look"]] += 1
        if npc["model"] is not None:
            e["model"][npc["model"]] += 1
        if (zone_norm, script) in talkers or (None, script) in talkers:
            e["talks"] = True
        if script in rov:
            e["rov"] = True
    census = {}
    for name, e in by_name.items():
        census[name] = {
            "race": e["race"].most_common(1)[0][0] if e["race"] else None,
            "look": e["look"].most_common(1)[0][0] if e["look"] else None,
            "model": e["model"].most_common(1)[0][0] if e["model"] else None,
            "zones": sorted(e["zones"]),
            "talks": e["talks"], "rov": e["rov"], "source": "lsb",
        }
    for name, zones in (client_names or {}).items():
        if name not in census:
            census[name] = {"race": None, "look": None, "model": None, "zones": sorted(f"zone{z}" for z in zones),
                            "talks": None, "rov": False, "source": "client"}
        else:
            census[name]["in_client"] = True
    return dict(sorted(census.items()))


def report(census, voices):
    cast = set(voices.get("kokoro", {}))
    talkers = {n: e for n, e in census.items() if e["talks"]}
    missing = {n: e for n, e in talkers.items() if n not in cast}
    print(f"{len(census)} names, {len(talkers)} speak, {len(missing)} speakers not in voices.json")
    races = Counter(e["race"] for e in missing.values())
    print("  by race:", dict(sorted(races.items(), key=lambda kv: (kv[0] is None, kv[0]))))
    rov_missing = sorted(n for n, e in missing.items() if e["rov"])
    print(f"  Rhapsodies speakers not cast ({len(rov_missing)}):", ", ".join(rov_missing))
    client_only = [n for n, e in census.items() if e["source"] == "client"]
    if client_only:
        print(f"  {len(client_only)} names only the client knows (no race); e.g.", ", ".join(client_only[:12]))


def main(argv=None):
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lsb", default=str(Path.home() / "scm/github/LandSandBoat/server"))
    ap.add_argument("--client", nargs="?", const=str(CLIENT_DEFAULT), default=None,
                    help="FFXI install folder; include names from its NPC list DATs")
    ap.add_argument("--out", default=str(here / "census.json"))
    ap.add_argument("--report", action="store_true", help="print speakers missing from voices.json")
    args = ap.parse_args(argv)
    if yaml is None:
        sys.exit("census.py needs PyYAML: run it with the system python3 (python3-pyyaml)")
    npcs, talkers, rov = read_lsb(args.lsb)
    client_names = read_client(args.client) if args.client else None
    census = build(npcs, talkers, rov, client_names)
    lines = ",\n".join(f" {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}" for k, v in census.items())
    Path(args.out).write_text("{\n" + lines + "\n}\n", encoding="utf-8")  # one name per line: diffable
    print(f"wrote {args.out}: {len(census)} names from {len(npcs)} NPC spawns")
    if args.report:
        voices_path = here / "voices.json"
        voices = json.loads(voices_path.read_text(encoding="utf-8")) if voices_path.exists() else {}
        report(census, voices)
    return 0


if __name__ == "__main__":
    sys.exit(main())
