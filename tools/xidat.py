#!/usr/bin/env python3
"""Read FFXI client DATs: dialogue tables, NPC lists, and event bytecode.

This is the foothold for docs/Event-Decoding-Handoff.md. Standard library
only; the client install is found under the Lumoria flatpak unless --client
points elsewhere.

    python3 tools/xidat.py zones                       # zones with an event DAT
    python3 tools/xidat.py dialog 249 [--grep Iroha]   # a zone's dialogue table
    python3 tools/xidat.py npcs 249                    # a zone's NPC list
    python3 tools/xidat.py events 249                  # actors and event ids
    python3 tools/xidat.py cutscene 249 368            # lines and speakers (heuristic)

Which DAT holds what for each zone comes from tools/zone_dats.json, the
per-zone table in atom0s/XiEvents ("Event DAT Files.md"): entity list,
event bytecode, and the JP and NA string tables.

Formats (verified against LandSandBoat's text ids and the XiEvents docs):
  dialogue (strings_na): every byte XOR 0x80, then a uint32 offset table
    from byte 4 and Shift-JIS strings each ending in NUL 0x07; index N is
    the game's text id N. Parameter codes can hold a NUL, so entries are
    cut at the offset table, not at the first NUL (tools/ffxitext.py
    knows the codes).
  NPC list (entities): 32-byte records, 28-byte name + uint32 id.
  event: BlockCount, BlockSizes[], then per block ActorNumber, TagCount,
    TagOffset[TagCount] (uint16), EventExecNum[TagCount] (uint16),
    ImmediateCount, Immediates[] (uint32), EventDataSize, bytecode.
"""

import argparse
import json
import re
import struct
import sys
from pathlib import Path

# Where the client is installed: under the Lumoria flatpak's prefix on Linux,
# in a CrossOver bottle named ffxi on macOS. The first that exists, else the
# first, so a message about a missing client names the usual place.
_INSTALL = "drive_c/Program Files (x86)/PlayOnline/SquareEnix/FINAL FANTASY XI"
CLIENT_CANDIDATES = (Path.home() / ".var/app/net.windower.Lumoria/data/prefixes/prefix-1/pfx" / _INSTALL,
                     Path.home() / "Library/Application Support/CrossOver/Bottles/ffxi" / _INSTALL)
CLIENT_DEFAULT = next((p for p in CLIENT_CANDIDATES if p.is_dir()), CLIENT_CANDIDATES[0])
HERE = Path(__file__).resolve().parent
ZONE_MAP = HERE / "zone_dats.json"

XOR80 = bytes(b ^ 0x80 for b in range(256))
PLAYER = {0x7FFFFFC0, 0x7FFFFFF0, 0x7FFFFFF9}
EVENT_ENTITY = 0x7FFFFFF8


class Client:
    def __init__(self, root=CLIENT_DEFAULT):
        self.root = Path(root)
        self.zones = json.loads(ZONE_MAP.read_text())["zones"]

    def zone_file(self, zone, kind):
        """kind: entities, events, strings_na, strings_jp."""
        rel = self.zones.get(str(zone), {}).get(kind)
        if not rel:
            raise FileNotFoundError(f"zone {zone} has no {kind} DAT in {ZONE_MAP.name}")
        p = self.root / rel
        if not p.exists():
            raise FileNotFoundError(f"{p} is missing from the client")
        return p

    def dialog_bytes(self, zone):
        """Each entry's raw Shift-JIS bytes, parameter codes included.

        Entries end in a NUL and a 0x07 filler; a NUL can also sit inside
        a parameter (`0x0C 0x00 [a/b]` picks alternative 0, `0x0A 0x00` is
        number 0), so only the terminator is stripped, never the first NUL
        (half of all entries have one inside)."""
        data = self.zone_file(zone, "strings_na").read_bytes().translate(XOR80)
        if len(data) < 8:
            return []
        first = struct.unpack_from("<I", data, 4)[0]
        count = first // 4  # the table runs up to the first string
        offs = struct.unpack_from(f"<{count}I", data, 4)
        out = []
        for k, o in enumerate(offs):
            end = offs[k + 1] if k + 1 < count else len(data) - 4
            s = data[4 + o:4 + end]
            if s.endswith(b"\0\x07"):
                s = s[:-2]
            elif s.endswith(b"\0"):
                s = s.rstrip(b"\0")
            out.append(s)
        return out

    def dialog(self, zone):
        """Entries decoded for reading; control and parameter bytes stay in."""
        return [s.decode("shift_jis", "replace") for s in self.dialog_bytes(zone)]

    def npcs(self, zone):
        d = self.zone_file(zone, "entities").read_bytes()
        return {struct.unpack_from("<I", d, i + 28)[0]: d[i:i + 28].split(b"\0")[0].decode("shift_jis", "replace")
                for i in range(0, len(d) - 31, 32)}

    def event_blocks(self, zone):
        e = self.zone_file(zone, "events").read_bytes()
        n = struct.unpack_from("<I", e, 0)[0]
        sizes = struct.unpack_from(f"<{n}I", e, 4)
        off = 4 + 4 * n
        for s in sizes:
            actor, tags = struct.unpack_from("<II", e, off)
            tag_off = struct.unpack_from(f"<{tags}H", e, off + 8)
            exec_num = struct.unpack_from(f"<{tags}H", e, off + 8 + 2 * tags)
            q = off + 8 + 4 * tags
            imm_count = struct.unpack_from("<I", e, q)[0]
            imm = struct.unpack_from(f"<{imm_count}I", e, q + 4)
            q2 = q + 4 + 4 * imm_count
            size = struct.unpack_from("<I", e, q2)[0]
            yield {"actor": actor, "events": dict(zip(exec_num, tag_off)), "imm": imm, "code": e[q2 + 4:q2 + 4 + size]}
            off += s


def event_segment(block, cs):
    start = block["events"][cs]
    later = sorted(o for o in block["events"].values() if o > start)
    return block["code"][start:later[0] if later else len(block["code"])]


def resolve_ref(imm, lo, hi):
    """getworkofs: a 16-bit operand with bit 15 set indexes the immediates."""
    v = lo | (hi << 8)
    if v & 0x8000 and (v & 0x7FFF) < len(imm):
        return imm[v & 0x7FFF]
    return None


def actor_name(value, block_actor, names):
    if value in PLAYER:
        return "<player>"
    if value == EVENT_ENTITY:
        value = block_actor
    return names.get(value, f"<{value:#x}>")


def cutscene_lines(client, zone, cs):
    """Heuristic pass: pattern-match the message opcodes instead of walking
    the bytecode, so jumps and variable-length opcodes are ignored. Good
    enough to read a cutscene; the handoff's step 1 replaces it."""
    strs = client.dialog(zone)
    names = client.npcs(zone)
    out = []
    for block in client.event_blocks(zone):
        if cs not in block["events"]:
            continue
        seg = event_segment(block, cs)
        if len(seg) < 3:
            continue
        imm = block["imm"]
        speaker = names.get(block["actor"], f"<{block['actor']:#x}>")
        i = 0
        while i < len(seg) - 2:
            op = seg[i]
            msg = who = None
            if op in (0x1D, 0x48, 0x49):                       # 1D m m: speaker is the block's entity
                msg = resolve_ref(imm, seg[i + 1], seg[i + 2])
                who = speaker if op == 0x1D else ""
            elif op == 0x2B and i + 7 <= len(seg):             # 2B a a a a m m
                msg = resolve_ref(imm, seg[i + 5], seg[i + 6])
                who = actor_name(struct.unpack_from("<I", seg, i + 1)[0], block["actor"], names)
            elif op == 0xB0 and i + 12 <= len(seg):            # B0 ? a a a a b b b b m m
                msg = resolve_ref(imm, seg[i + 10], seg[i + 11])
                who = actor_name(struct.unpack_from("<I", seg, i + 2)[0], block["actor"], names)
            if msg is not None and msg < len(strs) and strs[msg]:
                out.append({"actor": speaker, "speaker": who, "op": op, "text_id": msg, "text": strs[msg]})
                i += {0x2B: 7, 0xB0: 12}.get(op, 3)
            else:
                i += 1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", default=str(CLIENT_DEFAULT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("zones")
    d = sub.add_parser("dialog"); d.add_argument("zone", type=int); d.add_argument("--grep")
    n = sub.add_parser("npcs"); n.add_argument("zone", type=int)
    ev = sub.add_parser("events"); ev.add_argument("zone", type=int)
    c = sub.add_parser("cutscene"); c.add_argument("zone", type=int); c.add_argument("cs", type=int)
    c.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    client = Client(args.client)

    if args.cmd == "zones":
        for z, info in sorted(client.zones.items(), key=lambda kv: int(kv[0])):
            have = [k for k in ("entities", "events", "strings_na") if info.get(k) and (client.root / info[k]).exists()]
            print(f"{int(z):4} {info['name']:32} {' '.join(have)}")
    elif args.cmd == "dialog":
        for i, s in enumerate(client.dialog(args.zone)):
            if not args.grep or re.search(args.grep, s):
                print(i, repr(s))
    elif args.cmd == "npcs":
        for nid, name in client.npcs(args.zone).items():
            print(f"{nid:#x} {name}")
    elif args.cmd == "events":
        names = client.npcs(args.zone)
        for b in client.event_blocks(args.zone):
            print(f"{b['actor']:#x} {names.get(b['actor'], '?')!r}: events {sorted(b['events'])}")
    elif args.cmd == "cutscene":
        lines = cutscene_lines(client, args.zone, args.cs)
        if args.json:
            print(json.dumps(lines, ensure_ascii=False, indent=1))
        else:
            for ln in lines:
                print(f"[{ln['speaker']}] {ln['text']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
