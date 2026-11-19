"""Tests for the pack builder: naming, the corpus, and a build with a fake
engine and encoder, so no client, model, or opusenc is needed."""

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_pack as bp  # noqa: E402
import spoken_helper as sh  # noqa: E402


def row(speaker, variants, kind="say", zone=252, event=286, text_id=1, live_only=(), entity=None):
    return {"kind": kind, "speaker": speaker, "variants": list(variants), "live_only": list(live_only),
            "zone": zone, "event": event, "text_id": text_id, "text": "raw", "speaker_id": entity}


class Naming(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(bp.slug("I bid you finish your tasks, adventurer."), "i-bid-you-finish-your-tasks-adventurer")
        self.assertEqual(bp.slug("Yar, Iroha's already given me the skinny. So yer off ta meet the ancients?"),
                         "yar-iroha-s-already-given-me-the-skinny")
        self.assertEqual(bp.slug("..."), "line")

    def test_dir_name(self):
        self.assertEqual(bp.dir_name("Numi Adaligo"), "Numi Adaligo")
        self.assertEqual(bp.dir_name("Grav'iton"), "Grav'iton")
        self.assertEqual(bp.dir_name("???"), "unnamed")
        self.assertEqual(bp.dir_name(""), "narration")
        self.assertEqual(bp.dir_name('Door:"Marble Bridge"'), "DoorMarble Bridge")

    def test_file_name_is_stable_and_unique(self):
        taken = {}
        a = bp.file_name("Iroha", "Hello.", taken)
        self.assertEqual(a, bp.file_name("Iroha", "Hello.", taken), "same key, same name")
        self.assertTrue(a.startswith("Iroha/hello."))
        self.assertEqual(len(a.split(".")[1]), 4)
        # force a collision: another key pre-registered under the 4-char name
        h = bp.key_hash("Iroha", "Bye.")
        taken[f"Iroha/bye.{h[:4]}.opus"] = ("Iroha", "something else")
        b = bp.file_name("Iroha", "Bye.", taken)
        self.assertEqual(b, f"Iroha/bye.{h[:8]}.opus")


class Corpus(unittest.TestCase):
    def test_dedup_sources_and_skips(self):
        rows = [row("Iroha", ["Go to Norg."], text_id=1),
                row("Iroha", ["Go to Norg."], event=300, text_id=7),           # same key, second source
                row("Tenzen", ["Take his hand.", "Take her hand."], text_id=2),  # two variants
                row("", ["The ship departs."], kind="narration", text_id=3),
                row("", ["Pick one."], kind="menu", text_id=4),
                row("Iroha", [], text_id=5, live_only=["item", "number"]),
                row("Iroha", [], text_id=6),
                {"kind": "say", "speaker": "Iroha", "text_id": None, "variants": [], "zone": 1, "event": 1}]
        lines, skipped, blocked = bp.corpus(rows)
        self.assertEqual(blocked, [])
        self.assertEqual([(ln["speaker"], ln["text"]) for ln in lines],
                         [("Iroha", "Go to Norg."), ("Tenzen", "Take his hand."), ("Tenzen", "Take her hand."),
                          ("", "The ship departs.")])
        self.assertEqual(lines[0]["sources"], [{"zone": 252, "event": 286, "text_id": 1},
                                               {"zone": 252, "event": 300, "text_id": 7}])
        self.assertEqual(skipped, {"menu": 1, "live-only: item, number": 1, "empty text": 1, "variable text id": 1})
        lines, skipped, _ = bp.corpus(rows, narration=False)
        self.assertEqual((len(lines), skipped["narration"]), (3, 1))

    def test_what_the_exclude_rules_drop_is_skipped_under_its_class(self):
        import corpus
        rules = corpus.Exclude([corpus.Rule({"class": "system", "reason": "told to the player", "kind": "narration",
                                             "text": "^You\\b"}, "rule 1"),
                                corpus.Rule({"class": "boilerplate", "reason": "shop", "speaker": "Clerk"}, "rule 2")])
        rows = [row("Iroha", ["Go to Norg."], text_id=1), row("", ["You obtain a fish."], kind="narration", text_id=2),
                row("Clerk", ["Anything else?"], text_id=3), row("Iroha", ["...!?"], text_id=4)]
        lines, skipped, blocked = bp.corpus(rows, exclude=rules)
        self.assertEqual([ln["text"] for ln in lines], ["Go to Norg."])
        self.assertEqual(skipped, {"system: told to the player": 1, "boilerplate: shop": 1, "punctuation only": 1})

    def test_dialogue_with_no_speaker_blocks_the_pack_and_is_reported_without_its_text(self):
        rows = [row("Iroha", ["Go to Norg."], text_id=1), row("???", ["Who goes there?"], zone=84, event=7, text_id=2),
                row("", ["Stop!"], zone=84, event=7, text_id=3), row("", ["Run!"], zone=91, event=2, text_id=4)]
        lines, skipped, blocked = bp.corpus(rows)
        self.assertEqual([ln["text"] for ln in lines], ["Go to Norg."])
        self.assertEqual([b["text"] for b in blocked], ["Who goes there?", "Stop!", "Run!"])
        report = bp.blocked_report(blocked)
        self.assertEqual(report[0], "3 lines of dialogue have no speaker, in 2 scenes; "
                                    "helper/cast_as.yaml must say who speaks them")
        self.assertEqual(report[1:], ["  zone 84 event 7: 2", "  zone 91 event 2: 1"])
        self.assertNotIn("Stop", "".join(report))


class CastAs(unittest.TestCase):
    """A line whose shown name is "???" keeps that key but takes the voice
    of whoever helper/cast_as.yaml says is speaking."""

    def table(self, *entries):
        import cast_as  # helper/, put on sys.path by build_pack
        return cast_as.CastAs(list(entries))

    def test_the_line_carries_who_says_it_and_the_voice_follows(self):
        table = self.table({"zone": 84, "entity": 0x1054374, "lines": {8374: "Found ya at long last."},
                            "as": "Larzos"},
                           {"zone": 156, "entity": 0x109c071, "as": None})
        rows = [row("???", ["Found ya at long last."], zone=84, event=7, text_id=8374, entity=0x1054374),
                row("???", ["Found ya at long last."], zone=84, event=9, text_id=8374, entity=0x1054374),
                row("???", ["Who goes there?"], zone=84, event=7, text_id=8375, entity=0x1054374),
                row("2", ["Speak thy mind."], zone=156, event=8, text_id=8092, entity=0x109c071)]
        lines, skipped, blocked = bp.corpus(rows, cast_as=table)
        self.assertEqual([(b["speaker"], b["text"]) for b in blocked], [("???", "Who goes there?")],
                         "an uncast ??? line is nobody's, and blocks")
        lines.insert(1, {"speaker": "???", "text": "Who goes there?", "sources": blocked[0]["sources"]})
        self.assertEqual([(ln["speaker"], ln["text"], ln.get("as")) for ln in lines],
                         [("???", "Found ya at long last.", "Larzos"), ("???", "Who goes there?", None),
                          ("2", "Speak thy mind.", None)])
        self.assertEqual(len(lines[0]["sources"]), 2)
        self.assertEqual(table.unused(), [])
        builder = bp.Builder("/nonexistent", "p", "1", None, None, sh.Lexicon({}), lambda s: f"voice-of-{s}",
                             "/nonexistent")
        plan = builder.plan(lines)
        self.assertEqual([(r["speaker"], r["voice"], r.get("as")) for r in plan],
                         [("???", "voice-of-Larzos", "Larzos"), ("???", "voice-of-???", None), ("2", "voice-of-2", None)])
        self.assertEqual(plan[0]["file"].split("/")[0], plan[1]["file"].split("/")[0])  # same speaker folder

    def test_one_key_cast_two_ways_is_an_error(self):
        table = self.table({"zone": 84, "entity": 0x1054374, "as": "Larzos"})
        rows = [row("???", ["Found ya at long last."], zone=84, event=7, text_id=8374, entity=0x1054374),
                row("???", ["Found ya at long last."], zone=91, event=702, text_id=8108, entity=0x105b334)]
        with self.assertRaisesRegex(ValueError, "cast as Larzos .*zone 84 event 7 text 8374.* and as nobody "
                                                ".*zone 91 event 702 text 8108"):
            bp.corpus(rows, cast_as=table)

    def test_warnings_name_unused_entries_and_unknown_names(self):
        table = self.table({"zone": 84, "entity": 0x1054374, "as": "Larzos"},
                           {"zone": 85, "entity": 0x10552f6, "as": "Nobody Known"})
        bp.corpus([row("???", ["Found ya."], zone=84, event=7, text_id=1, entity=0x1054374)], cast_as=table)
        with tempfile.TemporaryDirectory() as d:
            census = Path(d) / "census.json"
            census.write_text(json.dumps({"Larzos": {"race": 3}}), encoding="utf-8")
            voices = Path(d) / "voices.json"
            voices.write_text(json.dumps({"kokoro": {"Iroha": "jf_alpha"}, "moss": {"Iroha": "iroha"}}),
                              encoding="utf-8")
            # a build that never reached zone 85's entry is not told about it
            warnings = bp.cast_as_warnings(table, voices, census)
            self.assertEqual(len(warnings), 1)
            self.assertIn("'Nobody Known' is in neither voices.json nor the census", warnings[0])
            # once the build has come near it (its text id, said by another entity) without a match, it is
            table.entries[1].lines = {8174: "Arrrgh!"}
            bp.corpus([row("???", ["Arrrgh!"], zone=85, event=1, text_id=8174, entity=0x10552f7)], cast_as=table)
            warnings = bp.cast_as_warnings(table, voices, census)
            self.assertEqual(len(warnings), 2)
            self.assertIn("matched no line: zone 85 entity 0x10552f6 lines [8174] as Nobody Known", warnings[0])
            # a name any engine's section knows is known
            table2 = self.table({"zone": 84, "entity": 0x1054374, "as": "Iroha"})
            bp.corpus([row("???", ["Found ya."], zone=84, event=7, text_id=1, entity=0x1054374)], cast_as=table2)
            self.assertEqual(bp.cast_as_warnings(table2, voices, census), [])
        self.assertEqual(bp.cast_as_warnings(None, "/nonexistent", "/nonexistent"), [])

    def test_summary_labels_a_recast_line(self):
        out = []
        bp.summarise([{"speaker": "???", "as": "Larzos", "voice": "larzos", "text": "Found ya."},
                      {"speaker": "???", "voice": "narrator", "text": "Who goes there?"}], {}, log=out.append)
        self.assertEqual(out[0], "2 lines from 2 speakers; skipped: nothing")
        self.assertEqual([ln.split() for ln in out[1:]], [["1", "???", "narrator"], ["1", "???", "as", "Larzos", "larzos"]])


class Directions(unittest.TestCase):
    def test_direction_only_lines_are_skipped_and_directions_not_spoken(self):
        rows = [row("Mayakov", ["<Sob>", "<Thniff> I mith her.", "Fine."], text_id=1)]
        lines, skipped, _ = bp.corpus(rows)
        self.assertEqual([ln["text"] for ln in lines], ["<Thniff> I mith her.", "Fine."])
        self.assertEqual(skipped, {"stage direction only": 1})
        b = Build()
        b.setUp()
        try:
            builder = b.builder()
            self.assertEqual(builder.spoken("<Thniff> I mith her, <player>."), "I mith her, adventurer.")
            self.assertEqual(builder.spoken("Go to Norg <sigh>."), "Go to [[nɔːɡ]].")
        finally:
            b.doCleanups()


class Build(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="spoken-pack-"))
        self.addCleanup(shutil.rmtree, self.dir)
        self.synthesised = []

    def synth(self, spoken, voice, wav):
        self.synthesised.append((spoken, voice))
        sh.write_wav(wav, [0.0] * 2400, 24000)  # a tenth of a second of silence

    @staticmethod
    def encode(wav, opus):
        Path(opus).parent.mkdir(parents=True, exist_ok=True)
        Path(opus).write_bytes(b"OggS" + Path(wav).read_bytes()[:64])

    def builder(self, version="0.1"):
        lexicon = sh.Lexicon({"Norg": {"ipa": "nɔːɡ"}})
        return bp.Builder(self.dir, "rhapsodies", version, self.synth, self.encode, lexicon,
                          lambda speaker: {"Iroha": "af_sarah"}.get(speaker, "am_adam"), self.dir / "cache",
                          game_text=lambda s: f"h{s['text_id']}", log=lambda *_: None,
                          engine={"name": "fake", "device": "cpu", "models": {}},
                          source={"commit": "abc123", "dirty": False, "builder": "test"})

    def lines(self):
        rows = [row("Iroha", ["Go to Norg, <player>."], text_id=1), row("Tenzen", ["Yes."], text_id=2)]
        return bp.corpus(rows)[:2]

    def test_build_writes_files_and_manifest(self):
        lines, skipped = self.lines()
        m = self.builder().build(lines, skipped)
        final = self.dir / "rhapsodies" / "0.1"
        self.assertTrue((final / "manifest.json").exists())
        self.assertFalse((self.dir / "rhapsodies" / "0.1.part").exists())
        self.assertEqual(m["pack"], "rhapsodies")
        self.assertEqual(len(m["lines"]), 2)
        first = m["lines"][0]
        self.assertEqual((first["speaker"], first["text"]), ("Iroha", "Go to Norg, <player>."), "the lookup key keeps the token")
        self.assertEqual(first["spoken"], "Go to [[nɔːɡ]], adventurer.", "the engine hears the neutral word and the lexicon")
        self.assertEqual(first["voice"], "af_sarah")
        self.assertEqual(first["sources"], [{"zone": 252, "event": 286, "text_id": 1, "game_text": "h1"}])
        self.assertAlmostEqual(first["duration"], 0.1)
        opus = final / first["file"]
        self.assertEqual(first["sha256"], hashlib.sha256(opus.read_bytes()).hexdigest())
        self.assertEqual(first["bytes"], opus.stat().st_size)
        self.assertEqual(m["duration"], 0.2)
        written = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(written["engine"]["name"], "fake")
        self.assertEqual(written["source"]["commit"], "abc123")

    def test_two_pass_backend_renders_codes_then_audio(self):
        calls = []

        class TwoPass:
            name = "fake2"

            def prepare(self, keys):
                calls.append(("prepare", tuple(keys)))

            def begin_generate(self):
                calls.append(("begin_generate",))

            def generate_codes(self, text, key, path):
                calls.append(("codes", text, key))
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(text)

            def begin_decode(self):
                calls.append(("begin_decode",))

            def decode_codes(self, path, out):
                calls.append(("decode", Path(path).read_text()))
                sh.write_wav(out, [0.0] * 2400, 24000)

        lines, skipped = self.lines()
        b = self.builder()
        b.two_pass = TwoPass()
        b.engine = lambda: {"name": "fake2", "clips": {"iroha": "abc"}}
        m = b.build(lines, skipped)
        self.assertEqual([c[0] for c in calls],
                         ["prepare", "begin_generate", "codes", "codes", "begin_decode", "decode", "decode"])
        self.assertEqual(calls[0], ("prepare", ("af_sarah", "am_adam")))
        self.assertEqual(self.synthesised, [], "the one-line path is not used")
        self.assertEqual(m["engine"], {"name": "fake2", "clips": {"iroha": "abc"}}, "recorded after the passes")
        self.assertEqual(list((self.dir / "cache" / "fake2-codes").glob("*.pt")), [], "codes are removed once decoded")
        self.assertEqual(list((self.dir / "cache").glob("*.tmp.wav")), [])
        # everything cached now: a rebuild runs neither pass
        calls.clear()
        b.build(lines, skipped, force=True)
        self.assertEqual(calls, [])

    def test_two_pass_resumes_in_the_decode_pass(self):
        lines, skipped = self.lines()
        b = self.builder()
        rows = b.plan(lines)
        codes_dir = self.dir / "cache" / "fake2-codes"
        codes_dir.mkdir(parents=True)
        for r in rows:   # pass one finished before an interruption
            (codes_dir / f"{b.wav_path(r).stem}.pt").write_text(r["spoken"])
        calls = []

        class TwoPass:
            name = "fake2"
            prepare = staticmethod(lambda keys: calls.append("prepare"))
            begin_generate = staticmethod(lambda: calls.append("begin_generate"))
            generate_codes = staticmethod(lambda *a: calls.append("codes"))
            begin_decode = staticmethod(lambda: calls.append("begin_decode"))
            decode_codes = staticmethod(lambda path, out: (calls.append("decode"),
                                                           sh.write_wav(out, [0.0] * 2400, 24000)))

        b.two_pass = TwoPass()
        b.build(lines, skipped)
        self.assertEqual(calls, ["prepare", "begin_decode", "decode", "decode"])

    def test_voices_archive_holds_exactly_the_clips_used(self):
        refs = self.dir / "refs"
        refs.mkdir()
        sh.write_wav(refs / "iroha.wav", [0.1] * 2400, 24000)
        sh.write_wav(refs / "tenzen.wav", [0.2] * 2400, 24000)
        (refs / "index.json").write_text(json.dumps({"calibration": "Wait here.", "model": "Q",
                                                     "voices": {"iroha": {"seed": 1}, "tenzen": {"seed": 2}}}))
        engine = {"clips": {"iroha": bp.sha256_file(refs / "iroha.wav")}}
        out = self.dir / "voices.zip"
        sha, size = bp.write_voices_archive(engine, refs, out)
        import zipfile
        with zipfile.ZipFile(out) as z:
            self.assertEqual(sorted(z.namelist()), ["voices/index.json", "voices/iroha.wav"])
            index = json.loads(z.read("voices/index.json"))
        self.assertEqual(index, {"calibration": "Wait here.", "model": "Q", "voices": {"iroha": {"seed": 1}}})
        self.assertIsNone(bp.write_voices_archive({"name": "kokoro"}, refs, self.dir / "none.zip"))
        sh.write_wav(refs / "iroha.wav", [0.3] * 2400, 24000)   # redesigned after the build
        with self.assertRaises(RuntimeError):
            bp.write_voices_archive(engine, refs, out)

    def test_engine_info_prefers_the_backends_record(self):
        class Rec:
            def engine_record(self):
                return {"name": "moss", "clips": {}}
        self.assertEqual(bp.engine_info(Rec()), {"name": "moss", "clips": {}})

    def test_archive_index_and_notes(self):
        lines, skipped = self.lines()
        m = self.builder().build(lines, skipped)
        final = self.dir / "rhapsodies" / "0.1"
        sha, size = bp.write_archive(final, self.dir / "rhapsodies-0.1.zip")
        import zipfile
        with zipfile.ZipFile(self.dir / "rhapsodies-0.1.zip") as z:
            names = sorted(z.namelist())
            self.assertEqual(names[-1], "rhapsodies/0.1/manifest.json")
            self.assertTrue(all(n.startswith("rhapsodies/0.1/") for n in names))
            self.assertEqual(len(names), 3)
            self.assertEqual(z.getinfo(names[0]).compress_type, zipfile.ZIP_STORED)
        self.assertEqual(sha, hashlib.sha256((self.dir / "rhapsodies-0.1.zip").read_bytes()).hexdigest())
        url = bp.download_url("delphyne/ffxi-spoken", "rhapsodies", "0.1")
        self.assertEqual(url, "https://github.com/delphyne/ffxi-spoken/releases/download/rhapsodies-0.1/rhapsodies-0.1.zip")
        self.assertIsNone(bp.download_url(None, "rhapsodies", "0.1"))
        index_path = self.dir / "packs" / "index.json"
        bp.update_index(index_path, dict(m, pack="core"), "x", 1, None)
        index = bp.update_index(index_path, m, sha, size, url)
        self.assertEqual(list(index["packs"]), ["core", "rhapsodies"], "sorted, earlier packs kept")
        entry = index["packs"]["rhapsodies"]
        self.assertEqual((entry["version"], entry["url"], entry["sha256"], entry["bytes"], entry["files"]),
                         ("0.1", url, sha, size, 2))
        self.assertEqual(entry["manifest"], "packs/rhapsodies/0.1/manifest.json")
        self.assertEqual(json.loads(index_path.read_text())["packs"]["rhapsodies"]["version"], "0.1")
        notes = bp.release_notes(m, sha)
        self.assertIn("rhapsodies 0.1: 2 lines from 2 speakers", notes)
        self.assertIn("from commit abc123.", notes)
        self.assertIn(f"sha256: {sha}", notes)
        self.assertIn("audio/rhapsodies/0.1/manifest.json", notes)

    def test_provenance_reads_git(self):
        prov = bp.provenance()
        self.assertEqual(prov["builder"], "tools/build_pack.py")
        self.assertTrue(prov["commit"] is None or len(prov["commit"]) == 40)
        self.assertEqual(bp.provenance(self.dir)["commit"], None, "not a repo")

    def test_rebuild_uses_the_wav_cache_and_needs_force(self):
        lines, skipped = self.lines()
        b = self.builder()
        b.build(lines, skipped)
        self.assertEqual(len(self.synthesised), 2)
        with self.assertRaises(FileExistsError):
            b.build(lines, skipped)
        b.build(lines, skipped, force=True)
        self.assertEqual(len(self.synthesised), 2, "cached WAVs are not synthesised again")
        self.builder("0.2").build(lines, skipped, limit=1)
        self.assertEqual(len(json.loads((self.dir / "rhapsodies/0.2/manifest.json").read_text())["lines"]), 1)


if __name__ == "__main__":
    unittest.main()
