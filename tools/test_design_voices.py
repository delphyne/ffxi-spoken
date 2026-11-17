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


def make_designs(**cast):
    cast = cast or {"Iroha": {"description": "Gender: female.", "seed": 7, "also": ["Volto Oscuro"]}}
    return designs.Designs({"calibration": "Wait here.", "cast": cast, "pools": {"hume_m": ["plain", "gruff"]}},
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
        todo = dv.plan(d, index, self.refs, dv.selected(d, kw.pop("only", None)), fresh=kw.pop("fresh", False))
        engine = FakeQwen()
        dv.design_all(d, todo, self.refs, engine, index, **kw)
        return todo, engine

    def test_first_run_designs_everything_with_each_seed(self):
        d = make_designs()
        todo, engine = self.run_design(d)
        self.assertEqual(todo, [("iroha", "missing"), ("hume_m-0", "missing"), ("hume_m-1", "missing")])
        self.assertEqual(engine.calls[0], ("Wait here.", "Gender: female."))
        self.assertEqual(engine.seeds, [7, 1, 1])
        index = json.loads((self.refs / "index.json").read_text())
        self.assertEqual(index["calibration"], "Wait here.")
        rec = index["voices"]["iroha"]
        self.assertEqual(rec["names"], ["Iroha", "Volto Oscuro"])
        self.assertEqual(rec["sha256"], dv.sha256_file(self.refs / "iroha.wav"))
        self.assertEqual(index["voices"]["hume_m-1"]["group"], "hume_m")

    def test_second_run_only_redoes_what_changed(self):
        self.run_design(make_designs())
        todo, _ = self.run_design(make_designs())
        self.assertEqual(todo, [])
        changed = make_designs(Iroha={"description": "Gender: female.", "seed": 8, "also": ["Volto Oscuro"]})
        todo, engine = self.run_design(changed)
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
        self.assertEqual([c[1] for c in engine.calls], ["plain", "gruff"])   # iroha was adopted
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


if __name__ == "__main__":
    unittest.main()
