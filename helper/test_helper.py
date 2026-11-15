"""Tests for the helper's queue tailing and status handling (no audio)."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import spoken_helper as sh  # noqa: E402


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

    def test_cache_key_depends_on_everything(self):
        a = sh.cache_key("Iroha", "Hi", "v1")
        self.assertNotEqual(a, sh.cache_key("Iroha", "Hi", "v2"))
        self.assertNotEqual(a, sh.cache_key("Other", "Hi", "v1"))
        self.assertNotEqual(a, sh.cache_key("Iroha", "Hi!", "v1"))
        self.assertEqual(a, sh.cache_key("Iroha", "Hi", "v1"))


if __name__ == "__main__":
    unittest.main()
