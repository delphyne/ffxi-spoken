"""Tests for the proper-noun ranking rules; the corpus itself needs the client."""

import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import proper_nouns as pn  # noqa: E402

ENGLISH = {"lion", "siren", "iroha", "gilgamesh", "lord", "phoenix", "the", "what", "sorry"}


def say(speaker, variants, text=None):
    return {"kind": "say", "speaker": speaker, "variants": variants, "text": text}


class Words(unittest.TestCase):
    def test_double_hyphen_splits(self):
        self.assertEqual(list(pn.words_in("Vana'diel--is it? Wh-what")), ["Vana'diel", "is", "it", "Wh-what"])

    def test_stage_directions_are_not_words(self):
        self.assertEqual(list(pn.words_in("<Thniff> Lord Ragelise <Sniffle, sob> is gone.")),
                         ["Lord", "Ragelise", "is", "gone"])

    def test_rov_words_counts_a_line_once_and_collects_speakers(self):
        rows = [say("Iroha", ["Go to Norg, Lion.", "Go to Norg, Lion."]),      # two variants, one line
                say("Tenzen", ["Norg awaits."]),
                say("<player>", ["Ha."]),                                     # spoken, but no speaker name
                {"kind": "narration", "speaker": "", "variants": ["Norg."]},
                say("Zeid", [], text="The \x0c\x00[Ram/Rams] of Norg")]          # live-only: fixed text counts
        words, speakers = pn.rov_words(rows)
        self.assertEqual(words, Counter({"Norg": 3, "Go": 1, "Lion": 1, "Ha": 1, "The": 1, "Ram": 1, "Rams": 1}))
        self.assertEqual(speakers, {"Iroha", "Tenzen", "Zeid"})


class IsProper(unittest.TestCase):
    def proper(self, w, cap=5, low=0, npc=(), speakers=()):
        return pn.is_proper(w, cap, low, ENGLISH, set(npc), frozenset(speakers))

    def test_dictionary_words_are_not_proper(self):
        self.assertFalse(self.proper("Lion"))
        self.assertFalse(self.proper("Lord's"), "possessive of an English word")
        self.assertTrue(self.proper("Vana'diel's"))

    def test_speaker_names_skip_the_dictionary(self):
        self.assertFalse(self.proper("Iroha"))
        self.assertTrue(self.proper("Iroha", speakers={"Iroha"}))
        self.assertTrue(self.proper("Gilgamesh", npc={"Gilgamesh"}) is False, "a census name does not")

    def test_stutters_and_shouting_are_skipped(self):
        self.assertFalse(self.proper("Wh-what"))
        self.assertFalse(self.proper("S-sorry"))
        self.assertFalse(self.proper("NORG"))
        self.assertTrue(self.proper("Hut-tut"))

    def test_sentence_initial_common_words_need_npc_rescue(self):
        self.assertFalse(self.proper("Figurin", cap=4, low=3))
        self.assertTrue(self.proper("Figurin", cap=4, low=3, npc={"Figurin"}))


if __name__ == "__main__":
    unittest.main()
