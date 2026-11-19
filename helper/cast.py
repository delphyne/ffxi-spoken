#!/usr/bin/env python3
"""Generate helper/voices.json: a voice per NPC for each engine.

Casting follows Spoken for WoW: a voice pool per casting group, which is a
race, a sex, and an age band (casting.py: hume_m_adult, mithra_f_elder) or
one of the special buckets (Zilart, deities, moogles, beastmen, the dark).
A group's members are spread round-robin over its pool in the order they
are listed here, so each pool voice carries an even share and a name added
at the end of a group moves nobody. Variants of one character (Trion and
Prince Trion, the twelve Cait Siths) are pinned to the first name's voice.
Elvaan and Zilart get British voices for San d'Oria's courtly register;
Tarutaru, Moogles, and Qiqirn the lightest; Galka and the great avatars the
deepest. Narration (empty speaker) gets a storyteller, as do the ??? voices
in cutscenes. Volto Oscuro is Iroha's shadow and shares her voice.

The MOSS engine's section comes from voice_designs.yaml: a character with
a design gets that voice (every speaker with twenty lines or more has one),
the rest of the cast their group's designed pool, spread the same way. Edit
the designs there, then re-run this.

The NPC list is Balloon's portrait set. Fix a miscast by moving the name to
another group here and re-running, or with a line in cast_groups.yaml
(speaker: group), which wins over this list and the census and is what the
voice review's Move writes; hand edits to voices.json are lost.

    python3 helper/cast.py            # rewrite helper/voices.json
    python3 helper/cast.py --check    # exit 1 if voices.json is stale
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from casting import POOLS, spread  # noqa: E402
from designs import load_default as load_designs  # noqa: E402

# Each entry is one character; a list is one character under several names.
# A group is race, sex, and age band (casting.py); members split round-robin over its pool.
CAST = {
    "hume_m_child": ["Young Aldo", "Mathilde's Son"],
    "hume_m_teen": ["Teodor"],
    "hume_m_young": ["Abquhbah", "August", "Joachim", "Levil", "Naji", "Ygnas"],
    "hume_m_adult": ["Ark Angel HM", "Aldo", "Aquila", "Balamor", "Berghent", "Bilp", "Cassard", "Darrcuiln",
        "Degenhard", "Eamonn", "Fusilton", "Gadalar", "Gilgamesh", "Jayramus", "Ken", "Kipligg", "Klythios", "Kumhau",
        "Luzaf", ["Maximilian", "Maximilian Berger"], "Mayakov", "Morimar", "Mutihb", "Olgald", "Pollimio", "Qultada",
        "Razfahd", "Reginald", "Tenzen", "Valeriano", "Volker", "Wasuhd", "Wyatt", "Yazquhl", "Wolfgang", "Schultz",
        "Rishfee", "Brenton", "Lucius", "Raubahn", "Justinius", "Ulrich", "Nadeey", "Brandolf", "Magh Bihu", "Helaku",
        "Alois", "Hani", "Prien", "Movamuq", "Nahshib", "Altair", "Oscairn", "Barnabas", "Daswil", "Turlough",
        "Elmric", "Foss", "Pius", "Povall", "Valkeng", "Arnau", "Irwyn", "Ren"],
    "hume_m_elder": [["President Karst", "Karst"], "Abelard", "Cid", "Ghatsad", "Maat"],
    "hume_f_child": [],
    "hume_f_teen": ["Aphmau", "Amnaf"],
    "hume_f_young": ["Arciela", "Ayame", "Cornelia", "Elivira", "Emeline", "Flit", "Fremilla",
        ["Iroha", "Iroha Red", "Volto Oscuro"], "Kagero", "Lilisette", "Lion", "Nashmeira", "Nyline", "Portia",
        "Sajj'aka", "Svenja", "Tiana", "Verena", "Darcia", "Nareema", "Raina", "Tateeya", "Judith", "Sueleen"],
    "hume_f_adult": ["Annika", "Brygid", "Cynthia", "Hilda", "Ingrid", "Klara", "Laila", "Margret", "Marin",
        "Mathilde", "Zweeha", "Palometa", "Amaura", "Tanja", "Adelheid", "Franziska"],
    "hume_f_elder": [],
    "elvaan_m_child": [],
    "elvaan_m_teen": [],
    "elvaan_m_young": [["Pieuje", "Prince Pieuje"], ["Trion", "Prince Trion"]],
    "elvaan_m_adult": ["Alaixion", "Alphonimile", "Areuhat", "Cyranuce", "Estienneux", "Excenmille", "Haudrale",
        "Hildebert", "King Ranperre", "Larzos", "Machegue", "Melvien", "Monberaux", "Phillieulais", "Ragelise",
        "Rahal", "Rainemard", "Rholont", "Riautte", ["Rongelouts", "Rongelouts N Distaud"], "Shamonde", "Valaineral",
        "Vestillet", "Vortimere", "Louverance", ["Halver", "Halver M Borel"], "Rochefogne", "Travialce", "Cousseraux",
        "Francmage", "Ambrotien", "Endracion", "Pherimociel", "Meransarget", "Andrause", "Nelcabrit", "Jazaraat",
        "Waudeen", "Zantaviat", "Chalvatot", "Corseihaut", "Coteaulepoint", "Raustigne", "Remiotte", "Riault",
        "Falzum", "Magriffon", "Courisaille", "Hegouard", "Dangueubert", "Henrinaud", "Vichauxdat"],
    "elvaan_m_elder": ["Kamui", "Despachiaire", ["King Destin", "Destin"], "Chasalvige"],
    "elvaan_f_child": [],
    "elvaan_f_teen": ["Prishe"],
    "elvaan_f_young": [["Princess Claidie", "Claidie"], "Ulmia"],
    "elvaan_f_adult": ["Ark Angel EV", "Altennia", "Andreine", "Curilla", "Fairaure", "Gissenne", "Grennith",
        "Illisory", "Mildaurion", "Najelith", "Noillurie", "Rosulatia", "Sempurne", "Eideialc", "Grilau",
        "Savae E Paleade", "Jima", "Meyaada", "Naillina"],
    "elvaan_f_elder": [],
    "taru_m_child": ["Kukki-Chebukki", "Makki-Chebukki", "Vukki-Chebukki"],
    "taru_m_teen": [],
    "taru_m_young": ["Ajido-Marujido"],
    "taru_m_adult": ["Ark Angel TT", "Atori-Tutori", "Iruki-Waraki", "Karaha-Baruha", "Musto-Rusto", "Tosuka-Porika",
        "Tuffle-Buffle", "Patt-Pott", "Robel-Akbel", "Abda-Lurabda", "Honoi-Gomoi", "Moreno-Toeno", "Chekochuk",
        "Heimji-Keimji", "Soni-Muni", "Kayeel-Payeel", "Velda-Galda", "Paya-Sabya", "Topuru-Kuperu", "Kerutoto",
        "Iru-Kuiru", "Rodin-Comidin", "Kyume-Romeh"],
    "taru_m_elder": ["Koru-Moru", "Yoran-Oran", "Zonpa-Zippa"],
    "taru_f_child": [],
    "taru_f_teen": ["Cherukiki", "Rukususu"],
    "taru_f_young": ["Chalvava"],
    "taru_f_adult": ["Amchuchu", "Apururu", "Chero-Machero", ["Shantotto", "D. Shantotto"], "Karababa", "Kasaroro"],
    "taru_f_elder": [["The Star Sybil", "Star Sibyl"]],
    "mithra_f_child": ["Hinaree"],
    "mithra_f_teen": [],
    "mithra_f_young": ["Nanaa Mihgo", "Rhea Myuliah"],
    "mithra_f_adult": ["Ark Angel MR", "Ghyo Molkot", "Ikhi Askamot", "Jakoh Wahcondalo", "Kuyin Hathdenna",
        "Lehko Habhoka", "Lhe Lhangavo", "Mihli Aliapoh", "Mufeh Hajrrha", "Naja Salaheem", "Nhev Befrathi",
        "Peladi Shalmohr", "Ploh Trishbahk", "Roh Latteh", "Semih Lafihna", "Shikaree Z", "Shla Kheefid",
        "Vhim Puralghi", "Fhu Madihmin", "Shikaree Y", "Shikaree X", "Maryoh Comyujah", "Numi Adaligo",
        "Pakh Jatalfih", "Ghebi Damomohe", "Rabntah"],
    "mithra_f_elder": ["Perih Vashai", "Romaa Mihgo"],
    "galka_m_child": [],
    "galka_m_teen": [],
    "galka_m_young": [],
    "galka_m_adult": ["Ark Angel GK", "Gowam", "Gratzigg", "Gumbah", "Iron Eater", "Zazarg", "Zeid", "Zogbog",
        "Jabbos", "Raogrimm", "Grohm", "Werei", "Dancing Wolf", "Gabbot", "Goggehn", "Cloud Walker", "Drake Fang"],
    "galka_m_elder": ["Oggbi"],
    "zilart_m": ["Kam'lanaut", "Nag'molada", "Grav'iton", "Eald'narche", "Selh'teus", "Illusory Image"],
    "zilart_f": ["Esha'ntarl", "Yve'noile"],
    "deity_m": ["Alexander", "Bahamut", "Diabolos", "Fenrir", "Hades", "Ifrit", "Leviathan", "Odin", "Ramuh", "Titan",
        "Gessho", "Mnejing", "Promathia"],
    "deity_f": ["Altana", "Garuda", "Phoenix", "Shiva", "Siren", "Ovjang"],
    "small": ["Moogle", "Nomad Moogle", "Kupofried", "Carbuncle", "Chacharoon", "Susuroon", "King of Hearts",
        "Stooge Moogle", "Talking Doll", "Megomak", "Riko Kupenreich", "Kupatete", "Kupert", "Kupignol", "Kupuckl",
        "Toto Kupeliaure", "Wawaroon", "Pyopyoroon", "Vavaroon", "Gogoroon", "Joker"],
    "beast": ["Fickblix", "Muckvix", "Dazbog", "Gulool Ja Ja", "Goblin Repossessor", "Goblin Grenadier",
        "Mawl'gofaur", "Geebeh", "Molaal Ja", "Gaheel Ja", "Goblin Geologist", "One-eyed Gwajboj", "Trikotrak",
        "Warlord Rojgnoj"],
    "dark": [["Shadow Lord", "The Shadow Lord"], "Dark Messenger", "The Keeper", "Atomos", "Cavernous Maw",
        "Duke Alloces", "Count Bifrons", "Shadow of Darkness"],
    "dark_f": [["Lady Lilith", "Lilith Ascendant", "Lilith Ascended"], "Cloud of Darkness", "Portentous Woman"],
    "cait": [["Cait Sith", "Cait Sith Prime", "Cait Sith Aon", "Cait Sith Dha", "Cait Sith Tri", "Cait Sith Ceithir", "Cait Sith Coig", "Cait Sith Sia", "Cait Sith Seachd", "Cait Sith Ochd", "Cait Sith Naoi", "Cait Sith Deich", "Regal Feline"]],
    "narrator": ["???"],
}

DEFAULTS = {"kokoro": {"default": "am_adam", "": "bm_fable"},
            "piper": {"default": "en_US-lessac-medium", "": "en_GB-alan-medium"}}


MOVES = Path(__file__).resolve().parent / "cast_groups.yaml"
MOVES_HEADER = "# Casting moves: speaker: group (helper/cast.py, make review-voices).\n\n"


def load_moves(path=MOVES):
    """cast_groups.yaml: speaker -> the group they are moved to. Missing
    or empty is no moves; an unknown group is an error."""
    path = Path(path)
    if not path.exists():
        return {}
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected speaker: group lines")
    moves = {str(name): str(group) for name, group in data.items()}
    for name, group in moves.items():
        if group not in POOLS:
            raise ValueError(f"{path.name}: {name!r} is moved to {group!r}, which is not a casting group")
    return moves


def write_moves(moves, path=MOVES):
    """Write the moves back, sorted, keeping the comment block that opens
    the file."""
    path = Path(path)
    old = path.read_text(encoding="utf-8") if path.exists() else MOVES_HEADER
    lines = old.splitlines(keepends=True)
    head = "".join(lines[:next((i for i, line in enumerate(lines) if line.strip() and not line.startswith("#")),
                               len(lines))])
    body = "".join(f"{json.dumps(name, ensure_ascii=False)}: {group}\n"
                   for name, group in sorted(moves.items(), key=lambda kv: kv[0].lower())) or "{}\n"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(head + body, encoding="utf-8")
    tmp.replace(path)


def moss_defaults(d):
    """The unnamed speaker is narration; anyone uncast and unknown to the
    census gets the first ordinary voice."""
    return {"default": d.pools["hume_m_adult"][0], "": d.pools["narrator"][0]}


def build(designs=None, moves=None):
    """voices.json: per engine, speaker -> voice. MOSS voices come from
    voice_designs.yaml: a character with a design gets their own voice, the
    rest of the cast their group's designed pool, spread the same way.
    `moves` (cast_groups.yaml unless given) puts a speaker in another
    group: they keep their turn in the listed group, so nobody after them
    shifts, and join the end of the new one."""
    if designs is None:
        designs = load_designs(groups=set(POOLS))
    if moves is None:
        moves = load_moves()
    out = {engine: dict(defaults) for engine, defaults in DEFAULTS.items()}
    out["moss"] = moss_defaults(designs)
    seen = set()
    turns = {}   # group -> (members listed, members on its MOSS pool), where the moved ones carry on from
    for group, characters in CAST.items():
        kokoro_pool, piper_voice = POOLS[group]
        pooled = 0   # members on the MOSS pool so far: the designed ones do not take a turn
        for i, names in enumerate(characters):
            names = [names] if isinstance(names, str) else names
            voice = spread(kokoro_pool, i)
            moss = designs.voice_for_speaker(names[0])
            if not moss:
                moss = spread(designs.pools[group], pooled)
                pooled += 1
            for name in names:
                if name in seen:
                    raise SystemExit(f"{name!r} is cast twice")
                seen.add(name)
                if moves.get(name, group) != group:
                    continue   # placed below, in the group they were moved to
                out["kokoro"][name] = voice
                out["piper"][name] = piper_voice
                out["moss"][name] = designs.voice_for_speaker(name) or moss
        turns[group] = (len(characters), pooled)
    for name, group in sorted(moves.items()):
        if name in out["kokoro"]:
            continue   # moved to the group the list already has them in
        kokoro_pool, piper_voice = POOLS[group]
        listed, pooled = turns.get(group, (0, 0))
        moss = designs.voice_for_speaker(name)
        out["kokoro"][name] = spread(kokoro_pool, listed)
        out["piper"][name] = piper_voice
        out["moss"][name] = moss or spread(designs.pools[group], pooled)
        turns[group] = (listed + 1, pooled + (0 if moss else 1))
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
