"""Tests for the helper's queue tailing and status handling (no audio)."""

import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import spoken_helper as sh  # noqa: E402
import respell  # noqa: E402


class QueueTailTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "queue.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def append(self, text):
        with self.path.open("a") as f:
            f.write(text)

    def test_missing_file_is_empty(self):
        self.assertEqual(sh.QueueTail(self.path).poll(), [])

    def test_yields_only_whole_lines(self):
        tail = sh.QueueTail(self.path)
        self.append('{"seq": 1, "text": "a"}\n{"seq": 2, "te')
        self.assertEqual([e["seq"] for e in tail.poll()], [1])
        self.append('xt": "b"}\n')
        self.assertEqual([e["seq"] for e in tail.poll()], [2])
        self.assertEqual(tail.poll(), [])

    def test_skips_lines_written_before_startup(self):
        self.append('{"seq": 1, "text": "old"}\n')
        tail = sh.QueueTail(self.path)
        self.assertEqual(tail.poll(), [])
        self.append('{"seq": 2, "text": "new"}\n')
        self.assertEqual([e["seq"] for e in tail.poll()], [2])
        self.assertEqual([e["seq"] for e in sh.QueueTail(self.path, replay=True).poll()], [1, 2])

    def test_restarts_after_truncation(self):
        tail = sh.QueueTail(self.path)
        self.append('{"seq": 1, "text": "a"}\n{"seq": 2, "text": "b"}\n')
        tail.poll()
        self.path.write_text('{"seq": 3, "text": "c"}\n')
        self.assertEqual([e["seq"] for e in tail.poll()], [3])

    def test_bad_lines_are_skipped(self):
        tail = sh.QueueTail(self.path)
        self.append('not json\n{"seq": 1, "text": "a"}\n')
        self.assertEqual([e["seq"] for e in tail.poll()], [1])


class HelperTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.data = Path(self.dir.name) / "data"
        self.data.mkdir()
        backend = sh.NoneBackend(Path(self.dir.name) / "voices", {"default": "narrator", "Iroha": "iroha"})
        self.helper = sh.Helper(self.data, backend, Path(self.dir.name) / "cache", player=None)

    def tearDown(self):
        self.dir.cleanup()

    def status(self):
        return json.loads((self.data / "status.json").read_text())

    def queue(self, entry):
        with (self.data / "queue.jsonl").open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def test_speak_then_done(self):
        self.queue({"seq": 1, "speaker": "Iroha", "text": "Hi", "prompted": True})
        self.helper.tick()
        self.assertEqual(self.status()["state"], "speaking")
        self.assertEqual(self.status()["seq"], 1)
        time.sleep(0.1)
        self.helper.tick()
        self.assertEqual(self.status()["state"], "done")

    def test_stop_goes_idle(self):
        self.queue({"seq": 1, "speaker": "", "text": "Hi there"})
        self.helper.tick()
        self.queue({"seq": 2, "stop": True, "reason": "release"})
        self.helper.tick()
        self.assertEqual(self.status()["state"], "idle")
        self.assertEqual(self.status()["reason"], "release")

    def test_newer_line_replaces_current(self):
        self.queue({"seq": 1, "speaker": "", "text": "one two three four five six"})
        self.helper.tick()
        self.queue({"seq": 2, "speaker": "", "text": "x"})
        self.helper.tick()
        self.assertEqual(self.status()["seq"], 2)

    def test_voice_map(self):
        self.assertEqual(self.helper.backend.voice_for("Iroha"), "iroha")
        self.assertEqual(self.helper.backend.voice_for("Nobody"), "narrator")

    def test_grouped_voice_map_picks_backend(self):
        path = Path(self.dir.name) / "voices.json"
        path.write_text(json.dumps({"kokoro": {"default": "am_adam", "Iroha": "jf_alpha"},
                                    "piper": {"default": "en_US-lessac-medium"}}))
        self.assertEqual(sh.load_voice_map(path, "kokoro"), {"default": "am_adam", "Iroha": "jf_alpha"})
        self.assertEqual(sh.load_voice_map(path, "piper"), {"default": "en_US-lessac-medium"})
        self.assertEqual(sh.load_voice_map(path, "espeak"), {})

    def test_flat_voice_map_applies_to_any_backend(self):
        path = Path(self.dir.name) / "voices.json"
        path.write_text(json.dumps({"default": "x", "Iroha": "y"}))
        self.assertEqual(sh.load_voice_map(path, "piper"), {"default": "x", "Iroha": "y"})
        self.assertEqual(sh.load_voice_map(path / "missing", "piper"), {})

    def test_cache_key_depends_on_everything(self):
        a = sh.cache_key("Iroha", "Hi", "v1")
        self.assertNotEqual(a, sh.cache_key("Iroha", "Hi", "v2"))
        self.assertNotEqual(a, sh.cache_key("Other", "Hi", "v1"))
        self.assertNotEqual(a, sh.cache_key("Iroha", "Hi!", "v1"))
        self.assertEqual(a, sh.cache_key("Iroha", "Hi", "v1"))


class CensusCastingTest(unittest.TestCase):
    census = {"Numi Adaligo": {"race": 7, "talks": True}, "Hyria": {"race": 2}, "Iroha": {"race": None, "model": 2449}}

    def test_race_picks_pool_deterministically(self):
        import casting
        v = casting.census_voice(self.census, "Numi Adaligo", "kokoro")
        self.assertIn(v, casting.POOLS["mithra"][0])
        self.assertEqual(v, casting.census_voice(self.census, "Numi Adaligo", "kokoro"))
        self.assertEqual(casting.census_voice(self.census, "Hyria", "piper"), casting.POOLS["hume_f"][1])
        self.assertIsNone(casting.census_voice(self.census, "Iroha", "kokoro"), "no race, no guess")
        self.assertIsNone(casting.census_voice(self.census, "Nobody", "kokoro"))
        self.assertIsNone(casting.census_voice(self.census, "Hyria", "espeak"))

    def test_backend_uses_fallback_then_default(self):
        import casting
        backend = sh.NoneBackend("voices", {"default": "narrator", "Iroha": "iroha"},
                                 fallback=lambda name: casting.census_voice(self.census, name, "kokoro"))
        self.assertEqual(backend.voice_for("Iroha"), "iroha")
        self.assertIn(backend.voice_for("Hyria"), casting.POOLS["hume_f"][0])
        self.assertEqual(backend.voice_for("Nobody"), "narrator")
        self.assertEqual(backend.voice_for(""), "narrator")

    def test_census_fallback_works_under_isolated_python(self):
        """`make helper` runs python -I from the repo root; casting.py must still import."""
        import subprocess
        here = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as d:
            census = Path(d) / "census.json"
            census.write_text(json.dumps(self.census))
            code = (f"import runpy; m = runpy.run_path({str(here / 'spoken_helper.py')!r}); "
                    f"print(m['census_fallback']({str(census)!r}, 'piper')('Hyria'))")
            out = subprocess.run([sys.executable, "-I", "-c", code], cwd=d, capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.strip(), "en_US-amy-medium")

    def test_census_fallback_loader(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(sh.census_fallback(Path(d) / "missing.json", "kokoro"))
            path = Path(d) / "census.json"
            path.write_text(json.dumps(self.census))
            fb = sh.census_fallback(path, "kokoro")
            self.assertIn(fb("Hyria"), sh.BACKENDS and __import__("casting").POOLS["hume_f"][0])


class CensusBuildTest(unittest.TestCase):
    """census.py's join with the event decoder: rov_speakers decides the
    Rhapsodies flag, and the report splits uncast speakers by whether a
    race pool can cover them."""
    npcs = {("mhaura", "Numi_Adaligo", 1): {"name": "Numi Adaligo", "zone": "mhaura", "zone_id": 249, "race": 7,
                                           "look": "equipped", "model": None},
            ("norg", "Kamui", 2): {"name": "Kamui", "zone": "norg", "zone_id": 252, "race": None,
                                   "look": "standard", "model": 93},
            ("norg", "Door", 3): {"name": "Oaken Door", "zone": "norg", "zone_id": 252, "race": None,
                                  "look": "door", "model": None}}

    def test_decoder_speakers_set_the_rov_flag(self):
        import census
        c = census.build(self.npcs, {(None, "Door")}, {"Door"}, None, {"Numi Adaligo": 19, "Kamui": 3, "???": 18})
        self.assertEqual({n for n, e in c.items() if e["rov"]}, {"Numi Adaligo", "Kamui", "???"})
        self.assertEqual(c["Kamui"]["rov_lines"], 3)
        self.assertEqual((c["???"]["source"], c["???"]["race"], c["???"]["talks"]), ("decoder", None, True))
        self.assertFalse(c["Oaken Door"]["rov"], "a triggered door is not a speaker once the decoder has spoken")

    def test_without_the_decoder_triggers_decide(self):
        import census
        c = census.build(self.npcs, {(None, "Door")}, {"Door"}, None, None)
        self.assertTrue(c["Oaken Door"]["rov"])
        self.assertNotIn("rov_lines", c["Oaken Door"])

    def test_audit_splits_pooled_from_hand_cast(self):
        import census
        c = census.build(self.npcs, set(), set(), None, {"Numi Adaligo": 19, "Kamui": 3, "???": 18, "Iroha": 382})
        pooled, by_hand = census.rov_audit(c, cast={"Iroha"})
        self.assertEqual(pooled, [("Numi Adaligo", 19, 7)])
        self.assertEqual(by_hand, [("???", 18, None), ("Kamui", 3, None)])


class ReviewTest(unittest.TestCase):
    """The review loop with scripted keys and a fake speaker."""

    class FakeSpeaker:
        def __init__(self):
            self.said = []

        def word(self, ipa):
            self.said.append(("word", ipa))

        def in_context(self, word, ipa, sentence):
            self.said.append(("context", ipa, sentence))

    def run_review(self, keys, candidates, progress=None, lexicon_text="Jeuno: Juno\n"):
        import review
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        lex = Path(d.name) / "lexicon.yaml"
        lex.write_text(lexicon_text)
        prog_path = Path(d.name) / "progress.json"
        keys = iter(keys)
        speaker = self.FakeSpeaker()
        out = []
        progress = progress if progress is not None else {}
        covered = review.review(candidates, progress, lex, speaker, read=lambda prompt: next(keys, "q"),
                                out=out.append, progress_path=prog_path)
        return covered, progress, lex.read_text(), speaker, "\n".join(out), prog_path

    def cands(self):
        return [{"word": "Altana", "uses": "1048", "rov_uses": "11", "zones": "40", "npc": "", "kokoro_ipa": "ɔːltˈɑːnə",
                 "example": "Praise be to Altana."},
                {"word": "Jeuno", "uses": "1739", "rov_uses": "22", "zones": "60", "npc": "", "kokoro_ipa": "dʒjˈuːnoʊ",
                 "example": ""},
                {"word": "Norg", "uses": "300", "rov_uses": "23", "zones": "9", "npc": "y", "kokoro_ipa": "nˈɔːɹɡ",
                 "example": "Norg is a pirate den."}]

    def test_accept_skip_and_lexicon_words_are_not_asked(self):
        covered, progress, lexicon, speaker, out, prog_path = self.run_review(["a", "s"], self.cands())
        self.assertEqual(covered, 2)
        self.assertEqual(progress["Altana"]["status"], "accepted")
        self.assertEqual(progress["Norg"]["status"], "skipped")
        self.assertNotIn("Jeuno", progress, "already in the lexicon")
        self.assertIn('Altana:\n    ipa: "ɔːltˈɑːnə"', lexicon)
        self.assertNotIn("Norg:", lexicon)
        self.assertEqual(speaker.said, [("word", "ɔːltˈɑːnə"), ("word", "nˈɔːɹɡ")])
        self.assertTrue(prog_path.exists())

    def test_reject_loops_until_accepted_and_validates(self):
        keys = ["j", "lIlI", "j", "ɔl.'tɑː.nə", "r", "e", "a", "q"]
        covered, progress, lexicon, speaker, out, _ = self.run_review(keys, self.cands())
        self.assertEqual(covered, 1, "quit on the second word")
        self.assertEqual(progress["Altana"]["ipa"], "ɔltˈɑːnə", "dots dropped, stress moved before the vowel")
        self.assertIn('Altana:\n    ipa: "ɔltˈɑːnə"', lexicon)
        self.assertIn("unknown IPA characters", out)
        self.assertEqual(speaker.said[1], ("word", "ɔltˈɑːnə"))
        self.assertEqual(speaker.said[2], ("word", "ɔltˈɑːnə"))
        self.assertEqual(speaker.said[3], ("context", "ɔltˈɑːnə", "Praise be to Altana."))
        self.assertIn("resumes here", out)

    def test_resumes_after_quit(self):
        _, progress, _, _, _, _ = self.run_review(["a", "q"], self.cands())
        covered, progress, _, speaker, out, _ = self.run_review(["s"], self.cands(), progress=progress)
        self.assertEqual(covered, 1)
        self.assertEqual(speaker.said, [("word", "nˈɔːɹɡ")], "only the uncovered word is asked")
        self.assertIn("all 1 reviewed", out)

    def test_back_undoes_an_accept_and_a_skip(self):
        keys = ["a", "s", "b", "b", "a", "s"]
        covered, progress, lexicon, speaker, out, _ = self.run_review(keys, self.cands())
        self.assertEqual(covered, 2, "two undone, two redone")
        self.assertEqual(progress["Altana"]["status"], "accepted")
        self.assertEqual(progress["Norg"]["status"], "skipped")
        self.assertEqual(lexicon.count("Altana:"), 1, "the undone accept left no stanza behind")
        self.assertEqual([s[1] for s in speaker.said], ["ɔːltˈɑːnə", "nˈɔːɹɡ", "nˈɔːɹɡ", "ɔːltˈɑːnə", "nˈɔːɹɡ"],
                         "each word is played again when it comes back: Norg from the end, then Altana")
        self.assertIn("back; its earlier choice is undone", out)

    def test_back_at_the_first_word_and_from_the_end(self):
        keys = ["b", "a", "a", "b", "s", "q"]
        covered, progress, lexicon, _, out, _ = self.run_review(keys, self.cands())
        self.assertIn("nothing before this word", out)
        self.assertIn("all 2 reviewed", out)
        self.assertEqual(progress["Norg"]["status"], "skipped", "redone after backing from the end")
        self.assertNotIn("Norg:", lexicon)
        self.assertEqual(covered, 2)

    def test_back_reaches_an_earlier_run_and_keeps_its_ipa(self):
        _, progress, _, _, _, _ = self.run_review(["j", "ɔl'tɑːnə", "a", "q"], self.cands())
        self.assertEqual(progress["Altana"]["ipa"], "ɔltˈɑːnə")
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        import review
        lex = Path(d.name) / "lexicon.yaml"
        lex.write_text("Jeuno: Juno\n")
        review.append_entry(lex, "Altana", "ɔltˈɑːnə", note="1048 uses")
        speaker = self.FakeSpeaker()
        ses = review.Session(self.cands(), progress, lex, speaker)
        self.assertEqual(ses.intro(), "1 to review, 1 covered before, 2 already in the lexicon")
        self.assertTrue(ses.advance())
        self.assertEqual(ses.word, "Norg", "the run starts after the covered word")
        self.assertTrue(ses.back())
        self.assertEqual((ses.word, ses.current, ses.engine_ipa, ses.mine), ("Altana", "ɔltˈɑːnə", "ɔːltˈɑːnə", True))
        self.assertNotIn("Altana", progress)
        self.assertEqual(lex.read_text(), "Jeuno: Juno\n", "the stanza from the earlier run is gone")
        self.assertEqual(ses.covered, 0, "undoing an earlier run's word does not count against this one")
        ses.accept()
        self.assertEqual(ses.word, "Norg")
        ses.skip()
        self.assertTrue(ses.done)
        self.assertEqual(ses.farewell(), "all 2 reviewed.")

    def test_remove_entry_takes_only_its_stanza(self):
        import review
        with tempfile.TemporaryDirectory() as d:
            lex = Path(d) / "lexicon.yaml"
            lex.write_text("Jeuno: Juno\n")
            review.append_entry(lex, "Cait Sith", "kˈeɪt sˈɪθ", note="456 uses")
            review.append_entry(lex, "Ru: Lude", "x")
            review.append_entry(lex, "Norg", "nˈɔːɹɡ")
            self.assertTrue(review.remove_entry(lex, "Ru: Lude"))
            self.assertFalse(review.remove_entry(lex, "Zi'Tah"))
            self.assertEqual(set(sh.Lexicon.load(lex).entries), {"Jeuno", "Cait Sith", "Norg"})
            self.assertTrue(review.remove_entry(lex, "Jeuno"), "a one-line entry")
            self.assertEqual(lex.read_text(), 'Cait Sith:\n    ipa: "kˈeɪt sˈɪθ"   # 456 uses\nNorg:\n    ipa: "nˈɔːɹɡ"\n')

    def test_words_of_multiword_entries_are_covered(self):
        import review
        self.assertEqual(review.covered_words({"Cait Sith": {}, "Jeuno": {}}), {"Cait Sith", "Cait", "Sith", "Jeuno"})
        cands = self.cands() + [{"word": "Sith", "uses": "596", "rov_uses": "6", "zones": "30", "npc": "y",
                                 "kokoro_ipa": "sˈɪθ", "example": ""}]
        covered, progress, _, speaker, out, _ = self.run_review(["s", "s"], cands,
                                                                 lexicon_text='Jeuno: Juno\n"Cait Sith":\n  ipa: "kˈeɪt sˈɪθ"\n')
        self.assertEqual([s[1] for s in speaker.said], ["ɔːltˈɑːnə", "nˈɔːɹɡ"], "Sith is covered by Cait Sith")
        self.assertIn("2 already in the lexicon", out)

    def test_commands_and_ipa_come_from_separate_readers(self):
        import review
        keys, lines = iter(["j", "a", "", "q"]), iter(["ɔl'tɑːnə"])
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        lex = Path(d.name) / "lexicon.yaml"
        lex.write_text("")
        progress = {}
        review.review(self.cands()[:2], progress, lex, self.FakeSpeaker(), read=lambda p: next(lines),
                      out=lambda s: None, key=lambda p: next(keys))
        self.assertEqual(progress["Altana"]["ipa"], "ɔltˈɑːnə")
        self.assertNotIn("Jeuno", progress, "an empty key is ignored, then q quits")

    def test_read_key_falls_back_to_lines_off_a_terminal(self):
        import io
        import review
        with unittest.mock.patch("sys.stdin", io.StringIO("a\n")), unittest.mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(review.read_key("> "), "a")
        with unittest.mock.patch("sys.stdin", io.StringIO("")), unittest.mock.patch("sys.stdout", io.StringIO()):
            self.assertRaises(EOFError, review.read_key, "> ")

    def test_append_entry_quotes_awkward_keys(self):
        import review
        with tempfile.TemporaryDirectory() as d:
            lex = Path(d) / "lexicon.yaml"
            review.append_entry(lex, "Cait Sith", "kˈeɪt sˈɪθ", note="456 uses")
            review.append_entry(lex, "Zi'Tah", "zˈiːtɑː")
            review.append_entry(lex, "Ru: Lude", "x")
            entries = sh.Lexicon.load(lex).entries
            self.assertEqual(set(entries), {"Cait Sith", "Zi'Tah", "Ru: Lude"})
            self.assertEqual(entries["Cait Sith"]["ipa"], "kˈeɪt sˈɪθ")


class ReviewEntriesTest(unittest.TestCase):
    """Reviewing the lexicon's existing entries: in-place edits, delete, and
    the way back to the incoming words."""

    LEXICON = ("# header\n\nVana'diel:\n    ipa: vɑː.nɑː.djɛl\nJeuno:\n    ipa: \"dʒjˈuːnoʊ\"   # 1739 uses\n"
               "\"Cait Sith\":\n    ipa: \"kˈɛt ʃˈiː\"\n    spell: KET shee\nNorg: Nawg\n")

    def setUp(self):
        import review
        self.review = review
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.lex = Path(self.dir.name) / "lexicon.yaml"
        self.lex.write_text(self.LEXICON, encoding="utf-8")
        self.prog = Path(self.dir.name) / "progress.json"

    def session(self, cands=None, ipa_for=lambda w: f"eng-{w}".replace("'", "").replace(" ", "")):
        self.speaker = ReviewTest.FakeSpeaker()
        return self.review.Session(cands if cands is not None else ReviewTest().cands(), {}, self.lex, self.speaker,
                                   self.prog, ipa_for=ipa_for)

    def test_replace_entry_keeps_place_comment_and_spell(self):
        r = self.review
        self.assertTrue(r.replace_entry(self.lex, "Jeuno", "ʒˈuːnoʊ"))
        self.assertTrue(r.replace_entry(self.lex, "Cait Sith", "kˈaɪt sˈɪθ"))
        self.assertTrue(r.replace_entry(self.lex, "Norg", "nˈɔːɡ"))
        self.assertFalse(r.replace_entry(self.lex, "Nobody", "x"))
        text = self.lex.read_text(encoding="utf-8")
        self.assertEqual(text, "# header\n\nVana'diel:\n    ipa: vɑː.nɑː.djɛl\n"
                               "Jeuno:\n    ipa: \"ʒˈuːnoʊ\"   # 1739 uses\n"
                               "\"Cait Sith\":\n    ipa: \"kˈaɪt sˈɪθ\"\n    spell: KET shee\n"
                               "Norg:\n    ipa: \"nˈɔːɡ\"\n    spell: Nawg\n")
        entries = sh.Lexicon.load(self.lex).entries
        self.assertEqual(entries["Norg"]["spell"], "Nawg", "a respelling-only entry keeps its respelling")

    def test_remove_entry_matches_however_the_key_is_quoted(self):
        self.assertTrue(self.review.remove_entry(self.lex, "Cait Sith"))
        self.assertTrue(self.review.remove_entry(self.lex, "Vana'diel"))
        self.assertFalse(self.review.remove_entry(self.lex, "Cait Sith"))
        self.assertEqual(list(sh.Lexicon.load(self.lex).entries), ["Jeuno", "Norg"])
        self.assertTrue(self.lex.read_text().startswith("# header\n\nJeuno:"))

    def test_step_through_entries_and_back_to_the_queue(self):
        ses = self.session()
        ses.advance()
        self.assertEqual((ses.mode, ses.word), ("queue", "Altana"))
        ses.open_entries()
        self.assertEqual(ses.entries(), ["Vana'diel", "Jeuno", "Cait Sith", "Norg"])
        self.assertEqual((ses.mode, ses.word, ses.position()), ("entries", "Vana'diel", (1, 4)))
        self.assertEqual(ses.saved_ipa, "vɑː.nɑː.djɛl", "shown with its syllable dots")
        self.assertEqual(ses.current, "vɑː.nɑː.djɛl")
        self.assertEqual(ses.engine_ipa, "eng-Vanadiel")
        ses.play()
        self.assertEqual(self.speaker.said[-1], ("word", "vɑː.nɑː.djɛl"), "the speaker strips the dots itself")
        self.assertTrue(ses.accept(), "accept unchanged keeps it and moves on")
        self.assertEqual(ses.word, "Jeuno")
        self.assertEqual(ses.detail(), "1739 uses, 22 in the storyline, 60 zones", "counts from the incoming words")
        ses.set_ipa("ʒˈuː.noʊ")
        self.assertEqual(ses.current, "ʒˈuː.noʊ", "dots kept when editing an entry")
        ses.accept()
        self.assertEqual(sh.Lexicon.load(self.lex).entries["Jeuno"]["ipa_marked"], "ʒˈuː.noʊ")
        self.assertEqual(ses.word, "Cait Sith")
        self.assertIn("not among the incoming words", ses.detail())
        self.assertTrue(ses.back())
        self.assertEqual((ses.word, ses.current), ("Jeuno", "ʒˈuː.noʊ"), "back shows the saved edit")
        ses.skip()
        ses.skip()
        self.assertEqual(ses.word, "Norg")
        self.assertEqual(ses.saved_ipa, None, "a respelling-only entry has no IPA")
        self.assertEqual(ses.current, "nˈɔːɹɡ", "so it starts from the engine's reading")
        self.assertEqual(ses.saved_spelling(), "Nawg")
        self.assertFalse(ses.skip(), "running off the end closes the entries")
        self.assertEqual((ses.mode, ses.word), ("queue", "Altana"), "back where the queue was left")
        self.assertEqual(ses.position(), (1, 1), "Jeuno and Norg are in the lexicon, so only Altana is new")

    def test_open_at_a_word_delete_and_done(self):
        ses = self.session()
        ses.advance()
        ses.open_entries("Cait Sith")
        self.assertEqual(ses.position(), (3, 4))
        self.assertTrue(ses.delete())
        self.assertNotIn("Cait Sith", sh.Lexicon.load(self.lex).entries)
        self.assertEqual(ses.word, "Norg", "delete moves to the next entry")
        self.assertEqual(json.loads(self.prog.read_text())["Cait Sith"]["deleted"], True)
        self.assertTrue(ses.close_entries())
        self.assertEqual((ses.mode, ses.word), ("queue", "Altana"))
        self.assertFalse(ses.close_entries())
        self.assertFalse(ses.delete(), "delete is for existing entries only")

    def test_first_entry_has_no_back(self):
        ses = self.session()
        ses.open_entries()
        self.assertFalse(ses.back())
        self.assertEqual(ses.word, "Vana'diel")

    def test_terminal_loop_over_entries(self):
        keys = iter(["a", "j", "ʒˈuːnoʊ", "a", "d", "s"])
        out = []
        speaker = ReviewTest.FakeSpeaker()
        covered = self.review.review(ReviewTest().cands(), {}, self.lex, speaker, read=lambda p: next(keys, "q"),
                                     out=out.append, progress_path=self.prog, entries=True)
        text = "\n".join(out)
        self.assertIn("4 entries in the lexicon", text)
        self.assertIn("kept Vana'diel", text)
        self.assertIn("updated Jeuno: ʒˈuːnoʊ", text)
        self.assertIn("deleted Cait Sith", text)
        self.assertIn("end of the lexicon's entries", text)
        self.assertEqual(covered, 2)
        self.assertEqual(list(sh.Lexicon.load(self.lex).entries), ["Vana'diel", "Jeuno", "Norg"])
        out.clear()
        self.review.review(ReviewTest().cands(), {}, self.lex, speaker, read=lambda p: "q", out=out.append,
                           entries="Norg")
        self.assertIn("[3/3] Norg", "\n".join(out), "--entry starts at the word")


class ReviewEntriesGuiTest(unittest.TestCase):
    """The window's lexicon bar, under a display with Tk (the venv)."""

    def test_open_edit_delete_and_done(self):
        import review_gui
        if not review_gui.available():
            self.skipTest("no display or Tk")
        t = ReviewEntriesTest()
        t.setUp()
        ses = t.session()
        try:
            w = review_gui.Window(ses)
        except Exception as e:  # noqa: BLE001 - a display that refuses connections
            self.skipTest(f"Tk could not open a window: {e}")
        self.addCleanup(w.root.destroy)
        ses.advance()
        w.show()
        self.assertFalse(w.delete_button.winfo_ismapped() and w.done_button.winfo_ismapped())
        w.v_find.set("cait")
        w.filter_entries()
        self.assertEqual(list(w.find.cget("values")), ["Cait Sith"])
        w.open_entry()
        w.root.update()
        self.assertEqual((ses.mode, ses.word), ("entries", "Cait Sith"))
        self.assertTrue(w.delete_button.winfo_ismapped() and w.done_button.winfo_ismapped())
        self.assertEqual(w.v_count.get(), "entry 3 / 4")
        self.assertIn("kˈɛt ʃˈiː", w.v_saved.get())
        w.v_ipa.set("x")
        w.reset_ipa()
        self.assertEqual(w.v_ipa.get(), "kˈɛt ʃˈiː", "reset on an entry goes back to the lexicon's IPA")
        w.delete()
        self.assertEqual(ses.word, "Norg")
        self.assertIn("respelling 'Nawg'", w.v_saved.get())
        w.close_entries()
        w.root.update()
        self.assertEqual((ses.mode, ses.word), ("queue", "Altana"))
        self.assertFalse(w.delete_button.winfo_ismapped())
        self.assertEqual(w.v_saved.get(), "")
        w.v_find.set("zzz")
        w.open_entry()
        self.assertIn("no lexicon entry matches", w.v_status.get())


class ReviewGuiTest(unittest.TestCase):
    """The window's tables, checked without Tk (the system python3 lacks it)."""

    def test_palette_glyphs_are_ipa_the_lexicon_accepts(self):
        import review_gui
        for group, glyphs in review_gui.PALETTE:
            for glyph, example in glyphs:
                self.assertTrue(example, f"{glyph!r} in {group} has no example word")
                sh.normalise_ipa("palette", f"b{glyph}d" if glyph.strip() else "bd")  # raises on unknown glyphs
        self.assertIn(("ɡ", "get"), dict(review_gui.PALETTE)["Consonants"], "U+0261, not ASCII g")

    def test_palette_covers_the_sounds_the_respeller_names(self):
        import review_gui
        shown = {g for _, glyphs in review_gui.PALETTE for g, _ in glyphs}
        core = {"iː", "ɪ", "ɛ", "æ", "ɑː", "ɔː", "ʊ", "uː", "ʌ", "ə", "ɚ", "ɜː", "eɪ", "aɪ", "ɔɪ", "oʊ", "aʊ",
                "θ", "ð", "ʃ", "ʒ", "ŋ", "ɹ", "tʃ", "dʒ", "ɡ", "j", respell.PRIMARY, respell.SECONDARY, respell.LENGTH}
        self.assertEqual(core - shown, set())

    def test_keys_name_window_commands(self):
        import review_gui
        for cmd in review_gui.KEYS.values():
            self.assertTrue(cmd == "field" or callable(getattr(review_gui.Window, cmd)), cmd)

    def test_available_needs_a_display(self):
        import review_gui
        with unittest.mock.patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}):
            self.assertFalse(review_gui.available())

    def test_session_set_ipa_does_not_play(self):
        import review
        with tempfile.TemporaryDirectory() as d:
            lex = Path(d) / "lexicon.yaml"
            lex.write_text("")
            speaker = ReviewTest.FakeSpeaker()
            ses = review.Session(ReviewTest().cands()[:1], {}, lex, speaker)
            self.assertTrue(ses.advance())
            self.assertEqual(ses.set_ipa("ɔl'tɑːnə"), "ɔltˈɑːnə")
            self.assertEqual(speaker.said, [])
            self.assertTrue(ses.mine)
            ses.accept()
            self.assertTrue(ses.done)
            self.assertIn("all 1 reviewed", ses.farewell())


class RespellTest(unittest.TestCase):
    def test_table(self):
        import respell
        self.assertEqual(respell.respell("vˈænɐdˌiːl"), "VA-nuh-deel")
        self.assertEqual(respell.respell("vɑnədiˈɛl"), "vah-nuh-dee-EHL")
        self.assertEqual(respell.respell("mˈɪθɹə"), "MIH-thruh")
        self.assertEqual(respell.respell("ʃæntˈɑːɾoʊ"), "shan-TAH-toh")
        self.assertEqual(respell.respell("sˌæn dˈɔːɹiə"), "san DAW-ree-uh")
        self.assertEqual(respell.respell("hjˈuːm"), "HYOOM")
        self.assertEqual(respell.respell("ɐdvˈɛntʃɚɹɚ."), "uhd-VEHN-chur-rur.")

    def test_maximal_onset(self):
        import respell
        self.assertEqual(respell.respell("wˈɪndɜːst"), "WIHN-durst")        # nd is not an onset
        self.assertEqual(respell.respell("ɡˈɪlɡeɪməʃ"), "GIHL-gay-muhsh")   # lɡ is not
        self.assertEqual(respell.respell("vɑːnɑːdjɛl"), "vah-nah-dyehl")   # dj is
        self.assertEqual(respell.respell("ˈæstɹə"), "A-struh")             # stɹ is
        self.assertEqual(respell.respell("ˈæktɹəs"), "AK-truhs")           # ktɹ is not, tɹ is

    def test_espeak_stress_placement(self):
        import respell
        self.assertEqual(respell.espeak_stress("ˌlɪl.ɪ.ˈsɛt"), "lˌɪl.ɪ.sˈɛt")
        self.assertEqual(respell.espeak_stress("ˈstɹɛs"), "stɹˈɛs")
        self.assertEqual(respell.espeak_stress("kɐsˈɛt"), "kɐsˈɛt", "already in place")
        self.assertEqual(respell.espeak_stress("ˈɛlvɑːn"), "ˈɛlvɑːn", "vowel-initial stays")
        self.assertEqual(respell.espeak_stress("sˌæn ˈdɔːɹiə"), "sˌæn dˈɔːɹiə")

    def test_explicit_syllable_dots_win(self):
        import respell
        self.assertEqual(respell.respell("vɑː.nɑː.djɛl"), "vah-nah-dyehl")
        self.assertEqual(respell.respell("vɑːn.ɑː.djɛl"), "vahn-ah-dyehl")
        self.assertEqual(respell.respell("ˈæk.tɹəs"), "AK-truhs")

    def test_unknown_symbols_survive(self):
        import respell
        self.assertEqual(respell.respell("ˈɛlʕ"), "EHLʕ")
        self.assertEqual(respell.respell(""), "")


class LexiconTest(unittest.TestCase):
    def test_ipa_entries_take_either_form(self):
        lex = sh.Lexicon({"Vana'diel": {"ipa": "vɑnədiˈɛl", "spell": "Vahna-dee-el"},
                          "Altana": {"ipa": "ɔːltˈɑːnə"},
                          "Jeuno": "Juno"})
        self.assertEqual(lex.apply("Altana of Vana'diel, in Jeuno.", phonemes=True),
                         "[[ɔːltˈɑːnə]] of [[vɑnədiˈɛl]], in Juno.")
        self.assertEqual(lex.apply("Altana of Vana'diel, in Jeuno."),
                         "awl-TAH-nuh of Vahna-dee-el, in Juno.")
        with self.assertRaises(ValueError):
            sh.Lexicon({"x": {}})

    def test_ipa_is_normalised_and_validated(self):
        lex = sh.Lexicon({"Lilisette": {"ipa": ",lɪl:ɪ'sɛt"}, "Vana'diel": {"ipa": "vɑː.nɑː.djɛl"}})
        self.assertEqual(lex.entries["Lilisette"]["ipa"], "lˌɪlːɪsˈɛt", "ASCII , ' : become ˌ ˈ ː; stress moves before the vowel")
        self.assertEqual(lex.entries["Vana'diel"]["ipa"], "vɑːnɑːdjɛl", "engines get no dots")
        self.assertEqual(lex.apply("Vana'diel", phonemes=True), "[[vɑːnɑːdjɛl]]")
        self.assertEqual(lex.spelling("Vana'diel"), "vah-nah-dyehl", "the respeller keeps the dots")
        with self.assertRaises(ValueError) as cm:
            sh.Lexicon({"Lilisette": {"ipa": "lIlI'sEt"}})
        self.assertIn("'EI'", str(cm.exception))

    def test_yaml_syntax_error_is_explained(self):
        with tempfile.TemporaryDirectory() as d:
            y = Path(d) / "lex.yaml"
            y.write_text("Lilisette:\n    ipa: ,lIlI'sɛt\n", encoding="utf-8")
            with self.assertRaises(ValueError) as cm:
                sh.Lexicon.load(y)
            self.assertIn("line 2", str(cm.exception))
            self.assertIn("need quotes", str(cm.exception))

    def test_splice_phonemes(self):
        fake = lambda part: "<" + part + ">"  # noqa: E731
        self.assertEqual(sh.splice_phonemes("Welcome to [[vɑnədiˈɛl]], adventurer.", fake),
                         "<Welcome to> vɑnədiˈɛl <, adventurer.>")
        self.assertEqual(sh.splice_phonemes("[[a]] [[b]]", fake), "a b")
        self.assertEqual(sh.splice_phonemes("plain", fake), "<plain>")

    def test_whole_words_only_longest_first(self):
        lex = sh.Lexicon({"Vana'diel": "Vahna-dee-el", "San d'Oria": "San Doria", "Oria": "wrong", "_about": "note"})
        self.assertEqual(lex.apply("Welcome to Vana'diel, and to San d'Oria."), "Welcome to Vahna-dee-el, and to San Doria.")
        self.assertEqual(lex.apply("Vana'diel's winds"), "Vana'diel's winds")  # possessive is a different token, left alone
        self.assertEqual(lex.apply("Orias and Oriana"), "Orias and Oriana")
        self.assertEqual(lex.apply("Oria!"), "wrong!")

    def test_empty_lexicon_is_identity(self):
        self.assertEqual(sh.Lexicon().apply("Hail, soldier."), "Hail, soldier.")
        self.assertEqual(sh.Lexicon.load(Path("/nonexistent/lexicon.json")).apply("x"), "x")

    def test_shipped_lexicon_loads_and_has_the_seed(self):
        # The shipped file is hand-edited, so check its shape, not its contents.
        lex = sh.Lexicon.load(Path(__file__).resolve().parent / "lexicon.yaml")
        self.assertTrue(lex.entries, "lexicon.yaml should have at least one entry")
        for key in lex.entries:
            self.assertNotEqual(lex.apply(key), key, f"{key!r} should be respelled")
            self.assertNotIn("\n", lex.spelling(key))

    def test_yaml_and_json_lexicons_load_alike(self):
        with tempfile.TemporaryDirectory() as d:
            y = Path(d) / "lex.yaml"
            y.write_text("# comment\nVana'diel: Vahna-dee-el\nSan d'Oria: \"San Doria\"\n_note: ignored\n", encoding="utf-8")
            j = Path(d) / "lex.json"
            j.write_text(json.dumps({"Vana'diel": "Vahna-dee-el", "San d'Oria": "San Doria", "_note": "ignored"}))
            self.assertEqual(sh.Lexicon.load(y).entries, sh.Lexicon.load(j).entries)
            self.assertEqual(sh.Lexicon.load(y).apply("San d'Oria, Vana'diel"), "San Doria, Vahna-dee-el")
            (Path(d) / "bad.yaml").write_text("- a list\n")
            with self.assertRaises(ValueError):
                sh.Lexicon.load(Path(d) / "bad.yaml")

    def test_helper_reloads_lexicon_when_file_changes(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d) / "data"; data.mkdir()
            lex = Path(d) / "lexicon.yaml"
            lex.write_text("Jeuno: Juno\n")
            backend = sh.NoneBackend(Path(d) / "voices", {"default": "n"})
            spoken = []
            backend.synthesise = lambda text, voice, out: spoken.append(text)
            helper = sh.Helper(data, backend, Path(d) / "cache", player=None,
                               lexicon=sh.Lexicon.load(lex), lexicon_path=lex)
            lex.write_text("Jeuno: Zhoono\n")
            os.utime(lex, (time.time() + 5, time.time() + 5))  # make the change visible to a coarse mtime
            with (data / "queue.jsonl").open("a") as f:
                f.write(json.dumps({"seq": 1, "speaker": "", "text": "Off to Jeuno."}) + "\n")
            helper.lexicon_checked = 0.0
            helper.tick()
            self.assertEqual(spoken, ["Off to Zhoono."])
            # a broken edit is reported and the previous lexicon kept
            lex.write_text("Jeuno:\n  ipa: \"lIlI\"\n")
            os.utime(lex, (time.time() + 10, time.time() + 10))
            helper.lexicon_checked = 0.0
            self.assertFalse(helper.reload_lexicon())
            self.assertEqual(helper.lexicon.apply("Jeuno"), "Zhoono")
            self.assertIn("unknown IPA", json.loads((data / "status.json").read_text())["lexicon_error"])
            lex.write_text("Jeuno: Zhoono\n")
            os.utime(lex, (time.time() + 15, time.time() + 15))
            helper.lexicon_checked = 0.0
            self.assertTrue(helper.reload_lexicon())
            helper.write_status(0, "idle")
            self.assertNotIn("lexicon_error", json.loads((data / "status.json").read_text()))

    def test_helper_applies_lexicon_before_caching(self):
        d = tempfile.TemporaryDirectory()
        try:
            data = Path(d.name) / "data"; data.mkdir()
            backend = sh.NoneBackend(Path(d.name) / "voices", {"default": "n"})
            spoken = []
            backend.synthesise = lambda text, voice, out: spoken.append(text)
            helper = sh.Helper(data, backend, Path(d.name) / "cache", player=None,
                               lexicon=sh.Lexicon({"Vana'diel": "Vahna-dee-el"}))
            with (data / "queue.jsonl").open("a") as f:
                f.write(json.dumps({"seq": 1, "speaker": "", "text": "Vana'diel awaits."}) + "\n")
            helper.tick()
            self.assertEqual(spoken, ["Vahna-dee-el awaits."])
        finally:
            d.cleanup()


class SayTest(unittest.TestCase):
    def test_say_applies_lexicon_and_plays_once(self):
        with tempfile.TemporaryDirectory() as d:
            backend = sh.NoneBackend(Path(d) / "voices", {"default": "n", "Iroha": "jf_alpha"})
            made = []
            backend.synthesise = lambda text, voice, out: (made.append((text, voice)), out.write_bytes(b"RIFF"))[1]
            played = []
            real_run = sh.subprocess.run
            sh.subprocess.run = lambda cmd, **kw: (played.append(cmd), type("R", (), {"returncode": 0})())[1]
            try:
                lex = sh.Lexicon({"Vana'diel": {"ipa": "vɑnədiˈɛl", "spell": "Vahna-dee-el"}})
                rc = sh.say(backend, lex, ["paplay"], "Vana'diel", "Iroha", Path(d) / "cache")
                rc2 = sh.say(backend, lex, ["paplay"], "Vana'diel", "Iroha", Path(d) / "cache")
                backend.phonemes = True
                rc3 = sh.say(backend, lex, ["paplay"], "Vana'diel", "Iroha", Path(d) / "cache")
            finally:
                sh.subprocess.run = real_run
            self.assertEqual((rc, rc2, rc3), (0, 0, 0))
            self.assertEqual(made, [("Vahna-dee-el", "jf_alpha"), ("[[vɑnədiˈɛl]]", "jf_alpha")],
                             "second call is a cache hit; phoneme form is a different line")
            self.assertEqual(len(played), 3)
            self.assertTrue(played[0][1].endswith(".wav"))


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.voices = Path(self.dir.name) / "voices"
        self.voices.mkdir()

    def tearDown(self):
        self.dir.cleanup()

    def test_kokoro_lang_from_voice_prefix(self):
        self.assertEqual(sh.kokoro_lang("af_heart"), "en-us")
        self.assertEqual(sh.kokoro_lang("bm_george"), "en-gb")
        self.assertEqual(sh.kokoro_lang("jf_alpha"), "ja")
        self.assertEqual(sh.kokoro_lang("weird"), "en-us")

    def test_kokoro_prefers_cuda_and_skips_tensorrt(self):
        self.assertEqual(sh.kokoro_provider(["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]),
                         "CUDAExecutionProvider")
        self.assertEqual(sh.kokoro_provider(["CPUExecutionProvider"]), "CPUExecutionProvider")

    def test_backends_need_their_models(self):
        self.assertFalse(sh.PiperBackend.available(self.voices))
        self.assertFalse(sh.KokoroBackend.available(self.voices))
        self.assertTrue(sh.NoneBackend.available(self.voices))

    def test_explicit_backend_wins(self):
        self.assertIs(sh.pick_backend("none", self.voices), sh.NoneBackend)
        self.assertIs(sh.pick_backend("piper", self.voices), sh.PiperBackend)

    def test_missing_default_voice_is_an_error(self):
        piper = sh.PiperBackend(self.voices, {})
        with self.assertRaises(ValueError):
            piper.synthesise("hi", piper.voice_for("Anyone"), Path(self.dir.name) / "out.wav")

    def test_write_wav(self):
        import wave
        out = Path(self.dir.name) / "out.wav"
        sh.write_wav(out, [0.0, 0.5, -0.5, 2.0], 24000)
        with wave.open(str(out)) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()), (1, 2, 24000, 4))
            frames = w.readframes(4)
        self.assertEqual(frames[-2:], (32767).to_bytes(2, "little", signed=True))


class VoicesJsonTest(unittest.TestCase):
    """helper/voices.json is the shipped casting; keep it loadable and sane."""

    def setUp(self):
        self.path = Path(__file__).resolve().parent / "voices.json"
        self.data = json.loads(self.path.read_text())

    def test_grouped_by_backend_with_defaults(self):
        for backend in ("kokoro", "piper"):
            self.assertIn("default", self.data[backend])
            self.assertIn("", self.data[backend], f"{backend} needs a narrator voice")

    def test_kokoro_voice_names_are_well_formed(self):
        for speaker, voice in self.data["kokoro"].items():
            self.assertRegex(voice, r"^[abefhijpz][fm]_[a-z]+$", f"{speaker!r} -> {voice!r}")

    def test_piper_voices_are_the_downloaded_ones(self):
        self.assertLessEqual(set(self.data["piper"].values()),
                             {"en_US-lessac-medium", "en_US-amy-medium", "en_GB-alan-medium"})

    def test_matches_generator(self):
        import cast
        self.assertEqual(cast.render(), self.path.read_text(encoding="utf-8"),
                         "voices.json is stale; run python3 helper/cast.py")

    def test_same_character_variants_share_a_voice(self):
        k = self.data["kokoro"]
        self.assertEqual(k["Shantotto"], k["D. Shantotto"])
        self.assertEqual(k["Trion"], k["Prince Trion"])
        self.assertEqual(k["Iroha"], k["Iroha Red"])
        self.assertEqual(len({k[n] for n in k if n.startswith("Cait Sith")}), 1)


if __name__ == "__main__":
    unittest.main()
