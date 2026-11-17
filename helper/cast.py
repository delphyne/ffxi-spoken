#!/usr/bin/env python3
"""Generate helper/voices.json: a voice per NPC for each engine.

Casting follows Spoken for WoW: a voice pool per race and sex, each NPC
drawn from their pool by a stable hash of their name so the file does not
reshuffle when names are added. Variants of one character (Trion and Prince
Trion, the twelve Cait Siths) are pinned to the first name's voice. Elvaan
and Zilart get British voices for San d'Oria's courtly register; Tarutaru,
Moogles, and Qiqirn the lightest; Galka and the great avatars the deepest.
Narration (empty speaker) gets a storyteller, as do the ??? voices in
cutscenes. Volto Oscuro is Iroha's shadow and shares her voice.

The MOSS engine's section comes from voice_designs.yaml: a character with
a design gets that voice, the rest of the cast their group's designed pool
by the same hash. Edit the designs there, then re-run this.

The NPC list is Balloon's portrait set. Fix a miscast by moving the name to
another group here and re-running; hand edits to voices.json are lost.

    python3 helper/cast.py            # rewrite helper/voices.json
    python3 helper/cast.py --check    # exit 1 if voices.json is stale
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from casting import POOLS, pick  # noqa: E402
from designs import load_default as load_designs  # noqa: E402

# Each entry is one character; a list is one character under several names.
CAST = {
    "hume_m": [
        "Ark Angel HM", "Abquhbah", "Aldo", "Aquila", "August", "Balamor", "Berghent", "Bilp", "Cassard",
        "Darrcuiln", "Degenhard", "Eamonn", "Fusilton", "Gadalar", "Gilgamesh", "Jayramus", "Joachim", "Ken",
        "Kipligg", "Klythios", "Kumhau", "Levil", "Luzaf", ["Maximilian", "Maximilian Berger"], "Mayakov",
        "Morimar", "Mutihb", "Naji", "Olgald", "Pollimio", "President Karst", "Qultada", "Razfahd", "Reginald",
        "Tenzen", "Teodor", "Valeriano", "Volker", "Wasuhd", "Wyatt", "Yazquhl", "Ygnas",
    ],
    "hume_f": [
        "Annika", "Arciela", "Ayame", "Brygid", "Cornelia", "Cynthia", "Elivira", "Emeline", "Flit", "Fremilla",
        "Hilda", "Ingrid", ["Iroha", "Iroha Red", "Volto Oscuro"], "Kagero", "Klara", "Laila", "Lilisette", "Lion", "Margret",
        "Marin", "Mathilde", "Nashmeira", "Nyline", "Portia", "Sajj'aka", "Svenja", "Tiana", "Verena", "Zweeha",
    ],
    "old_m": ["Abelard", "Cid", "Ghatsad", "Maat"],
    "old_elvaan_m": ["Kamui"],
    "boy": ["Young Aldo", "Mathilde's Son"],
    "elvaan_m": [
        "Alaixion", "Alphonimile", "Areuhat", "Cyranuce", "Despachiaire", "Estienneux", "Excenmille", "Haudrale",
        "Hildebert", "King Destin", "King Ranperre", "Larzos", "Machegue", "Melvien", "Monberaux", "Phillieulais",
        ["Pieuje", "Prince Pieuje"], "Ragelise", "Rahal", "Rainemard", "Rholont", "Riautte",
        ["Rongelouts", "Rongelouts N Distaud"], "Shamonde", ["Trion", "Prince Trion"], "Valaineral", "Vestillet",
        "Vortimere",
    ],
    "elvaan_f": [
        "Ark Angel EV", "Altennia", "Andreine", "Curilla", "Fairaure", "Gissenne", "Grennith", "Illisory",
        "Mildaurion", "Najelith", "Noillurie", "Princess Claidie", "Prishe", "Rosulatia", "Sempurne", "Ulmia",
    ],
    "taru_m": [
        "Ark Angel TT", "Ajido-Marujido", "Atori-Tutori", "Iruki-Waraki", "Karaha-Baruha", "Koru-Moru",
        "Kukki-Chebukki", "Makki-Chebukki", "Musto-Rusto", "Tosuka-Porika", "Tuffle-Buffle", "Yoran-Oran",
        "Zonpa-Zippa",
    ],
    "taru_f": [
        "Amchuchu", "Apururu", "Chalvava", "Chero-Machero", "Cherukiki", "Rukususu", ["Shantotto", "D. Shantotto"],
        "The Star Sybil",
    ],
    "mithra": [
        "Ark Angel MR", "Ghyo Molkot", "Ikhi Askamot", "Jakoh Wahcondalo", "Kuyin Hathdenna", "Lehko Habhoka",
        "Lhe Lhangavo", "Mihli Aliapoh", "Mufeh Hajrrha", "Naja Salaheem", "Nanaa Mihgo", "Nhev Befrathi",
        "Peladi Shalmohr", "Perih Vashai", "Ploh Trishbahk", "Rhea Myuliah", "Roh Latteh", "Romaa Mihgo",
        "Semih Lafihna", "Shikaree Z", "Shla Kheefid", "Vhim Puralghi",
    ],
    "galka": ["Ark Angel GK", "Gowam", "Gratzigg", "Gumbah", "Iron Eater", "Oggbi", "Zazarg", "Zeid", "Zogbog"],
    "zilart_m": ["Kam'lanaut", "Nag'molada", "Grav'iton", "Eald'narche", "Selh'teus"],
    "zilart_f": ["Esha'ntarl", "Yve'noile"],
    "deity_m": [
        "Alexander", "Bahamut", "Diabolos", "Fenrir", "Hades", "Ifrit", "Leviathan", "Odin", "Ramuh", "Titan",
        "Gessho", "Mnejing",
    ],
    "deity_f": ["Altana", "Garuda", "Phoenix", "Shiva", "Siren", "Ovjang"],
    "small": ["Moogle", "Nomad Moogle", "Kupofried", "Carbuncle", "Chacharoon", "Susuroon", "King of Hearts"],
    "dark": ["The Shadow Lord", "Dark Messenger", "The Keeper", "Atomos"],
    "dark_f": [["Lady Lilith", "Lilith Ascended"], "Cloud of Darkness", "Portentous Woman"],
    "cait": [["Cait Sith"] + ["Cait Sith " + s for s in "Prime Aon Dha Tri Ceithir Coig Sia Seachd Ochd Naoi Deich".split()]],
    # disembodied voices in cutscenes: the client shows them as ???
    "narrator": ["???"],
}

DEFAULTS = {"kokoro": {"default": "am_adam", "": "bm_fable"},
            "piper": {"default": "en_US-lessac-medium", "": "en_GB-alan-medium"}}


def moss_defaults(d):
    """The unnamed speaker is narration; anyone uncast and unknown to the
    census gets the first ordinary voice."""
    return {"default": d.pools["hume_m"][0], "": d.pools["narrator"][0]}


def build(designs=None):
    """voices.json: per engine, speaker -> voice. MOSS voices come from
    voice_designs.yaml: a character with a design gets their own voice, the
    rest of the cast their group's designed pool, by the same hash."""
    if designs is None:
        designs = load_designs(groups=set(POOLS))
    out = {engine: dict(defaults) for engine, defaults in DEFAULTS.items()}
    out["moss"] = moss_defaults(designs)
    seen = set()
    for group, characters in CAST.items():
        kokoro_pool, piper_voice = POOLS[group]
        for names in characters:
            names = [names] if isinstance(names, str) else names
            voice = pick(kokoro_pool, names[0])
            moss = designs.voice_for_speaker(names[0]) or pick(designs.pools[group], names[0])
            for name in names:
                if name in seen:
                    raise SystemExit(f"{name!r} is cast twice")
                seen.add(name)
                out["kokoro"][name] = voice
                out["piper"][name] = piper_voice
                out["moss"][name] = designs.voice_for_speaker(name) or moss
    # designed characters outside the hand cast (minor NPCs given a voice of their own)
    for name, voice in designs.speakers.items():
        out["moss"].setdefault(name, voice)
    order = lambda kv: (kv[0] != "default", kv[0] != "", kv[0])  # noqa: E731
    return {engine: dict(sorted(voices.items(), key=order)) for engine, voices in out.items()}


def render():
    return json.dumps(build(), indent=2, ensure_ascii=False) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = Path(__file__).resolve().parent / "voices.json"
    text = render()
    if "--check" in argv:
        if path.read_text(encoding="utf-8") != text:
            print(f"{path} is stale; run python3 helper/cast.py", file=sys.stderr)
            return 1
        return 0
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}: {len(build()['kokoro']) - 2} characters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
