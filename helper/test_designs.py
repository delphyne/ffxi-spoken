"""voice_designs.yaml: loading, validation, the meta and the instruction it
composes, editing and writing back, and how the MOSS engine casts from it
(cast.py's moss section and the census fallback)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import casting  # noqa: E402
import designs  # noqa: E402

GROUPS = {"hume_m_adult", "narrator"}
RACES = {"hume": "", "galka": "A huge beast-man.", "narrator": "", "mithra": "A cat-woman."}


def minimal(**override):
    data = {
        "calibration": "Wait here.\n  Do not look back.",
        "races": RACES,
        "cast": {"Iroha": {"gender": "female", "age": "young adult", "race": "hume",
                           "description": "Warm.", "seed": 7, "also": ["Volto Oscuro"]},
                 "Grav'iton": {"gender": "male", "race": "galka", "description": "Cold."}},
        "pools": {"hume_m_adult": [{"age": "thirties", "description": "one"}, "two"], "narrator": ["storyteller"]},
    }
    data.update(override)
    return data


class DesignsTest(unittest.TestCase):
    def test_voices_speakers_and_pools(self):
        d = designs.Designs(minimal(), groups=GROUPS)
        self.assertEqual(d.calibration, "Wait here. Do not look back.")
        self.assertEqual(d.speakers, {"Iroha": "iroha", "Volto Oscuro": "iroha", "Grav'iton": "grav-iton"})
        self.assertEqual(d.pools, {"hume_m_adult": ["hume_m_adult-0", "hume_m_adult-1"], "narrator": ["narrator-0"]})
        self.assertEqual(d.voices["iroha"], {"kind": "cast", "names": ["Iroha", "Volto Oscuro"], "gender": "female",
                                             "age": "young adult", "race": "hume", "description": "Warm.", "seed": 7})
        self.assertEqual((d.voices["grav-iton"]["seed"], d.voices["grav-iton"]["age"]), (1, ""))
        self.assertEqual(d.voices["hume_m_adult-0"], {"kind": "pool", "group": "hume_m_adult", "gender": "male",
                                                "age": "thirties", "race": "hume", "description": "one", "seed": 1})
        self.assertEqual(d.voices["hume_m_adult-1"]["age"], "adult", "a bare string is a description; the age is the band's")
        self.assertEqual(d.voice_for_speaker("Volto Oscuro"), "iroha")
        self.assertIsNone(d.voice_for_speaker("Nobody"))

    def test_compose(self):
        d = designs.Designs(minimal(), groups=GROUPS)
        self.assertEqual(d.compose("iroha"), "Gender: female. Age: young adult. Warm.")
        self.assertEqual(d.compose("grav-iton"), "Gender: male. A huge beast-man. Cold.", "no age, a race phrase")
        self.assertEqual(d.compose("hume_m_adult-0"), "Gender: male. Age: thirties. one")
        self.assertEqual(d.compose("iroha", age="", race="galka", description="Gruff."),
                         "Gender: female. A huge beast-man. Gruff.", "a draft overrides the saved design")
        self.assertEqual(d.compose(gender="unknown", age="", race="hume", description="Squeaky."), "Squeaky.")

    def test_slugs(self):
        self.assertEqual(designs.voice_slug("Cait Sith"), "cait-sith")
        self.assertEqual(designs.voice_slug("Grav'iton"), "grav-iton")
        self.assertEqual(designs.voice_slug("???  Lord"), "lord")
        with self.assertRaises(ValueError):
            designs.voice_slug("???")

    def test_rejections_name_the_problem(self):
        iroha = minimal()["cast"]["Iroha"]
        cases = [
            (minimal(calibration=""), "calibration"),
            (minimal(extra=1), "unknown section"),
            (minimal(races=["hume"]), "races must map"),
            (minimal(cast={"Iroha": "just a string"}), "must be a mapping"),
            (minimal(cast={"Iroha": dict(iroha, description="")}), "no description"),
            (minimal(cast={"Iroha": dict(iroha, seed="7")}), "whole number"),
            (minimal(cast={"Iroha": dict(iroha, seeds=7)}), "unknown key"),
            (minimal(cast={"Iroha": dict(iroha, race="moogle")}), "races section does not list"),
            (minimal(cast={"Iroha": dict(iroha, description="Gender: female. Warm.")}), "belongs in the meta"),
            (minimal(cast={"Iroha": dict(iroha), "Lion": {"description": "y", "also": ["Iroha"]}}), "cast twice"),
            (minimal(cast={"Cait Sith": {"description": "x"}, "Cait-Sith": {"description": "y"}}), "already has"),
            (minimal(pools={"hume_m_adult": ["a"], "narrator": ["b"], "orc": ["c"]}), "not a casting group"),
            (minimal(pools={"hume_m_adult": []}), "at least one"),
            (minimal(pools={"hume_m_adult": [{"description": "a", "gender": "female"}], "narrator": ["b"]}), "unknown key"),
            (minimal(pools={"hume_m_adult": ["a"]}), "no pool for casting group(s) narrator"),
        ]
        for data, message in cases:
            with self.subTest(message=message), self.assertRaises(ValueError) as cm:
                designs.Designs(data, groups=GROUPS)
            self.assertIn(message, str(cm.exception))

    def test_load_reads_yaml(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.yaml"
            p.write_text("calibration: Hello there.\nraces: {galka: Huge., hume: "", narrator: ""}\ncast:\n  Zeid:\n    gender: male\n"
                         "    race: galka\n    description: Deep.\n"
                         "pools:\n  hume_m_adult: [a]\n  narrator: [b]\n", encoding="utf-8")
            loaded = designs.Designs.load(p, groups=GROUPS)
        self.assertEqual(loaded.compose("zeid"), "Gender: male. Huge. Deep.")

    def test_committed_file_covers_every_casting_group(self):
        d = designs.Designs.load(designs.DEFAULT_PATH, groups=set(casting.POOLS))
        self.assertEqual(set(d.pools), set(casting.POOLS))
        self.assertTrue(d.calibration.endswith("look behind you."))
        # the listening spike's voices keep the seeds their clips were drawn with
        self.assertEqual(d.voices["tenzen"]["seed"], 1862)
        # gender, age, the hume phrase as it stands (edited in the review), then the description
        hume = d.races["hume"] + " " if d.races["hume"] else ""
        self.assertEqual(d.compose("tenzen"), f"Gender: male. Age: middle-aged. {hume}A calm, low, dignified "
                                              "samurai's voice with a slight Japanese accent; unhurried, precise, formal.")
        races = {designs.race_gender(g)[0] for g in casting.POOLS}
        self.assertEqual(races - set(d.races), set(), "every group's race has a phrase entry")


class EditAndDumpTest(unittest.TestCase):
    def test_race_gender(self):
        self.assertEqual(designs.race_gender("taru_f_child"), ("tarutaru", "female"))
        self.assertEqual(designs.race_gender(None), ("unknown", "unknown"))
        self.assertEqual(set(designs.GROUP_INFO), set(casting.POOLS), "every casting group has a race and gender")

    def test_update_and_add_cast(self):
        d = designs.Designs(minimal(), groups=GROUPS)
        d.update("iroha", "  New   words. ", 9, age="thirties")
        v = d.voices["iroha"]
        self.assertEqual((v["description"], v["seed"], v["age"], v["gender"]), ("New words.", 9, "thirties", "female"))
        with self.assertRaises(ValueError):
            d.update("iroha", "", 9)
        d.update("hume_m_adult-0", "Plain.", 2, gender="female", age="forties", race="galka")
        p = d.voices["hume_m_adult-0"]
        self.assertEqual((p["gender"], p["race"], p["age"]), ("male", "hume", "forties"),
                         "a pool voice keeps its group's gender and race")
        voice = d.add_cast("Numi Adaligo", "Sly.", 3, gender="female", age="twenties", race="mithra")
        self.assertEqual((voice, d.speakers["Numi Adaligo"]), ("numi-adaligo", "numi-adaligo"))
        self.assertEqual(d.compose(voice), "Gender: female. Age: twenties. A cat-woman. Sly.")
        with self.assertRaises(ValueError):
            d.add_cast("Volto Oscuro", "x")   # already shares Iroha's voice
        d.update_race("mithra", "  A lithe  cat-woman. ")
        self.assertEqual(d.compose(voice), "Gender: female. Age: twenties. A lithe cat-woman. Sly.")
        d.update_race("mithra", "")
        self.assertEqual(d.compose(voice), "Gender: female. Age: twenties. Sly.")
        with self.assertRaises(ValueError):
            d.update_race("moogle", "Small.")

    def test_dump_round_trips_and_keeps_the_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "v.yaml"
            p.write_text("# keep me\n\ncalibration: x\ncast: {}\npools: {hume_m_adult: [a], narrator: [b]}\n")
            d = designs.Designs(minimal(cast={"#7 Guard": {"description": "A gate guard.", "race": "hume"},
                                              "Cait Sith": {"description": "Prim.", "also": [f"Cait Sith {n}"
                                                            for n in range(12)]}}), groups=GROUPS)
            d.add_cast("Grav'iton", "Cold. " * 30, 5, gender="male", age="ageless", race="galka")
            d.dump(p)
            text = p.read_text(encoding="utf-8")
            again = designs.Designs.load(p, groups=GROUPS)
        self.assertTrue(text.startswith("# keep me\n\ncalibration: >-\n"))
        self.assertEqual((again.voices, again.speakers, again.pools, again.calibration, again.races),
                         (d.voices, d.speakers, d.pools, d.calibration, d.races))
        self.assertIn('"#7 Guard":', text, "a key YAML would misread as a comment is quoted")
        self.assertIn('    - age: thirties\n      description: "one"', text)

    def test_dump_of_the_committed_file_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "v.yaml"
            original = designs.DEFAULT_PATH.read_text(encoding="utf-8")
            p.write_text(original, encoding="utf-8")
            designs.Designs.load(p, groups=set(casting.POOLS)).dump(p)
            rewritten = p.read_text(encoding="utf-8")
        self.assertEqual(rewritten, original, "the committed file is already in the form dump writes")


class MossCastingTest(unittest.TestCase):
    def setUp(self):
        self.d = designs.Designs(minimal(races={r: "" for r in {designs.race_gender(g)[0] for g in casting.POOLS}},
                                         cast={"Iroha": {"description": "Warm.", "also": ["Volto Oscuro"]},
                                               "Grav'iton": {"description": "Cold."}},
                                         pools={g: [f"{g} voice"] * (3 if g == "galka_m_adult" else 1)
                                                for g in casting.POOLS}), groups=set(casting.POOLS))

    def test_pool_pick_is_stable_and_in_the_group(self):
        v = casting.voice_for_group("galka_m_adult", "Gumbah", "moss", designs=self.d)
        self.assertIn(v, self.d.pools["galka_m_adult"])
        self.assertEqual(v, casting.voice_for_group("galka_m_adult", "Gumbah", "moss", designs=self.d))
        self.assertIsNone(casting.voice_for_group("orc", "Gumbah", "moss", designs=self.d))
        self.assertIsNone(casting.voice_for_group("galka_m_adult", "Gumbah", "festival", designs=self.d))

    def test_census_fallback_uses_the_designed_pools(self):
        census = {"Numi Adaligo": {"race": 7}}
        self.assertEqual(casting.census_voice(census, "Numi Adaligo", "moss"),
                         casting.pick(casting.moss_pools()["mithra_f_adult"], "Numi Adaligo"))

    def test_cast_moss_section(self):
        import cast
        out = cast.build(self.d)["moss"]
        self.assertEqual(out["Iroha"], "iroha")              # designed
        self.assertEqual(out["Volto Oscuro"], "iroha")       # `also`
        self.assertEqual(out["Grav'iton"], "grav-iton")
        self.assertEqual(out[""], "narrator-0")
        self.assertEqual(out["default"], "hume_m_adult-0")
        self.assertIn(out["Gumbah"], self.d.pools["galka_m_adult"])  # cast but not designed: the group's pool
        variants = [out[n] for n in ("Shantotto", "D. Shantotto")]
        self.assertEqual(len(set(variants)), 1, "variants of one character share a voice")


if __name__ == "__main__":
    unittest.main()
