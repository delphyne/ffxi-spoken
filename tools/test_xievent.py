"""Tests for the event bytecode disassembler, on synthetic bytecode so no
client install is needed. Run with:

    python3 -I -m unittest discover -s tools -p 'test_*.py'
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xievent  # noqa: E402
from xievent import DecodeError, decode, disassemble, walk_reachable  # noqa: E402

END, EXIT, GOTO, CALL, RET, SAY, WAIT = b"\x00", b"\x21", b"\x01", b"\x1a", b"\x1b", b"\x1d", b"\x23"


def u16(v):
    return v.to_bytes(2, "little")


def IF(cond, else_to):
    return b"\x02" + u16(0x8000) + u16(0x8001) + bytes([cond]) + u16(else_to)


class SizeTable(unittest.TestCase):
    def test_json_matches_rules(self):
        table = json.loads(xievent.OPCODES_JSON.read_text())["opcodes"]
        multi = {int(k, 16) for k, v in table.items() if len(v["sizes"]) > 1}
        ruled = set(xievent.VARIABLE) | set(xievent.ALWAYS)
        self.assertEqual(multi - ruled, set(), "multi-size opcodes without a rule")
        for op in xievent.VARIABLE:
            self.assertIn(f"0x{op:02X}", table, "VARIABLE rule for an opcode XiEvents has no page for")
        self.assertEqual({int(k, 16) for k, v in table.items() if not v["sizes"]}, {0xCA, 0xCB})

    def test_fixed_sizes(self):
        self.assertEqual(decode(SAY + u16(0x8003)).size, 3)
        self.assertEqual(decode(b"\x2b" + b"\xf0\xff\xff\x7f" + u16(0x8000)).size, 7)
        self.assertEqual(decode(b"\x49" + b"\xf0\xff\xff\x7f" + u16(0x8000)).size, 7)
        self.assertEqual(decode(b"\xb0" + bytes(11)).size, 12)
        self.assertEqual(decode(b"\xc4" + bytes(11)).size, 12, "0xC4 is 12 despite the page saying 11")
        self.assertEqual(decode(b"\x5b" + bytes(16)).size, 15)

    def test_sub_case_sizes(self):
        self.assertEqual(decode(b"\x1f\x00" + bytes(6)).size, 8)
        self.assertEqual(decode(b"\x1f\x01").size, 2)
        self.assertEqual(decode(b"\xb4\x13" + bytes(18)).size, 20)
        self.assertEqual(decode(b"\xb4\x14" + bytes(10)).size, 12)
        self.assertEqual(decode(b"\x60\x07").size, 2, "DEFAULT covers unlisted sub-cases")

    def test_errors(self):
        with self.assertRaises(DecodeError) as cm:
            decode(b"\xff")
        self.assertIn("unknown opcode", str(cm.exception))
        with self.assertRaises(DecodeError) as cm:
            decode(b"\xca")
        self.assertIn("deprecated", str(cm.exception))
        with self.assertRaises(DecodeError) as cm:
            decode(b"\x5c\xb0\x00\x00")
        self.assertIn("sub-case 0xb0", str(cm.exception))
        with self.assertRaises(DecodeError) as cm:
            decode(SAY + b"\x00", 0)
        self.assertIn("overruns", str(cm.exception))
        with self.assertRaises(DecodeError):
            decode(b"\x9d\x0e" + bytes(8))  # size decided at run time


class Flow(unittest.TestCase):
    def test_if_else_is_absolute(self):
        ins = decode(b"\x10\x10" + IF(0, 0x4B02), 2)
        self.assertEqual(ins.flow(), [("next", None), ("jump", 0x4B02)])

    def test_terminators_and_jumps(self):
        self.assertEqual(decode(END).flow(), [("end", None)])
        self.assertEqual(decode(EXIT).flow(), [("end", None)])
        self.assertEqual(decode(RET).flow(), [("return", None)])
        self.assertEqual(decode(GOTO + u16(0x20)).flow(), [("jump", 0x20)])
        self.assertEqual(decode(CALL + u16(0x20)).flow(), [("call", 0x20), ("next", None)])
        self.assertEqual(decode(b"\x3e" + bytes(4) + u16(0x30)).flow(), [("next", None), ("jump", 0x30)])
        self.assertEqual(decode(b"\x44" + bytes(2) + u16(0x30)).flow(), [("next", None), ("jump", 0x30)])
        table = decode(b"\x9d\x0a" + u16(0x100) + bytes(6)).flow()
        self.assertIn(("table", 0x100), table)
        self.assertIn(("jumptable", 0x100), decode(b"\x9d\x07" + u16(0x100) + bytes(2)).flow())


class Linear(unittest.TestCase):
    def test_walks_segment(self):
        code = SAY + u16(0x8000) + WAIT + END
        self.assertEqual([i.op for i in disassemble(code)], [0x1D, 0x23, 0x00])
        self.assertEqual([i.op for i in disassemble(code, 3, 5)], [0x23, 0x00])

    def test_reads_tables_as_code_and_fails(self):
        code = RET + u16(0x8041) + u16(0x8042)   # 41 80 42 80: 0x41 is a fixed 1-byte op, 0x80 needs 5
        with self.assertRaises(DecodeError):
            disassemble(code)


class Reachable(unittest.TestCase):
    def test_skips_table_after_return(self):
        # 9D 0A reads a table at 0x0f; the RET keeps the walk out of it.
        head = b"\x9d\x0a" + u16(0x0F) + bytes(6) + CALL + u16(0x0E)
        code = head + RET + RET + u16(0x8041) + u16(0x8042)
        self.assertEqual(len(code), 0x13)
        r = walk_reachable(code, 0)
        self.assertEqual(sorted(r.instrs), [0x0, 0xA, 0xD, 0xE])
        self.assertEqual(r.tables, {0x0F})

    def test_follows_both_if_arms_and_calls(self):
        sub = SAY + u16(0x8002) + RET                       # at 0x16
        code = (IF(0, 0x0F) + SAY + u16(0x8000) + WAIT + GOTO + u16(0x12)   # 0x00..0x0E: then arm
                + SAY + u16(0x8001)                                         # 0x0F: else arm
                + CALL + u16(0x16) + END + sub)                             # 0x12 onwards
        self.assertEqual(code.index(sub), 0x16)
        r = walk_reachable(code, 0)
        said = [i.u16(1) for i in r.listing() if i.op == 0x1D]
        self.assertEqual(said, [0x8000, 0x8001, 0x8002])

    def test_end_of_block_counts_as_end(self):
        self.assertEqual(len(walk_reachable(b"\x4d", 0).instrs), 1)          # falls off the end
        self.assertEqual(len(walk_reachable(GOTO + u16(3), 0).instrs), 1)    # jumps to len(code)
        with self.assertRaises(DecodeError) as cm:
            walk_reachable(GOTO + u16(0x200), 0)
        self.assertIn("past end", str(cm.exception))

    def test_overlap_is_an_error(self):
        # The SAY is decoded first, then the GOTO jumps into its operand.
        code = SAY + u16(0x8000) + GOTO + u16(1)
        with self.assertRaises(DecodeError) as cm:
            walk_reachable(code, 0)
        self.assertIn("inside instruction", str(cm.exception))
        self.assertIn("reached via", str(cm.exception))

    def test_exit_stops_the_walk(self):
        code = EXIT + b"\xff"
        self.assertEqual(len(walk_reachable(code, 0).instrs), 1)


def actor(v):
    return v.to_bytes(4, "little")


class Speakers(unittest.TestCase):
    NAMES = {0x1000001: "Iroha", 0x1000002: "Tenzen"}
    STRS = ["zero", "one", "two", "three"]

    def block(self, code, imm=(0, 1, 2, 3), actor=0x1000001):
        return {"actor": actor, "events": {7: 0}, "imm": tuple(imm), "code": code}

    def said(self, code, **kw):
        return [(r["speaker"], r["text"]) for r in xievent.lines(self.block(code, **kw), 7, self.NAMES, self.STRS)]

    def test_block_entity_and_named_actor(self):
        code = SAY + u16(0x8000) + b"\x2b" + actor(0x1000002) + u16(0x8001) + END
        self.assertEqual(self.said(code), [("Iroha", "zero"), ("Tenzen", "one")])

    def test_player_and_event_entity_constants(self):
        code = b"\x2b" + actor(0x7FFFFFF0) + u16(0x8000) + b"\x2b" + actor(0x7FFFFFF8) + u16(0x8001) + END
        self.assertEqual(self.said(code), [("<player>", "zero"), ("Iroha", "one")])

    def test_7a_retargets_then_resets(self):
        code = (b"\x7a\x02" + actor(0x1000002) + SAY + u16(0x8000)
                + b"\x7a\x03" + SAY + u16(0x8001) + END)
        self.assertEqual(self.said(code), [("Tenzen", "zero"), ("Iroha", "one")])

    def test_entity_is_path_state(self):
        # IF: fall through retargets to Tenzen, else arm keeps Iroha; both reach the SAY.
        code = IF(0, 0x0E) + b"\x7a\x02" + actor(0x1000002) + SAY + u16(0x8000) + END
        self.assertEqual(len(code), 0x12)
        rows = xievent.lines(self.block(code), 7, self.NAMES, self.STRS)
        self.assertEqual(sorted(r["speaker"] for r in rows), ["Iroha", "Tenzen"])

    def test_unknown_actor_and_variable_message(self):
        code = b"\x2b" + actor(0x1000099) + u16(0x8002) + SAY + u16(0x0005) + END
        rows = xievent.lines(self.block(code), 7, self.NAMES, self.STRS)
        self.assertEqual(rows[0]["speaker"], "<0x1000099>")
        self.assertEqual((rows[1]["text_id"], rows[1]["var"], rows[1]["text"]), (None, 5, None))

    def test_narration_and_menu(self):
        code = b"\x48" + u16(0x8000) + b"\x49" + actor(0x1000002) + u16(0x8001) + b"\x24" + u16(0x8002) + bytes(4) + END
        rows = xievent.lines(self.block(code), 7, self.NAMES, self.STRS)
        self.assertEqual([(r["kind"], r["speaker"], r["text"]) for r in rows],
                         [("narration", "", "zero"), ("narration", "", "one"), ("menu", "", "two")])


if __name__ == "__main__":
    unittest.main()
