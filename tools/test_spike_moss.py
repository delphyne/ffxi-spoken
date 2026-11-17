"""The MOSS spike without the model: IPA span conversion, the render loop
with a fake engine, and the shared page growing MOSS columns."""

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spike_moss as sm  # noqa: E402
import spike_qwen3 as sp  # noqa: E402
import spoken_helper as sh  # noqa: E402


class FakeMoss:
    device = "cuda"

    def __init__(self):
        self.calls, self.freed = [], False

    def reseed(self, speaker):
        pass

    def peak_vram_mb(self):
        return 6000

    def clone(self, text, ref_wav):
        assert Path(ref_wav).exists(), ref_wav
        self.calls.append((text, Path(ref_wav).name))
        return [0.0] * 2400, 24000

    def free(self):
        self.freed = True


class IpaSpanTest(unittest.TestCase):
    def test_blocks_become_slashed_spans(self):
        self.assertEqual(sm.ipa_spans("What on [[vɑːnɑːdjɛl]]..."), "What on /vɑːnɑːdjɛl/...")
        self.assertEqual(sm.ipa_spans("[[hʊmf]]. Long as I'm [[ˌlɪlɪˈsɛt]]."), "/hʊmf/. Long as I'm /ˌlɪlɪˈsɛt/.")
        self.assertEqual(sm.ipa_spans("no blocks here"), "no blocks here")

    def test_plain_drops_stress_marks_only(self):
        self.assertEqual(sm.ipa_spans("[[ˌlɪlɪˈsɛt]] and [[ɹˈaɪzəndʒˌɪmə]]", plain=True), "/lɪlɪsɛt/ and /ɹaɪzəndʒɪmə/")
        self.assertEqual(sm.ipa_spans("[[vɑːnɑːdjɛl]]", plain=True), "/vɑːnɑːdjɛl/")   # length marks stay

    def test_texts_per_variant(self):
        lexicon = sh.Lexicon({"Vana'diel": {"ipa": "vɑː.nɑː.djɛl"}, "Lilisette": {"ipa": "ˌlɪl.ɪ.ˈsɛt"}})
        row = {"text": "<player>, Lilisette is on Vana'diel."}
        t = sm.moss_texts(row, lexicon, "adventurer")
        self.assertEqual(t["moss-raw"], "adventurer, Lilisette is on Vana'diel.")
        # the lexicon moves stress marks onto the vowel, so they come out as lˌɪl... here
        self.assertEqual(t["moss-ipa"], "adventurer, /lˌɪlɪsˈɛt/ is on /vɑːnɑːdjɛl/.")
        self.assertEqual(t["moss-ipa-plain"], "adventurer, /lɪlɪsɛt/ is on /vɑːnɑːdjɛl/.")


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(unittest.mock.patch.object(sp, "log", lambda *a: None))
        self.dir = tempfile.TemporaryDirectory()
        self.out = Path(self.dir.name) / "spike"
        (self.out / "ref").mkdir(parents=True)
        manifest = {"lines": [
            {"speaker": "Iroha", "text": "M-Master...", "spoken": "M-Master...", "voice": "af_sarah"},
            {"speaker": "Tenzen", "text": "What on Vana'diel...", "spoken": "What on [[vɑːnɑːdjɛl]]...", "voice": "am_adam"},
        ]}
        self.rows = sp.select_lines(manifest, [(l["speaker"], l["text"]) for l in manifest["lines"]])
        self.lexicon = sh.Lexicon({"Vana'diel": {"ipa": "vɑː.nɑː.djɛl"}})
        self.texts = {r["stem"]: sm.moss_texts(r, self.lexicon, "adventurer") for r in self.rows}
        self.timings = sp.Timings()

    def tearDown(self):
        self.dir.cleanup()

    def refs(self, *speakers):
        for s in speakers:
            sh.write_wav(self.out / "ref" / f"{sp.slug(s, 16)}.wav", [0.0] * 2400, 24000)

    def test_missing_reference_is_named(self):
        self.refs("Iroha")
        with self.assertRaises(FileNotFoundError) as cm:
            sm.render_moss(self.rows, self.texts, FakeMoss(), self.out, self.out / "ref", self.timings)
        self.assertIn("Tenzen", str(cm.exception))

    def test_three_variants_per_line_from_the_speakers_clip(self):
        self.refs("Iroha", "Tenzen")
        engine = FakeMoss()
        sm.render_moss(self.rows, self.texts, engine, self.out, self.out / "ref", self.timings)
        self.assertEqual(len(engine.calls), 6)
        self.assertIn(("What on /vɑːnɑːdjɛl/...", "tenzen.wav"), engine.calls)
        self.assertIn(("What on Vana'diel...", "tenzen.wav"), engine.calls)
        self.assertIn(("M-Master...", "iroha.wav"), engine.calls)
        for v in ("moss-ipa", "moss-ipa-plain", "moss-raw"):
            self.assertEqual(len(list(self.out.glob(f"*.{v}.wav"))), 2, v)
        self.assertTrue(engine.freed)
        summary = self.timings.summary()
        self.assertEqual(summary["moss-ipa"]["lines"], 2)
        self.assertEqual(summary["moss-ipa"]["peak_vram_mb"], 6000)
        # a rerun keeps everything
        again = FakeMoss()
        sm.render_moss(self.rows, self.texts, again, self.out, self.out / "ref", sp.Timings())
        self.assertEqual(again.calls, [])

    def test_page_gains_moss_columns_beside_kokoro(self):
        self.refs("Iroha", "Tenzen")
        sm.render_moss(self.rows, self.texts, FakeMoss(), self.out, self.out / "ref", self.timings)
        for r in self.rows:
            sh.write_wav(self.out / f"{r['stem']}.kokoro.wav", [0.0] * 2400, 24000)
        page_texts = {r["stem"]: sp.texts_for(r, self.lexicon, "adventurer") for r in self.rows}
        encode = lambda wav, opus: (Path(opus).parent.mkdir(parents=True, exist_ok=True), Path(opus).write_bytes(b"OggS"))  # noqa: E731
        page = sp.write_html(self.out, self.rows, page_texts, self.timings, sp.VARIANTS, src=sp.embedder(self.out, encode))
        text = page.read_text(encoding="utf-8")
        for v in ("kokoro", "moss-ipa", "moss-ipa-plain", "moss-raw"):
            self.assertIn(f"<th>{v}</th>", text, v)
        self.assertNotIn("<th>qwen-design</th>", text)
        self.assertEqual(text.count("src='data:audio/ogg;base64,"), 10)   # 2 lines x 4 columns + 2 reference clips


if __name__ == "__main__":
    unittest.main()
