"""MOSS-TTS-Local for the helper's moss backend (docs/MOSS-Backend-Handoff.md).

The model (3.06B, OpenMOSS-Team/MOSS-TTS-Local-Transformer) turns text into
audio codes, continuing a reference clip's codes so the line comes out in
that clip's voice; the codec (1.77B, OpenMOSS-Team/MOSS-Audio-Tokenizer)
turns clips into codes and codes into audio. On an 8 GB card the two do not
fit together, so `Engine` loads them separately and a pack build runs every
line through the model first and the codec second.

The pure functions at the top need nothing beyond the standard library and
are what the tests cover; `Engine` imports torch and transformers when it
first loads something, and needs .venv-moss.
"""

import os
import time
import wave
from pathlib import Path

MODEL_REPO = "OpenMOSS-Team/MOSS-TTS-Local-Transformer"
CODEC_REPO = "OpenMOSS-Team/MOSS-Audio-Tokenizer"
STRESS = "ˈˌ"
CODEC_MODES = ("cpu32", "gpu16")


def ipa_spans(text, block):
    """The helper's `[[ipa]]` blocks as MOSS's `/ipa/` spans, with the stress
    marks removed: with them MOSS says the names wrong, without them right
    (listening test, 2026-11-17). `block` is the compiled `[[...]]` pattern."""
    return block.sub(lambda m: "/" + "".join(c for c in m.group(1) if c not in STRESS) + "/", text)


class Settings:
    """What changes the audio besides the text and the clip. Read from the
    environment so a pack build can be tuned without code:

        MOSS_TEMPERATURE  audio sampling temperature (1.0)
        MOSS_N_VQ         codec depth used, 1-32 (32)
        MOSS_INT8         1 to load the model's weights in 8 bits (off)
        MOSS_CODEC        cpu32 or gpu16: where audio is decoded (cpu32)
    """

    def __init__(self, temperature=1.0, n_vq=32, int8=False, codec="cpu32"):
        if codec not in CODEC_MODES:
            raise ValueError(f"MOSS_CODEC must be one of {', '.join(CODEC_MODES)}, not {codec!r}")
        if not 1 <= int(n_vq) <= 32:
            raise ValueError(f"MOSS_N_VQ must be 1 to 32, not {n_vq!r}")
        self.temperature, self.n_vq, self.int8, self.codec = float(temperature), int(n_vq), bool(int8), codec

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        return cls(temperature=env.get("MOSS_TEMPERATURE", "1.0"), n_vq=env.get("MOSS_N_VQ", "32"),
                   int8=env.get("MOSS_INT8", "") not in ("", "0", "false", "no"),
                   codec=env.get("MOSS_CODEC", "cpu32"))

    def tag(self):
        """The settings as they go into a cache key: t1.0:q32:fp16:cpu32."""
        return f"t{self.temperature:g}:q{self.n_vq}:{'int8' if self.int8 else 'fp16'}:{self.codec}"

    def record(self):
        return {"temperature": self.temperature, "n_vq": self.n_vq,
                "weights": "int8" if self.int8 else "float16", "codec": self.codec}


def voice_key(voice, clip_sha, settings):
    """The string the helper's cache key is made from: the voice, the first
    eight hex digits of its clip, and the settings, so redesigning a voice or
    changing a setting re-renders exactly the lines it touches."""
    return f"moss:{voice}:{clip_sha[:8]}:{settings.tag()}"


def parse_voice_key(key):
    """The voice name back out of `voice_key`'s string."""
    parts = key.split(":")
    if len(parts) < 3 or parts[0] != "moss":
        raise ValueError(f"not a moss voice key: {key!r}")
    return parts[1]


def read_wav(path):
    """Mono float samples and the rate, through `wave`: torchaudio.load
    needs torchcodec, which does not support this machine's FFmpeg 9."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit PCM")
        rate, channels = w.getframerate(), w.getnchannels()
        pcm = w.readframes(w.getnframes())
    import torch
    samples = torch.frombuffer(bytearray(pcm), dtype=torch.int16).float() / 32768.0
    if channels > 1:
        samples = samples.view(-1, channels).mean(dim=1)
    return samples, rate


def snapshot_revision(repo, hf_home=None):
    """The commit of the locally cached snapshot of `repo`, or None."""
    root = Path(hf_home or os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface")
    snaps = root / "hub" / ("models--" + repo.replace("/", "--")) / "snapshots"
    revs = sorted(p.name for p in snaps.iterdir()) if snaps.is_dir() else []
    return revs[-1] if len(revs) == 1 else (revs or [None])[-1]


def log(*args):
    print(*args, flush=True)


class Engine:
    """The model and the codec, each loaded on demand and freed on request.

    generate() needs only the model; encode() and decode() only the codec.
    Callers that want both resident (one-off lines with the codec on the
    CPU) simply use both; a pack build frees one before loading the other.
    """

    def __init__(self, settings, device="cuda"):
        self.settings = settings
        self.device = device if self._cuda_available(device) else "cpu"
        self.model = None
        self.codec = None
        self._processor = None
        self.rate = None

    @staticmethod
    def _cuda_available(device):
        if not device.startswith("cuda"):
            return False
        import torch
        return torch.cuda.is_available()

    # -- the processor: text formatting, no weights of its own ------------

    def processor(self):
        """MOSS's processor built without its codec, so loading it costs no
        memory; the codec is attached and detached as needed."""
        if self._processor is None:
            from transformers import AutoConfig, AutoTokenizer
            from transformers.dynamic_module_utils import get_class_from_dynamic_module
            cls = get_class_from_dynamic_module("processing_moss_tts.MossTTSDelayProcessor", MODEL_REPO)
            config = AutoConfig.from_pretrained(MODEL_REPO, trust_remote_code=True)
            tokenizer = AutoTokenizer.from_pretrained(MODEL_REPO, trust_remote_code=True)
            self._processor = cls(tokenizer=tokenizer, audio_tokenizer=None, model_config=config)
            self.rate = int(config.sampling_rate)
        return self._processor

    # -- model -----------------------------------------------------------

    def load_model(self):
        if self.model is not None:
            return
        import torch
        from transformers import AutoModel
        torch.backends.cuda.enable_cudnn_sdp(False)  # the model card calls this backend broken
        t0 = time.perf_counter()
        self.processor()
        # Turing has no bfloat16; float16 keeps the 6 GB of weights at half size
        on_gpu = self.device.startswith("cuda")
        dtype = torch.float16 if on_gpu else torch.float32
        kwargs = dict(trust_remote_code=True, attn_implementation="sdpa", torch_dtype=dtype)
        if self.settings.int8 and on_gpu:
            from transformers import BitsAndBytesConfig
            self.model = AutoModel.from_pretrained(MODEL_REPO, device_map={"": self.device},
                                                   quantization_config=BitsAndBytesConfig(load_in_8bit=True), **kwargs)
        else:
            self.model = AutoModel.from_pretrained(MODEL_REPO, **kwargs).to(self.device)
        self.model.eval()
        log(f"moss    model on {self.device} "
            f"({'int8' if self.settings.int8 and on_gpu else str(dtype).split('.')[-1]}) "
            f"in {time.perf_counter() - t0:.0f}s")

    def free_model(self):
        if self.model is not None:
            self.model = None
            self._empty_cache()

    # -- codec -----------------------------------------------------------

    def load_codec(self):
        """float32 on the CPU (cpu32), or float16 on the GPU (gpu16): the
        float32 codec is 7.1 GB, more than an 8 GB card has free."""
        if self.codec is not None:
            return
        import torch
        from transformers import AutoModel
        t0 = time.perf_counter()
        proc = self.processor()
        codec = AutoModel.from_pretrained(CODEC_REPO, trust_remote_code=True)
        if self.settings.codec == "gpu16" and self.device.startswith("cuda"):
            codec = codec.to(self.device, dtype=torch.float16)
            where = f"{self.device} (float16)"
        else:
            codec = codec.float().to("cpu")
            where = "cpu (float32)"
        codec.eval()
        self.codec = proc.audio_tokenizer = codec
        log(f"moss    codec on {where} in {time.perf_counter() - t0:.0f}s")

    def free_codec(self):
        if self.codec is not None:
            self.codec = None
            if self._processor is not None:
                self._processor.audio_tokenizer = None
            self._empty_cache()

    def _empty_cache(self):
        import gc
        gc.collect()
        if self.device.startswith("cuda"):
            import torch
            torch.cuda.empty_cache()

    # -- work ------------------------------------------------------------

    @staticmethod
    def save_codes(obj, path):
        import torch
        tmp = Path(path).with_suffix(".tmp")
        torch.save(obj, tmp)
        tmp.replace(path)   # whole files only, so an interrupted build resumes cleanly

    @staticmethod
    def load_codes(path):
        import torch
        return torch.load(path)

    def encode(self, clip):
        """A reference clip's codes, shape (frames, 32), on the CPU."""
        import torch
        self.load_codec()
        samples, rate = read_wav(clip)
        with torch.no_grad():
            return self.processor().encode_audios_from_wav([samples.unsqueeze(0)], sampling_rate=rate)[0]

    def generate(self, text, ref_codes, ref_text, seed):
        """One line's codes, continuing the clip: the user turn is the clip's
        transcript and the line, the assistant turn starts with the clip's
        codes. Returns (start_length, ids) on the CPU, for decode()."""
        import torch
        self.load_model()
        proc = self.processor()
        conversation = [proc.build_user_message(text=f"{ref_text} {text}"),
                        proc.build_assistant_message(audio_codes_list=[ref_codes])]
        batch = proc([conversation], mode="continuation")
        torch.manual_seed(seed)
        with torch.no_grad():
            out = self.model.generate(input_ids=batch["input_ids"].to(self.device),
                                      attention_mask=batch["attention_mask"].to(self.device),
                                      max_new_tokens=1024, audio_temperature=self.settings.temperature,
                                      n_vq_for_inference=self.settings.n_vq)
        start, ids = out[0]
        return int(start), ids.detach().cpu()

    def decode(self, generated):
        """Samples (float32 numpy) from generate()'s result; the clip's own
        part is trimmed off by the processor."""
        import torch
        self.load_codec()
        start, ids = generated
        dev = next(self.codec.parameters()).device
        with torch.no_grad():
            message = self.processor().decode([(start, ids.to(dev))])[0]
        if message is None or not message.audio_codes_list:
            raise RuntimeError("MOSS produced no audio for this line")
        return message.audio_codes_list[0].float().cpu().numpy()
