"""The moss backend without torch: the pure helpers in moss.py, and
MossBackend driven by a fake engine."""

import json
import pickle
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import moss  # noqa: E402
import spoken_helper as sh  # noqa: E402


class FakeEngine:
    """Records calls; codes are plain Python objects pickled to disk."""

    device = "cuda"
    rate = 24000

    def __init__(self):
        self.calls = []

    def encode(self, clip):
        self.calls.append(("encode", Path(clip).name))
        return [Path(clip).stem]

    def generate(self, text, ref_codes, ref_text, seed):
        self.calls.append(("generate", text, tuple(ref_codes), ref_text, seed))
        return (3, text)

    def decode(self, generated):
        self.calls.append(("decode", generated))
        return [0.0] * 2400

    def save_codes(self, obj, path):
        Path(path).write_bytes(pickle.dumps(obj))

    def load_codes(self, path):
        return pickle.loads(Path(path).read_bytes())

    def free_codec(self):
        self.calls.append(("free_codec",))

    def free_model(self):
        self.calls.append(("free_model",))

    def load_model(self):
        self.calls.append(("load_model",))


class PureTest(unittest.TestCase):
    def test_ipa_spans_drop_stress(self):
        self.assertEqual(moss.ipa_spans("What on [[vɑːnɑːdjɛl]]... [[lˌɪlɪsˈɛt]]!", sh.PHONEME_BLOCK),
                         "What on /vɑːnɑːdjɛl/... /lɪlɪsɛt/!")
        self.assertEqual(moss.ipa_spans("plain text", sh.PHONEME_BLOCK), "plain text")

    def test_settings_from_env_and_tag(self):
        s = moss.Settings.from_env({})
        self.assertEqual(s.tag(), "t1:q32:fp16:cpu32")
        s = moss.Settings.from_env({"MOSS_TEMPERATURE": "0.7", "MOSS_N_VQ": "16", "MOSS_INT8": "1",
                                    "MOSS_CODEC": "gpu16"})
        self.assertEqual(s.tag(), "t0.7:q16:int8:gpu16")
        self.assertEqual(s.record(), {"temperature": 0.7, "n_vq": 16, "weights": "int8", "codec": "gpu16"})
        self.assertFalse(moss.Settings.from_env({"MOSS_INT8": "0"}).int8)
        with self.assertRaises(ValueError):
            moss.Settings(codec="gpu32")
        with self.assertRaises(ValueError):
            moss.Settings(n_vq=40)

    def test_voice_key_round_trip(self):
        key = moss.voice_key("cait-sith", "abcdef0123456789", moss.Settings())
        self.assertEqual(key, "moss:cait-sith:abcdef01:t1:q32:fp16:cpu32")
        self.assertEqual(moss.parse_voice_key(key), "cait-sith")
        with self.assertRaises(ValueError):
            moss.parse_voice_key("am_adam")

    def test_snapshot_revision(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(moss.snapshot_revision("A/B", hf_home=d))
            (Path(d) / "hub" / "models--A--B" / "snapshots" / "12aa734e").mkdir(parents=True)
            self.assertEqual(moss.snapshot_revision("A/B", hf_home=d), "12aa734e")


class MossBackendTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.voices = Path(self.dir.name) / "voices"
        self.refs = self.voices / "moss" / "refs"
        self.refs.mkdir(parents=True)
        for voice in ("iroha", "hume_m_adult-0"):
            sh.write_wav(self.refs / f"{voice}.wav", [0.0] * 2400, 24000)
        (self.refs / "index.json").write_text(json.dumps({
            "calibration": "Wait here.",
            "voices": {"iroha": {"sha256": "1111111122223333", "seed": 1733},
                       "hume_m_adult-0": {"sha256": "aaaaaaaabbbbcccc", "seed": 1}}}))
        self.engine = FakeEngine()
        self.backend = sh.MossBackend(self.voices, {"default": "hume_m_adult-0", "Iroha": "iroha"},
                                      settings=moss.Settings(), engine=self.engine)

    def tearDown(self):
        self.dir.cleanup()

    def test_available_needs_the_index(self):
        # transformers is not installed under the system python; the index check comes first either way
        self.assertFalse(sh.MossBackend.available(Path(self.dir.name) / "elsewhere"))

    def test_voice_for_carries_clip_hash_and_settings(self):
        self.assertEqual(self.backend.voice_for("Iroha"), "moss:iroha:11111111:t1:q32:fp16:cpu32")
        self.assertEqual(self.backend.voice_for("Nobody"), "moss:hume_m_adult-0:aaaaaaaa:t1:q32:fp16:cpu32")
        other = sh.MossBackend(self.voices, {"Iroha": "iroha"}, settings=moss.Settings(temperature=0.7),
                               engine=self.engine)
        self.assertNotEqual(other.voice_for("Iroha"), self.backend.voice_for("Iroha"))
        # the helper's cache key differs when the clip or a setting does
        a = sh.cache_key("Iroha", "Hi", self.backend.voice_for("Iroha"))
        self.assertNotEqual(a, sh.cache_key("Iroha", "Hi", other.voice_for("Iroha")))

    def test_missing_clip_or_default_is_an_error(self):
        backend = sh.MossBackend(self.voices, {"Iroha": "tenzen"}, settings=moss.Settings(), engine=self.engine)
        with self.assertRaises(FileNotFoundError):
            backend.voice_for("Iroha")
        with self.assertRaises(ValueError):
            backend.voice_for("Nobody")   # no default in the map

    def test_synthesise_continues_the_clip_with_its_seed(self):
        key = self.backend.voice_for("Iroha")
        out = Path(self.dir.name) / "line.wav"
        self.backend.synthesise("What on [[vˈɑːnɑːdjɛl]]...", key, out)
        self.assertEqual(self.engine.calls[0], ("encode", "iroha.wav"))
        self.assertEqual(self.engine.calls[1], ("generate", "What on /vɑːnɑːdjɛl/...", ("iroha",), "Wait here.", 1733))
        self.assertEqual(self.engine.calls[2], ("decode", (3, "What on /vɑːnɑːdjɛl/...")))
        with sh.wave.open(str(out)) as w:
            self.assertEqual(w.getframerate(), 24000)
        self.assertFalse(out.with_suffix(".codes.pt").exists(), "the one-off code file is cleaned up")
        self.assertTrue((self.refs / "iroha.11111111.codes.pt").exists(), "the clip's codes are kept")
        self.assertEqual(self.backend.used, {"iroha": "1111111122223333"})

    def test_clip_codes_are_reused_across_runs(self):
        key = self.backend.voice_for("Iroha")
        self.backend.synthesise("One.", key, Path(self.dir.name) / "a.wav")
        engine = FakeEngine()
        again = sh.MossBackend(self.voices, {"Iroha": "iroha"}, settings=moss.Settings(), engine=engine)
        again.synthesise("Two.", key, Path(self.dir.name) / "b.wav")
        self.assertNotIn("encode", [c[0] for c in engine.calls])

    def test_two_passes(self):
        keys = [self.backend.voice_for("Iroha"), self.backend.voice_for("Nobody")]
        self.backend.prepare(keys)
        self.backend.begin_generate()
        codes = Path(self.dir.name) / "codes"
        for i, key in enumerate(keys):
            self.backend.generate_codes(f"Line {i}.", key, codes / f"{i}.pt")
        self.backend.begin_decode()
        for i in range(2):
            self.backend.decode_codes(codes / f"{i}.pt", Path(self.dir.name) / f"{i}.wav")
        kinds = [c[0] for c in self.engine.calls]
        self.assertEqual(kinds, ["encode", "encode", "free_codec", "generate", "generate", "free_model",
                                 "decode", "decode"])

    def test_one_off_lines_refuse_the_gpu_codec(self):
        backend = sh.MossBackend(self.voices, {"Iroha": "iroha"}, settings=moss.Settings(codec="gpu16"),
                                 engine=self.engine)
        with self.assertRaises(ValueError):
            backend.synthesise("Hi.", backend.voice_for("Iroha"), Path(self.dir.name) / "x.wav")

    def test_engine_record(self):
        self.backend.synthesise("Hi.", self.backend.voice_for("Iroha"), Path(self.dir.name) / "x.wav")
        rec = self.backend.engine_record()
        self.assertEqual(rec["name"], "moss")
        self.assertEqual(rec["settings"]["codec"], "cpu32")
        self.assertEqual(rec["clips"], {"iroha": "1111111122223333"})
        self.assertEqual(rec["calibration"], "Wait here.")
        self.assertEqual(set(rec["models"]), {moss.MODEL_REPO, moss.CODEC_REPO})

    def test_not_in_auto(self):
        self.assertNotIn(sh.MossBackend, sh.AUTO_ORDER)
        self.assertIs(sh.pick_backend("moss", self.voices), sh.MossBackend)


if __name__ == "__main__":
    unittest.main()
