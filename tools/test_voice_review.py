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
    gender: female
    age: young adult
    race: hume
    description: Warm.
    seed: 7
    also: [Volto Oscuro]
  Ekokoko:
    gender: female
    race: tarutaru
    description: Small and chirpy.

pools:
""" + "".join(f"  {g}:\n    - age: thirties\n      description: \"{g} voice\"\n"
              + ("    - \"second hume\"\n" if g == "hume_m_adult" else "") for g in casting.POOLS)


class FakeEngine:
    def __init__(self):
        self.calls = []

    def manual_seed(self, seed):
        self.calls.append(("seed", seed))

    def design(self, text, description):
        self.calls.append(("design", text, description))
        return [0.25] * 2400, 24000


class FakeWorker:
    """Stands in for the MOSS worker: records requests and writes a WAV."""

    def __init__(self):
        self.requests, self.starts, self.closed = [], 0, False

    def start(self):
        self.starts += 1
        self.closed = False
        return self

    def say(self, request):
        self.requests.append(request)
        out = Path(request["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"RIFF")
        return out

    def close(self):
        self.closed = True


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
        self.voices_json.write_text(json.dumps(cast.build(d, moves={})), encoding="utf-8")
        census = root / "census.json"
        census.write_text(json.dumps({"Numi Adaligo": {"race": 7}, "Tonasav": {"race": 1}}))
        stories = root / "stories.json"
        stories.write_text(json.dumps({"stories": {"rov": "Rhapsodies of Vana'diel", "cop": "Chains of Promathia"},
                                       "speakers": {"Iroha": {"rov": 300}, "Numi Adaligo": {"rov": 19},
                                                    "Tonasav": {"rov": 3, "cop": 2}, "Prishe": {"cop": 400},
                                                    "": {"rov": 6}}}))
        lines = root / "lines.json"   # data/voice_lines.json: kept apart, never committed
        lines.write_text(json.dumps({"Iroha": ["Master, Vana'diel needs you.", "<Sigh> Follow me, <player>."],
                                     "Numi Adaligo": ["Welcome to Windurst Waters."],
                                     "Tonasav": ["The guards are asleep again."]}))
        self.engine = FakeEngine()
        self.worker = FakeWorker()
        lexicon = root / "lexicon.yaml"
        lexicon.write_text("Vana'diel:\n    ipa: \"vɑnədiˈɛl\"\n", encoding="utf-8")
        self.moves = root / "cast_groups.yaml"
        self.moves.write_text("# moves, kept\n\n{}\n", encoding="utf-8")
        self.ses = vr.Session(self.yaml, self.refs, self.voices_json, census, stories,
                              engine_factory=lambda: self.engine, rng=random.Random(4),
                              worker_factory=lambda: self.worker.start(), lexicon=lexicon, lines=lines,
                              moves=self.moves, groups_file=root / "casting_groups.json")
        self.groups_file = root / "casting_groups.json"
        self.built_in = set(casting.POOLS)

    def tearDown(self):
        for group in set(casting.POOLS) - self.built_in:   # a race a test added
            del casting.POOLS[group], casting.SPECIAL[group]
        designs.refresh_groups()
        self.dir.cleanup()

    def labels(self, **kw):
        return [r["label"] for r in self.ses.filter(**kw)]

    def test_catalogue_resolves_voices_groups_and_stories(self):
        iroha = self.ses.row("speaker:Iroha")
        self.assertEqual((iroha["voice"], iroha["kind"], iroha["race"], iroha["gender"], iroha["age"], iroha["lines"]),
                         ("iroha", "own voice", "hume", "female", "young adult", 300))
        self.assertEqual((iroha["via"], iroha["parent"]), ("own", "race:hume"))
        numi = self.ses.row("speaker:Numi Adaligo")   # census race 7: a mithra pool voice
        self.assertEqual((numi["kind"], numi["race"], numi["gender"]), ("on a pool voice", "mithra", "female"))
        self.assertTrue(numi["voice"].startswith("mithra_f_adult-"))
        self.assertEqual(numi["age"], "thirties", "a pool speaker's age is their pool voice's")
        self.assertEqual((numi["via"], numi["parent"]), ("cast.py", f"voice:{numi['voice']}"))
        tonasav = self.ses.row("speaker:Tonasav")      # not in cast.py: the census race byte casts them
        self.assertEqual((tonasav["via"], tonasav["kind"], tonasav["race"]), ("census", "on a pool voice", "hume"))
        self.assertIn("the census gives Tonasav race 1, group hume_m_adult; that puts them on voice ",
                      tonasav["how"])
        self.assertIn(" of 2 in that pool, hume_m_adult-", tonasav["how"])
        eko = self.ses.row("speaker:Ekokoko")         # race and gender from the design's own meta
        self.assertEqual((eko["race"], eko["gender"]), ("tarutaru", "female"))
        self.assertEqual(self.ses.row("speaker:")["label"], vr.NARRATION)
        self.assertEqual(self.ses.row("speaker:")["via"], "voices.json", "narration is cast by voices.json alone")
        pool = self.ses.row(f"voice:{numi['voice']}")
        self.assertEqual((pool["kind"], pool["stories"], pool["via"], pool["parent"]),
                         ("pool voice", {"rov": 19}, "mithra_f_adult", "race:mithra"))
        self.assertIn("Numi Adaligo", self.ses.users[numi["voice"]])
        self.assertEqual(self.labels(kind="on a pool voice")[0], "Prishe", "most-spoken first")
        prishe = self.ses.row("speaker:Prishe")   # in the hand cast, no design: the group's pool
        self.assertEqual(prishe["via"], "cast.py")
        self.assertIn("cast.py puts Prishe in group elvaan_f_teen", prishe["how"])
        self.assertEqual(self.ses.row(f"voice:{prishe['voice']}")["lines"], 400, "a pool counts its speakers' lines")
        self.assertEqual(self.ses.row("speaker:Volto Oscuro")["via"], "shares")
        mithra = self.ses.row("race:mithra")        # a race counts everything under it
        self.assertEqual((mithra["kind"], mithra["parent"], mithra["lines"]), ("race", "", 19))
        self.assertNotIn("phrase", mithra, "a race is a heading; it has no prompt of its own")
        self.assertEqual(len(self.ses.race_voices("mithra")), 5, "every mithra pool voice, one per band")
        self.assertEqual(self.ses.row("race:hume")["lines"], 300 + 3 + 2,
                         "Iroha's own voice and Tonasav on a hume pool; narration is its own race")
        shown = self.ses.with_parents([numi])
        self.assertEqual(set(shown), {"race:mithra", f"voice:{numi['voice']}", "speaker:Numi Adaligo"},
                         "a row comes with the rows above it")

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
        self.assertEqual(self.labels(story="rov", kind="on a pool voice"), ["Numi Adaligo", vr.NARRATION, "Tonasav"],
                         "sorted by lines in the chosen storyline")
        self.assertEqual(self.labels(story="cop", kind="on a pool voice"), ["Prishe", "Tonasav"])
        self.assertEqual(self.labels(story="cop", kind="race"), ["elvaan", "hume"])
        cop = self.labels(story="cop")
        self.assertIn("Prishe", cop)
        self.assertNotIn("Iroha", cop)
        self.assertTrue(all(r["gender"] == "female" for r in self.ses.filter(gender="female")))
        self.assertTrue(all(r["race"] == "mithra" for r in self.ses.filter(race="mithra")))
        self.assertTrue(all(r["kind"] == "pool voice" for r in self.ses.filter(kind="pool voice")))
        self.assertEqual(self.labels(query="mithra female adult", kind="pool voice"), ["pool mithra female adult 1 of 1"])
        self.assertEqual(len(self.labels(query="mithra", kind="pool voice")), 5, "one pool per age band")
        opts = self.ses.options()
        self.assertIn(("cop", "Chains of Promathia"), opts["story"])
        self.assertIn(("mithra", "mithra"), opts["race"])
        self.assertEqual(opts["kind"][0], ("all", "all kinds"))
        self.assertIn("tarutaru", opts["meta_race"])
        self.assertIn("young adult", opts["meta_age"])
        self.assertEqual(opts["meta_gender"], ["female", "male", "unknown"])
        self.assertNotIn(("", ""), opts["gender"], "a race row's blank gender is not a choice")

    def test_state_composes_the_instruction(self):
        st = self.ses.state("iroha")
        self.assertEqual((st["gender"], st["age"], st["race"]), ("female", "young adult", "hume"))
        self.assertEqual(st["instruct"], "Female, young adult. High quality.\nWarm.")
        self.assertEqual(st["saved_instruct"], st["instruct"])
        self.assertEqual(self.ses.compose("iroha", "Brisk.", gender="female", age="", race="mithra"),
                         "Female. High quality.\nBrisk.", "what the prompt popout shows as you type")
        self.assertEqual(self.ses.compose("mithra_f_adult-0", "Sly.", gender="male", age="forties", race="hume"),
                         "Female, forties. High quality.\nSly.", "a pool voice keeps its group's meta")
        self.assertIsNone(st["clip_instruct"])
        self.assertFalse(st["stale"])

    def test_audition_then_accept_writes_clip_index_and_yaml(self):
        take = self.ses.audition("iroha", "Warmer.", age="thirties")
        self.assertEqual(self.engine.calls, [("seed", 7), ("design", "Wait here.",
                                                            "Female, thirties. High quality.\nWarmer.")])
        self.assertEqual(take, self.refs / ".candidates" / "iroha.7.wav")
        st = self.ses.state("iroha")
        self.assertEqual((st["description"], st["saved_description"], st["age"]), ("Warmer.", "Warm.", "thirties"))
        self.assertEqual(st["candidate"], take)
        # meanwhile someone edits another voice in the file by hand
        self.yaml.write_text(self.yaml.read_text().replace("Small and chirpy.", "Small, chirpy, hand-edited."))
        clip = self.ses.accept("iroha")
        self.assertEqual(clip.read_bytes(), take.read_bytes())
        text = self.yaml.read_text()
        self.assertTrue(text.startswith("# header comment that must survive\n# a second line\n"))
        self.assertIn("Warmer.", text)
        self.assertIn("Small, chirpy, hand-edited.", text, "only the accepted voice changes")
        saved = designs.Designs.load(self.yaml, groups=set(casting.POOLS))
        self.assertEqual((saved.voices["iroha"]["seed"], saved.voices["iroha"]["age"]), (7, "thirties"))
        self.assertEqual(saved.voices["ekokoko"]["race"], "tarutaru", "meta survives a save")
        rec = dv.load_index(self.refs)["iroha"]
        self.assertEqual((rec["description"], rec["instruct"], rec["how"], rec["sha256"]),
                         ("Warmer.", "Female, thirties. High quality.\nWarmer.", "voice review", dv.sha256_file(clip)))
        st = self.ses.state("iroha")
        self.assertFalse(st["pending"])
        self.assertEqual(st["clip_instruct"], "Female, thirties. High quality.\nWarmer.")
        self.assertFalse(st["stale"])

    def test_a_clip_from_an_older_design_is_marked_stale(self):
        self.ses.audition("iroha", "Warm.")
        self.ses.accept("iroha")
        text = self.yaml.read_text().replace("    age: young adult\n    race: hume\n", "    age: thirties\n    race: hume\n")
        self.yaml.write_text(text)
        self.ses.reload()
        st = self.ses.state("iroha")
        self.assertTrue(st["stale"])
        self.assertEqual(st["clip_instruct"], "Female, young adult. High quality.\nWarm.")
        self.assertEqual(st["instruct"], "Female, thirties. High quality.\nWarm.")

    def test_race_is_not_part_of_the_prompt(self):
        self.ses.audition("iroha", "Warm.")
        self.ses.accept("iroha")
        self.yaml.write_text(self.yaml.read_text().replace("    race: hume\n    description: >-\n      Warm.",
                                                           "    race: mithra\n    description: >-\n      Warm."))
        self.ses.reload()
        st = self.ses.state("iroha")
        self.assertEqual(st["race"], "mithra", "still shown while designing")
        self.assertFalse(st["stale"], "the clip was made with the same prompt")

    def test_accent_persona_and_emotion_compose_and_save(self):
        self.ses.edit_voice("iroha", "Measured and formal.", accent=" Japanese ", persona="a priestess.",
                            emotion="unyielding, determined")
        st = self.ses.state("iroha")
        self.assertEqual((st["accent"], st["persona"], st["emotion"]),
                         ("Japanese", "a priestess.", "unyielding, determined"))
        prompt = ("Native Japanese. Female, young adult. High quality.\n"
                  "Persona: a priestess. Emotion: unyielding, determined.\nMeasured and formal.")
        self.assertEqual(st["instruct"], prompt)
        self.ses.audition("iroha", "Measured and formal.", accent="Japanese", persona="a priestess",
                          emotion="unyielding, determined")
        self.assertEqual(self.engine.calls[-1][2], prompt)
        self.ses.accept("iroha")
        text = self.yaml.read_text(encoding="utf-8")
        self.assertIn('    race: hume\n    accent: Japanese\n    persona: a priestess\n'
                      '    emotion: "unyielding, determined"\n', text)
        self.assertEqual(self.ses.state("iroha")["saved_instruct"], prompt)
        self.assertFalse(self.ses.state("iroha")["stale"])
        # a pool voice has none of the three, whatever is typed
        self.assertEqual(self.ses.compose("mithra_f_adult-0", "Sly.", accent="French", persona="a thief", age="forties"),
                         "Female, forties. High quality.\nSly.")
        self.ses.edit_voice("mithra_f_adult-0", "mithra_f_adult voice", accent="French")
        self.assertFalse(self.ses.state("mithra_f_adult-0")["pending"])

    def test_the_quality_level_is_the_sessions(self):
        ses = vr.Session(self.yaml, self.refs, self.voices_json, engine_factory=lambda: self.engine, quality="studio")
        self.assertEqual(ses.state("iroha")["instruct"], "Female, young adult. Studio quality.\nWarm.")
        ses.audition("iroha", "Warm.")
        ses.accept("iroha")
        self.assertFalse(ses.state("iroha")["stale"])
        self.ses.reload()
        self.assertTrue(self.ses.state("iroha")["stale"], "a clip made at another quality level is from another prompt")

    def test_save_writes_the_fields_with_no_take(self):
        self.ses.audition("iroha", "Warm.")
        self.ses.accept("iroha")                                  # a clip from the design as it was
        with self.assertRaisesRegex(ValueError, "nothing has changed"):
            self.ses.save("iroha")
        self.ses.edit_voice("iroha", "Measured.", age="14 years old", accent="Japanese", persona="a priestess")
        self.yaml.write_text(self.yaml.read_text().replace("Small and chirpy.", "Small, chirpy, hand-edited."))
        self.ses.save("iroha")
        self.assertEqual(len(self.engine.calls), 2, "nothing was designed for the save")
        saved = designs.Designs.load(self.yaml, groups=set(casting.POOLS))
        v = saved.voices["iroha"]
        self.assertEqual((v["description"], v["age"], v["accent"], v["persona"], v["seed"]),
                         ("Measured.", "14 years old", "Japanese", "a priestess", 7))
        self.assertEqual(saved.voices["ekokoko"]["description"], "Small, chirpy, hand-edited.",
                         "only the saved voice changes")
        st = self.ses.state("iroha")
        self.assertFalse(st["pending"])
        self.assertTrue(st["stale"], "the clip is from the design before the save")
        self.assertEqual(st["instruct"], "Native Japanese. Female, 14 years old. High quality.\n"
                                         "Persona: a priestess.\nMeasured.")

    def test_save_keeps_a_new_voice_without_a_take(self):
        voice = self.ses.give_own_voice("Numi Adaligo")
        self.ses.edit_voice(voice, "A sly merchant.", persona="a merchant", gender="female", age="forties", race="mithra")
        self.ses.save(voice)
        saved = designs.Designs.load(self.yaml, groups=set(casting.POOLS))
        self.assertEqual(saved.voice_for_speaker("Numi Adaligo"), "numi-adaligo")
        self.assertEqual((saved.voices[voice]["persona"], saved.voices[voice]["description"]),
                         ("a merchant", "A sly merchant."))
        self.assertEqual(json.loads(self.voices_json.read_text())["moss"]["Numi Adaligo"], "numi-adaligo")
        self.assertEqual(self.ses.row("speaker:Numi Adaligo")["kind"], "own voice")
        self.assertIsNone(self.ses.clip(voice))

    def test_a_speaker_on_a_pool_voice_can_be_moved_to_another_group(self):
        tonasav = self.ses.row("speaker:Tonasav")
        self.assertEqual((tonasav["group"], tonasav["via"]), ("hume_m_adult", "census"))
        before = json.loads(self.voices_json.read_text())
        voice = self.ses.move("Tonasav", "small")
        self.assertEqual(voice, "small-0")
        row = self.ses.row("speaker:Tonasav")
        self.assertEqual((row["group"], row["via"], row["race"], row["parent"]),
                         ("small", "moved", "creature", "voice:small-0"))
        self.assertIn("cast_groups.yaml moves Tonasav to group small", row["how"])
        self.assertEqual(self.moves.read_text(encoding="utf-8"), '# moves, kept\n\n"Tonasav": small\n')
        after = json.loads(self.voices_json.read_text())
        self.assertEqual(after["moss"]["Tonasav"], "small-0")
        self.assertEqual({e: {n: v for n, v in after[e].items() if n != "Tonasav"} for e in after}, before,
                         "nobody else changes voice")
        self.assertIn("Tonasav", after["kokoro"], "every engine learns the move")
        # someone cast.py lists: moved, then put back, which needs no line at all
        self.assertEqual(self.ses.row("speaker:Prishe")["group"], "elvaan_f_teen")
        self.ses.move("Prishe", "elvaan_f_young")
        self.assertEqual((self.ses.row("speaker:Prishe")["group"], self.ses.row("speaker:Prishe")["via"]),
                         ("elvaan_f_young", "moved"))
        self.ses.move("Prishe", "elvaan_f_teen")
        self.assertEqual(self.ses.row("speaker:Prishe")["via"], "cast.py")
        self.assertNotIn("Prishe", self.moves.read_text(encoding="utf-8"))
        for name, group, message in (("Tonasav", "nosuch", "not a casting group"), (None, "small", "select a speaker"),
                                     ("Iroha", "small", "voice of their own")):
            with self.assertRaisesRegex(ValueError, message):
                self.ses.move(name, group)
        labels = [label for _, label in self.ses.options()["group"]]
        self.assertEqual(len(labels), len(set(labels)), "every group reads differently in the picker")
        self.assertEqual([g for g, _ in self.ses.options()["group"]], list(casting.POOLS) + ["silent"])

    def test_race_unknown_lists_the_speakers_nothing_places(self):
        self.assertEqual(self.labels(status="uncast"), [])
        self.ses.speaker_stories.update({"Gukool Ja": {"cop": 9}, "Zona Shodhun": {"rov": 4}})
        self.ses.reload()
        unplaced = [r for r in self.ses.rows if r["via"] == "default"]
        self.assertTrue(unplaced, "the fixture has a speaker on the default voice")
        self.assertEqual(self.labels(status="uncast"), [r["label"] for r in unplaced])
        self.assertEqual({(r["status"], r["needs"]) for r in unplaced}, {("uncast", "a casting group")})
        self.assertEqual(self.ses.uncast(), len(unplaced))
        before = self.ses.progress()
        first = unplaced[0]
        self.ses.move(first["name"], first["group"])   # even to the default's own group: now it is on record
        self.assertEqual(self.ses.row(first["key"])["via"], "moved")
        self.assertNotIn(first["label"], self.labels(status="uncast"))
        self.assertEqual((self.ses.uncast(), self.ses.progress()), (len(unplaced) - 1, before), "prompts are counted apart")

    def test_a_new_race_is_a_group_with_a_pool_to_move_speakers_to(self):
        group = self.ses.add_race("Testling")
        self.assertEqual((group, casting.group_label(group), designs.race_gender(group)),
                         ("testling", "testling", ("testling", "unknown")))
        self.assertEqual(json.loads(self.groups_file.read_text())["testling"], {"race": "testling", "sex": "unknown"})
        saved = designs.Designs.load(self.yaml, groups=set(casting.POOLS))
        self.assertEqual(saved.pools["testling"], ["testling-0"])
        self.assertIn("testling", saved.voices["testling-0"]["description"])
        self.assertIn(("testling", "testling (testling)"), self.ses.options()["group"])
        self.assertIn("testling", self.ses.options()["meta_race"], "an own voice can be that race too")
        self.assertEqual(self.ses.move("Tonasav", "testling"), "testling-0")
        row = self.ses.row("speaker:Tonasav")
        self.assertEqual((row["race"], row["group"], row["via"]), ("testling", "testling", "moved"))
        self.assertIn("race:testling", {r["key"] for r in self.ses.rows})
        self.assertEqual(self.ses.add_race("testbeast", "male"), "testbeast_m")
        self.assertEqual(self.ses.add_race("Testbeast", "female"), "testbeast_f", "a second gender is a second group")
        for race, gender, message in (("testling", "unknown", "already a casting group"), ("hume", "male", "already"),
                                      ("", "male", "race name"), ("orc2", "male", "race name"),
                                      ("orc", "both", "sex is one of")):
            with self.assertRaisesRegex(ValueError, message):
                self.ses.add_race(race, gender)
        # another session, another process: the file is what carries the groups
        del casting.POOLS["testbeast_f"], casting.SPECIAL["testbeast_f"]
        casting.read_added(self.groups_file)
        self.assertEqual(casting.group_info("testbeast_f"), ("testbeast", "female", None))

    def test_a_speaker_can_be_silenced_and_found_again(self):
        self.assertIn(("silent", "silent (never voiced)"), self.ses.options()["group"])
        self.ses.move("Tonasav", "silent")
        self.assertIn('"Tonasav": silent', self.moves.read_text(encoding="utf-8"))
        self.assertNotIn("Tonasav", json.loads(self.voices_json.read_text())["moss"])
        row = self.ses.row("speaker:Tonasav")
        self.assertEqual((row["status"], row["via"], row["group"]), ("silent", "silent", "silent"))
        self.assertIn("never voiced", row["how"])
        self.assertEqual((self.labels(status="silent"), self.labels(status="uncast")), (["Tonasav"], []))
        del self.ses.speaker_stories["Tonasav"]   # the story counts drop the silent once they are regenerated
        self.ses.reload()
        self.assertEqual(self.labels(status="silent"), ["Tonasav"], "still listed, to be resolved later")
        self.assertEqual(self.ses.move("Tonasav", "small"), "small-0")
        self.assertEqual(self.labels(status="silent"), [])
        with self.assertRaisesRegex(ValueError, "voice of their own"):
            self.ses.move("Iroha", "silent")

    def test_status_is_the_burn_down_of_own_voices(self):
        self.assertEqual(self.ses.progress(), (2, 0), "Iroha and Ekokoko; Volto Oscuro shares and is not counted")
        iroha = self.ses.row("speaker:Iroha")
        self.assertEqual((iroha["status"], iroha["needs"]), ("todo", "accent, persona, emotion, line"))
        self.assertEqual(self.ses.row("speaker:Volto Oscuro")["status"], "")
        self.assertEqual(self.ses.row("speaker:Numi Adaligo")["status"], "", "a pool speaker has no prompt")
        self.assertEqual(self.labels(status="todo"), ["Iroha", "Ekokoko"], "characters only, most-spoken first")
        self.assertEqual(self.labels(status="ready"), [])
        self.ses.edit_voice("iroha", "Warm.", accent="Japanese", persona="a priestess", emotion="grave")
        self.ses.save("iroha")
        self.assertEqual(self.ses.row("speaker:Iroha")["needs"], "line", "everything but what she is designed saying")
        self.ses.edit_voice("iroha", "Warm.", line=self.ses.design_line("iroha", "Master,  Vana'diel needs you."))
        self.ses.save("iroha")
        self.assertEqual((self.ses.progress(), self.labels(status="todo"), self.labels(status="ready")),
                         ((1, 1), ["Ekokoko"], ["Iroha"]))
        st = self.ses.state("iroha")
        self.assertRegex(st["line"], r"^sha1:[0-9a-f]{12}$", "one of her lines is kept as a reference")
        self.assertEqual(st["line_text"], "Master, Vana'diel needs you.")
        self.assertNotIn("needs you", self.yaml.read_text(encoding="utf-8"), "the game's text stays out of the file")
        self.assertIn("    line: >-\n      sha1:", self.yaml.read_text(encoding="utf-8"))
        self.assertEqual(self.ses.design_line("iroha", " Written for  the purpose. "), "Written for the purpose.",
                         "text that is no line of hers is kept as it is")
        self.assertIsNone(self.ses.line_text("ekokoko", st["line"]), "not a line Ekokoko has")
        self.assertIn("Nothing else is judged", self.ses.STATUS_RULE)
        self.assertEqual(self.ses.row("speaker:Iroha")["needs"], "")
        self.assertEqual(self.labels(status="todo", race="hume"), [], "the other filters still apply")

    def test_a_missing_lines_file_is_known(self):
        self.assertIsNone(self.ses.lines_path, "the file is there")
        missing = Path(self.dir.name) / "nowhere" / "voice_lines.json"
        ses = vr.Session(self.yaml, self.refs, self.voices_json, lines=missing)
        self.assertEqual((ses.lines_path, ses.lines_of("iroha", "Iroha")), (missing, []))

    def test_an_audition_without_qwen_says_what_is_missing(self):
        def no_torch():
            raise ModuleNotFoundError("No module named 'torch'")
        ses = vr.Session(self.yaml, self.refs, self.voices_json, engine_factory=no_torch)
        self.assertEqual(ses.state("iroha")["instruct"], "Female, young adult. High quality.\nWarm.",
                         "the prompt needs no engine")
        with self.assertRaisesRegex(RuntimeError, "auditions need Qwen3.*make venv-qwen"):
            ses.audition("iroha", "Warm.")
        self.assertFalse(ses.state("iroha")["candidate"])

    def test_a_pool_voice_keeps_its_groups_gender_and_race(self):
        self.ses.audition("mithra_f_adult-0", "Sly.", gender="male", race="hume", age="forties")
        self.assertEqual(self.engine.calls[-1][2], "Female, forties. High quality.\nSly.")
        self.ses.accept("mithra_f_adult-0")
        v = designs.Designs.load(self.yaml).voices["mithra_f_adult-0"]
        self.assertEqual((v["gender"], v["race"], v["age"]), ("female", "mithra", "forties"))

    def test_an_unknown_race_is_refused(self):
        with self.assertRaises(ValueError):
            self.ses.audition("iroha", "Warm.", race="nosuch")

    def test_regenerate_draws_a_new_seed_and_accept_saves_it(self):
        self.ses.regenerate("iroha", "Warm.")
        seed = self.engine.calls[0][1]
        self.assertNotEqual(seed, 7)
        self.ses.accept("iroha")
        self.assertEqual(designs.Designs.load(self.yaml).voices["iroha"]["seed"], seed)

    def test_reset_drops_the_take_and_edits(self):
        self.ses.audition("iroha", "Something else.", age="elderly")
        self.ses.reset("iroha")
        st = self.ses.state("iroha")
        self.assertEqual((st["description"], st["age"]), ("Warm.", "young adult"))
        self.assertIsNone(st["candidate"])
        self.assertFalse(st["pending"])
        with self.assertRaises(ValueError):
            self.ses.accept("iroha")

    def test_give_own_voice_then_accept_updates_voices_json(self):
        voice = self.ses.give_own_voice("Numi Adaligo")
        self.assertEqual(voice, "numi-adaligo")
        row = self.ses.row("speaker:Numi Adaligo")
        self.assertEqual((row["voice"], row["kind"], row["race"]), ("numi-adaligo", "own voice", "mithra"))
        st = self.ses.state(voice)
        self.assertEqual((st["description"], st["gender"], st["age"], st["race"]),
                         ("mithra_f_adult voice", "female", "thirties", "mithra"), "starts from the pool's design")
        self.ses.audition(voice, "A sly merchant.", age="forties")
        self.assertEqual(self.engine.calls[-1][2], "Female, forties. High quality.\nA sly merchant.")
        self.ses.accept(voice)
        saved = designs.Designs.load(self.yaml, groups=set(casting.POOLS))
        self.assertEqual(saved.voice_for_speaker("Numi Adaligo"), "numi-adaligo")
        v = saved.voices["numi-adaligo"]
        self.assertEqual((v["gender"], v["age"], v["race"]), ("female", "forties", "mithra"))
        self.assertEqual(json.loads(self.voices_json.read_text())["moss"]["Numi Adaligo"], "numi-adaligo")
        with self.assertRaises(ValueError):
            self.ses.give_own_voice("Iroha")

    def test_reset_of_an_unaccepted_new_voice_removes_it(self):
        self.ses.give_own_voice("Tonasav")
        self.ses.reset("tonasav")
        self.assertEqual(self.ses.row("speaker:Tonasav")["kind"], "on a pool voice")
        self.assertNotIn("tonasav", self.ses.designs.voices)
        self.assertNotIn("Tonasav", designs.Designs.load(self.yaml).speakers)

    def test_typed_fields_are_a_draft_until_auditioned(self):
        self.ses.edit_voice("iroha", " Warm  and low. ", age="thirties")
        st = self.ses.state("iroha")
        self.assertEqual((st["description"], st["age"], st["gender"], st["pending"], st["candidate"]),
                         ("Warm and low.", "thirties", "female", True, None))
        self.assertEqual(st["instruct"], "Female, thirties. High quality.\nWarm and low.")
        with self.assertRaisesRegex(ValueError, "audition a take"):
            self.ses.accept("iroha")
        self.ses.edit_voice("iroha", "Warm.", age="young adult")
        self.assertFalse(self.ses.state("iroha")["pending"], "typed back to what is saved: no draft")
        self.ses.edit_voice("iroha", "Warm and low.")
        self.ses.audition("iroha", "Warm and low.", age="thirties")
        st = self.ses.state("iroha")
        self.assertTrue(st["candidate"])
        self.assertFalse(st["edited_since_take"])
        self.ses.edit_voice("iroha", "Warm and lower.", age="thirties")
        st = self.ses.state("iroha")
        self.assertEqual((st["description"], st["edited_since_take"]), ("Warm and lower.", True))
        with self.assertRaisesRegex(ValueError, "changed since the take"):
            self.ses.accept("iroha")
        self.ses.edit_voice("iroha", "Warm and low.", age="thirties")
        self.assertFalse(self.ses.state("iroha")["edited_since_take"], "typed back to the take: it can be accepted")
        self.ses.edit_voice("iroha", "Warm and lower.", age="thirties")
        self.ses.audition("iroha", "Warm and lower.", age="thirties")
        self.assertFalse(self.ses.state("iroha")["edited_since_take"])
        self.ses.accept("iroha")
        self.assertEqual(designs.Designs.load(self.yaml, groups=set(casting.POOLS)).voices["iroha"]["description"],
                         "Warm and lower.")
        # a pool voice's gender and race stay its group's whatever is typed
        self.ses.edit_voice("mithra_f_adult-0", "Sly.", gender="male", race="hume")
        st = self.ses.state("mithra_f_adult-0")
        self.assertEqual((st["description"], st["gender"], st["race"]), ("Sly.", "female", "mithra"))
        self.ses.reset("mithra_f_adult-0")
        self.assertFalse(self.ses.state("mithra_f_adult-0")["pending"])

    def test_hear_renders_a_line_through_moss_continuing_the_take_else_the_clip(self):
        self.assertEqual(self.ses.lines_of("iroha"), ["Master, Vana'diel needs you.", "<Sigh> Follow me, <player>."])
        self.assertEqual(self.ses.lines_of("mithra_f_adult-0", "Tonasav"),
                         ["The guards are asleep again.", "Welcome to Windurst Waters."],
                         "the row's speaker first, then everyone else on the voice")
        with self.assertRaisesRegex(ValueError, "nothing to continue"):
            self.ses.hear("iroha", "Hello.")
        take = self.ses.audition("iroha", "Warmer.", age="thirties")
        self.assertIs(self.ses._engine, self.engine)
        wav, how = self.ses.hear("iroha", "<Sigh> Follow me, <player>.")
        self.assertEqual(how, "take")
        self.assertIsNone(self.ses._engine, "Qwen is let go before MOSS loads")
        self.assertEqual(self.worker.starts, 1)
        self.assertEqual(self.worker.requests, [{"ref": str(take), "ref_text": "Wait here.", "seed": 7,
                                                 "text": "Follow me, adventurer.", "out": str(wav)}])
        self.assertEqual(wav.parent, self.refs / ".candidates" / "hear")
        self.ses.hear("iroha", "<Sigh> Follow me, <player>.")
        self.assertEqual(len(self.worker.requests), 1, "a line heard before is not made twice")
        wav2, _ = self.ses.hear("iroha", "Master, Vana'diel needs you.")
        self.assertEqual(self.worker.requests[-1]["text"], "Master, [[vɑnədiˈɛl]] needs you.", "the lexicon applies")
        self.assertNotEqual(wav, wav2)
        with self.assertRaisesRegex(ValueError, "nothing to say"):
            self.ses.hear("iroha", "<Sigh>")
        # a take edited since cannot be what is heard; the saved clip can
        self.ses.edit_voice("iroha", "Warmest.", age="thirties")
        with self.assertRaisesRegex(ValueError, "nothing to continue"):
            self.ses.hear("iroha", "Hello.")
        self.ses.edit_voice("iroha", "Warmer.", age="thirties")
        self.ses.accept("iroha")
        wav3, how = self.ses.hear("iroha", "Hello.")
        self.assertEqual((how, self.worker.requests[-1]["ref"]), ("saved clip", str(self.refs / "iroha.wav")))
        self.assertEqual(self.worker.requests[-1]["ref_text"], "Wait here.", "the clip's own sentence, from the index")
        # the next take lets MOSS go, and the next line lets Qwen go again
        self.ses.audition("iroha", "Warmer still.")
        self.assertTrue(self.worker.closed)
        self.assertIs(self.ses._engine, self.engine)
        self.ses.hear("iroha", "Hello again.")
        self.assertEqual((self.worker.starts, self.ses._engine), (2, None))
        self.ses.close()
        self.assertTrue(self.worker.closed)

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

    def test_only_what_a_pack_would_voice_counts_under_who_says_it(self):
        say = "Found you, " + "x" * 60 + "."
        lines = {"q-jeuno": [{"speaker": "Iroha", "class": "dialogue", "as": None},
                             {"speaker": "???", "class": "dialogue", "as": "Larzos", "kind": "say", "variants": [say]},
                             {"speaker": "", "class": "narration", "as": None, "kind": "narration", "variants": [say]},
                             {"speaker": "???", "class": "unattributed", "as": None},
                             {"speaker": "Clerk", "class": "boilerplate", "as": None, "kind": "say", "variants": [say]},
                             {"speaker": "", "class": "system", "as": None}, {"speaker": "", "class": "menu", "as": None},
                             {"speaker": "Iroha", "class": "unvoiceable", "as": None}],
                 "mog-house": [{"speaker": "Clerk", "class": "boilerplate", "as": None}]}
        data = voice_stories.build(lambda code: lines[code], {"q-jeuno": "J", "mog-house": "M"})
        self.assertEqual(data["speakers"], {"": {"q-jeuno": 1}, "Iroha": {"q-jeuno": 1}, "Larzos": {"q-jeuno": 1}})
        self.assertEqual(data["stories"], {"q-jeuno": "J"}, "a storyline with nothing to voice is left out")
        self.assertEqual(data["lines"], {"Larzos": [say]}, "samples are dialogue, under who says it")
        again = {"mog-house": [{"speaker": "Moogle", "variants": ["Kupo!"]}, {"speaker": "Moogle", "variants": ["Kupo!"]},
                               {"speaker": "Moogle", "variants": ["Kupo?"]}]}
        self.assertEqual(voice_stories.build(lambda code: again[code], {"mog-house": "M"})["speakers"],
                         {"Moogle": {"mog-house": 2}}, "the same line in two zones is one line")
        words = voice_stories.build_words(lambda code: lines[code], {"q-jeuno": "J"})
        self.assertEqual(words["Found"]["speakers"], {"Larzos": 1})

    def test_every_storyline_code_names_one_source(self):
        named = [voice_stories.source_of(code) for code in voice_stories.STORIES]
        self.assertEqual(len(named), len(set(named)))
        self.assertEqual(voice_stories.source_of("rov"), "missions/rov")
        self.assertEqual(voice_stories.source_of("q-toau"), "quests/ahtUrhgan")
        self.assertEqual(voice_stories.source_of("mog-house"), "companions/mog-house")
        self.assertLessEqual(set(voice_stories.SOURCES), set(voice_stories.STORIES))

    def test_sample_lines_are_whole_middling_sentences_longest_first(self):
        texts = ["Hi.", "<Sigh> " + "a" * 70 + ".", "B" * 65 + "!", "C" * 80 + "?", "C" * 80 + "?",
                 "d" * 300 + ".", "E" * 70, "F" * 90 + ".", "G" * 61 + ".", "H" * 62 + "."]
        self.assertEqual(voice_stories.sample_lines(texts),
                         ["F" * 90 + ".", "C" * 80 + "?", "B" * 65 + "!", "H" * 62 + "."],
                         "no direction, 60 to 200 characters, ending a sentence, each once, four at most")
        self.assertEqual(voice_stories.sample_lines(["Hi.", "Hi.", "Yo"]), ["Hi.", "Yo"],
                         "when none qualifies, the first lines seen")
        self.assertEqual(voice_stories.sample_lines([]), [])
        lines = {"rov": [{"kind": "say", "speaker": "Iroha", "variants": ["Master, " + "x" * 60 + "."]},
                         {"kind": "menu", "speaker": "Iroha", "variants": ["Menu " + "x" * 60 + "."]},
                         {"kind": "say", "speaker": "Tenzen", "variants": []}]}
        data = voice_stories.build(lambda code: lines[code], {"rov": "R"})
        self.assertEqual(data["lines"], {"Iroha": ["Master, " + "x" * 60 + "."]}, "spoken lines with text only")

    def test_word_index_counts_storylines_and_speakers(self):
        lines = {"rov": [{"kind": "say", "speaker": "Iroha", "text": "Master, Vana'diel needs you.",
                          "variants": ["Master, Vana'diel needs you.", "Master, Vana'diel needs you!"]},
                         {"kind": "say", "speaker": "Tenzen", "text": "What on Vana'diel..."},
                         {"kind": "menu", "speaker": "Iroha", "text": "Vana'diel"}],
                 "cop": [{"kind": "say", "speaker": "", "text": "Prishe and Vana'diel."}]}
        words = voice_stories.build_words(lambda code: lines[code], {"rov": "R", "cop": "C"})
        self.assertEqual(words["Vana'diel"], {"stories": {"cop": 1, "rov": 2},
                                              "speakers": {"Iroha": 1, "Tenzen": 1, "": 1}},
                         "a line's variants count once; only spoken lines count")
        self.assertEqual(words["Prishe"]["speakers"], {"": 1})
        self.assertIn("Master", words, "every capitalised word; the review decides what is a name")
        self.assertNotIn("needs", words)

    def test_committed_index_has_the_rhapsodies_cast(self):
        data = json.loads((Path(voice_stories.OUT)).read_text(encoding="utf-8"))
        self.assertIn("rov", data["stories"])
        self.assertIn("rov", data["speakers"]["Iroha"])


class GuiTest(unittest.TestCase):
    def test_available_needs_a_display(self):
        import voice_review_gui
        with unittest.mock.patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}), \
                unittest.mock.patch.object(sys, "platform", "linux"):
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
        races = [r for r in t.ses.rows if r["kind"] == "race"]
        self.assertEqual(len(w.tree.get_children()), len(races), "the top level is the races")
        self.assertIn("voice:mithra_f_adult-0", w.tree.get_children("race:mithra"))
        self.assertEqual(w.tree.get_children("voice:mithra_f_adult-0")[0], "speaker:Numi Adaligo",
                         "the speakers on a pool voice, most-spoken first")
        self.assertIn("speaker:Iroha", w.tree.get_children("race:hume"), "an own voice sits under its race")
        self.assertEqual(w.tree.set("speaker:Iroha", "via"), "own")
        self.assertEqual(w.tree.set("speaker:Numi Adaligo", "via"), "cast.py")
        def order(parent=""):
            return [w.tree.item(i, "text") for i in w.tree.get_children(parent)]
        w.sort_by("#0")
        self.assertEqual(order(), sorted(order(), key=str.lower), "races sort A to Z")
        hume = order("race:hume")
        self.assertEqual(hume, sorted(hume, key=str.lower), "and so do the rows under one")
        self.assertTrue(w.tree.heading("#0", "text").endswith("▲"))
        w.sort_by("#0")
        self.assertEqual(order(), sorted(order(), key=str.lower, reverse=True), "a second click reverses")
        w.sort_by("lines")
        lines = [int(w.tree.set(i, "lines") or 0) for i in w.tree.get_children()]
        self.assertEqual(lines, sorted(lines, reverse=True), "lines sort as numbers, most first")
        self.assertEqual(w.tree.heading("#0", "text"), "speaker", "only the sorted column is marked")
        w.sort_by("age")
        ages = [w.tree.set(i, "age") for i in w.tree.get_children("race:hume")]
        known = [a for a in ages if a]
        self.assertEqual(known, sorted(known, key=voice_review_gui.AGE_ORDER.get), "ages run youngest first")
        self.assertEqual(ages[len(known):], [""] * (len(ages) - len(known)), "no age sorts last")
        w.query.set("numi")
        self.assertEqual(w.tree.get_children(), ("race:mithra",), "a match brings the rows above it")
        self.assertEqual(w.tree.item("race:mithra", "tags"), ("context",), "greyed: shown only for what is under it")
        self.assertEqual(w.tree.get_children("voice:mithra_f_adult-0"), ("speaker:Numi Adaligo",))
        self.assertEqual(w.tree.item("speaker:Numi Adaligo", "tags"), "")
        w.tree.selection_set("speaker:Numi Adaligo")
        w.select()
        self.assertEqual(w.title.cget("text"), "Pool voice: mithra female adult 1 of 1", "the pane names what it edits")
        self.assertEqual(w.line_box.cget("values"), ("Welcome to Windurst Waters.",), "Numi's own lines first")
        self.assertIn("Editing here changes every speaker on mithra_f_adult-0", w.info.cget("text"))
        self.assertIn("cast.py puts Numi Adaligo in group mithra_f_adult", w.info.cget("text"))
        self.assertTrue(w.own.winfo_manager(), "a pool speaker gets the own-voice button")
        self.assertTrue(w.move_box.winfo_manager(), "and can be moved to another pool")
        self.assertEqual(w.move_var.get(), "mithra female adult", "the picker starts on their group")
        self.assertIn("mithra_f_adult voice", w.text.get("1.0", "end"))
        self.assertEqual(str(w.meta["gender"][1].cget("state")), "disabled", "a pool voice's gender is its group's")
        self.assertEqual(w.meta["age"][0].get(), "thirties")
        # the prompt is always in view and follows the fields as they change
        self.assertFalse(hasattr(w, "calibration"), "no clip sentence in the pane any more")
        self.assertEqual(w.hear_note.cget("text"), "", "nothing to say when there are lines to pick")
        self.assertNotIn("show_prompt", w.buttons)
        self.assertEqual(w.prompt_text.get("1.0", "end").strip(), "Female, thirties. High quality.\nmithra_f_adult voice")
        w.meta["age"][0].set("forties")
        w.text.delete("1.0", "end")
        w.text.insert("1.0", "Sly.")
        w.refresh(keep="speaker:Numi Adaligo")   # as after an audition or a filter change
        w.root.update()
        self.assertEqual(w.prompt_text.get("1.0", "end").strip(), "Female, forties. High quality.\nSly.")
        w.copy_prompt()
        self.assertEqual(w.root.clipboard_get(), "Female, forties. High quality.\nSly.")
        self.assertEqual(w.status_line.cget("text"), "prompt copied")
        self.assertEqual(w.meta["age"][0].get(), "forties", "edits survive the list being refreshed")
        w.query.set("")
        w.tree.selection_set("speaker:Iroha")
        w.root.update()
        self.assertEqual(str(w.meta["race"][1].cget("state")), "readonly", "an own voice's race can change")
        self.assertEqual(w.prompt_text.get("1.0", "end").strip(), "Female, young adult. High quality.\nWarm.")
        self.assertEqual(w.title.cget("text"), "Own voice iroha")
        self.assertEqual(w.line_box.cget("values"), ("Master, Vana'diel needs you.", "<Sigh> Follow me, <player>."),
                         "the speaker's own lines to hear the voice say")
        self.assertEqual(w.line_var.get(), "Master, Vana'diel needs you.")
        self.assertIn("disabled", w.buttons["hear"].state(), "nothing to continue yet: no take, no clip")
        self.assertEqual(str(w.meta["accent"][1].cget("state")), "normal", "an own voice has an accent")
        w.meta["accent"][0].set("Japanese")
        w.meta["persona"][0].set("a priestess")
        w.root.update()
        self.assertEqual(w.prompt_text.get("1.0", "end").strip(),
                         "Native Japanese. Female, young adult. High quality.\nPersona: a priestess.\nWarm.")
        self.assertEqual(w.tree.set("speaker:Iroha", "state"), "edited")
        self.assertNotIn("disabled", w.buttons["save"].state(), "an edit can be saved with no take")
        w.save()
        w.root.update()
        self.assertIn("with no take", w.status_line.cget("text"))
        self.assertIn("    accent: Japanese\n    persona: a priestess\n", t.yaml.read_text(encoding="utf-8"))
        self.assertEqual(w.tree.set("speaker:Iroha", "state"), "no clip")
        self.assertIn("disabled", w.buttons["save"].state())
        w.meta["accent"][0].set("")
        w.meta["persona"][0].set("")
        w.root.update()
        w.save()
        w.root.update()
        self.assertEqual(w.meta["accent"][0].get(), "")
        # a race row is a heading: nothing to edit, audition, or hear
        w.tree.selection_set("race:mithra")
        w.root.update()
        self.assertEqual(w.title.cget("text"), "Race mithra")
        self.assertIn("not part of a prompt", w.info.cget("text"))
        self.assertEqual((w.line_box.cget("values"), w.line_var.get()), ("", ""), "a race says nothing")
        self.assertEqual(w.text.get("1.0", "end").strip(), "")
        self.assertEqual(str(w.meta["age"][1].cget("state")), "disabled")
        self.assertFalse(w.own.winfo_manager())
        self.assertEqual(w.prompt_text.get("1.0", "end").strip(), "", "a race has no prompt")
        for name in ("accept", "reset", "audition", "copy_prompt", "hear", "play_saved"):
            self.assertIn("disabled", w.buttons[name].state(), name)
        self.assertEqual(w.tree.set("race:mithra", "state"), "")
        w.tree.selection_set("speaker:Numi Adaligo")   # the pool voice's edits from before are still there
        w.root.update()
        self.assertEqual(w.prompt_text.get("1.0", "end").strip(), "Female, forties. High quality.\nSly.")
        self.assertEqual(w.tree.set("speaker:Numi Adaligo", "state"), "edited")
        self.assertEqual(str(w.meta["accent"][1].cget("state")), "disabled", "a pool voice has no accent")
        w.tree.selection_set("speaker:Iroha")
        w.root.update()
        self.assertFalse(w.move_box.winfo_manager(), "an own voice is not moved between pools")
        # the burn-down: a flat list of the prompts to do, and saving one moves on to the next
        var, box = w.filters["status"]
        var.set("to do")
        w.refresh()
        w.root.update()
        self.assertEqual(w.tree.get_children(), ("speaker:Iroha", "speaker:Ekokoko"), "flat, most-spoken first")
        self.assertEqual(w.tree.set("speaker:Iroha", "needs"), "accent, persona, emotion, line")
        self.assertIn("prompts: 2 to do, 0 ready", w.count.cget("text"))
        w.tree.selection_set("speaker:Iroha")
        w.root.update()
        self.assertIn("Prompt: to do, missing accent, persona, emotion, line. A prompt is ready when",
                      w.info.cget("text"), "the pane says what decides it")
        self.assertIn("Design line: none yet", w.design_label.cget("text"))
        for field, value in (("accent", "Japanese"), ("persona", "a priestess"), ("emotion", "grave")):
            w.meta[field][0].set(value)
        w.root.update()
        w.save()
        w.root.update()
        self.assertEqual(w.tree.set("speaker:Iroha", "needs"), "line", "still to do: nothing to be designed saying")
        w.line_var.set("<Sigh> Follow me, <player>.")
        w.use_line()
        w.root.update()
        self.assertEqual(w.design_label.cget("text"), "Design line: <Sigh> Follow me, <player>.")
        self.assertRegex(w.design_var.get(), r"^sha1:", "one of her lines: recorded as a reference")
        self.assertEqual(w.tree.set("speaker:Iroha", "state"), "edited")
        w.save()
        w.root.update()
        self.assertEqual(w.tree.get_children(), ("speaker:Ekokoko",), "the finished one leaves the list")
        self.assertEqual(w.design_label.cget("text"), "Design line: none yet. Pick or type a line above and press "
                                                      "Use for design.", "Ekokoko's, not the one just saved")
        self.assertEqual((w.tree.selection(), w.row["key"], w.voice), (("speaker:Ekokoko",), "speaker:Ekokoko", "ekokoko"),
                         "and the next one is selected and shown")
        self.assertIn("prompts: 1 to do, 1 ready", w.count.cget("text"))
        var.set("all prompts")
        w.refresh()
        w.root.update()
        self.assertIn("race:hume", w.tree.get_children(), "back to the tree")
        # moving a pool speaker from the window
        w.tree.selection_set("speaker:Tonasav")
        w.root.update()
        w.move_var.set("creature (small)")
        w.move()
        w.root.update()
        self.assertIn("moved Tonasav to small: now on small-0", w.status_line.cget("text"))
        self.assertEqual(w.tree.parent("speaker:Tonasav"), "voice:small-0")
        self.assertEqual((w.tree.set("speaker:Tonasav", "via"), w.move_var.get()), ("moved", "creature (small)"))
        w.move()
        self.assertIn("already in small", w.status_line.cget("text"))
        # a race the groups do not have: created from the window, then moved to
        self.assertFalse(w.new_race("hume", "male"))
        self.assertIn("already a race", w.status_line.cget("text"))
        self.assertTrue(w.new_race("Testling", "unknown"))
        self.assertEqual(w.move_var.get(), "testling (testling)", "left picked, so Move is the next press")
        self.assertIn("testling", w.meta["race"][1].cget("values"))
        w.move()
        w.root.update()
        self.assertIn("moved Tonasav to testling: now on testling-0", w.status_line.cget("text"))
        self.assertEqual(w.tree.parent("speaker:Tonasav"), "voice:testling-0")
        w.ask_race()
        w.root.update()
        w.race_window.destroy()
        w.tree.selection_set("speaker:Iroha")
        w.root.update()
        self.assertIn("Prompt: ready.", w.info.cget("text"))
        self.assertEqual(w.design_label.cget("text"), "Design line: <Sigh> Follow me, <player>.")
        for field in ("accent", "persona", "emotion"):
            w.meta[field][0].set("")
        w.design_var.set("")
        w.root.update()
        w.save()
        w.root.update()
        # a voice's typed fields survive the trip too, and a take finishing elsewhere does not move the selection
        w.tree.selection_set("speaker:Iroha")
        w.root.update()
        w.text.delete("1.0", "end")
        w.text.insert("1.0", "Warm and low.")
        w.meta["age"][0].set("thirties")
        w.root.update()
        self.assertEqual(w.tree.set("speaker:Iroha", "state"), "edited")
        self.assertIn("edited, not auditioned", w.seed.cget("text"))
        self.assertIn("disabled", w.buttons["accept"].state())
        self.assertNotIn("disabled", w.buttons["reset"].state())
        w.tree.selection_set("race:mithra")
        w.root.update()
        w.done("take for mithra_f_adult-0 ready", "speaker:Numi Adaligo", None)
        w.root.update()
        self.assertEqual(w.row["key"], "race:mithra", "the row being edited stays selected")
        w.tree.selection_set("speaker:Iroha")
        w.root.update()
        self.assertEqual(w.text.get("1.0", "end").strip(), "Warm and low.", "typed text survives the trip")
        self.assertEqual(w.meta["age"][0].get(), "thirties")
        w.reset()
        w.root.update()
        self.assertEqual(w.text.get("1.0", "end").strip(), "Warm.")
        self.assertEqual(w.tree.set("speaker:Iroha", "state"), "no clip")


if __name__ == "__main__":
    unittest.main()
