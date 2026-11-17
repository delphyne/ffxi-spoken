"""voice_designs.yaml: loading, validation, and how the MOSS engine casts
from it (cast.py's moss section and the census fallback)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import casting  # noqa: E402
import designs  # noqa: E402

GROUPS = {"hume_m", "narrator"}


def minimal(**override):
    data = {
        "calibration": "Wait here.\n  Do not look back.",
        "cast": {"Iroha": {"description": "Gender: female.", "seed": 7, "also": ["Volto Oscuro"]},
                 "Grav'iton": {"description": "Gender: male."}},
        "pools": {"hume_m": ["one", "two"], "narrator": ["storyteller"]},
    }
    data.update(override)
    return data


class DesignsTest(unittest.TestCase):
    def test_voices_speakers_and_pools(self):
        d = designs.Designs(minimal(), groups=GROUPS)
        self.assertEqual(d.calibration, "Wait here. Do not look back.")
        self.assertEqual(d.speakers, {"Iroha": "iroha", "Volto Oscuro": "iroha", "Grav'iton": "grav-iton"})
        self.assertEqual(d.pools, {"hume_m": ["hume_m-0", "hume_m-1"], "narrator": ["narrator-0"]})
        self.assertEqual(d.voices["iroha"], {"kind": "cast", "names": ["Iroha", "Volto Oscuro"],
                                             "description": "Gender: female.", "seed": 7})
        self.assertEqual(d.voices["grav-iton"]["seed"], 1)
        self.assertEqual(d.voices["hume_m-1"], {"kind": "pool", "group": "hume_m", "description": "two", "seed": 1})
        self.assertEqual(d.voice_for_speaker("Volto Oscuro"), "iroha")
        self.assertIsNone(d.voice_for_speaker("Nobody"))

    def test_slugs(self):
        self.assertEqual(designs.voice_slug("Cait Sith"), "cait-sith")
        self.assertEqual(designs.voice_slug("Grav'iton"), "grav-iton")
        self.assertEqual(designs.voice_slug("???  Lord"), "lord")
        with self.assertRaises(ValueError):
            designs.voice_slug("???")

    def test_rejections_name_the_problem(self):
        cases = [
            (minimal(calibration=""), "calibration"),
            (minimal(extra=1), "unknown section"),
            (minimal(cast={"Iroha": "just a string"}), "must be a mapping"),
            (minimal(cast={"Iroha": {"description": ""}}), "no description"),
            (minimal(cast={"Iroha": {"description": "x", "seed": "7"}}), "whole number"),
            (minimal(cast={"Iroha": {"description": "x", "seeds": 7}}), "unknown key"),
            (minimal(cast={"Iroha": {"description": "x"}, "Lion": {"description": "y", "also": ["Iroha"]}}),
             "cast twice"),
            (minimal(cast={"Cait Sith": {"description": "x"}, "Cait-Sith": {"description": "y"}}), "already has"),
            (minimal(pools={"hume_m": ["a"], "narrator": ["b"], "orc": ["c"]}), "not a casting group"),
            (minimal(pools={"hume_m": []}), "at least one"),
            (minimal(pools={"hume_m": ["a"]}), "no pool for casting group(s) narrator"),
        ]
        for data, message in cases:
            with self.subTest(message=message), self.assertRaises(ValueError) as cm:
                designs.Designs(data, groups=GROUPS)
            self.assertIn(message, str(cm.exception))

    def test_load_reads_yaml(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.yaml"
            p.write_text("calibration: Hello there.\ncast:\n  Zeid:\n    description: Deep.\n"
                         "pools:\n  hume_m: [a]\n  narrator: [b]\n", encoding="utf-8")
            loaded = designs.Designs.load(p, groups=GROUPS)
        self.assertEqual(loaded.speakers, {"Zeid": "zeid"})

    def test_committed_file_covers_every_casting_group(self):
        d = designs.Designs.load(designs.DEFAULT_PATH, groups=set(casting.POOLS))
        self.assertEqual(set(d.pools), set(casting.POOLS))
        self.assertTrue(d.calibration.endswith("look behind you."))
        # the listening spike's voices keep the seeds their clips were drawn with
        self.assertEqual(d.voices["tenzen"]["seed"], 1862)


class EditAndDumpTest(unittest.TestCase):
    def test_group_metadata_and_race_gender(self):
        data = minimal(cast={"Ekokoko": {"description": "Chirpy.", "group": "hume_m"}})
        self.assertEqual(designs.Designs(data, groups=GROUPS).voices["ekokoko"]["group"], "hume_m")
        with self.assertRaises(ValueError):
            designs.Designs(minimal(cast={"E": {"description": "x", "group": "orc"}}), groups=GROUPS)
        self.assertEqual(designs.race_gender("taru_f"), ("tarutaru", "female"))
        self.assertEqual(designs.race_gender(None), ("unknown", "unknown"))
        self.assertEqual(set(designs.GROUP_INFO), set(casting.POOLS), "every casting group has a race and gender")

    def test_update_and_add_cast(self):
        d = designs.Designs(minimal(), groups=GROUPS)
        d.update("iroha", "  New   words. ", 9)
        self.assertEqual((d.voices["iroha"]["description"], d.voices["iroha"]["seed"]), ("New words.", 9))
        with self.assertRaises(ValueError):
            d.update("iroha", "", 9)
        voice = d.add_cast("Numi Adaligo", "Sly.", 3, "hume_m")
        self.assertEqual((voice, d.speakers["Numi Adaligo"]), ("numi-adaligo", "numi-adaligo"))
        with self.assertRaises(ValueError):
            d.add_cast("Volto Oscuro", "x")   # already shares Iroha's voice

    def test_dump_round_trips_and_keeps_the_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "v.yaml"
            p.write_text("# keep me\n\ncalibration: x\ncast: {}\npools: {hume_m: [a], narrator: [b]}\n")
            d = designs.Designs(minimal(cast={"#7 Guard": {"description": "A gate guard."},
                                              "Cait Sith": {"description": "Prim.", "also": [f"Cait Sith {n}"
                                                            for n in range(12)]}}), groups=GROUPS)
            d.add_cast("Grav'iton", "Cold. " * 30, 5, "hume_m")
            d.dump(p)
            text = p.read_text(encoding="utf-8")
            again = designs.Designs.load(p, groups=GROUPS)
        self.assertTrue(text.startswith("# keep me\n\ncalibration: >-\n"))
        self.assertEqual((again.voices, again.speakers, again.pools, again.calibration),
                         (d.voices, d.speakers, d.pools, d.calibration))
        self.assertTrue(all(len(line) <= designs.WIDTH for line in text.splitlines()), "lines stay wrapped")
        self.assertIn('"#7 Guard":', text, "a key YAML would misread as a comment is quoted")

    def test_dump_of_the_committed_file_changes_nothing_it_means(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "v.yaml"
            p.write_text(designs.DEFAULT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            d = designs.Designs.load(p, groups=set(casting.POOLS))
            d.dump(p)
            again = designs.Designs.load(p, groups=set(casting.POOLS))
            header = p.read_text(encoding="utf-8").split("\ncalibration:")[0]
        self.assertEqual(again.voices, d.voices)
        self.assertIn("# Voice designs for the MOSS backend", header)


class MossCastingTest(unittest.TestCase):
    def setUp(self):
        self.d = designs.Designs(minimal(pools={g: [f"{g} voice"] * (3 if g == "galka" else 1)
                                                for g in casting.POOLS}), groups=set(casting.POOLS))

    def test_pool_pick_is_stable_and_in_the_group(self):
        v = casting.voice_for_group("galka", "Gumbah", "moss", designs=self.d)
        self.assertIn(v, self.d.pools["galka"])
        self.assertEqual(v, casting.voice_for_group("galka", "Gumbah", "moss", designs=self.d))
        self.assertIsNone(casting.voice_for_group("orc", "Gumbah", "moss", designs=self.d))
        self.assertIsNone(casting.voice_for_group("galka", "Gumbah", "festival", designs=self.d))

    def test_census_fallback_uses_the_designed_pools(self):
        census = {"Numi Adaligo": {"race": 7}}
        self.assertEqual(casting.census_voice(census, "Numi Adaligo", "moss"),
                         casting.pick(casting.moss_pools()["mithra"], "Numi Adaligo"))

    def test_cast_moss_section(self):
        import cast
        out = cast.build(self.d)["moss"]
        self.assertEqual(out["Iroha"], "iroha")              # designed
        self.assertEqual(out["Volto Oscuro"], "iroha")       # `also`
        self.assertEqual(out["Grav'iton"], "grav-iton")
        self.assertEqual(out[""], "narrator-0")
        self.assertEqual(out["default"], "hume_m-0")
        self.assertIn(out["Gumbah"], self.d.pools["galka"])  # cast but not designed: the group's pool
        variants = [out[n] for n in ("Shantotto", "D. Shantotto")]
        self.assertEqual(len(set(variants)), 1, "variants of one character share a voice")


if __name__ == "__main__":
    unittest.main()
