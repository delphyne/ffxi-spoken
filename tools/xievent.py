#!/usr/bin/env python3
"""Disassemble FFXI event bytecode (docs/Event-Decoding-Handoff.md, step 1).

    python3 tools/xievent.py disasm 252 286          # listing of one event
    python3 tools/xievent.py lines 252 286 [--json]  # who says what (step 2)
    python3 tools/xievent.py lines --rov             # speakers across Rhapsodies
    python3 tools/xievent.py check --rov             # walk every Rhapsodies event
    python3 tools/xievent.py check --zone 252        # walk every event in a zone
    python3 tools/xievent.py check --all             # walk every event in every zone
    python3 tools/xievent.py regen [--xievents DIR]  # rebuild tools/opcodes.json

Opcode sizes come from atom0s/XiEvents, one page per opcode under
`OpCodes/`. `regen` reads the "OpCode Size" row of each page into
tools/opcodes.json (checked in, like zone_dats.json). An opcode with one
size is table-driven; the forty or so whose size depends on the byte after
the opcode are spelled out in VARIABLE below, from each page's pseudo code.
Two opcodes (0xCA, 0xCB) have no handler in the client and are errors here.

The walk is linear: from an event's start offset to the next event's, one
instruction after another, with 0x00 (END) treated as an ordinary one-byte
instruction because code after it is reachable by jumps. An unknown opcode,
an instruction overrunning the segment, or a sub-case the client resolves
at run time stops the walk with DecodeError so gaps surface rather than
being papered over. Control flow is recorded on each instruction (jump
targets, embedded data tables) for the step 3 walker.
"""

import argparse
import json
import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ffxitext  # noqa: E402
import xidat  # noqa: E402

OPCODES_JSON = HERE / "opcodes.json"
XIEVENTS_DEFAULT = Path.home() / "scm/github/atom0s/XiEvents"


class DecodeError(Exception):
    def __init__(self, offset, op, why, path=()):
        shown = (["..."] if len(path) > 6 else []) + [f"{p:#x}" for p in path[-6:]]
        via = f", reached via {' > '.join(shown)}" if path else ""
        super().__init__(f"{why} at {offset:#x} (opcode {op:#04x}){via}")
        self.offset, self.op, self.why, self.path = offset, op, why, path


def _range(lo, hi, size):
    return {k: size for k in range(lo, hi + 1)}


# Size by the byte after the opcode. A DEFAULT entry covers unlisted
# sub-cases; without one, an unlisted sub-case is a DecodeError.
DEFAULT = "default"
VARIABLE = {
    0x1F: {0: 8, 1: 2},                      # MOVE: 00 starts, 01 polls
    0x31: {0: 10, 1: 2},                     # SMOVE
    0x46: {0: 2, 1: 2, 2: 4, 3: 8},          # DEFCAMERA; 3: undocumented, by inspection (zone 210)
    0x47: {0: 10, 1: 2},                     # position update, 01 waits
    0x59: {0: 4, 1: 8, 2: 4, 3: 8, 4: 8, 5: 7, 6: 6, 7: 4, 8: 8},
    0x5A: {0: 8, 1: 2},                      # MOVE2
    0x5C: {**_range(0, 7, 4), **_range(0x80, 0x87, 6), 0xA0: 6, 0xA1: 6},
    0x5F: {0: 2, 1: 2, 2: 6, 3: 16, 4: 16, 5: 18, 6: 18, 7: 14},
    0x60: {0: 4, 1: 4, 2: 6, DEFAULT: 2},
    0x71: {0: 2, 1: 2, 2: 2, 3: 4, 0x10: 4, 0x11: 4, 0x12: 6, 0x13: 4,
           0x20: 16, 0x21: 2, 0x30: 4, 0x31: 4, 0x32: 6, 0x40: 4, 0x41: 8,
           0x50: 4, 0x51: 2, 0x52: 4, 0x53: 2, 0x54: 10, 0x55: 4},
    0x72: {0: 4, 1: 6},                      # GETWEATHER
    0x75: {0: 4, 1: 2, 2: 2},                # LOADROOM
    0x79: {0: 10, 1: 12, 2: 10},             # look at
    0x7A: {0: 6, 1: 7, 2: 6, 3: 2, 4: 8, 5: 6},
    0x7E: {0: 6, 1: 6, 2: 6, 3: 16, 4: 6, 5: 6, 6: 18, 7: 8, 8: 6},
    0x8C: {0: 8, 1: 2, 2: 12, 3: 10, 4: 10, 5: 14},
    0x9D: {0: 8, 1: 8, 2: 6, 3: 8, 4: 8, 5: 8, 6: 8, 7: 6, 8: 23, 9: 9,
           0xA: 10, 0xB: 10, 0xC: 8, 0xD: 10, 0xF: 10, 0x10: 10},
    # 0x9D 0x0E is 8 or 10 depending on a zone table at run time.
    0xA6: {0: 2, 1: 2, 2: 4},
    0xA7: {0: 2, 1: 4},
    0xAB: {**_range(0, 0x10, 2), 0x11: 4, 0x12: 2, 0x13: 2, **_range(0x14, 0x18, 4),
           0x19: 2, 0x1A: 2, 0x1B: 6, 0x1C: 6},
    0xAC: {0: 4, 1: 4, 2: 6, 3: 6, 4: 8},
    # 0xAE case 8: the page says 10 but reads only an actor; Desuetia event 100
    # has two back to back at 6 bytes each, so 6.
    0xAE: {0: 6, 1: 8, 2: 8, 3: 8, 4: 8, 5: 10, 6: 6, 7: 10, 8: 6},
    0xB1: {0: 4, 1: 4, 2: 4, 3: 4, 4: 6},   # 3 and 4: undocumented, by inspection (zone 26)
    0xB2: {0: 4, 1: 2},
    0xB3: {0: 4, 1: 14, 2: 2, 3: 4, 4: 4, 5: 18, 6: 4, 7: 4, 8: 2, 9: 4, DEFAULT: 2},
    0xB4: {0: 20, 1: 6, 2: 6, 3: 2, 4: 6, 5: 3, 6: 3, 7: 4, 8: 2, 9: 4, 0xA: 4,
           0xB: 2, 0xC: 4, 0xD: 2, 0xE: 2, 0xF: 6, 0x10: 6, 0x11: 6, 0x12: 6, 0x13: 20, 0x14: 12, 0x15: 2},
    0xB6: {**_range(0, 0xA, 4), 0xB: 20, 0xC: 4, 0xD: 14, 0xE: 16, 0xF: 4,
           0x10: 2, 0x11: 4, 0x12: 2, 0x13: 2, 0x14: 6, 0x15: 6,
           0x16: 22, 0x17: 4},                   # 0x16, 0x17: undocumented, by inspection (zone 210)
    0xB7: {0: 10, 1: 8, 2: 8, 3: 8, 4: 8},
    0xBF: {0: 8, 0x20: 10, 0x40: 10, 0x60: 8},
    0xC2: {1: 4, 2: 6, DEFAULT: 2},
    0xCC: {0: 10, 1: 10, 2: 14, 3: 10, 0x10: 6, 0x11: 4, 0x20: 4},
    0xD4: {0: 8, 1: 8, 2: 8, 3: 6, 4: 12, 5: 12},
    0xD8: {0: 6, 1: 8, 2: 8, 3: 8, 4: 12, DEFAULT: 6},
}
# Pages whose size row disagrees with the handler: 0x5B/0x66 always step 15
# (the 17-byte form is reached only through 0x5F); 0xC4 calls the 0x73 helper
# with a one-byte shift, so it is 12, not 11 (checked against zone 25 event 17).
ALWAYS = {0x5B: 15, 0x66: 15, 0xC4: 12}
# Opcodes XiEvents has no page for, sized from the one place each occurs:
# 0xDA in Xarcabard event 68 is followed by a well-formed IF at +3.
INFERRED = {0xDA: 3}

# Message opcodes: (operand offset of the 16-bit message id, speaker rule).
MESSAGE_OPS = {
    0x1D: (1, "entity"),   # 1D m m: the current event entity
    0x2B: (5, "actor"),    # 2B a a a a m m
    0x48: (1, "none"),     # 48 m m: system text
    0x49: (5, "none"),     # 49 a a a a m m: narration tied to an actor
    0xB0: (10, "actor"),   # B0 ? a a a a b b b b m m
    0x24: (1, "menu"),     # choice menu text
}
# Only 0x7A changes who 0x1D speaks for: case 2 points the VM at another
# entity, case 3 points it back at the block's own. (The handoff's earlier
# list also named 0x1F/0x31/0x36/0x37; those only move the entity.)
ENTITY_SET, ENTITY_RESET = (0x7A, 2), (0x7A, 3)
PLAYER = {0x7FFFFFC0, 0x7FFFFFF0, 0x7FFFFFF9}
PARTY = set(range(0x7FFFFFC1, 0x7FFFFFD2)) | set(range(0x7FFFFFF1, 0x7FFFFFF6))
EVENT_ENTITY = 0x7FFFFFF8


def load_sizes(path=OPCODES_JSON):
    """Fixed sizes from opcodes.json, checked against VARIABLE/ALWAYS."""
    table = json.loads(path.read_text())["opcodes"]
    fixed, known = {}, set()
    for key, info in table.items():
        op = int(key, 16)
        known.add(op)
        sizes = info["sizes"]
        if op in ALWAYS:
            fixed[op] = ALWAYS[op]
        elif op in VARIABLE:
            continue
        elif len(sizes) == 1:
            fixed[op] = sizes[0]
        elif sizes:
            raise RuntimeError(f"opcode {op:#04x} has sizes {sizes} but no VARIABLE rule")
        # no sizes at all: deprecated, left out so it decodes as unknown
    fixed.update(INFERRED)
    return fixed, known


FIXED, KNOWN = load_sizes() if OPCODES_JSON.exists() else ({}, set())


class Instr:
    __slots__ = ("offset", "op", "size", "data")

    def __init__(self, offset, op, size, data):
        self.offset, self.op, self.size, self.data = offset, op, size, data

    @property
    def sub(self):
        return self.data[1] if len(self.data) > 1 else None

    def u16(self, at):
        return struct.unpack_from("<H", self.data, at)[0]

    def u32(self, at):
        return struct.unpack_from("<I", self.data, at)[0]

    @property
    def end(self):
        return self.offset + self.size

    def flow(self):
        """Control flow out of this instruction, for the step 3 walker.

        Returns a list of (kind, arg) with kind one of "next" (fall through
        to self.end), "jump"/"call" (arg is an absolute offset into the
        block's code), "return", "end", "table" (arg is the offset of a
        16-bit value table embedded in the code), or "jumptable" (a table
        of code offsets indexed by an event variable; targets unknown).
        """
        op, d = self.op, self.data
        if op == 0x00 or op == 0x21:         # END; 0x21 sets EventExecEnd and never advances
            return [("end", None)]
        if op == 0x01:
            return [("jump", self.u16(1))]
        if op == 0x02:                       # if: fall through, else jump
            # XiEvents writes the else as `ExecPointer += val3`, but the data
            # says absolute: Norg event 286 has `02 .. 02 4b` at 0x4ad5 with
            # the next IF at exactly 0x4b02.
            return [("next", None), ("jump", self.u16(6))]
        if op == 0x1A:
            return [("call", self.u16(1)), ("next", None)]
        if op == 0x1B:
            return [("return", None)]
        if op == 0x3E or op == 0x82:
            return [("next", None), ("jump", self.u16(5))]
        if op == 0x44:
            return [("next", None), ("jump", self.u16(3))]
        if op == 0x9D:
            sub = d[1]
            if sub in (0, 1, 0xA, 0xB):      # value tables read at u16(+2)
                return [("next", None), ("table", self.u16(2))]
            if sub in (5, 6):
                return [("next", None), ("table", self.u16(2))]
            if sub == 7:                     # call through a table of code offsets
                return [("next", None), ("jumptable", self.u16(2))]
            if sub == 8:
                return [("next", None), ("jump", self.u16(21))]
            if sub == 9:
                return [("next", None), ("jump", self.u16(7))]
            if sub in (0xF, 0x10):
                return [("next", None), ("table", self.u16(2))]
        return [("next", None)]

    def __repr__(self):
        return f"{self.offset:04x}: {self.data.hex(' ')}"


def size_of(code, pos):
    """Size of the instruction at pos, or raise DecodeError."""
    op = code[pos]
    if op in FIXED:
        return FIXED[op]
    rule = VARIABLE.get(op)
    if rule is None:
        raise DecodeError(pos, op, "deprecated opcode" if op in KNOWN else "unknown opcode")
    if pos + 1 >= len(code):
        raise DecodeError(pos, op, "sub-case byte past end")
    sub = code[pos + 1]
    if sub in rule:
        return rule[sub]
    if DEFAULT in rule:
        return rule[DEFAULT]
    raise DecodeError(pos, op, f"sub-case {sub:#04x} not decodable statically")


def decode(code, pos=0, end=None):
    end = len(code) if end is None else end
    size = size_of(code, pos)
    if pos + size > end:
        raise DecodeError(pos, code[pos], f"size {size} overruns segment end {end:#x}")
    return Instr(pos, code[pos], size, code[pos:pos + size])


def disassemble(code, start=0, end=None):
    """Linear walk from start to end; returns [Instr] or raises DecodeError."""
    end = len(code) if end is None else end
    out, pos = [], start
    while pos < end:
        ins = decode(code, pos, end)
        out.append(ins)
        pos = ins.end
    return out


class Reach:
    """Result of walk_reachable: instructions by offset, data tables, and
    the jump tables whose targets could not be followed."""
    def __init__(self):
        self.instrs, self.tables, self.jumptables = {}, set(), set()

    def listing(self):
        return [self.instrs[k] for k in sorted(self.instrs)]


def walk_reachable(code, start):
    """Follow control flow from start over the whole block's code.

    Event segments are not self-contained: subroutines reached by CALL are
    shared between events, and 0x9D parks 16-bit tables after a RET where a
    linear walk would read them as opcodes. So the walk is a worklist over
    jump, call, and fall-through edges, bounded by the block, and an
    instruction that lands inside an already-decoded one is an error (a
    wrong size somewhere upstream)."""
    r = Reach()
    owner = {}  # byte offset -> instruction start
    pred = {start: None}  # for the error trail

    def trail(pos):
        out = []
        while pos is not None and len(out) < 60:
            out.append(pos)
            pos = pred.get(pos)
        return tuple(reversed(out))

    work = [start]
    while work:
        pos = work.pop()
        if pos in r.instrs:
            continue
        if pos == len(code):
            continue  # falling or jumping exactly off the end of the block acts as END
        if pos >= len(code):
            raise DecodeError(pos, 0, f"jump target past end of block ({len(code):#x})", trail(pred[pos]))
        if pos in owner:
            raise DecodeError(pos, code[pos], f"target inside instruction at {owner[pos]:#x}", trail(pred[pos]))
        try:
            ins = decode(code, pos)
        except DecodeError as e:
            raise DecodeError(e.offset, e.op, e.why, trail(pred[pos])) from None
        for k in range(pos, ins.end):
            if k in owner:
                raise DecodeError(pos, ins.op, f"overlaps instruction at {owner[k]:#x}", trail(pred[pos]))
            owner[k] = pos
        r.instrs[pos] = ins
        for kind, arg in ins.flow():
            if kind == "next":
                pred.setdefault(ins.end, pos)
                work.append(ins.end)
            elif kind in ("jump", "call"):
                pred.setdefault(arg, pos)
                work.append(arg)
            elif kind == "table":
                r.tables.add(arg)
            elif kind == "jumptable":
                r.jumptables.add(arg)
    return r


def resolve_actor(value, entity):
    """GetActorIndex: an actor operand to (server id or tag, kind)."""
    if value in PLAYER:
        return "player", "player"
    if value in PARTY:
        return "party", "party"
    if value == EVENT_ENTITY or not value & 0xFF000000:
        return entity, "entity"       # the VM's current entity, by default the block's
    return value, "actor"


def actor_label(ref, names):
    if ref == "player":
        return "<player>"
    if ref == "party":
        return "<party member>"
    return names.get(ref, f"<{ref:#x}>")


def lines(block, cs, names, strs, zone=None):
    """Every message instruction reachable from the event, with its speaker.

    The walk carries the VM's current entity as state so a 0x7A that
    retargets it changes who later 0x1D lines belong to; an instruction
    reached with two different entities yields two rows. Rows come out in
    offset order (step 3 will add path order). text_id is None when the
    message id is an event variable rather than an immediate (step 4).
    `strs` may hold bytes (Client.dialog_bytes) or str; with bytes each row
    also gets `variants`, the cleaned renderings a pack would hold, and
    `live_only`, the parameter kinds that stop it being enumerated."""
    code, imm = block["code"], block["imm"]
    start = block["events"][cs]
    rows = {}
    seen = set()
    work = [(start, block["actor"])]
    while work:
        pos, entity = work.pop()
        if (pos, entity) in seen or pos >= len(code):
            continue
        seen.add((pos, entity))
        ins = decode(code, pos)
        if ins.op in MESSAGE_OPS:
            at, rule = MESSAGE_OPS[ins.op]
            lo, hi = ins.data[at], ins.data[at + 1]
            text_id = xidat.resolve_ref(imm, lo, hi)
            if rule == "entity":
                who, kind = entity, "entity"
            elif rule == "actor":
                who, kind = resolve_actor(ins.u32(1 if ins.op == 0x2B else 2), entity)
            else:
                who, kind = None, rule
            key = (pos, who)
            if key not in rows:
                rows[key] = {
                    "zone": zone, "event": cs, "block_actor": block["actor"], "offset": pos,
                    "op": ins.op, "kind": {"none": "narration", "menu": "menu"}.get(rule, "say"),
                    "speaker_id": who if isinstance(who, int) else None,
                    "speaker": actor_label(who, names) if who is not None else "",
                    "text_id": text_id,
                    "text": None,
                    "var": None if text_id is not None else lo | (hi << 8),
                }
                if text_id is not None and text_id < len(strs):
                    raw = strs[text_id]
                    if isinstance(raw, bytes):
                        rows[key]["text"] = raw.decode("cp932", "replace")
                        rows[key]["variants"] = ffxitext.variants(raw)
                        rows[key]["live_only"] = ffxitext.expand(raw).live_only
                    else:
                        rows[key]["text"] = raw
        if (ins.op, ins.sub) == ENTITY_SET:
            entity = resolve_actor(ins.u32(2), entity)[0]
        elif (ins.op, ins.sub) == ENTITY_RESET:
            entity = block["actor"]
        for kind, arg in ins.flow():
            if kind == "next":
                work.append((ins.end, entity))
            elif kind in ("jump", "call"):
                work.append((arg, entity))
    return [rows[k] for k in sorted(rows, key=lambda k: (k[0], str(k[1])))]


def event_bounds(block, cs):
    start = block["events"][cs]
    later = sorted(o for o in block["events"].values() if o > start)
    return start, later[0] if later else len(block["code"])


def walk_event(block, cs, linear=False):
    """Instructions of one event: reachable from its entry by default, or
    the linear segment up to the next event's entry with linear=True."""
    start, end = event_bounds(block, cs)
    if linear:
        return disassemble(block["code"], start, end)
    return walk_reachable(block["code"], start).listing()


def mnemonic(ins):
    names = {0x00: "END", 0x21: "EXIT", 0x01: "GOTO", 0x02: "IF", 0x1A: "CALL", 0x1B: "RET",
             0x1D: "SAY", 0x2B: "SAY_AS", 0x48: "SYSTEM", 0x49: "NARRATE", 0xB0: "SAY_TO",
             0x24: "MENU", 0x23: "WAIT_MSG", 0x25: "WAIT_MENU"}
    name = names.get(ins.op, f"op{ins.op:02X}")
    if ins.op in MESSAGE_OPS:
        at = MESSAGE_OPS[ins.op][0]
        return f"{name} msg={ins.u16(at):#06x}"
    return name


def iter_events(client, zones):
    for zone in zones:
        try:
            blocks = list(client.event_blocks(zone))
        except FileNotFoundError:
            continue
        for block in blocks:
            for cs in sorted(block["events"]):
                yield zone, block, cs


def cmd_check(client, args):
    if args.rov:
        import rov_events
        rows = []
        for lua in sorted((Path(args.lsb) / "scripts/missions/rov").glob("*.lua")):
            rows.extend(rov_events.events_in(lua, rov_events.zone_ids(Path(args.lsb))))
        wanted = {(r["zone_id"], r["event"]) for r in rows if r["zone_id"] is not None}
        zones = sorted({z for z, _ in wanted})
    else:
        zones = [args.zone] if args.zone is not None else sorted(int(z) for z in client.zones)
        wanted = None
    ok = bad = computed = 0
    failures, ids = {}, set()
    for zone, block, cs in iter_events(client, zones):
        if wanted is not None and (zone, cs) not in wanted:
            continue
        ids.add((zone, cs))
        try:
            if args.linear:
                walk_event(block, cs, linear=True)
            else:
                r = walk_reachable(block["code"], event_bounds(block, cs)[0])
                computed += bool(r.jumptables)
            ok += 1
        except DecodeError as e:
            bad += 1
            key = re.sub(r"0x[0-9a-f]+", "N", e.why)
            failures.setdefault(f"{key} ({e.op:#04x})", []).append((zone, cs, e))
            if args.verbose:
                print(f"zone {zone} event {cs}: {e}")
    print(f"{ok} events walked ({len(ids)} distinct zone/event ids; an id can recur across a zone's blocks), {bad} failed"
          + (f"; {computed} reach a jump table whose targets are not followed" if not args.linear else ""))
    for why, rows in sorted(failures.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(rows):6}  {why}   e.g. zone {rows[0][0]} event {rows[0][1]}: {rows[0][2]}")
    return 1 if bad else 0


def cmd_disasm(client, args):
    names = client.npcs(args.zone)
    strs = client.dialog(args.zone)
    for block in client.event_blocks(args.zone):
        if args.cs not in block["events"]:
            continue
        start, end = event_bounds(block, args.cs)
        print(f"# block actor {block['actor']:#x} {names.get(block['actor'], '?')!r}, "
              f"event {args.cs} at {start:#x}..{end:#x}, {len(block['imm'])} immediates")
        try:
            if args.linear:
                listing = disassemble(block["code"], start, end)
            else:
                r = walk_reachable(block["code"], start)
                listing = r.listing()
                for t in sorted(r.tables):
                    print(f"# value table at {t:#x}")
                for t in sorted(r.jumptables):
                    print(f"# jump table at {t:#x} (targets not followed)")
        except DecodeError as e:
            listing = disassemble(block["code"], start, e.offset)
            print(f"# DecodeError: {e}")
        for ins in listing:
            line = f"{ins.offset:04x}  {ins.data.hex(' '):<36} {mnemonic(ins)}"
            if ins.op in MESSAGE_OPS:
                msg = xidat.resolve_ref(block["imm"], ins.data[MESSAGE_OPS[ins.op][0]],
                                        ins.data[MESSAGE_OPS[ins.op][0] + 1])
                if msg is not None and msg < len(strs):
                    line += f"  ; {strs[msg]!r}"
            print(line)
    return 0


def rov_rows(lsb):
    import rov_events
    ids = rov_events.zone_ids(Path(lsb))
    rows = []
    for lua in sorted((Path(lsb) / "scripts/missions/rov").glob("*.lua")):
        rows.extend(rov_events.events_in(lua, ids))
    return sorted({(r["zone_id"], r["event"]) for r in rows if r["zone_id"] is not None})


def cmd_lines(client, args):
    targets = rov_rows(args.lsb) if args.rov else [(args.zone, args.cs)]
    out, speakers, unresolved = [], {}, []
    for zone, cs in targets:
        names, strs = client.npcs(zone), client.dialog_bytes(zone)
        for block in client.event_blocks(zone):
            if cs not in block["events"]:
                continue
            for row in lines(block, cs, names, strs, zone):
                out.append(row)
                if row["kind"] == "say":
                    speakers.setdefault(row["speaker"], set()).add((zone, cs))
                if row["text_id"] is None or row["speaker"].startswith("<0x"):
                    unresolved.append(row)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    elif args.rov:
        said = [r for r in out if r["kind"] == "say"]
        live = [r for r in said if r.get("live_only")]
        reasons = {}
        for r in live:
            for why in set(r["live_only"]):
                reasons[why] = reasons.get(why, 0) + 1
        nvar = sum(len(r.get("variants", [])) for r in said)
        print(f"{len(said)} spoken lines, {sum(r['kind'] == 'narration' for r in out)} narration, "
              f"{sum(r['kind'] == 'menu' for r in out)} menus; {len(speakers)} speakers; {len(unresolved)} unresolved")
        print(f"{nvar} pack texts from {len(said) - len(live)} lines; {len(live)} lines live-only: "
              + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])))
        for name, where in sorted(speakers.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            n = sum(r["speaker"] == name for r in said)
            print(f"  {n:5} lines  {name:28} in {len(where)} scenes")
        for row in unresolved[:20]:
            print(f"  ? zone {row['zone']} event {row['event']} at {row['offset']:#x}: speaker {row['speaker']!r} text {row['text_id']}")
    else:
        for row in out:
            tag = {"narration": "*", "menu": "?"}.get(row["kind"], "")
            note = f"  (live-only: {', '.join(row['live_only'])})" if row.get("live_only") else ""
            for v in row.get("variants") or [row["text"]]:
                print(f"{row['offset']:04x} [{tag}{row['speaker']}] {v!r}{note}")
    return 0


def regen(xievents):
    pages = sorted((Path(xievents) / "OpCodes").glob("0x*.md"))
    if not pages:
        raise SystemExit(f"no OpCodes/*.md under {xievents}")
    table = {}
    for page in pages:
        text = page.read_text(encoding="utf-8")
        m = re.search(r"\*\*OpCode Size\*\*\s*\|\s*([^|]+)\|", text)
        sizes = [int(s) for s in re.findall(r"`(\d+)`", m.group(1))] if m else []
        name = re.search(r"//\s*PS2:\s*XiEvent::(\w+)", text)
        entry = {"sizes": sizes}
        if name:
            entry["name"] = name.group(1)
        table[f"0x{int(page.stem, 16):02X}"] = entry
    OPCODES_JSON.write_text(json.dumps({"source": "atom0s/XiEvents OpCodes/*.md, 'OpCode Size' rows",
                                        "opcodes": table}, indent=1) + "\n")
    print(f"wrote {OPCODES_JSON} with {len(table)} opcodes")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", default=str(xidat.CLIENT_DEFAULT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("disasm"); d.add_argument("zone", type=int); d.add_argument("cs", type=int)
    d.add_argument("--linear", action="store_true")
    ln = sub.add_parser("lines"); ln.add_argument("zone", type=int, nargs="?"); ln.add_argument("cs", type=int, nargs="?")
    ln.add_argument("--rov", action="store_true"); ln.add_argument("--json", action="store_true")
    ln.add_argument("--lsb", default=str(Path.home() / "scm/github/LandSandBoat/server"))
    c = sub.add_parser("check")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--rov", action="store_true"); g.add_argument("--zone", type=int); g.add_argument("--all", action="store_true")
    c.add_argument("--lsb", default=str(Path.home() / "scm/github/LandSandBoat/server"))
    c.add_argument("-v", "--verbose", action="store_true")
    c.add_argument("--linear", action="store_true", help="walk the segment linearly instead of following flow")
    r = sub.add_parser("regen"); r.add_argument("--xievents", default=str(XIEVENTS_DEFAULT))
    args = ap.parse_args(argv)
    if args.cmd == "regen":
        regen(args.xievents)
        return 0
    client = xidat.Client(args.client)
    if args.cmd == "lines" and not args.rov and args.cs is None:
        ap.error("lines needs ZONE CS or --rov")
    return {"disasm": cmd_disasm, "check": cmd_check, "lines": cmd_lines}[args.cmd](client, args)


if __name__ == "__main__":
    sys.exit(main())
