"""The listening spike without any model: the line list against the committed
manifest, and the files and page it writes from fake engines."""

import json
import sys
import tempfile
import unittest
import unittest.mock
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spike_qwen3 as sp  # noqa: E402
import spoken_helper as sh  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def silence(path, seconds=0.5, rate=24000):
    sh.write_wav(path, [0.0] * int(seconds * rate), rate)


class FakeQwen:
    """Records what it was asked and writes a short tone's worth of silence."""

    def __init__(self):
        self.designs, self.clones, self.freed = [], [], False

    def reseed(self, speaker):
        pass

    def peak_vram_mb(self):
        return 4321

    def design(self, text, instruct):
        self.designs.append((text, instruct))
        return [0.0] * 2400, 24000

    def clone_prompt(self, ref_audio, ref_text):
        assert Path(ref_audio).exists(), ref_audio
        return ("prompt", Path(ref_audio).stem)

    def clone(self, text, prompt):
        self.clones.append((text, prompt))
        return [0.0] * 2400, 24000

    def free(self):
        self.freed = True


class LineListTest(unittest.TestCase):
    def test_every_line_is_in_the_committed_manifest(self):
        with open(sp.MANIFEST, encoding="utf-8") as f:
            manifest = json.load(f)
        rows = sp.select_lines(manifest)
        self.assertEqual(len(rows), len(sp.LINES))
        self.assertEqual([r["n"] for r in rows], list(range(1, len(rows) + 1)))
        self.assertTrue(all(r["stem"].startswith(f"{r['n']:02d}-") for r in rows))
        self.assertEqual(len({r["stem"] for r in rows}), len(rows), "stems collide")
        self.assertTrue(all(r["speaker"] in sp.VOICES for r in rows))

    def test_missing_line_is_named(self):
        manifest = {"lines": [{"speaker": "Iroha", "text": "Yes.", "spoken": "Yes.", "voice": "af_sarah"}]}
        with self.assertRaises(KeyError) as cm:
            sp.select_lines(manifest, [("Iroha", "Yes."), ("Zeid", "No.")])
        self.assertIn("Zeid: 'No.'", str(cm.exception))

    def test_limit_and_description_check(self):
        manifest = {"lines": [{"speaker": "Nobody", "text": "Hi.", "spoken": "Hi.", "voice": "x"},
                              {"speaker": "Iroha", "text": "Yes.", "spoken": "Yes.", "voice": "af_sarah"}]}
        with self.assertRaises(KeyError):
            sp.select_lines(manifest, [("Iroha", "Yes."), ("Nobody", "Hi.")])
        rows = sp.select_lines(manifest, [("Iroha", "Yes."), ("Nobody", "Hi.")], limit=1)
        self.assertEqual([r["speaker"] for r in rows], ["Iroha"])

    def test_texts_for_each_engine(self):
        lexicon = sh.Lexicon({"Lilisette": {"ipa": "ˌlɪl.ɪ.ˈsɛt"}, "Hmph": {"ipa": "hʊmf", "spell": "Hmf"}})
        row = {"text": "Hmph. <player>, this is Lilisette.", "spoken": "[[hʊmf]]. adventurer, this is [[ˌlɪlɪˈsɛt]]."}
        t = sp.texts_for(row, lexicon, "adventurer")
        self.assertEqual(t["kokoro"], row["spoken"])
        self.assertEqual(t["raw"], "Hmph. adventurer, this is Lilisette.")
        self.assertTrue(t["qwen"].startswith("Hmf. adventurer, this is "))
        self.assertNotIn("[[", t["qwen"])
        self.assertNotIn("Lilisette", t["qwen"])


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(unittest.mock.patch.object(sp, "log", lambda *a: None))
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        manifest = {"lines": [
            {"speaker": "Iroha", "text": "M-Master...", "spoken": "M-Master...", "voice": "af_sarah"},
            {"speaker": "Tenzen", "text": "What on Vana'diel...", "spoken": "What on [[vɑːnɑːdjɛl]]...",
             "voice": "am_adam"},
            {"speaker": "Iroha", "text": "Hmph.", "spoken": "[[hʊmf]].", "voice": "af_sarah"},
        ]}
        self.rows = sp.select_lines(manifest, [(l["speaker"], l["text"]) for l in manifest["lines"]])
        lexicon = sh.Lexicon({"Vana'diel": {"ipa": "vɑː.nɑː.djɛl"}, "Hmph": {"ipa": "hʊmf", "spell": "Hmf"}})
        self.texts = {r["stem"]: sp.texts_for(r, lexicon, "adventurer") for r in self.rows}
        self.out = self.root / "spike"
        self.out.mkdir()
        self.timings = sp.Timings()

    def tearDown(self):
        self.dir.cleanup()

    def test_kokoro_uses_the_pack_cache(self):
        cache = self.root / "cache"
        synthesised = []

        def synth(text, voice, path):
            synthesised.append((text, voice))
            silence(path)

        # pre-seed one line the way the pack build would have cached it
        cache.mkdir()
        pre = cache / f"{sh.cache_key('Tenzen', 'What on [[vɑːnɑːdjɛl]]...', 'am_adam')}.wav"
        silence(pre, seconds=1.0)
        sp.render_kokoro(self.rows, self.texts, synth, lambda t, stem: f"<{t}>", cache, self.out, self.timings)
        self.assertEqual([v for _, v in synthesised], ["af_sarah", "af_sarah"])
        self.assertEqual(synthesised[1][0], "[[hʊmf]].")
        self.assertEqual([r["kokoro_cached"] for r in self.rows], [False, True, False])
        files = sorted(p.name for p in self.out.glob("*.kokoro.wav"))
        self.assertEqual(files, ["01-iroha-m-master.kokoro.wav", "02-tenzen-what-on-vana-diel.kokoro.wav",
                                 "03-iroha-hmph.kokoro.wav"])
        self.assertEqual(self.rows[1]["phonemes"], "<What on Vana'diel...>")
        summary = self.timings.summary()["kokoro"]
        self.assertEqual(summary["lines"], 3)
        self.assertAlmostEqual(summary["audio"], 2.0)

    def test_qwen_designs_then_clones_per_speaker(self):
        engine = FakeQwen()
        sp.render_qwen(self.rows, self.texts, engine, self.out, self.timings, also_raw=True)
        # a design per line plus one calibration clip per speaker, in order
        self.assertEqual([d[0] for d in engine.designs],
                         ["M-Master...", self.texts[self.rows[1]["stem"]]["qwen"], "Hmf.",
                          sp.CALIBRATION, sp.CALIBRATION])
        self.assertEqual(engine.designs[0][1], sp.VOICES["Iroha"])
        self.assertEqual(engine.designs[3][1], sp.VOICES["Iroha"])
        self.assertEqual(engine.designs[4][1], sp.VOICES["Tenzen"])
        self.assertTrue((self.out / "ref" / "iroha.wav").exists())
        self.assertTrue((self.out / "ref" / "tenzen.wav").exists())
        # clones: one call per line and variant, Iroha's lines first, respelled then raw per line
        self.assertEqual(engine.clones[:4], [("M-Master...", ("prompt", "iroha")), ("M-Master...", ("prompt", "iroha")),
                                             ("Hmf.", ("prompt", "iroha")), ("Hmph.", ("prompt", "iroha"))])
        self.assertEqual(engine.clones[4][1], ("prompt", "tenzen"))
        self.assertEqual(len(engine.clones), 6)
        for v in ("qwen-design", "qwen-clone", "qwen-clone-raw"):
            self.assertEqual(len(list(self.out.glob(f"*.{v}.wav"))), 3, v)
        self.assertTrue(engine.freed)
        summary = self.timings.summary()
        self.assertEqual(summary["qwen-design"]["peak_vram_mb"], 4321)
        self.assertEqual(summary["qwen-design"]["lines"], 5)
        self.assertEqual(summary["qwen-clone"]["lines"], 3)
        self.assertEqual(summary["qwen-clone-raw"]["lines"], 3)

    def test_rerun_keeps_existing_files(self):
        first = FakeQwen()
        sp.render_qwen(self.rows, self.texts, first, self.out, self.timings)
        again = FakeQwen()
        timings = sp.Timings()
        sp.render_qwen(self.rows, self.texts, again, self.out, timings, also_raw=True)
        self.assertEqual(again.designs, [])              # designs and refs all kept
        self.assertEqual(len(again.clones), 3)           # only the raw variant was missing
        self.assertTrue(all(t == "Hmph." or "[[" not in t for t, _ in again.clones))
        self.assertNotIn("qwen-design", timings.summary())
        self.assertEqual(timings.summary()["qwen-clone-raw"]["lines"], 3)
        # the previous run's numbers survive a rerun through timings.json
        self.timings.dump(self.out / "timings.json")
        merged = sp.Timings.load(self.out / "timings.json")
        sp.render_qwen(self.rows, self.texts, FakeQwen(), self.out, merged, also_raw=True)
        self.assertEqual(merged.summary()["qwen-design"]["lines"], 5)
        self.assertEqual(merged.summary()["qwen-clone"]["lines"], 3)
        merged.record("qwen-clone", self.rows[0]["stem"], 9.0, 1.0)   # a re-render replaces, not duplicates
        self.assertEqual(merged.summary()["qwen-clone"]["lines"], 3)
        self.assertEqual(sp.Timings.load(self.out / "missing.json").variants, {})
        fresh = FakeQwen()
        sp.render_qwen(self.rows, self.texts, fresh, self.out, sp.Timings(), fresh=True)
        self.assertEqual(len(fresh.designs), 5)
        with wave.open(str(self.out / "01-iroha-m-master.qwen-clone.wav")) as w:
            self.assertEqual(w.getframerate(), 24000)

    def test_without_raw_no_raw_files(self):
        engine = FakeQwen()
        sp.render_qwen(self.rows, self.texts, engine, self.out, self.timings)
        self.assertEqual(len(engine.clones), 3)
        self.assertEqual(list(self.out.glob("*.qwen-clone-raw.wav")), [])

    def test_page_lists_present_variants_only(self):
        for r in self.rows:
            silence(self.out / f"{r['stem']}.kokoro.wav")
            r["phonemes"] = "ˈɛmmˈæstɚ" if r["n"] == 1 else "x"
            self.timings.record("kokoro", r["stem"], 0.1, 0.5)
        self.timings.note("kokoro", device="cpu")
        copies = []

        def encode(wav, opus):
            copies.append(Path(wav).name)
            Path(opus).parent.mkdir(parents=True, exist_ok=True)
            Path(opus).write_bytes(b"OggS" + Path(wav).name.encode())

        page = sp.write_html(self.out, self.rows, self.texts, self.timings, sp.VARIANTS,
                             src=sp.embedder(self.out, encode))
        text = page.read_text(encoding="utf-8")
        self.assertIn("<th>kokoro</th>", text)
        self.assertNotIn("<th>qwen-design</th>", text)
        self.assertIn("title='01-iroha-m-master.kokoro.wav'", text)
        self.assertEqual(text.count("src='data:audio/ogg;base64,"), 3)
        self.assertEqual(sorted(copies), sorted(f"{r['stem']}.kokoro.wav" for r in self.rows))
        self.assertTrue((self.out / ".opus" / "01-iroha-m-master.kokoro.opus").exists())
        # a second page reuses the encoded files; a linked page points at the WAVs
        sp.write_html(self.out, self.rows, self.texts, self.timings, sp.VARIANTS, src=sp.embedder(self.out, encode))
        self.assertEqual(len(copies), 3)
        linked = sp.write_html(self.out, self.rows, self.texts, self.timings, sp.VARIANTS,
                               src=lambda p: str(Path(p).relative_to(self.out))).read_text()
        self.assertIn("src='01-iroha-m-master.kokoro.wav'", linked)
        self.assertIn("ˈɛmmˈæstɚ", text)
        self.assertIn("Qwen text:", text)   # Hmph became Hmf, so the respelled form is shown
        self.assertIn("Hmf.", text)
        self.assertIn("What on Vana&#x27;diel...", text)
        self.timings.dump(self.out / "timings.json")
        data = json.loads((self.out / "timings.json").read_text())
        self.assertEqual(data["summary"]["kokoro"]["rtf"], 0.2)
        self.assertEqual(data["summary"]["kokoro"]["device"], "cpu")


if __name__ == "__main__":
    unittest.main()
