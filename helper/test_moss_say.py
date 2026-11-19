"""Tests for moss_say.py: the voice review's MOSS worker, with a fake engine."""

import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import moss_say  # noqa: E402
from test_moss import FakeEngine  # noqa: E402


class SayerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.clip = self.root / "iroha.wav"
        with wave.open(str(self.clip), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\0\0" * 2400)
        self.engine = FakeEngine()
        self.sayer = moss_say.Sayer(self.engine)

    def tearDown(self):
        self.dir.cleanup()

    def request(self, text="Master, [[vɑnədiˈɛl]] needs you.", out="hear/iroha.1.wav"):
        return {"ref": str(self.clip), "ref_text": "Wait here.", "seed": 7, "text": text,
                "out": str(self.root / out)}

    def test_a_line_continues_the_clip_with_the_lexicon_as_spans(self):
        out = self.sayer.say(self.request())
        self.assertEqual(out, str(self.root / "hear" / "iroha.1.wav"))
        self.assertEqual(self.engine.calls[:2], [("encode", "iroha.wav"),
                                                 ("generate", "Master, /vɑnədiɛl/ needs you.", ("iroha",), "Wait here.", 7)])
        with wave.open(out, "rb") as w:
            self.assertEqual((w.getnchannels(), w.getframerate(), w.getnframes()), (1, 24000, 2400))
        # the clip is encoded once while it does not change
        self.sayer.say(self.request(text="Again.", out="hear/iroha.2.wav"))
        self.assertEqual([c[0] for c in self.engine.calls].count("encode"), 1)
        self.clip.touch()
        self.sayer.say(self.request(text="Once more.", out="hear/iroha.3.wav"))
        self.assertEqual([c[0] for c in self.engine.calls].count("encode"), 2, "a changed clip is encoded again")

    def test_a_request_missing_a_field_is_refused(self):
        with self.assertRaisesRegex(ValueError, "lacks 'seed'"):
            self.sayer.say({"ref": str(self.clip), "ref_text": "x", "text": "y", "out": "z"})

    def test_serve_answers_one_reply_per_request_line(self):
        inp = io.StringIO(json.dumps(self.request()) + "\n\n" + json.dumps({"ref": "nowhere.wav"}) + "\n")
        out = io.StringIO()
        moss_say.serve(inp, out, self.sayer)
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(len(replies), 2, "a blank line is not a request")
        self.assertTrue(replies[0]["ok"])
        self.assertEqual(replies[0]["out"], str(self.root / "hear" / "iroha.1.wav"))
        self.assertIn("seconds", replies[0])
        self.assertFalse(replies[1]["ok"])
        self.assertIn("lacks 'ref_text'", replies[1]["error"])


if __name__ == "__main__":
    unittest.main()
