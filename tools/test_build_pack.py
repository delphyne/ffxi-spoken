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


def row(speaker, variants, kind="say", zone=252, event=286, text_id=1, live_only=()):
    return {"kind": kind, "speaker": speaker, "variants": list(variants), "live_only": list(live_only),
            "zone": zone, "event": event, "text_id": text_id, "text": "raw"}


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
        lines, skipped = bp.corpus(rows)
        self.assertEqual([(ln["speaker"], ln["text"]) for ln in lines],
                         [("Iroha", "Go to Norg."), ("Tenzen", "Take his hand."), ("Tenzen", "Take her hand."),
                          ("", "The ship departs.")])
        self.assertEqual(lines[0]["sources"], [{"zone": 252, "event": 286, "text_id": 1},
                                               {"zone": 252, "event": 300, "text_id": 7}])
        self.assertEqual(skipped, {"menu": 1, "live-only: item, number": 1, "empty text": 1, "variable text id": 1})
        self.assertEqual(len(bp.corpus(rows, narration=False)[0]), 3)


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
                          game_text=lambda s: f"h{s['text_id']}", log=lambda *_: None, engine="fake")

    def lines(self):
        rows = [row("Iroha", ["Go to Norg, <player>."], text_id=1), row("Tenzen", ["Yes."], text_id=2)]
        return bp.corpus(rows)

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
        self.assertEqual(json.loads((final / "manifest.json").read_text(encoding="utf-8"))["engine"], "fake")

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
