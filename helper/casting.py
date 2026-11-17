"""Voice pools shared by cast.py (the hand-maintained cast) and the helper's
census fallback (everyone else). Standard library only.

A group is a race-and-sex bucket. Each has a pool of Kokoro voices and one
Piper voice; a name is drawn from its pool by a stable hash so the choice
never changes when other names are added. The MOSS engine's pools are the
designed voices in voice_designs.yaml (designs.py), drawn the same way; only
that engine needs PyYAML."""

import hashlib

# group -> (kokoro pool, piper voice). Elvaan and the Zilart get British voices
# for San d'Oria's courtly register; Tarutaru, Moogles, and Qiqirn get the
# lightest voice; Galka and the great avatars the deepest.
POOLS = {
    "hume_m":   (["am_adam", "am_michael", "am_eric", "am_liam"], "en_US-lessac-medium"),
    "hume_f":   (["af_sarah", "af_bella", "af_jessica", "af_heart"], "en_US-amy-medium"),
    "old_m":    (["am_santa"], "en_US-lessac-medium"),
    "old_elvaan_m": (["bm_george"], "en_GB-alan-medium"),
    "boy":      (["am_puck"], "en_US-lessac-medium"),
    "elvaan_m": (["bm_george", "bm_daniel", "bm_lewis"], "en_GB-alan-medium"),
    "elvaan_f": (["bf_emma", "bf_isabella", "bf_alice"], "en_US-amy-medium"),
    "taru_m":   (["am_puck"], "en_US-lessac-medium"),
    "taru_f":   (["af_sky", "af_nova"], "en_US-amy-medium"),
    "mithra":   (["af_kore", "af_nicole", "af_river"], "en_US-amy-medium"),
    "galka":    (["am_onyx", "am_fenrir"], "en_GB-alan-medium"),
    "zilart_m": (["bm_fable"], "en_GB-alan-medium"),
    "zilart_f": (["bf_isabella"], "en_US-amy-medium"),
    "deity_m":  (["am_onyx"], "en_GB-alan-medium"),
    "deity_f":  (["af_aoede"], "en_US-amy-medium"),
    "small":    (["am_puck"], "en_US-lessac-medium"),   # moogles, qiqirn, carbuncle
    "dark":     (["am_fenrir"], "en_GB-alan-medium"),
    "dark_f":   (["bf_lily"], "en_US-amy-medium"),
    "cait":     (["bf_lily"], "en_US-amy-medium"),
    "narrator": (["bm_fable"], "en_GB-alan-medium"),
}

# The client's race byte for NPCs that wear player models (LandSandBoat's
# `look.race`). 0 and 29-31 are special models and give no group.
RACE_GROUPS = {
    1: "hume_m", 2: "hume_f", 3: "elvaan_m", 4: "elvaan_f",
    5: "taru_m", 6: "taru_f", 7: "mithra", 8: "galka",
}

ENGINE_INDEX = {"kokoro": 0, "piper": 1}
ENGINES = ("kokoro", "piper", "moss")


def pick(pool, name):
    return pool[int(hashlib.sha1(name.encode("utf-8")).hexdigest(), 16) % len(pool)]


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
