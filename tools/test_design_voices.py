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

GROUPS = {"hume_m"}


IROHA = {"gender": "female", "age": "young adult", "race": "hume", "description": "Warm.", "seed": 7,
         "also": ["Volto Oscuro"]}


def make_designs(**cast):
    cast = cast or {"Iroha": IROHA}
    return designs.Designs({"calibration": "Wait here.", "races": {"hume": "", "galka": "Huge."}, "cast": cast,
                            "pools": {"hume_m": ["plain", {"age": "fifties", "description": "gruff"}]}},
                           groups=GROUPS)


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
        self.assertEqual(todo, [("iroha", "missing"), ("hume_m-0", "missing"), ("hume_m-1", "missing")])
        self.assertEqual(engine.calls, [("Wait here.", "Gender: female. Age: young adult. Warm."),
                                        ("Wait here.", "Gender: male. plain"),
                                        ("Wait here.", "Gender: male. Age: fifties. gruff")],
                         "Qwen is given the composed instruction")
        self.assertEqual(engine.seeds, [7, 1, 1])
        index = json.loads((self.refs / "index.json").read_text())
        self.assertEqual(index["calibration"], "Wait here.")
        rec = index["voices"]["iroha"]
        self.assertEqual(rec["names"], ["Iroha", "Volto Oscuro"])
        self.assertEqual((rec["gender"], rec["age"], rec["race"], rec["description"]),
                         ("female", "young adult", "hume", "Warm."))
        self.assertEqual(rec["instruct"], "Gender: female. Age: young adult. Warm.")
        self.assertEqual(rec["sha256"], dv.sha256_file(self.refs / "iroha.wav"))
        self.assertEqual(index["voices"]["hume_m-1"]["group"], "hume_m")

    def test_changed_designs_are_listed_and_redone_only_on_request(self):
        self.run_design(make_designs())
        todo, _ = self.run_design(make_designs())
        self.assertEqual(todo, [])
        for change in ({"seed": 8}, {"race": "galka"}, {"age": "forties"}, {"description": "Cool."}):
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
        (self.refs / "hume_m-0.wav").unlink()
        todo, _ = self.run_design(changed)
        self.assertEqual(todo, [("hume_m-0", "missing")])
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
        self.assertEqual([c[1] for c in engine.calls], ["Gender: male. plain", "Gender: male. Age: fifties. gruff"])
        self.assertEqual((self.refs / "iroha.wav").read_bytes(), (spike / "iroha.wav").read_bytes())
        index = json.loads((self.refs / "index.json").read_text())["voices"]
        self.assertTrue(index["iroha"]["how"].startswith("adopted from"))
        self.assertEqual(index["hume_m-0"]["how"], "designed")

    def test_review_page_lists_every_voice(self):
        d = make_designs()
        self.run_design(d, only=["Iroha"])
        with unittest.mock.patch("spike_qwen3.embedder", lambda out: lambda p: f"data:{Path(p).name}"):
            page = dv.write_review(d, self.refs, dv.load_index(self.refs))
        text = page.read_text(encoding="utf-8")
        self.assertIn("data:iroha.wav", text)
        self.assertIn("Iroha, Volto Oscuro", text)
        self.assertIn("pool: hume_m", text)
        self.assertEqual(text.count("not designed yet"), 2)
        self.assertIn("Gender: female. Age: young adult. Warm.", text, "the page shows the composed instruction")

    def test_old_records_compare_by_their_whole_description(self):
        """Index records from before the meta split kept the whole instruction
        as `description`; a hume's composed instruction equals it, so its clip
        stays current."""
        d = make_designs()
        rec = {"description": "Gender: female. Age: young adult. Warm.", "seed": 7, "calibration": "Wait here."}
        self.assertEqual(dv.made_with(rec), d.compose("iroha"))
        self.assertFalse(dv.is_changed(d, "iroha", rec))
        self.assertTrue(dv.is_changed(make_designs(Iroha=dict(IROHA, race="galka")), "iroha", rec))


if __name__ == "__main__":
    unittest.main()
