#!/usr/bin/env python3
"""List the cutscenes Rhapsodies of Vana'diel runs, from LandSandBoat's
mission scripts: (mission, zone id, zone, event id). This is the burn-down
list for docs/Event-Decoding-Handoff.md; feed each row to
`tools/xidat.py cutscene <zone id> <event id>`.

    python3 tools/rov_events.py [--lsb path] [--missions rov] [--json]

Zone constants (xi.zone.MHAURA) are LandSandBoat's zone_settings names
upper-cased, so the ids come from sql/zone_settings.sql.
"""

import argparse
import json
import re
import sys
from pathlib import Path

LSB_DEFAULT = Path.home() / "scm/github/LandSandBoat/server"
ZONE_SECTION = re.compile(r"\[xi\.zone\.([A-Z0-9_]+)\]\s*=")
EVENT_IDS = re.compile(r"(?:progressEvent|startEvent|startCutscene|startOptionalCutscene)\((\d+)")
ON_ZONE_IN = re.compile(r"onZoneIn\s*=\s*function[^\n]*\n(?:[^\n]*\n){0,6}?\s*return\s+(\d+)")
FINISH_KEY = re.compile(r"^\s*\[(\d+)\]\s*=\s*function\s*\(player,\s*csid", re.M)


def zone_ids(lsb):
    text = (lsb / "sql/zone_settings.sql").read_text(encoding="utf-8", errors="replace")
    ids = {}
    for zid, name in re.findall(r"INSERT INTO `zone_settings` VALUES \((\d+),'[^']*',\d+,'([^']+)'", text):
        ids[re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")] = int(zid)
    return ids


def events_in(path, ids):
    src = path.read_text(encoding="utf-8", errors="replace")
    rows = []
    sections = list(ZONE_SECTION.finditer(src))
    for k, m in enumerate(sections):
        body = src[m.end():sections[k + 1].start() if k + 1 < len(sections) else len(src)]
        zone = m.group(1)
        found = set(int(x) for x in EVENT_IDS.findall(body))
        found.update(int(x) for x in ON_ZONE_IN.findall(body))
        found.update(int(x) for x in FINISH_KEY.findall(body))
        for cs in sorted(found):
            rows.append({"mission": path.stem, "zone_id": ids.get(zone), "zone": zone, "event": cs})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lsb", default=str(LSB_DEFAULT))
    ap.add_argument("--missions", default="rov", help="folder under scripts/missions")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    lsb = Path(args.lsb)
    ids = zone_ids(lsb)
    rows = []
    for lua in sorted((lsb / "scripts/missions" / args.missions).glob("*.lua")):
        rows.extend(events_in(lua, ids))
    if args.json:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            print(f"{r['mission']:48} {str(r['zone_id']):>4} {r['zone']:28} {r['event']}")
        zones = {r["zone"] for r in rows}
        print(f"# {len(rows)} cutscenes in {len(zones)} zones from {args.missions}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
