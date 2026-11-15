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
