"""The MOSS backend's voice designs, from helper/voice_designs.yaml.

A design is what Qwen3-TTS VoiceDesign turns into a reference clip
(tools/design_voices.py); MOSS-TTS then continues that clip for every line in
the voice. Each design has a voice name, which is also its clip's file name:

    cast voice   the character's name as a slug: "Cait Sith" -> cait-sith
    pool voice   the group and its index: hume_m_adult-0, hume_m_adult-1

A design is meta plus a description. The meta is gender, age, and race: a
cast voice carries all three; a pool voice takes gender and race from its
casting group (casting.py: race, sex, and age band) and an age of its own,
by default the band's. The description is everything else
(timbre, accent, pace, manner). `compose` builds the string Qwen is given:

    Gender: female. Age: young adult. <the race's phrase> <description>

The race phrases are the file's `races` section, because Qwen has no idea
what a Galka or a Tarutaru sounds like unless told. A blank phrase, or an
unknown gender or age, leaves that part out.

`Designs` answers which voice a named character has, what each group's pool
is, and what every voice's design is. It validates the file on load so a
typo fails here rather than halfway through designing, and `dump` writes it
back keeping the comment block that opens it.
"""

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
CAST_KEYS = {"gender", "age", "race", "description", "seed", "also"}
POOL_KEYS = {"age", "description", "seed"}


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


class Designs:
    """Parsed and checked voice_designs.yaml.

    voices   voice name -> {"kind" ("cast"|"pool"), "gender", "age", "race",
             "description", "seed", and "names" (cast: every speaker name)
             or "group" (pool)}
    speakers speaker name -> voice name, for the cast section and its `also`
    pools    group -> [voice names]
    races    race -> the phrase `compose` puts in for it
    """

    def __init__(self, data, groups=None, source="voice_designs.yaml"):
        if not isinstance(data, dict):
            raise ValueError(f"{source}: expected a mapping with calibration, races, cast, and pools")
        unknown = set(data) - {"calibration", "races", "cast", "pools"}
        if unknown:
            raise ValueError(f"{source}: unknown section(s) {', '.join(sorted(unknown))}")
        self.source = source
        self.calibration = tidy(data.get("calibration"))
        if not self.calibration:
            raise ValueError(f"{source}: calibration sentence is missing")
        races = data.get("races") or {}
        if not isinstance(races, dict):
            raise ValueError(f"{source}: races must map each race to a phrase (blank for none)")
        self.races = {str(k): tidy(v) for k, v in races.items()}
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
                      gender=entry.get("gender"), age=entry.get("age"), race=entry.get("race"),
                      description=entry.get("description"), seed=entry.get("seed", 1))
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

    def _check(self, what, gender, age, race, description, seed):
        gender, age, race, description = tidy(gender), tidy(age), tidy(race), tidy(description)
        if not description:
            raise ValueError(f"{self.source}: {what} has no description")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError(f"{self.source}: {what} seed must be a whole number, not {seed!r}")
        if race and self.races and race not in self.races:
            raise ValueError(f"{self.source}: {what} has race {race!r}, which the races section does not list "
                             f"({', '.join(sorted(self.races))})")
        if re.match(r"(?i)\s*(gender|age)\s*:", description):
            raise ValueError(f"{self.source}: {what}'s description starts with a {description.split(':')[0]}; "
                             "that belongs in the meta, which compose() puts in front")
        return {"gender": gender or "unknown", "age": age, "race": race or "unknown",
                "description": description, "seed": seed}

    def _add(self, voice, what, kind, gender, age, race, description, seed, **info):
        if voice in self.voices:
            raise ValueError(f"{self.source}: {what} gives voice name {voice!r}, which another entry already has")
        self.voices[voice] = dict(info, kind=kind, **self._check(what, gender, age, race, description, seed))

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

    # -- the string Qwen is given ------------------------------------------

    def compose(self, voice=None, races=None, **design):
        """Qwen's instruction for a voice, or for a design not saved yet
        (gender, age, race, description as keywords). `races` tries other
        phrases than the saved ones."""
        d = dict(self.voices[voice]) if voice else {}
        d.update({k: v for k, v in design.items() if v is not None})
        races = self.races if races is None else races
        parts = []
        gender, age = tidy(d.get("gender")), tidy(d.get("age"))
        if gender and gender != "unknown":
            parts.append(f"Gender: {gender}.")
        if age:
            parts.append(f"Age: {age}.")
        phrase = races.get(tidy(d.get("race")), "")
        if phrase:
            parts.append(phrase)
        parts.append(tidy(d.get("description")))
        return " ".join(p for p in parts if p)

    # -- editing (the voice review) ----------------------------------------

    def update(self, voice, description, seed, gender=None, age=None, race=None):
        """A new design for an existing voice. A pool voice's gender and race
        are its group's and cannot change here."""
        v = self.voices[voice]
        if v["kind"] == "pool":
            gender, race = v["gender"], v["race"]
        new = self._check(voice, v["gender"] if gender is None else gender, v["age"] if age is None else age,
                          v["race"] if race is None else race, description, seed)
        v.update(new)

    def update_race(self, race, phrase):
        """A new phrase for a race the races section lists; blank for none.
        Every voice of that race composes differently afterwards."""
        if race not in self.races:
            raise ValueError(f"{self.source}: race {race!r} is not in the races section "
                             f"({', '.join(sorted(self.races))})")
        self.races[race] = tidy(phrase)

    def add_cast(self, name, description, seed=1, gender=None, age=None, race=None):
        """Give `name` a voice of their own; returns the new voice name."""
        if name in self.speakers:
            raise ValueError(f"{name!r} already has a voice, {self.speakers[name]}")
        voice = voice_slug(name)
        self._add(voice, f"new voice for {name!r}", kind="cast", names=[name], gender=gender, age=age,
                  race=race, description=description, seed=seed)
        self.speakers[name] = voice
        return voice

    def dump(self, path):
        """Write the designs back, keeping the comment block that opens the
        file and laying out the rest the way the hand-written file does."""
        path = Path(path)
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        header = old[:old.index("\ncalibration:") + 1] if "\ncalibration:" in old else ""
        out = [header + "calibration: >-", *fold(self.calibration, 2), "", "races:"]
        out += [f"  {yaml_key(race)}: {json.dumps(phrase, ensure_ascii=False)}" for race, phrase in self.races.items()]
        out += ["", "cast:"]
        for v in self.voices.values():
            if v["kind"] != "cast":
                continue
            name, *also = v["names"]
            out.append(f"  {yaml_key(name)}:")
            out += [f"    {k}: {yaml_key(v[k])}" for k in ("gender", "age", "race") if v[k] and v[k] != "unknown"]
            out += ["    description: >-", *fold(v["description"], 6)]
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
