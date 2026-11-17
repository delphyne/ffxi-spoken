"""The MOSS backend's voice designs, from helper/voice_designs.yaml.

A design is a description Qwen3-TTS VoiceDesign turns into a reference clip
(tools/design_voices.py); MOSS-TTS then continues that clip for every line in
the voice. Each design has a voice name, which is also its clip's file name:

    cast voice   the character's name as a slug: "Cait Sith" -> cait-sith
    pool voice   the group and its index: hume_m-0, hume_m-1, ...

`Designs` answers which voice a named character has, what each group's pool
is, and what every voice's description and seed are. It validates the file
on load so a typo fails here rather than halfway through designing.
"""

import json
import re
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PATH = HERE / "voice_designs.yaml"

# casting group -> (race, gender), for the voice review's filters. The group
# comes from a design's own `group`, else cast.py's CAST, else the census.
GROUP_INFO = {
    "hume_m": ("hume", "male"), "hume_f": ("hume", "female"), "old_m": ("hume", "male"),
    "boy": ("hume", "male"), "elvaan_m": ("elvaan", "male"), "elvaan_f": ("elvaan", "female"),
    "old_elvaan_m": ("elvaan", "male"), "taru_m": ("tarutaru", "male"), "taru_f": ("tarutaru", "female"),
    "mithra": ("mithra", "female"), "galka": ("galka", "male"), "zilart_m": ("zilart", "male"),
    "zilart_f": ("zilart", "female"), "deity_m": ("deity", "male"), "deity_f": ("deity", "female"),
    "small": ("creature", "unknown"), "dark": ("dark", "male"), "dark_f": ("dark", "female"),
    "cait": ("cait sith", "female"), "narrator": ("narrator", "male"),
}


def race_gender(group):
    return GROUP_INFO.get(group, ("unknown", "unknown"))


def voice_slug(name):
    """A character's voice name: lowercase, runs of other characters as one
    hyphen. "Grav'iton" -> grav-iton."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not s:
        raise ValueError(f"{name!r} has no letters or digits to name a voice by")
    return s


def pool_voice(group, index):
    return f"{group}-{index}"


class Designs:
    """Parsed and checked voice_designs.yaml.

    voices   voice name -> {"description", "seed", "kind" ("cast"|"pool"),
             "group" (pool voices), "names" (cast voices: every speaker name)}
    speakers speaker name -> voice name, for the cast section and its `also`
    pools    group -> [voice names]
    """

    def __init__(self, data, groups=None, source="voice_designs.yaml"):
        if not isinstance(data, dict):
            raise ValueError(f"{source}: expected a mapping with calibration, cast, and pools")
        unknown = set(data) - {"calibration", "cast", "pools"}
        if unknown:
            raise ValueError(f"{source}: unknown section(s) {', '.join(sorted(unknown))}")
        self.calibration = " ".join(str(data.get("calibration") or "").split())
        if not self.calibration:
            raise ValueError(f"{source}: calibration sentence is missing")
        self.voices, self.speakers, self.pools = {}, {}, {}

        for name, entry in (data.get("cast") or {}).items():
            name = str(name)
            if not isinstance(entry, dict):
                raise ValueError(f"{source}: cast entry {name!r} must be a mapping with a description")
            extra = set(entry) - {"description", "seed", "also", "group"}
            if extra:
                raise ValueError(f"{source}: cast entry {name!r} has unknown key(s) {', '.join(sorted(extra))}")
            voice = voice_slug(name)
            also = [str(a) for a in (entry.get("also") or [])]
            info = {"kind": "cast", "names": [name] + also}
            if entry.get("group") is not None:
                group = str(entry["group"])
                if groups is not None and group not in groups:
                    raise ValueError(f"{source}: cast entry {name!r} has group {group!r}, not a casting group")
                info["group"] = group
            self._add(voice, entry.get("description"), entry.get("seed", 1), source, f"cast entry {name!r}", **info)
            for speaker in [name] + also:
                if speaker in self.speakers:
                    raise ValueError(f"{source}: {speaker!r} is cast twice "
                                     f"({self.speakers[speaker]} and {voice})")
                self.speakers[speaker] = voice

        for group, descriptions in (data.get("pools") or {}).items():
            group = str(group)
            if groups is not None and group not in groups:
                raise ValueError(f"{source}: pool {group!r} is not a casting group "
                                 f"(one of {', '.join(sorted(groups))})")
            if not isinstance(descriptions, list) or not descriptions:
                raise ValueError(f"{source}: pool {group!r} needs a list of at least one description")
            self.pools[group] = []
            for i, d in enumerate(descriptions):
                voice = pool_voice(group, i)
                self._add(voice, d, 1, source, f"pool {group!r} voice {i}", kind="pool", group=group)
                self.pools[group].append(voice)
        if groups is not None:
            missing = sorted(set(groups) - set(self.pools))
            if missing:
                raise ValueError(f"{source}: no pool for casting group(s) {', '.join(missing)}")

    def _add(self, voice, description, seed, source, what, **info):
        description = " ".join(str(description or "").split())
        if not description:
            raise ValueError(f"{source}: {what} has no description")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError(f"{source}: {what} seed must be a whole number, not {seed!r}")
        if voice in self.voices:
            raise ValueError(f"{source}: {what} gives voice name {voice!r}, which another entry already has")
        self.voices[voice] = dict(info, description=description, seed=seed)

    @classmethod
    def load(cls, path=DEFAULT_PATH, groups=None):
        """Read and check the YAML. `groups` is the set of casting groups the
        pools must cover exactly; None skips that check."""
        try:
            import yaml
        except ImportError as e:
            raise RuntimeError("voice_designs.yaml needs PyYAML (python3-pyyaml, or make venv)") from e
        path = Path(path)
        with path.open(encoding="utf-8") as f:
            return cls(yaml.safe_load(f), groups=groups, source=path.name)

    def voice_for_speaker(self, speaker):
        return self.speakers.get(speaker)

    # -- editing (the voice review) ----------------------------------------

    def update(self, voice, description, seed):
        """A new description and seed for an existing voice."""
        description = " ".join(str(description).split())
        if not description:
            raise ValueError(f"{voice}: the description is empty")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError(f"{voice}: seed must be a whole number")
        self.voices[voice].update(description=description, seed=seed)

    def add_cast(self, name, description, seed=1, group=None):
        """Give `name` a voice of their own; returns the new voice name."""
        if name in self.speakers:
            raise ValueError(f"{name!r} already has a voice, {self.speakers[name]}")
        voice = voice_slug(name)
        if voice in self.voices:
            raise ValueError(f"voice name {voice!r} is taken")
        info = {"kind": "cast", "names": [name]}
        if group:
            info["group"] = group
        self._add(voice, description, seed, "voice review", f"new voice for {name!r}", **info)
        self.speakers[name] = voice
        return voice

    def dump(self, path):
        """Write the designs back, keeping the comment block that opens the
        file and laying out the rest the way the hand-written file does."""
        path = Path(path)
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        header = old[:old.index("\ncalibration:") + 1] if "\ncalibration:" in old else ""
        out = [header + "calibration: >-", *fold(self.calibration, 2), "", "cast:"]
        for voice, v in self.voices.items():
            if v["kind"] != "cast":
                continue
            name, *also = v["names"]
            out += [f"  {yaml_key(name)}:", "    description: >-", *fold(v["description"], 6)]
            if v["seed"] != 1:
                out.append(f"    seed: {v['seed']}")
            if v.get("group"):
                out.append(f"    group: {v['group']}")
            if also:
                out += wrap_list("    also: [", [yaml_key(a) for a in also])
        out += ["", "pools:"]
        for group, voices in self.pools.items():
            out.append(f"  {group}:")
            out += [f"    - {json.dumps(self.voices[v]['description'], ensure_ascii=False)}" for v in voices]
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
        tmp.replace(path)


WIDTH = 80
PLAIN_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9 .'\-]*$")


def yaml_key(name):
    """A name as a YAML key or list item: plain when safe, else quoted."""
    return name if PLAIN_KEY.match(name) and not name.endswith(" ") else json.dumps(name, ensure_ascii=False)


def wrap_list(lead, items):
    """A flow list broken between items, continuation lines aligned under
    the first item."""
    lines, line = [], lead
    for i, item in enumerate(items):
        piece = item + ("]" if i == len(items) - 1 else ",")
        if line != lead and len(line) + 1 + len(piece) > WIDTH:
            lines.append(line)
            line = " " * len(lead) + piece
        else:
            line += ("" if line == lead else " ") + piece
    return lines + [line]


def fold(text, indent):
    """A folded (>-) block's lines: wrapped, indented."""
    return textwrap.wrap(text, width=WIDTH, initial_indent=" " * indent, subsequent_indent=" " * indent,
                         break_long_words=False, break_on_hyphens=False)


_cached = {}


def load_default(groups=None):
    """voice_designs.yaml beside this file, read once per process."""
    key = tuple(sorted(groups)) if groups is not None else None
    if key not in _cached:
        _cached[key] = Designs.load(DEFAULT_PATH, groups=groups)
    return _cached[key]
