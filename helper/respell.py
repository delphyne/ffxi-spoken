"""IPA to pronunciation respelling, for engines that cannot take phonemes.

The input is the espeak flavour of IPA that Piper and Kokoro consume
(`vˈænɐdˌiːl`), where a stress mark sits just before its vowel. The output
is a hyphenated respelling in the style of Wikipedia's pronunciation key,
with the primary-stressed syllable in capitals: `VA-nuh-deel`. It is a
heuristic; a hand `spell:` in lexicon.yaml overrides it.

Standard library only.
"""

# Longest match first. Vowels carry a syllable; consonants do not.
VOWELS = {
    "aɪ": "ai", "aʊ": "ow", "eɪ": "ay", "oʊ": "oh", "əʊ": "oh", "ɔɪ": "oy",
    "ɪə": "eer", "eə": "air", "ʊə": "oor", "ɛə": "air",
    "iː": "ee", "uː": "oo", "ɑː": "ah", "ɔː": "aw", "ɜː": "ur", "ɛː": "air", "oː": "oh", "eː": "ay", "aː": "ah",
    "i": "ee", "ɪ": "ih", "ɛ": "eh", "æ": "a", "ɑ": "ah", "ɒ": "o", "ɔ": "aw", "ʊ": "oo", "u": "oo",
    "ʌ": "uh", "ə": "uh", "ɐ": "uh", "ɚ": "ur", "ɜ": "ur", "ᵻ": "ih", "ɨ": "ih", "e": "eh", "a": "a", "o": "oh",
}
CONSONANTS = {
    "tʃ": "ch", "dʒ": "j", "ʃ": "sh", "ʒ": "zh", "θ": "th", "ð": "dh", "ŋ": "ng",
    "j": "y", "ɹ": "r", "r": "r", "ɾ": "t", "ɡ": "g", "g": "g", "ɫ": "l", "ʔ": "", "x": "kh", "ɱ": "m",
    "b": "b", "d": "d", "f": "f", "h": "h", "k": "k", "l": "l", "m": "m", "n": "n", "p": "p",
    "s": "s", "t": "t", "v": "v", "w": "w", "z": "z",
}
PRIMARY, SECONDARY, LENGTH, SYLLABLE = "ˈ", "ˌ", "ː", "."
_KEYS = sorted(list(VOWELS) + list(CONSONANTS), key=len, reverse=True)

# Consonant clusters English allows at the start of a syllable (by IPA
# symbol, before mapping): a stop or fricative plus a liquid or glide, s plus
# a stop or nasal or liquid, and s plus stop plus liquid or glide.
_LIQUID_GLIDE = set("ɹrlɫjw")
_STOP_FRIC = set("pbtdkɡgfvθðsʃʒh") | {"tʃ", "dʒ"}
_S_SECOND = set("ptkmnlwfj")


def legal_onset(cluster):
    if len(cluster) <= 1:
        return True
    if len(cluster) == 2:
        a, b = cluster
        return (a in _STOP_FRIC and b in _LIQUID_GLIDE) or (a == "s" and b in _S_SECOND) or b == "j"
    if len(cluster) == 3:
        a, b, c = cluster
        return a == "s" and b in set("ptk") and c in _LIQUID_GLIDE
    return False


def espeak_stress(ipa):
    """Move each stress mark to where espeak puts it: directly before the
    vowel of its syllable, after any onset consonants. `ˈsɛt` becomes
    `sˈɛt`; a mark already before a vowel stays. Kokoro was trained on that
    placement and misreads the other as a syllable break."""
    out, held, i = [], "", 0
    while i < len(ipa):
        ch = ipa[i]
        if ch in (PRIMARY, SECONDARY):
            held = ch
            i += 1
            continue
        key = next((k for k in _KEYS if ipa.startswith(k, i)), None)
        if key in VOWELS:
            out.append(held + key)
            held = ""
            i += len(key)
        elif key is not None:
            out.append(key)
            i += len(key)
        else:
            if ch == " " and held:      # stress with no vowel after it in this word: keep it put
                out.append(held)
                held = ""
            out.append(ch)
            i += 1
    return "".join(out) + held


def tokenize(ipa):
    """[(kind, respelling, stress, ipa)] with kind 'v' or 'c'; stress on
    vowels is 1 for primary, 2 for secondary, 0 otherwise. Unknown characters
    are kept as consonant-like text so nothing is silently lost."""
    out, stress, i = [], 0, 0
    while i < len(ipa):
        ch = ipa[i]
        if ch == PRIMARY:
            stress, i = 1, i + 1
            continue
        if ch == SECONDARY:
            stress, i = 2, i + 1
            continue
        if ch == LENGTH or ch == "͡" or ch == "̯":  # stray length / tie / non-syllabic marks
            i += 1
            continue
        for key in _KEYS:
            if ipa.startswith(key, i):
                if key in VOWELS:
                    out.append(("v", VOWELS[key], stress, key))
                    stress = 0
                else:
                    out.append(("c", CONSONANTS[key], 0, key))
                i += len(key)
                break
        else:
            out.append(("c", ch, 0, ch))
            i += 1
    return out


def syllables(tokens):
    """Group tokens into syllables, one vowel each. Between two vowels the
    consonants are split so the next syllable gets the longest legal
    English onset (maximal onset): `nd` splits as n|d, `dj` stays together.
    Returns [(text, stress)]."""
    vowel_at = [k for k, t in enumerate(tokens) if t[0] == "v"]
    if not vowel_at:
        return [("".join(t[1] for t in tokens), 0)] if tokens else []
    cuts = [0]
    for prev, cur in zip(vowel_at, vowel_at[1:]):
        cluster = [t[3] for t in tokens[prev + 1:cur]]
        keep = len(cluster)
        while keep and not legal_onset(cluster[len(cluster) - keep:]):
            keep -= 1
        cuts.append(cur - keep)
    cuts.append(len(tokens))
    out = []
    for a, b in zip(cuts, cuts[1:]):
        chunk = tokens[a:b]
        stress = next((t[2] for t in chunk if t[0] == "v"), 0)
        out.append(("".join(t[1] for t in chunk), stress))
    return out


def respell_word(ipa):
    """Dots in the IPA are explicit syllable breaks and win over the
    heuristic; without them the word is syllabified by `syllables`."""
    if SYLLABLE in ipa:
        groups = [[(t[1], t[2]) for t in tokenize(part)] for part in ipa.split(SYLLABLE) if part]
        sylls = [("".join(text for text, _ in g), max((st for _, st in g), default=0)) for g in groups]
    else:
        sylls = syllables(tokenize(ipa))
    parts = []
    for text, stress in sylls:
        if not text:
            continue
        parts.append(text.upper() if stress == 1 else text)
    return "-".join(parts)


def respell(ipa):
    """Respell a phrase; words are separated by spaces, punctuation is kept."""
    words = []
    for word in ipa.split():
        lead = word[:len(word) - len(word.lstrip('"\'([{'))]
        core = word.strip('"\'([{')
        trail = core[len(core.rstrip('.,!?;:"\')]}')):]
        core = core.rstrip('.,!?;:"\')]}')
        words.append(lead + respell_word(core) + trail)
    return " ".join(words)
