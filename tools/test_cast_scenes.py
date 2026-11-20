"""Tests for tools/cast_scenes.py: python3 -I -m unittest discover -s tools -p 'test_*.py'"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "helper"))
import cast_scenes  # noqa: E402
import casting  # noqa: E402

GROUP = "hume_m_adult"
POOL = [f"{GROUP}-0", f"{GROUP}-1"]


def designs(pools, own=()):
    return SimpleNamespace(pools=pools, voice_for_speaker=lambda name: name.lower() if name in own else None)


class Scenes(unittest.TestCase):
    def test_scenes_come_from_voiced_lines_under_whoever_speaks_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [{"speaker": "Ann", "class": "dialogue", "sources": [{"zone": 1, "event": 2}, {"zone": 3, "event": 2}]},
                    {"speaker": "???", "as": "Bob", "class": "dialogue", "sources": [{"zone": 1, "event": 2}]},
                    {"speaker": "Cy", "class": "silent", "sources": [{"zone": 1, "event": 2}]},
                    {"speaker": "", "class": "narration", "sources": [{"zone": 1, "event": 2}]}]
            (Path(tmp) / "missions").mkdir()
            (Path(tmp) / "missions" / "x.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            self.assertEqual(dict(cast_scenes.scenes_of(tmp)), {(1, 2): {"Ann", "Bob"}, (3, 2): {"Ann"}})

    def solve(self, scenes, pools=None, own=(), moves=None, base=None):
        census = {n: {"race": 1} for s in scenes.values() for n in s}
        return cast_scenes.solve(scenes, designs(pools or {GROUP: POOL}, own), moves or {}, census, base or {})

    def test_two_in_a_scene_are_split_and_nobody_else_moves(self):
        names = ["Ann", "Bob", "Cy", "Di"]
        base = {n: POOL[0] for n in names}
        pins, before, after = self.solve({(1, 1): {"Ann", "Bob"}, (1, 2): {"Cy"}, (1, 3): {"Di", "Own"}}, own={"Own"}, base=base)
        self.assertEqual(len(before), 1)
        self.assertEqual(after, [])
        self.assertEqual(len(pins), 1, "one of the pair moves; the rest stay")
        self.assertEqual(list(pins.values()), [(GROUP, 1)])
        self.assertLess(set(pins), {"Ann", "Bob"})

    def test_a_scene_the_pool_can_cover_wins_over_one_it_cannot(self):
        base = {n: POOL[0] for n in "ABCDE"}
        scenes = {(1, 1): {"A", "B"}, (1, 2): {"A", "C", "D"}, (1, 3): {"B", "C", "E"}}
        pins, before, after = self.solve(scenes, base=base)
        self.assertEqual(len(before), 3)
        self.assertEqual([s for s, _, _ in after], [(1, 2), (1, 3)], "three on two voices must double up")
        final = {n: pins.get(n, (GROUP, 0))[1] for n in "AB"}
        self.assertNotEqual(final["A"], final["B"])

    def test_a_pool_of_one_and_other_groups_are_left(self):
        pins, before, after = self.solve({(1, 1): {"Ann", "Bob"}}, pools={GROUP: POOL[:1]}, base={})
        self.assertEqual((pins, len(before), len(after)), ({}, 1, 1))
        pins, before, after = self.solve({(1, 1): {"Ann", "Bob"}}, pools={GROUP: POOL, "small": ["small-0"]},
                                         moves={"Bob": "small"}, base={"Ann": POOL[0], "Bob": "small-0"})
        self.assertEqual((pins, before), ({}, []), "different groups never share a pool voice")
        pins, before, _ = self.solve({(1, 1): {"Ann", "Bob"}}, moves={"Bob": "silent"}, base={"Ann": POOL[0]})
        self.assertEqual((pins, before), ({}, []))

    def test_the_census_hash_is_the_start_for_the_unlisted(self):
        a, b = "Ann", "Bob"
        pins, before, after = self.solve({(1, 1): {a, b}})
        same = casting.pick(POOL, a) == casting.pick(POOL, b)
        self.assertEqual((len(before), len(pins), after), (int(same), int(same), []))

    def test_report(self):
        lines = []
        cast_scenes.report([1, 2], [((1, 2), GROUP, {0: ["A", "B"], 1: ["C"]})], designs({GROUP: POOL}), lines.append)
        self.assertEqual(lines[0], "scenes where two speakers share a pool voice: 2 before, 1 after")
        self.assertIn("pool 2", lines[2])
        self.assertIn("3: 1", lines[2])


if __name__ == "__main__":
    unittest.main()
