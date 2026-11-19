#!/usr/bin/env python3
"""Host-side helper for the Spoken FFXI addon. Work in progress.

Tails data/queue.jsonl, synthesises each dialogue line, plays it, and
reports progress in data/status.json. See docs/Protocol.md.

The helper itself is standard library only; the neural backends need the
.venv that `make venv` builds (Python 3.12 with kokoro-onnx and piper-tts)
and the models that `make voices` downloads into helper/voices/. Backends,
in the order `auto` tries them:
  kokoro  Kokoro 82M, ~50 voices in one model; most natural; GPU via make venv-cuda
  piper   Piper, one .onnx per voice; fast, clear, flat
  espeak  espeak-ng, robotic but installed almost everywhere; for testing
  none    print what would be spoken and pretend it took a moment
Not tried by `auto`, because it runs at about 2.4 times real time:
  moss    MOSS-TTS-Local continuing a designed reference clip per voice, for
          audio packs (docs/MOSS-Backend-Handoff.md); needs .venv-moss and
          the clips `make design-voices` makes

    .venv/bin/python -I helper/spoken_helper.py --data ./data [--backend piper]

Try a pronunciation or a voice without the game (plays once and exits):

    make say TEXT="Welcome to Vana'diel." [SPEAKER=Iroha] [BACKEND=piper]
    .venv/bin/python -I helper/spoken_helper.py --say "Vana'diel" --speaker Iroha --no-lexicon
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

# `python -I` leaves the script's folder off sys.path; casting.py lives beside us.
sys.path.insert(0, str(Path(__file__).resolve().parent))


# ---------------------------------------------------------------------------
# queue

class QueueTail:
    """Yields new JSON entries appended to a file, surviving truncation.

    Starts at the end of whatever is already there: lines written before the
    helper came up are old dialogue and must not be replayed."""

    def __init__(self, path, replay=False):
        self.path = Path(path)
        self.offset = 0
        if not replay and self.path.exists():
            self.offset = self.path.stat().st_size

    def poll(self):
        if not self.path.exists():
            return []
        size = self.path.stat().st_size
        if size < self.offset:
            self.offset = 0  # file was truncated or replaced
        if size == self.offset:
            return []
        with self.path.open("rb") as f:
            f.seek(self.offset)
            chunk = f.read()
        # only consume whole lines
        last_newline = chunk.rfind(b"\n")
        if last_newline < 0:
            return []
        self.offset += last_newline + 1
        entries = []
        for raw in chunk[:last_newline].split(b"\n"):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entries.append(json.loads(raw.decode("utf-8")))
            except (ValueError, UnicodeDecodeError) as e:
                print(f"skipping bad queue line: {e}", file=sys.stderr)
        return entries


# ---------------------------------------------------------------------------
# pronunciation

PHONEME_OPEN, PHONEME_CLOSE = "[[", "]]"   # Piper's inline-IPA markup; used as our neutral form too
PHONEME_BLOCK = re.compile(r"\[\[(.+?)\]\]")


class Lexicon:
    """Pronunciations for names the engines mangle, from helper/lexicon.yaml.

    An entry is either a respelling string, or a mapping with `ipa` (espeak
    style IPA, as Piper and Kokoro consume it) and optionally `spell`, a hand
    respelling for engines that cannot take phonemes; without `spell` one is
    derived from the IPA by respell.py. `apply(text, phonemes=True)` emits
    `[[ipa]]` for engines that take phonemes, `phonemes=False` the respelling.

    An entry applies to every backend unless its mapping names the ones it
    is for: `backends: [kokoro, piper]` (a list or one name, from
    LEXICON_BACKENDS). `Lexicon(entries, backend="moss")` then leaves the
    others out of `apply` while `entries` still lists them all, which is
    what the review tool shows; with no backend given everything applies.

    Longest key first so a phrase beats a word inside it; keys starting with
    `_` are notes. Matches are case-sensitive and bounded by non-word
    characters, apostrophes and hyphens counting as word characters."""

    def __init__(self, entries=None, backend=None):
        self.entries = {}
        self.backend = backend
        for key, value in (entries or {}).items():
            if key.startswith("_"):
                continue
            if isinstance(value, dict):
                ipa = value.get("ipa")
                spell = value.get("spell")
                if not ipa and not spell:
                    raise ValueError(f"lexicon entry {key!r} needs ipa or spell")
                marked = normalise_ipa(key, str(ipa)) if ipa else None
                # engines get the IPA without syllable dots (Kokoro pauses at them);
                # the respeller keeps them as explicit syllable breaks
                self.entries[key] = {"ipa": marked.replace(".", "") if marked else None,
                                     "ipa_marked": marked, "spell": spell,
                                     "backends": lexicon_backends(key, value.get("backends"))}
            else:
                self.entries[key] = {"ipa": None, "ipa_marked": None, "spell": str(value), "backends": None}
        active = [k for k in self.entries if self.applies(k)]
        if active:
            words = sorted(active, key=len, reverse=True)
            alts = "|".join(re.escape(w) for w in words)
            self.pattern = re.compile(r"(?<![\w'\-])(?:" + alts + r")(?![\w'\-])")
        else:
            self.pattern = None

    def applies(self, key):
        """Whether `key` is for this lexicon's backend: always when the entry
        names no backends or the lexicon was built for none in particular."""
        limit = self.entries[key]["backends"]
        return limit is None or self.backend is None or self.backend in limit

    @classmethod
    def load(cls, path, backend=None):
        """The file's entries, for `backend` (a name from LEXICON_BACKENDS)
        or for all of them."""
        p = Path(path)
        if not p.exists():
            return cls(backend=backend)
        with p.open(encoding="utf-8") as f:
            if p.suffix in (".yaml", ".yml"):
                try:
                    import yaml
                except ImportError as e:
                    raise RuntimeError(f"{p.name} needs PyYAML; run make venv") from e
                try:
                    data = yaml.safe_load(f) or {}
                except yaml.YAMLError as e:
                    mark = getattr(e, "problem_mark", None)
                    where = f" at line {mark.line + 1}" if mark else ""
                    raise ValueError(f"{p.name}{where}: {getattr(e, 'problem', e)}. YAML values that start "
                                     "with punctuation such as , ' \" [ { or a stress mark need quotes: "
                                     "ipa: \"ˌlɪlɪˈsɛt\"") from e
            else:
                data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"{p}: expected a mapping of word to pronunciation")
        return cls({str(k): v for k, v in data.items()}, backend=backend)

    def spelling(self, key):
        """The respelling for an entry: the hand one, else derived from IPA."""
        e = self.entries[key]
        if e["spell"]:
            return e["spell"]
        import respell  # beside this file
        return respell.respell(e["ipa_marked"] or e["ipa"])

    def replacement(self, key, phonemes):
        e = self.entries[key]
        if phonemes and e["ipa"]:
            return PHONEME_OPEN + e["ipa"] + PHONEME_CLOSE
        return self.spelling(key)

    def apply(self, text, phonemes=False):
        if not self.pattern:
            return text
        return self.pattern.sub(lambda m: self.replacement(m.group(0), phonemes), text)


def lexicon_backends(key, value):
    """An entry's `backends` as a frozenset of known names, or None for
    all. A single name may be given bare; an unknown one is an error, so a
    typo does not silently drop the entry from every engine."""
    if value is None:
        return None
    names = [value] if isinstance(value, str) else list(value or [])
    if not names:
        raise ValueError(f"lexicon entry {key!r}: backends must name at least one backend or be left out")
    bad = [str(n) for n in names if n not in LEXICON_BACKENDS]
    if bad:
        raise ValueError(f"lexicon entry {key!r}: unknown backends {', '.join(bad)}; "
                         f"known: {', '.join(sorted(LEXICON_BACKENDS))}")
    return frozenset(names)


# Stage directions in a line, "<Sigh>", "<Sniffle, sob>", "<Thniff>", are
# shown and not said: dropped from what the engine is given, but left in
# the line itself, since the addon looks audio up by the text as written.
# `<player>` is the one span that is a word.
DIRECTION = re.compile(r"<(?!player>)[A-Za-z][A-Za-z ,'\-]{0,39}>")


def strip_directions(text):
    """`text` without its stage directions, the spacing and punctuation
    around them closed up; "" when it was nothing but directions."""
    out = DIRECTION.sub(" ", text)
    out = re.sub(r"\s+([,.!?;:…])", r"\1", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def normalise_ipa(key, ipa):
    """Accept the common slips in hand-typed IPA and reject the rest.
    ASCII ' and , become the stress marks ˈ and ˌ and : the length mark ː;
    stress marks move to espeak's position, just before the vowel;
    syllable dots are kept
    here (the respeller honours them; the caller strips them for the
    engines). Anything outside the espeak phoneme set the engines know is an
    error naming the characters, so a Kirshenbaum-style `lIlI'sEt` is
    caught rather than mumbled."""
    import respell  # beside this file
    ipa = ipa.replace("'", respell.PRIMARY).replace(",", respell.SECONDARY).replace(":", respell.LENGTH)
    ipa = respell.espeak_stress(ipa)
    known = set("".join(respell.VOWELS) + "".join(respell.CONSONANTS)) | {respell.PRIMARY, respell.SECONDARY,
                                                                          respell.LENGTH, respell.SYLLABLE, " "}
    bad = sorted({c for c in ipa if c not in known})
    if bad:
        raise ValueError(f"lexicon entry {key!r}: unknown IPA characters {''.join(bad)!r} in {ipa!r}. "
                         "Use espeak-style IPA (ɪ not I, ɛ not E, ˈ before the stressed vowel); "
                         "see the header of lexicon.yaml for how to get a starting point.")
    return ipa


def splice_phonemes(text, phonemize):
    """Turn text with `[[ipa]]` blocks into one phoneme string: the plain
    stretches go through `phonemize`, the blocks are used verbatim."""
    out = []
    for k, part in enumerate(PHONEME_BLOCK.split(text)):
        if k % 2:
            out.append(part.strip())
        elif part.strip():
            out.append(phonemize(part.strip()))
    return " ".join(out)


# ---------------------------------------------------------------------------
# synthesis

def cache_key(speaker, text, voice):
    h = hashlib.sha256()
    h.update(voice.encode("utf-8"))
    h.update(b"\0")
    h.update(speaker.encode("utf-8"))
    h.update(b"\0")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def write_wav(path, samples, rate):
    """Write mono 16-bit PCM from float samples in [-1, 1]."""
    try:
        import numpy as np  # present wherever kokoro is; not needed otherwise
        pcm = (np.clip(np.asarray(samples, dtype="float32"), -1.0, 1.0) * 32767).astype("<i2").tobytes()
    except ImportError:
        import array
        pcm = array.array("h", (int(max(-1.0, min(1.0, s)) * 32767) for s in samples))
        if sys.byteorder != "little":
            pcm.byteswap()
        pcm = pcm.tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def find_tool(name):
    """A console script installed next to this interpreter wins over PATH,
    so running the helper from .venv finds .venv's piper."""
    beside = Path(sys.executable).parent / name
    if beside.is_file() and os.access(beside, os.X_OK):
        return str(beside)
    return shutil.which(name)


class Backend:
    """Turns text into a WAV. `voices_dir` is helper/voices; each backend
    keeps its models in a subfolder named after itself."""

    name = "none"
    phonemes = False  # True when synthesise() understands [[ipa]] blocks

    def __init__(self, voices_dir, voice_map, fallback=None):
        self.voices_dir = Path(voices_dir)
        self.voice_map = voice_map
        self.fallback = fallback  # speaker -> voice or None, for names not in the map

    @property
    def model_dir(self):
        return self.voices_dir / self.name

    @classmethod
    def available(cls, voices_dir):
        return True

    def voice_for(self, speaker):
        voice = self.voice_map.get(speaker)
        if not voice and self.fallback and speaker:
            voice = self.fallback(speaker)
        return voice or self.voice_map.get("default") or "default"

    def synthesise(self, text, voice, out_path):
        raise NotImplementedError


class NoneBackend(Backend):
    name = "none"

    def synthesise(self, text, voice, out_path):
        print(f"[{voice}] {text}")
        return None  # nothing to play


class EspeakBackend(Backend):
    name = "espeak"

    @classmethod
    def available(cls, voices_dir):
        return shutil.which("espeak-ng") is not None

    def synthesise(self, text, voice, out_path):
        args = ["espeak-ng", "-w", str(out_path)]
        if voice != "default":
            args += ["-v", voice]
        subprocess.run(args, input=text.encode("utf-8"), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_path


class PiperBackend(Backend):
    """Piper (https://github.com/OHF-Voice/piper1-gpl). Voices are
    `<name>.onnx` plus `<name>.onnx.json` in voices/piper/, fetched with
    `python -m piper.download_voices <name>`. Fast; clear but flat. Reads
    `[[ipa]]` blocks natively."""

    name = "piper"
    phonemes = True

    @classmethod
    def available(cls, voices_dir):
        return find_tool("piper") is not None and any((Path(voices_dir) / cls.name).glob("*.onnx"))

    def synthesise(self, text, voice, out_path):
        if voice == "default":
            raise ValueError("voices.json needs a piper default voice")
        model = self.model_dir / f"{voice}.onnx"
        if not model.exists():
            raise FileNotFoundError(f"no piper voice at {model}")
        piper = find_tool("piper")
        if piper is None:
            raise FileNotFoundError("piper is not installed; run make venv")
        subprocess.run([piper, "-m", str(model), "-f", str(out_path)],
                       input=text.encode("utf-8"), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_path


# Kokoro voice names start with a language letter: af_heart is American
# English female, bm_george British male, jf_alpha Japanese female...
KOKORO_LANGS = {
    "a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
    "i": "it", "j": "ja", "p": "pt-br", "z": "cmn",
}


def kokoro_lang(voice):
    return KOKORO_LANGS.get(voice[:1], "en-us")


def kokoro_provider(available):
    """The onnxruntime provider to run Kokoro on. CUDA when the GPU build
    and its libraries are present, else CPU. TensorRT is skipped: it needs
    an engine build per session and fails over noisily without one."""
    if "CUDAExecutionProvider" in available:
        return "CUDAExecutionProvider"
    return "CPUExecutionProvider"


class KokoroBackend(Backend):
    """Kokoro 82M via kokoro-onnx. One model file and one voice pack in
    voices/kokoro/ give ~50 voices; the voice name selects one. More
    natural than Piper. About a third of realtime on CPU, six times faster
    on an NVIDIA GPU with `make venv-cuda`."""

    name = "kokoro"
    phonemes = True
    model_file = "kokoro-v1.0.onnx"
    voices_file = "voices-v1.0.bin"

    def __init__(self, voices_dir, voice_map, fallback=None):
        super().__init__(voices_dir, voice_map, fallback)
        self._engine = None
        self.device = None

    @classmethod
    def available(cls, voices_dir):
        d = Path(voices_dir) / cls.name
        if not ((d / cls.model_file).exists() and (d / cls.voices_file).exists()):
            return False
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError:
            return False
        return True

    def engine(self):
        if self._engine is None:
            try:
                import onnxruntime as ort
                from kokoro_onnx import Kokoro
            except ImportError as e:
                raise RuntimeError("kokoro-onnx is not installed; run make venv") from e
            if hasattr(ort, "preload_dlls"):
                ort.preload_dlls()  # finds the CUDA libraries installed by pip
            ort.set_default_logger_severity(3)  # errors only; CUDA placement warnings are noise
            provider = kokoro_provider(ort.get_available_providers())
            os.environ.setdefault("ONNX_PROVIDER", provider)  # kokoro-onnx reads this
            self._engine = Kokoro(str(self.model_dir / self.model_file),
                                  str(self.model_dir / self.voices_file))
            self.device = "cuda" if "CUDA" in self._engine.sess.get_providers()[0] else "cpu"
            print(f"kokoro: running on {self.device}")
        return self._engine

    def warm_up(self):
        """Load the model (and the GPU) before the first line arrives."""
        self.engine()

    def synthesise(self, text, voice, out_path):
        if voice == "default":
            raise ValueError("voices.json needs a kokoro default voice")
        engine = self.engine()
        lang = kokoro_lang(voice)
        if PHONEME_OPEN in text:
            # kokoro-onnx has no inline phoneme markup; phonemise the plain
            # parts ourselves and hand it the whole line as phonemes
            phonemes = splice_phonemes(text, lambda part: engine.tokenizer.phonemize(part, lang))
            samples, rate = engine.create(phonemes, voice=voice, lang=lang, is_phonemes=True)
        else:
            samples, rate = engine.create(text, voice=voice, lang=lang)
        write_wav(out_path, samples, rate)
        return out_path


class MossBackend(Backend):
    """MOSS-TTS-Local continuing a designed reference clip (helper/moss.py).

    voices.json's moss section names a designed voice per speaker; the clip
    for voice V is voices/moss/refs/V.wav, and refs/index.json gives its
    sha256, its seed, and the sentence it says. `voice_for` returns a key
    that carries the clip's hash and the settings (moss.voice_key), so the
    cache re-renders a line when either changes.

    One line at a time (`synthesise`, for make say) loads the model on the
    GPU and the codec on the CPU together. A pack build uses the two-pass
    interface instead: `generate_codes` for every line with only the model
    loaded, then `decode_codes` for every line with only the codec, so each
    pass has the card to itself."""

    name = "moss"
    phonemes = True
    two_pass = True

    def __init__(self, voices_dir, voice_map, fallback=None, settings=None, engine=None):
        super().__init__(voices_dir, voice_map, fallback)
        import moss  # beside this file; standard library until something loads
        self.moss = moss
        self.settings = settings or moss.Settings.from_env()
        self._engine = engine
        self._index = None
        self._ref_codes = {}
        self.used = {}      # voice -> clip sha256, for the pack's engine record
        self.device = None

    @property
    def refs_dir(self):
        return self.model_dir / "refs"

    @classmethod
    def available(cls, voices_dir):
        if not (Path(voices_dir) / cls.name / "refs" / "index.json").exists():
            return False
        import importlib.util
        return importlib.util.find_spec("transformers") is not None

    def index(self):
        if self._index is None:
            path = self.refs_dir / "index.json"
            if not path.exists():
                raise FileNotFoundError(f"no {path}; run make design-voices")
            self._index = json.loads(path.read_text(encoding="utf-8"))
        return self._index

    def clip(self, voice):
        rec = self.index()["voices"].get(voice)
        if rec is None or not (self.refs_dir / f"{voice}.wav").exists():
            raise FileNotFoundError(f"no designed clip for voice {voice!r}; run make design-voices")
        return rec

    def voice_for(self, speaker):
        voice = super().voice_for(speaker)
        if voice == "default":
            raise ValueError("voices.json needs a moss default voice; run python3 helper/cast.py")
        return self.moss.voice_key(voice, self.clip(voice)["sha256"], self.settings)

    def engine(self):
        if self._engine is None:
            self._engine = self.moss.Engine(self.settings)
        self.device = self._engine.device
        return self._engine

    def text(self, text):
        return self.moss.ipa_spans(text, PHONEME_BLOCK)

    def reference(self, voice):
        """The clip's codes, encoded once and kept beside the clip as
        refs/V.<sha8>.codes.pt so later runs skip the codec."""
        if voice not in self._ref_codes:
            rec = self.clip(voice)
            path = self.refs_dir / f"{voice}.{rec['sha256'][:8]}.codes.pt"
            if path.exists():
                codes = self.engine().load_codes(path)
            else:
                codes = self.engine().encode(self.refs_dir / f"{voice}.wav")
                self.engine().save_codes(codes, path)
            self._ref_codes[voice] = codes
            self.used[voice] = rec["sha256"]
        return self._ref_codes[voice]

    def prepare(self, keys):
        """Encode every clip the given voice keys need, so the codec can be
        freed before the model loads."""
        for key in keys:
            self.reference(self.moss.parse_voice_key(key))

    def generate_codes(self, text, key, codes_path):
        voice = self.moss.parse_voice_key(key)
        rec = self.clip(voice)
        generated = self.engine().generate(self.text(text), self.reference(voice),
                                           self.index()["calibration"], rec["seed"])
        Path(codes_path).parent.mkdir(parents=True, exist_ok=True)
        self.engine().save_codes(generated, codes_path)
        return codes_path

    def decode_codes(self, codes_path, out_path):
        engine = self.engine()
        write_wav(out_path, engine.decode(engine.load_codes(codes_path)), engine.rate)
        return out_path

    def begin_generate(self):
        """Pass one: free the codec so the model has the card."""
        self.engine().free_codec()

    def begin_decode(self):
        """Pass two: free the model so the codec can load."""
        self.engine().free_model()

    def warm_up(self):
        self.engine().load_model()

    def synthesise(self, text, voice, out_path):
        if self.settings.codec != "cpu32":
            raise ValueError("one-off lines decode on the CPU; unset MOSS_CODEC or use a pack build")
        codes = Path(out_path).with_suffix(".codes.pt")
        self.generate_codes(text, voice, codes)
        try:
            return self.decode_codes(codes, out_path)
        finally:
            codes.unlink(missing_ok=True)

    def engine_record(self):
        """What the pack manifest records about the engine: both models'
        snapshot revisions, the settings, and every clip used."""
        return {"name": self.name, "device": self.device,
                "models": {self.moss.MODEL_REPO: self.moss.snapshot_revision(self.moss.MODEL_REPO),
                           self.moss.CODEC_REPO: self.moss.snapshot_revision(self.moss.CODEC_REPO)},
                "settings": self.settings.record(),
                "calibration": self.index().get("calibration"),
                "clips": dict(sorted(self.used.items()))}


BACKENDS = {"none": NoneBackend, "espeak": EspeakBackend, "piper": PiperBackend, "kokoro": KokoroBackend,
            "moss": MossBackend}
AUTO_ORDER = (KokoroBackend, PiperBackend, EspeakBackend, NoneBackend)
# names a lexicon entry's `backends:` may use: the helper's, and the Qwen3
# path tools/spike_qwen3.py gives the lexicon's respellings to
LEXICON_BACKENDS = frozenset(BACKENDS) | {"qwen"}


def pick_backend(name, voices_dir):
    if name != "auto":
        return BACKENDS[name]
    for cls in AUTO_ORDER:
        if cls.available(voices_dir):
            return cls
    return NoneBackend


# ---------------------------------------------------------------------------
# playback

def pick_player():
    for candidate in (["paplay"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"], ["aplay", "-q"]):
        if shutil.which(candidate[0]):
            return candidate
    return None


# ---------------------------------------------------------------------------
# helper

class Helper:
    def __init__(self, data_dir, backend, cache_dir, player, poll_interval=0.1, lexicon=None, lexicon_path=None):
        self.lexicon = lexicon or Lexicon()
        self.lexicon_path = Path(lexicon_path) if lexicon_path else None
        self.lexicon_mtime = self._lexicon_mtime()
        self.lexicon_checked = 0.0
        self.lexicon_error = None  # last reload failure, also reported in status.json
        self.data_dir = Path(data_dir)
        self.queue = QueueTail(self.data_dir / "queue.jsonl")
        self.status_path = self.data_dir / "status.json"
        self.backend = backend
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.player = player
        self.poll_interval = poll_interval
        self.playing = None      # (seq, Popen)
        self.fake_until = None   # (seq, deadline) for the none backend

    def _lexicon_mtime(self):
        try:
            return self.lexicon_path.stat().st_mtime if self.lexicon_path else None
        except OSError:
            return None

    def reload_lexicon(self, force=False):
        """Pick up edits to the lexicon file without a restart. Checked about
        once a second; a file that fails to parse is reported and the old
        lexicon kept."""
        if self.lexicon_path is None:
            return False
        now = time.time()
        if not force and now - self.lexicon_checked < 1.0:
            return False
        self.lexicon_checked = now
        mtime = self._lexicon_mtime()
        if mtime == self.lexicon_mtime:
            return False
        self.lexicon_mtime = mtime
        try:
            self.lexicon = Lexicon.load(self.lexicon_path, backend=self.backend.name)
        except (ValueError, RuntimeError) as e:
            self.lexicon_error = str(e)
            print(f"lexicon not reloaded: {e}", file=sys.stderr)
            self.write_status(0, "idle" if not self.playing else "speaking", lexicon_error=self.lexicon_error)
            return False
        self.lexicon_error = None
        print(f"lexicon reloaded: {len(self.lexicon.entries)} entries")
        return True

    def write_status(self, seq, state, **extra):
        payload = {"seq": seq, "state": state, "backend": self.backend.name, "t": time.time()}
        if self.lexicon_error:
            payload["lexicon_error"] = self.lexicon_error
        payload.update(extra)
        fd, tmp = tempfile.mkstemp(dir=self.data_dir, prefix=".status-")
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, self.status_path)

    def stop(self):
        if self.playing:
            seq, proc = self.playing
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            self.playing = None
        self.fake_until = None

    def speak(self, entry):
        self.stop()
        seq = entry["seq"]
        speaker = entry.get("speaker", "")
        text = strip_directions(entry.get("text", ""))
        if not text:   # "<Sigh>": nothing to say, so the line is done at once
            self.write_status(seq, "done", reason="stage direction only")
            return
        text = self.lexicon.apply(text, phonemes=self.backend.phonemes)
        voice = self.backend.voice_for(speaker)
        wav = self.cache_dir / f"{cache_key(speaker, text, voice)}.wav"
        cached = wav.exists()
        try:
            if not cached:
                self.write_status(seq, "generating", cached=False)
                result = self.backend.synthesise(text, voice, wav)
                if result is None:
                    # dry run: pretend it takes about as long as reading it would
                    self.fake_until = (seq, time.time() + 0.05 * max(1, len(text.split())))
                    self.write_status(seq, "speaking", cached=False)
                    return
        except Exception as e:  # noqa: BLE001 - report everything to the addon
            self.write_status(seq, "error", message=str(e))
            return
        if self.player is None:
            self.write_status(seq, "error", message="no audio player found")
            return
        proc = subprocess.Popen(self.player + [str(wav)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.playing = (seq, proc)
        self.write_status(seq, "speaking", cached=cached)

    def tick(self):
        self.reload_lexicon()
        for entry in self.queue.poll():
            if entry.get("stop"):
                self.stop()
                self.write_status(entry.get("seq", 0), "idle", reason=entry.get("reason"))
            elif "text" in entry:
                self.speak(entry)
        if self.playing:
            seq, proc = self.playing
            if proc.poll() is not None:
                self.playing = None
                self.write_status(seq, "done" if proc.returncode == 0 else "error",
                                  message=None if proc.returncode == 0 else f"player exited {proc.returncode}")
        if self.fake_until and time.time() >= self.fake_until[1]:
            seq = self.fake_until[0]
            self.fake_until = None
            self.write_status(seq, "done")

    def run(self):
        print(f"spoken helper: backend={self.backend.name} data={self.data_dir}")
        warm = getattr(self.backend, "warm_up", None)
        if warm:
            try:
                warm()
            except Exception as e:  # noqa: BLE001 - keep going; speak() will report it
                print(f"warm-up failed: {e}", file=sys.stderr)
        self.write_status(0, "idle")
        try:
            while True:
                self.tick()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            self.stop()


def census_fallback(path, backend_name):
    """Cast speakers missing from voices.json by the race the census
    recorded for them (helper/census.py). None when there is no census."""
    p = Path(path)
    if not p.exists():
        return None
    import casting  # beside this file
    with p.open(encoding="utf-8") as f:
        census = json.load(f)
    return lambda speaker: casting.census_voice(census, speaker, backend_name)


def load_voice_map(path, backend_name):
    """voices.json maps speaker -> voice, either flat or grouped by backend:
    {"kokoro": {"default": "am_adam", "Iroha": "jf_alpha"}, "piper": {...}}.
    Voice names differ per engine, so the grouped form is the useful one."""
    p = Path(path)
    if not p.exists():
        return {}
    with p.open() as f:
        data = json.load(f)
    if any(isinstance(v, dict) for v in data.values()):
        return data.get(backend_name, {})
    return data


def say(backend, lexicon, player, text, speaker, cache_dir, fresh=False):
    """Speak one line the way the helper would, print what was sent to the
    engine, and wait for playback to finish."""
    plain = strip_directions(text)
    spoken = lexicon.apply(plain, phonemes=backend.phonemes)
    voice = backend.voice_for(speaker)
    print(f"speaker: {speaker or '<narrator>'}  voice: {voice}  backend: {backend.name}"
          f"  ({'takes phonemes' if backend.phonemes else 'respelling only'})")
    if plain != text:
        print(f"directions dropped: {text!r} -> {plain!r}")
        if not plain:
            print("nothing left to say")
            return 0
        text = plain
    if spoken != text:
        print(f"lexicon: {text!r} -> {spoken!r}")
        if backend.phonemes:
            print(f"         respelling would be {lexicon.apply(text)!r}")
    else:
        print(f"text:    {spoken!r}")
    wav = Path(cache_dir) / f"{cache_key(speaker, spoken, voice)}.wav"
    if fresh and wav.exists():
        wav.unlink()
    if not wav.exists():
        wav.parent.mkdir(parents=True, exist_ok=True)
        if backend.synthesise(spoken, voice, wav) is None:
            return 0
    else:
        print("(cached)")
    if player is None:
        print(f"no audio player found; wrote {wav}", file=sys.stderr)
        return 1
    return subprocess.run(player + [str(wav)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode


def main(argv=None):
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(here.parent / "data"), help="the addon's data folder")
    ap.add_argument("--backend", default="auto", choices=["auto", *BACKENDS])
    ap.add_argument("--voices", default=str(here / "voices"), help="folder with a subfolder of models per backend")
    ap.add_argument("--voice-map", default=str(here / "voices.json"), help="speaker -> voice name, per backend")
    ap.add_argument("--cache", default=str(here / "cache"))
    ap.add_argument("--lexicon", default=str(here / "lexicon.yaml"), help="word -> respelling (YAML or JSON), applied before synthesis")
    ap.add_argument("--census", default=str(here / "census.json"), help="NPC -> race, for casting speakers not in the voice map")
    ap.add_argument("--say", metavar="TEXT", help="speak TEXT once and exit, instead of watching the queue")
    ap.add_argument("--speaker", default="", help="with --say: the NPC name to cast the voice from")
    ap.add_argument("--no-lexicon", action="store_true", help="with --say: skip the lexicon, to hear the raw text")
    ap.add_argument("--fresh", action="store_true", help="with --say: regenerate even if the line is cached")
    args = ap.parse_args(argv)

    sys.stdout.reconfigure(line_buffering=True)  # progress shows up when logged to a file
    backend_cls = pick_backend(args.backend, args.voices)
    backend = backend_cls(args.voices, load_voice_map(args.voice_map, backend_cls.name),
                          fallback=census_fallback(args.census, backend_cls.name))
    lexicon = Lexicon() if args.no_lexicon else Lexicon.load(args.lexicon, backend=backend_cls.name)
    if args.say is not None:
        return say(backend, lexicon, pick_player(), args.say, args.speaker, args.cache, fresh=args.fresh)
    Path(args.data).mkdir(parents=True, exist_ok=True)
    Helper(args.data, backend, args.cache, pick_player(), lexicon=lexicon,
           lexicon_path=None if args.no_lexicon else args.lexicon).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
