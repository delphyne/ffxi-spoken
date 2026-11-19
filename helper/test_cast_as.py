"""Tests for cast_as.py: the table of who really says a nameless line."""

import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cast_as  # noqa: E402


def row(zone, entity, event, text_id, text, speaker="???"):
    return {"zone": zone, "event": event, "text_id": text_id, "speaker": speaker,
            "speaker_id": entity, "variants": [text]}


class Matching(unittest.TestCase):
    def test_an_entity_entry_covers_every_line_it_says(self):
        table = cast_as.CastAs([{"zone": 136, "entity": 0x1088356, "as": "Cait Sith"}])
        self.assertEqual(table.who(row(136, 0x1088356, 13, 1, "Meow!", speaker="")), "Cait Sith")
        self.assertEqual(table.who(row(136, 0x1088356, 30, 2, "Nyaaa!", speaker="")), "Cait Sith")
        self.assertIsNone(table.who(row(136, 0x1088357, 13, 1, "Meow!", speaker="")))
        self.assertIsNone(table.who(row(137, 0x1088356, 13, 1, "Meow!", speaker="")))
        self.assertEqual(table.unused(), [])

    def test_lines_narrow_an_entry_and_check_the_first_words(self):
        table = cast_as.CastAs([{"zone": 84, "entity": 0x1054374, "lines": {8374: "Found ya at long last."},
                                 "as": "Larzos"}])
        self.assertEqual(table.who(row(84, 0x1054374, 7, 8374, "Found ya at long last.")), "Larzos")
        self.assertIsNone(table.who(row(84, 0x1054374, 7, 8375, "Found ya at long last.")))
        with self.assertRaisesRegex(ValueError, "no longer starts with 'Found ya"):
            table.who(row(84, 0x1054374, 7, 8374, "Lost ya."))
        # a row with no cleaned text (live-only) is not checked
        r = row(84, 0x1054374, 7, 8374, "")
        r["variants"] = []
        self.assertEqual(table.who(r), "Larzos")

    def test_events_narrow_and_the_most_specific_entry_wins(self):
        table = cast_as.CastAs([
            {"zone": 137, "entity": 0x108929b, "as": "Nobody"},
            {"zone": 137, "entity": 0x108929b, "events": [18], "as": "Flit"},
            {"zone": 137, "entity": 0x108929b, "lines": {8479: "Is...is someone there?"}, "as": "Portia"},
        ])
        self.assertEqual(table.who(row(137, 0x108929b, 18, 8284, "What took the two of you tho long")), "Flit")
        self.assertEqual(table.who(row(137, 0x108929b, 23, 8479, "Is...is someone there?")), "Portia")
        self.assertEqual(table.who(row(137, 0x108929b, 23, 8480, "Hello?")), "Nobody")
        self.assertEqual(table.unused(), [])

    def test_two_entries_as_specific_as_each_other_is_an_error(self):
        table = cast_as.CastAs([
            {"zone": 85, "entity": 0x10552f6, "lines": {8220: "Portia!"}, "as": "Mayakov"},
            {"zone": 85, "entity": 0x10552f6, "lines": {8220: "Portia!"}, "as": "Ragelise"},
        ])
        with self.assertRaisesRegex(ValueError, "entry 1 and .* entry 2 both cover zone 85 event 2 text 8220"):
            table.who(row(85, 0x10552f6, 2, 8220, "Portia!"))

    def test_a_blank_as_is_undecided_but_not_unused(self):
        table = cast_as.CastAs([{"zone": 156, "entity": 0x109c071, "as": None, "note": "shown as 2"},
                                {"zone": 156, "entity": 0x109c070, "events": [8], "as": "  "}])
        self.assertIsNone(table.who(row(156, 0x109c071, 8, 8089, "In ages past", speaker="2")))
        self.assertEqual([str(e) for e in table.unused()], ["zone 156 entity 0x109c070 events [8] as (blank)"])
        self.assertEqual(table.names(), [])

    def test_only_entries_the_rows_came_near_count_as_unused(self):
        table = cast_as.CastAs([{"zone": 84, "entity": 0x1054374, "lines": {8374: "Found ya"}, "as": "Larzos"},
                                {"zone": 137, "entity": 0x108929b, "events": [18], "as": "Flit"},
                                {"zone": 136, "entity": 0x1088356, "as": "Cait Sith"}])
        self.assertEqual(table.unused(), [])                     # nothing seen yet: nothing to report
        table.who(row(84, 0x1054374, 7, 8375, "Who goes there?"))  # the entity, but not that text id
        table.who(row(137, 0x108929c, 23, 1, "Hello"))            # the zone, but not that event
        table.who(row(136, 0x1088357, 13, 1, "Meow"))             # a neighbour of the nameless cait
        self.assertEqual(table.unused(), [])
        table.who(row(84, 0x1054375, 7, 8374, "Found ya"))        # the text id, said by another entity
        table.who(row(137, 0x108929c, 18, 1, "Hello"))            # the event, another speaker
        self.assertEqual([e.who for e in table.unused()], ["Larzos", "Flit"])


class Validation(unittest.TestCase):
    def test_shape(self):
        for bad, why in [
            ({"zone": 1}, "entity must be an integer"),
            ({"zone": "1", "entity": 2}, "zone must be an integer"),
            ({"zone": 1, "entity": 2, "who": "x"}, "unknown key"),
            ({"zone": 1, "entity": 2, "as": 3}, "as must be a name"),
            ({"zone": 1, "entity": 2, "events": 18}, "events must be a list"),
            ({"zone": 1, "entity": 2, "events": []}, "events must be a list"),
            ({"zone": 1, "entity": 2, "lines": [1]}, "lines must map"),
            ({"zone": 1, "entity": 2, "lines": {"a": "b"}}, "lines must map"),
            ("Larzos", "an entry is a mapping"),
        ]:
            with self.assertRaisesRegex(ValueError, why, msg=repr(bad)):
                cast_as.CastAs([bad])
        with self.assertRaisesRegex(ValueError, "expected a list"):
            cast_as.CastAs({"zone": 1})

    def test_str_names_the_entry(self):
        e = cast_as.CastAs([{"zone": 84, "entity": 0x1054374, "lines": {8374: "Found"}, "events": [7],
                             "as": "Larzos"}]).entries[0]
        self.assertEqual(str(e), "zone 84 entity 0x1054374 lines [8374] events [7] as Larzos")


@unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML not installed")
class TheFile(unittest.TestCase):
    def test_the_shipped_table_loads(self):
        table = cast_as.CastAs.load()
        self.assertGreater(len(table.entries), 20)
        self.assertIn("Larzos", table.names())
        self.assertIn("Cait Sith", table.names())
        # ids are hex in the file and integers here
        self.assertTrue(all(e.entity > 0x1000000 for e in table.entries))

    def test_an_empty_file_is_an_empty_table(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cast_as.yaml"
            p.write_text("# nothing yet\n", encoding="utf-8")
            self.assertEqual(cast_as.CastAs.load(p).entries, [])


if __name__ == "__main__":
    unittest.main()
