"""Tests for corpus.py: sources, classes, and the deny-list, on made-up rows
and a made-up LandSandBoat tree, so no client install is needed."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus  # noqa: E402


def row(speaker, variants, kind="say", zone=1, event=10, text_id=5, offset=0, live_only=None):
    return {"zone": zone, "event": event, "kind": kind, "speaker": speaker, "text_id": text_id,
            "offset": offset, "variants": variants, "live_only": live_only or []}


def rules(*entries):
    return corpus.Exclude(corpus.Rule(e, f"rule {n}") for n, e in enumerate(entries, 1))


class FakeCast:
    def __init__(self, table):
        self.table = table

    def who(self, r):
        return self.table.get((r["zone"], r["text_id"]))


class Classes(unittest.TestCase):
    def test_each_row_gets_one_class(self):
        ex = rules({"class": "system", "reason": "told to the player", "kind": "narration", "text": "^You\\b"},
                   {"class": "boilerplate", "reason": "shop", "speaker": "Clerk"})
        cases = [
            (row("Iroha", ["Master."]), "dialogue"),
            (row("", ["And so it began."], kind="narration"), "narration"),
            (row("", ["You find nothing."], kind="narration"), "system"),
            (row("Iroha", ["You find nothing."]), "dialogue"),          # the rule is for narration only
            (row("Clerk", ["Anything else?"]), "boilerplate"),
            (row("", ["Who goes there?"]), "unattributed"),
            (row("???", ["Who goes there?"]), "unattributed"),
            (row("<0x1054374>", ["Who goes there?"]), "unattributed"),
            (row("", ["Go on. Stay."], kind="menu"), "menu"),
        ]
        for r, want in cases:
            self.assertEqual(corpus.classify(r, r["variants"][0], ex)[0], want, r)

    def test_what_cannot_be_said_is_unvoiceable_with_its_reason(self):
        self.assertEqual(corpus.classify(row("Iroha", [], text_id=None), None, None), ("unvoiceable", "variable text id"))
        self.assertEqual(corpus.classify(row("Iroha", [], live_only=["number", "item"]), None, None),
                         ("unvoiceable", "live-only: item, number"))
        self.assertEqual(corpus.classify(row("Iroha", []), None, None), ("unvoiceable", "empty text"))
        self.assertEqual(corpus.classify(row("Iroha", ["<Sigh>"]), "<Sigh>", None), ("unvoiceable", "stage direction only"))
        self.assertEqual(corpus.classify(row("Iroha", ["...!?"]), "...!?", None), ("unvoiceable", "punctuation only"))

    def test_a_cast_line_is_dialogue_under_the_character_who_says_it(self):
        cast = FakeCast({(1, 5): "Cait Sith"})
        lines, counts = corpus.extract([row("", ["Mew."]), row("", ["Hm."], text_id=6)], None, cast)
        self.assertEqual([(r["class"], r.get("as")) for r in lines], [("dialogue", "Cait Sith"), ("unattributed", None)])
        self.assertEqual(lines[0]["speaker"], "", "the key keeps the shown name")
        self.assertEqual(counts["speakers"], {"Cait Sith": {"lines": 1, "chars": 4}})
        self.assertEqual(counts["unattributed"], [{"zone": 1, "event": 10, "lines": 1}])


class Extract(unittest.TestCase):
    def test_lines_are_unique_and_counts_hold_no_text(self):
        rows = [row("Iroha", ["Master.", "Mistress."]), row("Iroha", ["Master."], event=11),
                row("Tenzen", ["I, Tenzen, am here."]), row("", ["And so it began."], kind="narration"),
                row("", ["a", "b"], kind="menu"), row("Iroha", [], live_only=["item"])]
        lines, counts = corpus.extract(rows)
        master = [r for r in lines if r["text"] == "Master."]
        self.assertEqual(len(master), 1)
        self.assertEqual([s["event"] for s in master[0]["sources"]], [10, 11])
        self.assertEqual(counts["classes"]["dialogue"], {"lines": 3, "chars": 35})
        self.assertEqual(counts["voiced"], {"lines": 4, "chars": 51})
        self.assertEqual(list(counts["speakers"]), ["Iroha", "Tenzen"], "most lines first")
        self.assertEqual(counts["reasons"], {"menu": {"menu": 2}, "unvoiceable": {"live-only: item": 1}})
        dumped = json.dumps(counts)
        for text in ("Master", "And so"):
            self.assertNotIn(text, dumped, "the committed counts never carry the game's text")

    def test_the_first_matching_rule_wins_and_unused_rules_are_stale(self):
        ex = rules({"class": "system", "reason": "first", "text": "gil"},
                   {"class": "boilerplate", "reason": "second", "text": "gil"},
                   {"class": "system", "reason": "zone and event", "zone": 9, "event": 3})
        _, counts = corpus.extract([row("", ["Lost 5 gil."], kind="narration"), row("A", ["x"], zone=9, event=4)], ex)
        self.assertEqual(counts["reasons"]["system"], {"first": 1})
        self.assertEqual([r.reason for r in ex.stale()], ["second", "zone and event"])

    def test_a_bad_rule_is_refused_where_it_stands(self):
        for bad, why in (({"class": "menu", "reason": "x", "text": "y"}, "class must be"),
                         ({"class": "system", "text": "y"}, "needs a reason"),
                         ({"class": "system", "reason": "x", "event": 3}, "needs text, speaker, or zone"),
                         ({"class": "system", "reason": "x", "text": "y", "tex": "z"}, "unknown field 'tex'")):
            with self.assertRaisesRegex(ValueError, "rule 1: .*" + why):
                rules(bad)

    def test_the_committed_exclude_file_loads(self):
        ex = corpus.Exclude.load()
        self.assertTrue(ex.rules)
        player = row("", ["<player> feels a chill."], kind="narration")
        self.assertEqual(corpus.classify(player, player["variants"][0], ex)[0], "system")
        story = row("", ["And so the age of beasts began."], kind="narration")
        self.assertEqual(corpus.classify(story, story["variants"][0], ex)[0], "narration")


class Sources(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.lsb = Path(self.dir.name)
        (self.lsb / "sql").mkdir()
        (self.lsb / "sql/zone_settings.sql").write_text(
            "INSERT INTO `zone_settings` VALUES (230,'127.0.0.1',54230,'Southern_San_dOria');\n"
            "INSERT INTO `zone_settings` VALUES (280,'127.0.0.1',54230,'Mog_Garden');\n")
        script = "[xi.zone.SOUTHERN_SAN_DORIA] =\n{ return quest:progressEvent(%d) }\n"
        for path, event in (("missions/rov/1.lua", 7), ("quests/jeuno/a.lua", 8), ("quests/jeuno/deep/b.lua", 9),
                            ("quests/loose.lua", 4)):
            p = self.lsb / "scripts" / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(script % event)
        self.companions = self.lsb / "companions.yaml"
        self.companions.write_text("garden:\n  zones: [280]\nhouse:\n  zones: [230]\n  speakers: [Moogle]\n")

    def tearDown(self):
        self.dir.cleanup()

    def test_sources_are_mission_folders_quest_regions_and_companions(self):
        self.assertEqual(corpus.sources(self.lsb, self.companions),
                         ["missions/rov", "quests/jeuno", "quests/misc", "companions/garden", "companions/house"])

    def test_targets_come_from_the_scripts_or_from_whole_zones(self):
        self.assertEqual(corpus.targets(None, self.lsb, "quests/jeuno", self.companions), ([(230, 8), (230, 9)], 2))
        self.assertEqual(corpus.targets(None, self.lsb, "quests/misc", self.companions), ([(230, 4)], 1))

        class Client:
            def event_blocks(self, zone):
                return [{"events": {3: 0, 5: 9}}, {"events": {5: 0}}]
        self.assertEqual(corpus.targets(Client(), self.lsb, "companions/garden", self.companions), ([(280, 3), (280, 5)], None))
        with self.assertRaisesRegex(FileNotFoundError, "not a LandSandBoat folder"):
            corpus.targets(None, self.lsb, "missions/nowhere", self.companions)
        with self.assertRaisesRegex(ValueError, "not in companions.yaml"):
            corpus.targets(None, self.lsb, "companions/nobody", self.companions)

    def test_the_committed_companions_file_loads(self):
        table = corpus.companions()
        self.assertEqual(table["mog-garden"]["speakers"], None)
        self.assertEqual(table["mog-house"]["speakers"], ["Moogle"])

    def test_text_goes_to_one_tree_and_counts_to_another(self):
        lines, counts = corpus.extract([row("Iroha", ["Master."])])
        text, count = corpus.write("missions/rov", lines, {"source": "missions/rov", **counts},
                                   self.lsb / "text", self.lsb / "counts")
        self.assertEqual(text, self.lsb / "text/missions/rov.jsonl")
        self.assertEqual(json.loads(text.read_text())["text"], "Master.")
        self.assertEqual(json.loads(count.read_text())["voiced"], {"lines": 1, "chars": 7})
        self.assertIn("missions/rov: 1 lines to voice, 7 characters, 1 speakers; 1 dialogue",
                      corpus.summary({"source": "missions/rov", **counts}))


if __name__ == "__main__":
    unittest.main()
