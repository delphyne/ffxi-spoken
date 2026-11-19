"""design_voices.py without Qwen: planning, adopting, the index, the page."""

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import design_voices as dv  # noqa: E402
import designs  # noqa: E402
import spoken_helper as sh  # noqa: E402

GROUPS = {"hume_m_adult"}


IROHA = {"gender": "female", "age": "young adult", "race": "hume", "description": "Warm.", "seed": 7,
         "also": ["Volto Oscuro"]}


def make_designs(quality="high", **cast):
    cast = cast or {"Iroha": IROHA}
    return designs.Designs({"calibration": "Wait here.", "cast": cast,
                            "pools": {"hume_m_adult": ["plain", {"age": "fifties", "description": "gruff"}]}},
                           groups=GROUPS, quality=quality)


class FakeQwen:
    def __init__(self):
        self.calls, self.seeds = [], []

    def manual_seed(self, seed):
        self.seeds.append(seed)

    def design(self, text, instruct):
        self.calls.append((text, instruct))
        return [0.0] * 2400, 24000


class DesignVoicesTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(unittest.mock.patch.object(dv, "log", lambda *a: None))
        self.dir = tempfile.TemporaryDirectory()
        self.refs = Path(self.dir.name) / "refs"

    def tearDown(self):
        self.dir.cleanup()

    def run_design(self, d, **kw):
        index = dv.load_index(self.refs)
        todo = dv.plan(d, index, self.refs, dv.selected(d, kw.pop("only", None)), fresh=kw.pop("fresh", False),
                       changed=kw.pop("changed", False))
        engine = FakeQwen()
        dv.design_all(d, todo, self.refs, engine, index, **kw)
        return todo, engine

    def test_first_run_designs_everything_with_each_seed(self):
        d = make_designs()
        todo, engine = self.run_design(d)
        self.assertEqual(todo, [("iroha", "missing"), ("hume_m_adult-0", "missing"), ("hume_m_adult-1", "missing")])
        self.assertEqual(engine.calls, [("Wait here.", "Female, young adult. High quality.\nWarm."),
                                        ("Wait here.", "Male, adult. High quality.\nplain"),
                                        ("Wait here.", "Male, fifties. High quality.\ngruff")],
                         "Qwen is given the composed instruction")
        self.assertEqual(engine.seeds, [7, 1, 1])
        index = json.loads((self.refs / "index.json").read_text())
        self.assertEqual(index["calibration"], "Wait here.")
        rec = index["voices"]["iroha"]
        self.assertEqual(rec["names"], ["Iroha", "Volto Oscuro"])
        self.assertEqual((rec["gender"], rec["age"], rec["race"], rec["description"]),
                         ("female", "young adult", "hume", "Warm."))
        self.assertEqual(rec["instruct"], "Female, young adult. High quality.\nWarm.")
        self.assertEqual(rec["sha256"], dv.sha256_file(self.refs / "iroha.wav"))
        self.assertEqual(index["voices"]["hume_m_adult-1"]["group"], "hume_m_adult")

    def test_changed_designs_are_listed_and_redone_only_on_request(self):
        self.run_design(make_designs())
        todo, _ = self.run_design(make_designs())
        self.assertEqual(todo, [])
        unchanged = make_designs(Iroha=dict(IROHA, race="galka"))
        self.assertEqual(dv.stale(unchanged, dv.load_index(self.refs), self.refs, list(unchanged.voices)), [],
                         "race is not in the prompt")
        self.assertEqual(dv.stale(make_designs(quality="studio"), dv.load_index(self.refs), self.refs, ["iroha"]),
                         ["iroha"], "the quality level is")
        for change in ({"seed": 8}, {"accent": "Japanese"}, {"persona": "a priestess"}, {"emotion": "grave"},
                       {"age": "forties"}, {"description": "Cool."}):
            changed = make_designs(Iroha=dict(IROHA, **change))
            with self.subTest(change=change):
                todo, _ = self.run_design(changed)
                self.assertEqual(todo, [], "a heard clip is not replaced unasked")
                self.assertEqual(dv.stale(changed, dv.load_index(self.refs), self.refs, list(changed.voices)),
                                 ["iroha"])
        changed = make_designs(Iroha=dict(IROHA, seed=8))
        todo, engine = self.run_design(changed, changed=True)
        self.assertEqual(todo, [("iroha", "changed")])
        self.assertEqual(engine.seeds, [8])
        (self.refs / "hume_m_adult-0.wav").unlink()
        todo, _ = self.run_design(changed)
        self.assertEqual(todo, [("hume_m_adult-0", "missing")])
        todo, _ = self.run_design(changed, only=["Volto Oscuro"], fresh=True)
        self.assertEqual(todo, [("iroha", "fresh")])

    def test_unknown_only_is_an_error(self):
        with self.assertRaises(SystemExit):
            dv.selected(make_designs(), ["Nobody"])

    def test_adopt_copies_heard_clips_instead_of_redesigning(self):
        spike = Path(self.dir.name) / "spike"
        spike.mkdir()
        sh.write_wav(spike / "iroha.wav", [0.1] * 4800, 24000)
        _, engine = self.run_design(make_designs(), adopt=spike)
        self.assertEqual([c[1] for c in engine.calls], ["Male, adult. High quality.\nplain", "Male, fifties. High quality.\ngruff"])
        self.assertEqual((self.refs / "iroha.wav").read_bytes(), (spike / "iroha.wav").read_bytes())
        index = json.loads((self.refs / "index.json").read_text())["voices"]
        self.assertTrue(index["iroha"]["how"].startswith("adopted from"))
        self.assertEqual(index["hume_m_adult-0"]["how"], "designed")

    def test_audit_says_what_a_run_would_do_and_why(self):
        d = make_designs()
        voices = list(d.voices)
        index = {}
        engine = FakeQwen()
        dv.design_all(d, dv.plan(d, index, self.refs, voices), self.refs, engine, index)
        rows = dv.audit(d, dv.load_index(self.refs), self.refs, voices)
        self.assertEqual([s for _, s, _ in rows], ["current"] * 3)
        self.assertEqual(rows[0][2], "designed")
        # one voice edited, one reseeded, one clip gone
        d.update("hume_m_adult-0", "plainer", 1)
        d.update("hume_m_adult-1", "gruff", 9, age="fifties")
        (self.refs / "iroha.wav").unlink()
        index = dv.load_index(self.refs)
        rows = {v: (s, dt) for v, s, dt in dv.audit(d, index, self.refs, voices)}
        self.assertEqual(rows["iroha"], ("missing", "no clip"))
        self.assertEqual(rows["hume_m_adult-0"], ("stale", "instruction"), "kept unless CHANGED=1")
        self.assertEqual(rows["hume_m_adult-1"], ("stale", "seed 1 -> 9"))
        rows = {v: s for v, s, _ in dv.audit(d, index, self.refs, voices, changed=True)}
        self.assertEqual(rows, {"iroha": "missing", "hume_m_adult-0": "changed", "hume_m_adult-1": "changed"})
        rows = {v: (s, dt) for v, s, dt in dv.audit(d, index, self.refs, voices, fresh=True)}
        self.assertEqual(rows["hume_m_adult-1"], ("fresh", "FRESH=1; seed 1 -> 9"))
        self.assertEqual(rows["iroha"][0], "missing")
        # the audit's plan is design-voices' plan
        for flags in ({}, {"changed": True}, {"fresh": True}):
            planned = [v for v, _ in dv.plan(d, index, self.refs, voices, **flags)]
            audited = [v for v, s, _ in dv.audit(d, index, self.refs, voices, **flags)
                       if s in ("missing", "changed", "fresh")]
            self.assertEqual(planned, audited, str(flags))
        self.assertEqual(dv.who_is(d, "iroha"), "Iroha, Volto Oscuro")
        self.assertEqual(dv.who_is(d, "hume_m_adult-1"), "pool hume male adult 2 of 2")
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            dv.print_audit(d, index, self.refs, dv.audit(d, index, self.refs, voices))
        text = out.getvalue()
        self.assertIn("Will design 1:", text)
        self.assertIn("Stale, kept (2; CHANGED=1 redesigns them):", text)
        self.assertIn("Current: 0", text)
        self.assertIn("iroha", text)
        self.assertIn("pool hume male adult 1 of 2", text)

    def test_review_page_lists_every_voice(self):
        d = make_designs()
        self.run_design(d, only=["Iroha"])
        with unittest.mock.patch("spike_qwen3.embedder", lambda out: lambda p: f"data:{Path(p).name}"):
            page = dv.write_review(d, self.refs, dv.load_index(self.refs))
        text = page.read_text(encoding="utf-8")
        self.assertIn("data:iroha.wav", text)
        self.assertIn("Iroha, Volto Oscuro", text)
        self.assertIn("pool: hume_m_adult", text)
        self.assertEqual(text.count("not designed yet"), 2)
        self.assertIn("Female, young adult. High quality.\nWarm.", text, "the page shows the composed instruction")

    def test_old_records_compare_by_their_whole_description(self):
        """Index records from before the meta split kept the whole instruction
        as `description`, and are compared by it."""
        d = make_designs()
        rec = {"description": "Female, young adult. High quality.\nWarm.", "seed": 7, "calibration": "Wait here."}
        self.assertEqual(dv.made_with(rec), d.compose("iroha"))
        self.assertFalse(dv.is_changed(d, "iroha", rec))
        self.assertTrue(dv.is_changed(make_designs(Iroha=dict(IROHA, age="forties")), "iroha", rec))

    def test_prompts_are_printed_for_pasting(self):
        d = make_designs(Iroha=dict(IROHA, accent="Japanese", persona="a priestess", emotion="unyielding, determined"))
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            dv.print_prompts(d, dv.selected(d, ["Volto Oscuro"]))
        self.assertEqual(out.getvalue(), "== Iroha, Volto Oscuro (iroha; hume)\n"
                                         "Native Japanese. Female, young adult. High quality.\n"
                                         "Persona: a priestess. Emotion: unyielding, determined.\nWarm.\n\n")


if __name__ == "__main__":
    unittest.main()
