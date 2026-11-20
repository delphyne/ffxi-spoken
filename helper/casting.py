"""Voice pools shared by cast.py (the hand-maintained cast) and the helper's
census fallback (everyone else). Standard library only.

A casting group is a race, a sex, and an age band: hume_m_adult,
mithra_f_elder. The five playable races each have every band; the other
groups (Zilart, deities, moogles and small creatures, beastmen, the dark,
Cait Sith, the narrator) are one bucket each. Each group has a pool of
Kokoro voices and one Piper voice; the hand cast is spread round-robin over
a pool in cast.py's order (so a name added at the end moves nobody), and a
name the census casts is drawn by a stable hash. The MOSS engine's pools are
the designed voices in voice_designs.yaml (designs.py), two per group for
the playable races; only that engine needs PyYAML.

casting_groups.json adds groups to the special ones: a race the game has
that the list below does not (a moogle, a yagudo), one bucket for each sex
it is given. The voice review's "New race" writes it; it is JSON so that
this module stays standard library, and is read when the module loads."""

import hashlib
import json
import re
from pathlib import Path

EXTRA = Path(__file__).resolve().parent / "casting_groups.json"

# race -> the sexes it has; the key is the race's short name used in group ids
RACES = {"hume": "mf", "elvaan": "mf", "taru": "mf", "mithra": "f", "galka": "m"}
RACE_NAMES = {"hume": "hume", "elvaan": "elvaan", "taru": "tarutaru", "mithra": "mithra", "galka": "galka"}
SEXES = {"m": "male", "f": "female"}

# age bands, youngest first, and the age word a pool voice composes with by default
BANDS = ("child", "teen", "young", "adult", "elder")
BAND_LABELS = {"child": "child", "teen": "teen", "young": "young adult", "adult": "adult", "elder": "elder"}
BAND_AGES = {"child": "child", "teen": "teenager", "young": "young adult", "adult": "adult", "elder": "elderly"}

# race and sex -> (kokoro pool, piper voice). Elvaan and the Zilart get British
# voices for San d'Oria's courtly register; Tarutaru, Moogles, and Qiqirn get the
# lightest voice; Galka and the great avatars the deepest.
_BASE = {
    "hume_m":   (["am_adam", "am_michael", "am_eric", "am_liam"], "en_US-lessac-medium"),
    "hume_f":   (["af_sarah", "af_bella", "af_jessica", "af_heart"], "en_US-amy-medium"),
    "elvaan_m": (["bm_george", "bm_daniel", "bm_lewis"], "en_GB-alan-medium"),
    "elvaan_f": (["bf_emma", "bf_isabella", "bf_alice"], "en_US-amy-medium"),
    "taru_m":   (["am_puck"], "en_US-lessac-medium"),
    "taru_f":   (["af_sky", "af_nova"], "en_US-amy-medium"),
    "mithra_f": (["af_kore", "af_nicole", "af_river"], "en_US-amy-medium"),
    "galka_m":  (["am_onyx", "am_fenrir"], "en_GB-alan-medium"),
}

# the groups that are not a race, sex, and band: (race name, sex, kokoro pool, piper voice)
SPECIAL = {
    "zilart_m": ("zilart", "male", ["bm_fable"], "en_GB-alan-medium"),
    "zilart_f": ("zilart", "female", ["bf_isabella"], "en_US-amy-medium"),
    "deity_m":  ("deity", "male", ["am_onyx"], "en_GB-alan-medium"),
    "deity_f":  ("deity", "female", ["af_aoede"], "en_US-amy-medium"),
    "small":    ("creature", "unknown", ["am_puck"], "en_US-lessac-medium"),   # moogles, qiqirn, carbuncle, cardians
    "beast":    ("beastman", "male", ["am_onyx"], "en_GB-alan-medium"),         # goblins, orcs, quadav, yagudo, mamool ja
    "dark":     ("dark", "male", ["am_fenrir"], "en_GB-alan-medium"),
    "dark_f":   ("dark", "female", ["bf_lily"], "en_US-amy-medium"),
    "cait":     ("cait sith", "female", ["bf_lily"], "en_US-amy-medium"),
    "narrator": ("narrator", "male", ["bm_fable"], "en_GB-alan-medium"),
}


def group_id(race, sex, band):
    return f"{race}_{sex}_{band}"


def band_groups():
    """Every race, sex, and band group, in race, sex, band order."""
    return [group_id(race, sex, band) for race, sexes in RACES.items() for sex in sexes for band in BANDS]


def split_group(group):
    """(race, sex, band) for a band group, else None."""
    parts = group.rsplit("_", 2)
    if len(parts) == 3 and parts[0] in RACES and parts[1] in RACES[parts[0]] and parts[2] in BANDS:
        return tuple(parts)
    return None


def group_info(group):
    """(race name, sex, band) for any group; band is None for the special
    groups and everything unknown for a group that is not one."""
    parts = split_group(group)
    if parts:
        race, sex, band = parts
        return RACE_NAMES[race], SEXES[sex], band
    if group in SPECIAL:
        return SPECIAL[group][0], SPECIAL[group][1], None
    return "unknown", "unknown", None


def group_label(group):
    """"hume male young adult", "deity male"."""
    race, sex, band = group_info(group)
    return " ".join(p for p in (race, sex if sex != "unknown" else "", BAND_LABELS.get(band, "")) if p)


POOLS = {g: _BASE[f"{split_group(g)[0]}_{split_group(g)[1]}"] for g in band_groups()}
POOLS.update({g: (kokoro, piper) for g, (_, _, kokoro, piper) in SPECIAL.items()})

# an added group's Kokoro pool and Piper voice, by sex: the MOSS and
# ElevenLabs voices are what matter, these only keep the other engines whole
_ADDED = {"male": (["am_onyx"], "en_GB-alan-medium"), "female": (["af_kore"], "en_US-amy-medium"),
          "unknown": (["am_puck"], "en_US-lessac-medium")}
_SUFFIX = {"male": "_m", "female": "_f", "unknown": ""}
BUILT_IN = frozenset(POOLS)


def added_group_id(race, sex):
    """The group id a new race and sex get: "moogle", "yagudo_m"."""
    race = " ".join(str(race).lower().split())
    if not re.fullmatch(r"[a-z][a-z' -]*", race):
        raise ValueError(f"a race name is letters, spaces, hyphens, and apostrophes, not {race!r}")
    if sex not in _SUFFIX:
        raise ValueError(f"sex is one of {', '.join(_SUFFIX)}, not {sex!r}")
    return re.sub(r"[^a-z]+", "_", race).strip("_") + _SUFFIX[sex], race


def add_group(race, sex):
    """A new special group for `race` and `sex`, in SPECIAL and POOLS;
    returns its id. The race's name may be one a group already has (a
    second sex for it), the group may not exist."""
    group, race = added_group_id(race, sex)
    if group in POOLS or split_group(group):
        raise ValueError(f"there is already a casting group {group!r}")
    if group == "silent":
        raise ValueError("silent is what a speaker with no voice is marked, so it cannot be a race")
    if race in RACES or race in RACE_NAMES.values():
        raise ValueError(f"{race} is already a race, with a group for each sex and age")
    kokoro, piper = _ADDED[sex]
    SPECIAL[group] = (race, sex, list(kokoro), piper)
    POOLS[group] = (list(kokoro), piper)
    return group


def load_added(path=EXTRA):
    """group -> {"race", "sex"} from casting_groups.json; {} without it."""
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected group: {{race, sex}}")
    return data


def write_added(path=EXTRA):
    """Every group that is not built in, as casting_groups.json."""
    data = {g: {"race": SPECIAL[g][0], "sex": SPECIAL[g][1]} for g in POOLS if g not in BUILT_IN}
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_added(path=EXTRA):
    """Take the groups a file adds into SPECIAL and POOLS; the ones already
    there are left alone. Stops on a file whose ids do not match its
    races, which only a hand edit can cause."""
    for group, entry in load_added(path).items():
        if group in POOLS:
            continue
        if added_group_id(entry["race"], entry["sex"])[0] != group:
            raise ValueError(f"{Path(path).name}: {group!r} is not the id of race {entry['race']!r}, sex {entry['sex']!r}")
        add_group(entry["race"], entry["sex"])


read_added()

# The client's race byte for NPCs that wear player models (LandSandBoat's
# `look.race`): the census knows a race, not an age, so it casts as an adult.
# 0 and 29-31 are special models and give no group.
RACE_GROUPS = {
    1: "hume_m_adult", 2: "hume_f_adult", 3: "elvaan_m_adult", 4: "elvaan_f_adult",
    5: "taru_m_adult", 6: "taru_f_adult", 7: "mithra_f_adult", 8: "galka_m_adult",
}

ENGINE_INDEX = {"kokoro": 0, "piper": 1}
ENGINES = ("kokoro", "piper", "moss")


def pick(pool, name):
    """A voice by a stable hash of the name: the census's thousands of
    minor NPCs, where nobody keeps a list."""
    return pool[int(hashlib.sha1(name.encode("utf-8")).hexdigest(), 16) % len(pool)]


def spread(pool, index):
    """The voice for the index-th member of a group: round-robin, so a
    group's members split evenly across its pool."""
    return pool[index % len(pool)]


def moss_pools(designs=None):
    """group -> designed voice names, from voice_designs.yaml unless given."""
    if designs is None:
        import designs as designs_module  # beside this file
        designs = designs_module.load_default(groups=set(POOLS))
    return designs.pools


def voice_for_group(group, name, engine, designs=None):
    """A voice from `group`'s pool for `engine`, or None if either is unknown."""
    if group not in POOLS or engine not in ENGINES:
        return None
    if engine == "moss":
        return pick(moss_pools(designs)[group], name)
    kokoro_pool, piper_voice = POOLS[group]
    return pick(kokoro_pool, name) if engine == "kokoro" else piper_voice


def census_voice(census, name, engine):
    """Fallback casting for a speaker absent from voices.json: the race the
    census recorded for that name decides the pool."""
    entry = census.get(name)
    if not entry:
        return None
    return voice_for_group(RACE_GROUPS.get(entry.get("race")), name, engine)
