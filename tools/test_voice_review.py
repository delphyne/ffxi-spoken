"""The voice review without Tk or Qwen: the catalogue, filters, search, and
audition/accept/regenerate/reset against files in a temp folder; the
storyline index; and, under a display with Tk, the window opening."""

import json
import os
import random
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "helper"))
import casting  # noqa: E402
import design_voices as dv  # noqa: E402
import designs  # noqa: E402
import voice_review as vr  # noqa: E402
import voice_stories  # noqa: E402

YAML = """# header comment that must survive
# a second line

calibration: Wait here.

cast:
  Iroha:
    description: Gender female, warm.
    seed: 7
    also: [Volto Oscuro]
  Ekokoko:
    description: Small and chirpy.
    group: taru_f

pools:
""" + "".join(f"  {g}:\n    - \"{g} voice\"\n" + ("    - \"second hume\"\n" if g == "hume_m" else "")
              for g in casting.POOLS)


class FakeEngine:
    def __init__(self):
        self.calls = []

    def manual_seed(self, seed):
        self.calls.append(("seed", seed))

    def design(self, text, description):
        self.calls.append(("design", text, description))
        return [0.25] * 2400, 24000


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.yaml = root / "voice_designs.yaml"
        self.yaml.write_text(YAML, encoding="utf-8")
        self.refs = root / "refs"
        self.refs.mkdir()
        d = designs.Designs.load(self.yaml, groups=set(casting.POOLS))
        import cast
        self.voices_json = root / "voices.json"
        self.voices_json.write_text(json.dumps(cast.build(d)), encoding="utf-8")
        census = root / "census.json"
        census.write_text(json.dumps({"Numi Adaligo": {"race": 7}, "Tonasav": {"race": 1}}))
        stories = root / "stories.json"
        stories.write_text(json.dumps({"stories": {"rov": "Rhapsodies of Vana'diel", "cop": "Chains of Promathia"},
                                       "speakers": {"Iroha": {"rov": 300}, "Numi Adaligo": {"rov": 19},
                                                    "Tonasav": {"rov": 3, "cop": 2}, "Prishe": {"cop": 400},
                                                    "": {"rov": 6}}}))
        self.engine = FakeEngine()
        self.ses = vr.Session(self.yaml, self.refs, self.voices_json, census, stories,
                              engine_factory=lambda: self.engine, rng=random.Random(4))

    def tearDown(self):
        self.dir.cleanup()

    def labels(self, **kw):
        return [r["label"] for r in self.ses.filter(**kw)]

    def test_catalogue_resolves_voices_groups_and_stories(self):
        iroha = self.ses.row("speaker:Iroha")
        self.assertEqual((iroha["voice"], iroha["kind"], iroha["race"], iroha["gender"], iroha["lines"]),
                         ("iroha", "own voice", "hume", "female", 300))
        numi = self.ses.row("speaker:Numi Adaligo")   # census race 7: a mithra pool voice
        self.assertEqual((numi["kind"], numi["race"], numi["gender"]), ("pool voice", "mithra", "female"))
        self.assertTrue(numi["voice"].startswith("mithra-"))
        eko = self.ses.row("speaker:Ekokoko")         # group from the design's own metadata
        self.assertEqual((eko["race"], eko["gender"]), ("tarutaru", "female"))
        self.assertEqual(self.ses.row("speaker:")["label"], vr.NARRATION)
        pool = self.ses.row(f"voice:{numi['voice']}")
        self.assertEqual(pool["stories"], {"rov": 19})
        self.assertIn("Numi Adaligo", self.ses.users[numi["voice"]])
        self.assertEqual(self.labels(kind="pool voice")[0], "Prishe", "most-spoken first")
        prishe = self.ses.row("speaker:Prishe")   # in the hand cast, no design: the group's pool
        self.assertEqual(self.ses.row(f"voice:{prishe['voice']}")["lines"], 400, "a pool counts its speakers' lines")

    def test_filters_and_partial_search(self):
        self.assertEqual(self.labels(query="numi"), ["Numi Adaligo"])
        self.assertIn("Numi Adaligo", self.labels(query="ada"))
        self.assertTrue(all("ada" in label.lower() or "ada" in r["voice"]
                            for label, r in ((r["label"], r) for r in self.ses.filter(query="ada"))))
        self.assertEqual(self.labels(query="IROH"), ["Iroha", "Iroha Red", "Volto Oscuro"],
                         "a voice's name finds everyone who shares it")
        self.assertIn("Volto Oscuro", self.labels(query="volto"))
        rov = self.labels(story="rov", kind="own voice")
        self.assertEqual(rov, ["Iroha"])
        self.assertEqual(self.labels(story="rov", kind="pool voice"), ["Numi Adaligo", vr.NARRATION, "Tonasav"],
                         "sorted by lines in the chosen storyline")
        self.assertEqual(self.labels(story="cop", kind="pool voice"), ["Prishe", "Tonasav"])
        cop = self.labels(story="cop")
        self.assertIn("Prishe", cop)
        self.assertNotIn("Iroha", cop)
        self.assertTrue(all(r["gender"] == "female" for r in self.ses.filter(gender="female")))
        self.assertTrue(all(r["race"] == "mithra" for r in self.ses.filter(race="mithra")))
        self.assertTrue(all(r["kind"] == "pools" for r in self.ses.filter(kind="pools")))
        opts = self.ses.options()
        self.assertIn(("cop", "Chains of Promathia"), opts["story"])
        self.assertIn(("mithra", "mithra"), opts["race"])
        self.assertEqual(opts["kind"][0], ("all", "all"))

    def test_audition_then_accept_writes_clip_index_and_yaml(self):
        take = self.ses.audition("iroha", "Gender female, warmer.")
        self.assertEqual(self.engine.calls, [("seed", 7), ("design", "Wait here.", "Gender female, warmer.")])
        self.assertEqual(take, self.refs / ".candidates" / "iroha.7.wav")
        st = self.ses.state("iroha")
        self.assertEqual((st["description"], st["saved_description"]), ("Gender female, warmer.", "Gender female, warm."))
        self.assertEqual(st["candidate"], take)
        # meanwhile someone edits another voice in the file by hand
        self.yaml.write_text(self.yaml.read_text().replace("Small and chirpy.", "Small, chirpy, hand-edited."))
        clip = self.ses.accept("iroha")
        self.assertEqual(clip.read_bytes(), take.read_bytes())
        text = self.yaml.read_text()
        self.assertTrue(text.startswith("# header comment that must survive\n# a second line\n"))
        self.assertIn("Gender female, warmer.", text)
        self.assertIn("Small, chirpy, hand-edited.", text, "only the accepted voice changes")
        saved = designs.Designs.load(self.yaml, groups=set(casting.POOLS))
        self.assertEqual(saved.voices["iroha"]["seed"], 7)
        self.assertEqual(saved.voices["ekokoko"]["group"], "taru_f", "metadata survives a save")
        rec = dv.load_index(self.refs)["iroha"]
        self.assertEqual((rec["description"], rec["how"], rec["sha256"]),
                         ("Gender female, warmer.", "voice review", dv.sha256_file(clip)))
        self.assertFalse(self.ses.state("iroha")["pending"])

    def test_regenerate_draws_a_new_seed_and_accept_saves_it(self):
        self.ses.regenerate("iroha", "Gender female, warm.")
        seed = self.engine.calls[0][1]
        self.assertNotEqual(seed, 7)
        self.ses.accept("iroha")
        self.assertEqual(designs.Designs.load(self.yaml).voices["iroha"]["seed"], seed)

    def test_reset_drops_the_take_and_edits(self):
        self.ses.audition("iroha", "Something else.")
        self.ses.reset("iroha")
        st = self.ses.state("iroha")
        self.assertEqual(st["description"], "Gender female, warm.")
        self.assertIsNone(st["candidate"])
        self.assertFalse(st["pending"])
        with self.assertRaises(ValueError):
            self.ses.accept("iroha")

    def test_give_own_voice_then_accept_updates_voices_json(self):
        voice = self.ses.give_own_voice("Numi Adaligo")
        self.assertEqual(voice, "numi-adaligo")
        row = self.ses.row("speaker:Numi Adaligo")
        self.assertEqual((row["voice"], row["kind"], row["race"]), ("numi-adaligo", "own voice", "mithra"))
        self.assertEqual(self.ses.state(voice)["description"], "mithra voice", "starts from the pool's description")
        self.ses.audition(voice, "Gender female. A sly merchant.")
        self.ses.accept(voice)
        saved = designs.Designs.load(self.yaml, groups=set(casting.POOLS))
        self.assertEqual(saved.voice_for_speaker("Numi Adaligo"), "numi-adaligo")
        self.assertEqual(saved.voices["numi-adaligo"]["group"], "mithra")
        self.assertEqual(json.loads(self.voices_json.read_text())["moss"]["Numi Adaligo"], "numi-adaligo")
        with self.assertRaises(ValueError):
            self.ses.give_own_voice("Iroha")

    def test_reset_of_an_unaccepted_new_voice_removes_it(self):
        self.ses.give_own_voice("Tonasav")
        self.ses.reset("tonasav")
        self.assertEqual(self.ses.row("speaker:Tonasav")["kind"], "pool voice")
        self.assertNotIn("tonasav", self.ses.designs.voices)
        self.assertNotIn("Tonasav", designs.Designs.load(self.yaml).speakers)

    def test_empty_description_is_refused(self):
        with self.assertRaises(ValueError):
            self.ses.audition("iroha", "   ")


class StoriesTest(unittest.TestCase):
    def test_counts_lines_per_speaker_per_story(self):
        lines = {"rov": [{"speaker": "Iroha"}, {"speaker": "Iroha"}, {"speaker": ""}],
                 "cop": [{"speaker": "Prishe"}, {"speaker": "Iroha"}], "tvr": []}
        data = voice_stories.build(lambda code: lines[code], {"rov": "R", "cop": "C", "tvr": "T"})
        self.assertEqual(data["stories"], {"rov": "R", "cop": "C"}, "a storyline with no lines is left out")
        self.assertEqual(data["speakers"], {"": {"rov": 1}, "Iroha": {"cop": 1, "rov": 2}, "Prishe": {"cop": 1}})

    def test_committed_index_has_the_rhapsodies_cast(self):
        data = json.loads((Path(voice_stories.OUT)).read_text(encoding="utf-8"))
        self.assertIn("rov", data["stories"])
        self.assertIn("rov", data["speakers"]["Iroha"])


class GuiTest(unittest.TestCase):
    def test_available_needs_a_display(self):
        import voice_review_gui
        with unittest.mock.patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}):
            self.assertFalse(voice_review_gui.available())

    def test_window_opens_and_filters(self):
        import voice_review_gui
        if not voice_review_gui.available():
            self.skipTest("no display or Tk")
        t = SessionTest()
        t.setUp()
        self.addCleanup(t.tearDown)
        try:
            w = voice_review_gui.Window(t.ses, player=[])
        except Exception as e:  # noqa: BLE001 - a display that refuses connections
            self.skipTest(f"Tk could not open a window: {e}")
        self.addCleanup(w.root.destroy)
        self.assertEqual(len(w.tree.get_children()), len(t.ses.rows))
        w.query.set("numi")
        self.assertEqual(w.tree.get_children(), ("speaker:Numi Adaligo",))
        w.tree.selection_set("speaker:Numi Adaligo")
        w.select()
        self.assertTrue(w.own.winfo_manager(), "a pool speaker gets the own-voice button")
        self.assertIn("mithra voice", w.text.get("1.0", "end"))


if __name__ == "__main__":
    unittest.main()
