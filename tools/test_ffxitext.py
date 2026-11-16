"""Tests for the Python text cleaner. The first block mirrors the text.clean
cases in test/test_Spoken.lua one for one; keep them in step."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ffxitext  # noqa: E402
from ffxitext import clean, expand, variants  # noqa: E402

PROMPT = b"\x7f\x31"
AUTO_PROMPT = b"\x7f\x34\x01"


class CleanMirrorsLua(unittest.TestCase):
    def test_speaker_and_prompt(self):
        self.assertEqual(clean(b"Iroha : Welcome, traveler." + PROMPT), ("Iroha", "Welcome, traveler.", True))

    def test_narration(self):
        self.assertEqual(clean(b"Some narration here."), ("", "Some narration here.", False))

    def test_colour_codes(self):
        self.assertEqual(clean(b"Iroha : Bring \x1e\x03the key\x1e\x01 now.")[1], "Bring the key now.")

    def test_line_break(self):
        self.assertEqual(clean(b"Iroha : First line.\x07Second line.")[1], "First line. Second line.")

    def test_elements(self):
        self.assertEqual(clean(b"Iroha : \xef\x1f and \xef\x24 crystals")[1], "Fire and Water crystals")

    def test_music_note(self):
        self.assertEqual(clean(b"Hegouard : Make merry this night!\x81\xed" + PROMPT)[1], "Make merry this night!")

    def test_ideographic_space_and_star(self):
        self.assertEqual(clean(b"Iroha : \x81\x40indented\x81\x99")[1], "indented")

    def test_auto_prompt_stripped_not_prompted(self):
        self.assertEqual(clean(b"Iroha : So..." + AUTO_PROMPT + b"\x07So what?")[1:], ("So... So what?", False))


class CleanDetails(unittest.TestCase):
    def test_speaker_prefix_must_be_short(self):
        long = b"x" * 40 + b" : y"
        self.assertEqual(clean(long)[0], "")

    def test_dat_entries_skip_speaker_rule(self):
        self.assertEqual(clean(b"Time : 5", with_speaker=False), ("", "Time : 5", False))

    def test_quotes_and_accent(self):
        self.assertEqual(clean(b"\x87\xb2Emptiness\x87\xb3 caf\x88\x69", with_speaker=False)[1], '"Emptiness" café')

    def test_str_input(self):
        self.assertEqual(clean("Iroha : Hi")[0:2], ("Iroha", "Hi"))


class Expand(unittest.TestCase):
    def test_plain_line_is_one_variant(self):
        self.assertEqual(expand(b"Who dares approach?" + PROMPT).variants, [b"Who dares approach?" + PROMPT])

    def test_choice_and_gender(self):
        e = expand(b"guide \x7f\x85[him/her] \x0c\x00[now/ later].")
        self.assertEqual(e.variants, [b"guide him now.", b"guide him  later.", b"guide her now.", b"guide her  later."])
        self.assertEqual(e.live_only, [])

    def test_plural_selector_and_player(self):
        self.assertEqual(expand(b"\x08, \x7f\x92\x01[it has/they have] gone").variants,
                         [b"<player>, it has gone", b"<player>, they have gone"])

    def test_live_only(self):
        self.assertEqual(expand(b"\n\x00 minutes").live_only, ["number"])
        self.assertEqual(expand(b"benefits of \x01\x05%\x82\x81\x80\x80 and").live_only, ["item"])
        self.assertEqual(expand(b"\x7f\x80\x01 odd").live_only, ["7f80"])
        self.assertEqual(expand(b"\n\x00").variants, [])

    def test_cap(self):
        many = b"[" + b"/".join(b"%d" % k for k in range(40)) + b"]"
        self.assertIn("too many variants", expand(b"\x0c\x00" + many).live_only)

    def test_variants_are_cleaned_and_deduped(self):
        v = variants(b"And \x7f\x85[his/her] legend.\x7f1")
        self.assertEqual(v, ["And his legend.", "And her legend."])
        self.assertEqual(variants(b"\x0c\x00[a/a]"), ["a"])
        self.assertEqual(ffxitext.PLAYER, "<player>")


if __name__ == "__main__":
    unittest.main()
