"""The voice designs, from helper/voice_designs.yaml.

A design is the prompt a voice is made from: pasted into ElevenLabs' voice
design for a named character (docs/ElevenLabs-Handoff.md; `make
voice-prompts` prints them), and what Qwen3-TTS VoiceDesign turns into a
reference clip for the MOSS backend (tools/design_voices.py). Each design
has a voice name, which is also its clip's file name:

    cast voice   the character's name as a slug: "Cait Sith" -> cait-sith
    pool voice   the group and its index: hume_m_adult-0, hume_m_adult-1

A design is meta plus a description. A cast voice's meta is gender, age,
race, accent, persona (two to five words), and emotion (two or three
adjectives); a pool voice takes gender and race from its casting group
(casting.py: race, sex, and age band) and has an age of its own, by default
the band's, and nothing else, since the pools are to be stock voices. The
description is one or two sentences on timbre, pacing, and delivery.
`compose` builds the prompt, in the form ElevenLabs asks for:

    Native <accent>. <Gender>, <age>. <Quality> quality.
    Persona: <persona>. Emotion: <emotion>.
    <description>

A blank field, or an unknown gender, leaves its part out; the description
may be blank too when there is a persona or an emotion. The quality level
is not in the file: it is the run's (`make ... QUALITY=`, high by default).
Race is never in the prompt; it is kept because it helps whoever is
designing the voice, and must be one of RACES, the casting groups' races.

A cast voice also records its `line`: what the voice says while it is being
designed (ElevenLabs speaks a text in the voice it drafts). It is one of
the character's own lines, kept as a reference (`line_ref`: "sha1:" and a
short hash) because this file is committed and the game's text is not, or
any text written for the purpose, kept as it is.

`Designs` answers which voice a named character has, what each group's pool
is, and what every voice's design is. It validates the file on load so a
typo fails here rather than halfway through designing, and `dump` writes it
back keeping the comment block that opens it.
"""

import hashlib
import json
import re
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import casting  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_PATH = HERE / "voice_designs.yaml"

# casting group -> (race, gender): a pool voice's meta, and a speaker's when
# they have no design of their own (via cast.py's CAST or the census race)
# group -> (race, gender): the band groups and the special ones, from casting.py
GROUP_INFO = {g: casting.group_info(g)[:2] for g in casting.POOLS}
GENDERS = ("female", "male", "unknown")
AGES = ("child", "young", "teenager", "late teens", "young adult", "twenties", "adult", "thirties", "forties", "fifties",
        "middle-aged", "mature", "older", "elderly", "ageless")
# the races a design may have: the casting groups', in their order
RACES = tuple(dict.fromkeys(race for race, _ in GROUP_INFO.values()))
# a design's meta; the last three are a cast voice's only
META = ("gender", "age", "race", "accent", "persona", "emotion")
PROMPT_KEYS = ("accent", "persona", "emotion")
# everything about a design but its description and seed: the meta, and the
# line a cast voice is designed saying
FIELDS = (*META, "line")
CAST_KEYS = {*FIELDS, "description", "seed", "also"}
LINE_REF = "sha1:"
POOL_KEYS = {"age", "description", "seed"}
DEFAULT_QUALITY = "high"


def race_gender(group):
    return GROUP_INFO.get(group, ("unknown", "unknown"))


def band_age(group):
    """The age word a pool voice of `group` composes with when its entry
    gives none: the band's, or nothing for a special group."""
    band = casting.group_info(group)[2]
    return casting.BAND_AGES.get(band, "")


def pool_label(group, index, count):
    """"hume male young adult 1 of 2": what a pool voice is called where
    people read it; its name (hume_m_young-0) is for files."""
    return f"{casting.group_label(group)} {index + 1} of {count}"


def voice_slug(name):
    """A character's voice name: lowercase, runs of other characters as one
    hyphen. "Grav'iton" -> grav-iton."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not s:
        raise ValueError(f"{name!r} has no letters or digits to name a voice by")
    return s


def pool_voice(group, index):
    return f"{group}-{index}"


def tidy(text):
    return " ".join(str(text or "").split())


def line_ref(text):
    """How a line of the game is named in the file: the file is committed
    and the game's text never is, so a design keeps a short hash of the
    line, and the text is found again in data/ (resolve_line)."""
    return LINE_REF + hashlib.sha1(tidy(text).encode("utf-8")).hexdigest()[:12]


def resolve_line(value, candidates):
    """The text a design's `line` stands for: a reference is looked for
    among `candidates` (the speaker's lines on this machine) and is None
    when it is not there; anything else is the text itself."""
    value = tidy(value)
    if not value.startswith(LINE_REF):
        return value
    return next((tidy(c) for c in candidates if line_ref(c) == value), None)


def phrase(text):
    """A field as it goes into a prompt sentence: no full stop of its own."""
    return tidy(text).rstrip(". ")


def sentence(text):
    text = phrase(text)
    return text[:1].upper() + text[1:] + "." if text else ""


class Designs:
    """Parsed and checked voice_designs.yaml.

    voices   voice name -> {"kind" ("cast"|"pool"), the META fields,
             "description", "seed", and "names" (cast: every speaker name)
             or "group" (pool)}
    speakers speaker name -> voice name, for the cast section and its `also`
    pools    group -> [voice names]
    quality  the quality level `compose` asks for
    """

    def __init__(self, data, groups=None, source="voice_designs.yaml", quality=DEFAULT_QUALITY):
        if not isinstance(data, dict):
            raise ValueError(f"{source}: expected a mapping with calibration, cast, and pools")
        if "races" in data:
            raise ValueError(f"{source}: the races section is gone; race phrases are no longer composed into "
                             "a prompt, so delete it")
        unknown = set(data) - {"calibration", "cast", "pools"}
        if unknown:
            raise ValueError(f"{source}: unknown section(s) {', '.join(sorted(unknown))}")
        self.source = source
        self.quality = tidy(quality)
        self.calibration = tidy(data.get("calibration"))
        if not self.calibration:
            raise ValueError(f"{source}: calibration sentence is missing")
        self.voices, self.speakers, self.pools = {}, {}, {}

        for name, entry in (data.get("cast") or {}).items():
            name = str(name)
            if not isinstance(entry, dict):
                raise ValueError(f"{source}: cast entry {name!r} must be a mapping with a description")
            extra = set(entry) - CAST_KEYS
            if extra:
                raise ValueError(f"{source}: cast entry {name!r} has unknown key(s) {', '.join(sorted(extra))}")
            also = [str(a) for a in (entry.get("also") or [])]
            self._add(voice_slug(name), f"cast entry {name!r}", kind="cast", names=[name] + also,
                      description=entry.get("description"), seed=entry.get("seed", 1),
                      **{k: entry.get(k) for k in FIELDS})
            for speaker in [name] + also:
                if speaker in self.speakers:
                    raise ValueError(f"{source}: {speaker!r} is cast twice "
                                     f"({self.speakers[speaker]} and {voice_slug(name)})")
                self.speakers[speaker] = voice_slug(name)

        for group, entries in (data.get("pools") or {}).items():
            group = str(group)
            if groups is not None and group not in groups:
                raise ValueError(f"{source}: pool {group!r} is not a casting group "
                                 f"(one of {', '.join(sorted(groups))})")
            if not isinstance(entries, list) or not entries:
                raise ValueError(f"{source}: pool {group!r} needs a list of at least one voice")
            race, gender = race_gender(group)
            self.pools[group] = []
            for i, e in enumerate(entries):
                e = e if isinstance(e, dict) else {"description": e}
                extra = set(e) - POOL_KEYS
                if extra:
                    raise ValueError(f"{source}: pool {group!r} voice {i} has unknown key(s) "
                                     f"{', '.join(sorted(extra))}")
                voice = pool_voice(group, i)
                self._add(voice, f"pool {group!r} voice {i}", kind="pool", group=group, gender=gender,
                          age=e.get("age") or band_age(group), race=race, description=e.get("description"),
                          seed=e.get("seed", 1))
                self.pools[group].append(voice)
        if groups is not None:
            missing = sorted(set(groups) - set(self.pools))
            if missing:
                raise ValueError(f"{source}: no pool for casting group(s) {', '.join(missing)}")

    def _check(self, what, gender, age, race, description, seed, accent=None, persona=None, emotion=None,
               line=None):
        gender, age, race, description = tidy(gender), tidy(age), tidy(race), tidy(description)
        if not description and not (phrase(persona) or phrase(emotion)):
            raise ValueError(f"{self.source}: {what} has no description (nor a persona or an emotion to stand "
                             "in for one)")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError(f"{self.source}: {what} seed must be a whole number, not {seed!r}")
        if race and race not in RACES:
            raise ValueError(f"{self.source}: {what} has race {race!r}, which is not a casting group's "
                             f"({', '.join(RACES)})")
        if re.match(r"(?i)\s*((gender|age|persona|emotion|delivery)\s*:|native\s)", description):
            raise ValueError(f"{self.source}: {what}'s description starts with {description.split()[0]!r}; "
                             "that belongs in the meta, which compose() puts in front")
        return {"gender": gender or "unknown", "age": age, "race": race or "unknown",
                "accent": phrase(accent), "persona": phrase(persona), "emotion": phrase(emotion),
                "line": tidy(line), "description": description, "seed": seed}

    def _add(self, voice, what, kind, description, seed, gender=None, age=None, race=None, accent=None,
             persona=None, emotion=None, line=None, **info):
        if voice in self.voices:
            raise ValueError(f"{self.source}: {what} gives voice name {voice!r}, which another entry already has")
        self.voices[voice] = dict(info, kind=kind, **self._check(what, gender, age, race, description, seed,
                                                                 accent, persona, emotion, line))

    @classmethod
    def load(cls, path=DEFAULT_PATH, groups=None, quality=DEFAULT_QUALITY):
        """Read and check the YAML. `groups` is the set of casting groups the
        pools must cover exactly; None skips that check."""
        try:
            import yaml
        except ImportError as e:
            raise RuntimeError("voice_designs.yaml needs PyYAML (python3-pyyaml, or make venv)") from e
        path = Path(path)
        with path.open(encoding="utf-8") as f:
            return cls(yaml.safe_load(f), groups=groups, source=path.name, quality=quality)

    def voice_for_speaker(self, speaker):
        return self.speakers.get(speaker)

    # -- the prompt ---------------------------------------------------------

    def compose(self, voice=None, quality=None, **design):
        """The prompt for a voice, or for a design not saved yet (the META
        fields and description as keywords); `quality` instead of the
        run's. Three lines, each left out when it has nothing to say."""
        d = dict(self.voices[voice]) if voice else {}
        d.update({k: v for k, v in design.items() if v is not None})
        quality = phrase(self.quality if quality is None else quality)
        gender = phrase(d.get("gender"))
        who = ", ".join(p for p in ("" if gender == "unknown" else gender, phrase(d.get("age"))) if p)
        accent, persona, emotion = (phrase(d.get(k)) for k in PROMPT_KEYS)
        first = [f"Native {accent}." if accent else "", sentence(who), sentence(f"{quality} quality") if quality else ""]
        second = [f"Persona: {persona}." if persona else "", f"Emotion: {emotion}." if emotion else ""]
        lines = [" ".join(p for p in first if p), " ".join(p for p in second if p), tidy(d.get("description"))]
        return "\n".join(line for line in lines if line)

    # -- editing (the voice review) ----------------------------------------

    def update(self, voice, description, seed, **meta):
        """A new design for an existing voice; `meta` is any of FIELDS,
        the rest staying as they are. A pool voice keeps its group's
        gender and race and has no accent, persona, emotion, or line."""
        v = self.voices[voice]
        unknown = set(meta) - set(FIELDS)
        if unknown:
            raise TypeError(f"update() got unknown field(s) {', '.join(sorted(unknown))}")
        new = {k: v[k] if meta.get(k) is None else meta[k] for k in FIELDS}
        if v["kind"] == "pool":
            new.update(gender=v["gender"], race=v["race"], accent="", persona="", emotion="", line="")
        v.update(self._check(voice, description=description, seed=seed, **new))

    def add_cast(self, name, description, seed=1, **meta):
        """Give `name` a voice of their own; returns the new voice name."""
        if name in self.speakers:
            raise ValueError(f"{name!r} already has a voice, {self.speakers[name]}")
        voice = voice_slug(name)
        self._add(voice, f"new voice for {name!r}", kind="cast", names=[name],
                  description=description, seed=seed, **meta)
        self.speakers[name] = voice
        return voice

    def dump(self, path):
        """Write the designs back, keeping the comment block that opens the
        file and laying out the rest the way the hand-written file does."""
        path = Path(path)
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        header = old[:old.index("\ncalibration:") + 1] if "\ncalibration:" in old else ""
        out = [header + "calibration: >-", *fold(self.calibration, 2), "", "cast:"]
        for v in self.voices.values():
            if v["kind"] != "cast":
                continue
            name, *also = v["names"]
            out.append(f"  {yaml_key(name)}:")
            out += [f"    {k}: {yaml_key(v[k])}" for k in META if v[k] and v[k] != "unknown"]
            if v["description"]:
                out += ["    description: >-", *fold(v["description"], 6)]
            if v["line"]:
                out += ["    line: >-", *fold(v["line"], 6)]
            if v["seed"] != 1:
                out.append(f"    seed: {v['seed']}")
            if also:
                out += wrap_list("    also: [", [yaml_key(a) for a in also])
        out += ["", "pools:"]
        for group, voices in self.pools.items():
            out.append(f"  {group}:")
            for voice in voices:
                v = self.voices[voice]
                lead = "    - "
                if v["age"]:
                    out.append(f"{lead}age: {yaml_key(v['age'])}")
                    lead = "      "
                out.append(f"{lead}description: {json.dumps(v['description'], ensure_ascii=False)}")
                if v["seed"] != 1:
                    out.append(f"      seed: {v['seed']}")
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
        tmp.replace(path)


WIDTH = 80
PLAIN_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9 .'\-]*$")


def yaml_key(name):
    """A name as a YAML key or value: plain when safe, else quoted."""
    name = str(name)
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
