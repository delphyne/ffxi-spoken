#!/usr/bin/env python3
"""Review and edit the designed voices (helper/voice_designs.yaml) by ear.

    make review-voices
    .venv-qwen/bin/python -I tools/voice_review.py

A window shows the three layers a voice is composed from, as a tree:

    race            its phrase from the races section, shared by every
                    voice of that race
      pool voice    one design (age, description, seed) per voice of a
                    casting group (race and gender), shared by the
                    speakers hashed onto it
        speaker     everyone on that pool voice, with how they got there
      own voice     a named character's design from the cast section

with filters for race, gender, storyline, and kind, and a search box that
matches any part of a name. Selecting a speaker on a pool voice edits that
pool voice, and the pane says so. For a voice you can edit its meta
(gender, age, race) and description, see the instruction they compose into
(designs.compose), audition it (Qwen3 VoiceDesign speaks the calibration
sentence with that instruction and seed), regenerate with a new seed, then
accept the take or reset to what is saved. What is typed for a voice is a
draft that moving to another row keeps, as is its take; a take heard
before a later edit cannot be accepted until the voice is auditioned
again. A speaker on a shared pool voice can be given a voice of their own,
starting from the pool's design. A race row edits the race's phrase, which
is saved straight to the YAML: it cannot be auditioned alone, since a take
needs a gender and an age too.

Accepting writes the clip to helper/voices/moss/refs/, its record to the
clips' index, and the description and seed to voice_designs.yaml; a new
voice also regenerates helper/voices.json. The YAML is re-read from disk
before each write and only the accepted voice changes, so edits made in an
editor meanwhile survive. Takes not accepted stay in refs/.candidates/.

A speaker's casting group comes from helper/cast.py's CAST, else from the
census race; a hash of the name picks one voice from the group's pool. Race
and gender come from a design's meta when the speaker has one, else from
the group. Storylines come from helper/voice_stories.json (make
voice-stories).

Runs under .venv-qwen, which has Tk and the Qwen3 model. `Session` is the
logic and has no Tk in it; voice_review_gui.py is the window.
"""

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "helper"))
import casting  # noqa: E402
import design_voices as dv  # noqa: E402
import designs as designs_module  # noqa: E402
import spoken_helper as sh  # noqa: E402

NARRATION = "(narration)"
# a row's kind: a race, a pool voice, a speaker with a voice of their own, a speaker on a pool voice
KINDS = ("all", "race", "pool voice", "own voice", "on a pool voice")


def cast_groups():
    """speaker -> casting group, from cast.py's hand cast."""
    import cast
    out = {}
    for group, characters in cast.CAST.items():
        for names in characters:
            for name in [names] if isinstance(names, str) else names:
                out[name] = group
    return out


def load_json(path, default):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


class Session:
    """Everything the window shows and does, without the window.

    `engine` designs a clip: `manual_seed(seed)` then `design(text,
    description) -> (samples, rate)`; it is made on first use by
    `engine_factory`, because loading Qwen takes a while."""

    def __init__(self, designs_path=designs_module.DEFAULT_PATH, refs=dv.REFS,
                 voices_json=REPO / "helper" / "voices.json", census=REPO / "helper" / "census.json",
                 stories=REPO / "helper" / "voice_stories.json", engine_factory=None, rng=None):
        self.designs_path, self.refs = Path(designs_path), Path(refs)
        self.voices_json = Path(voices_json)
        self.census = load_json(census, {})
        data = load_json(stories, {"stories": {}, "speakers": {}})
        self.story_names, self.speaker_stories = data["stories"], data["speakers"]
        self.cast_groups = cast_groups()
        self.engine_factory = engine_factory or (lambda: dv.Qwen())
        self._engine = None
        self.rng = rng or random.Random()
        self.drafts = {}   # voice -> {"meta", "description", "seed", "candidate", "new": speaker or None}
        self.race_drafts = {}   # race -> a phrase typed and not saved yet; auditions of that race use it
        self.reload()

    # -- catalogue -------------------------------------------------------

    def reload(self):
        self.designs = designs_module.Designs.load(self.designs_path, groups=set(casting.POOLS))
        for voice, draft in self.drafts.items():   # a voice given in this session, not yet accepted
            if draft.get("new") and voice not in self.designs.voices:
                self.designs.add_cast(draft["new"], draft["description"], draft["seed"], **draft["meta"])
        self.index = dv.load_index(self.refs)
        self.voice_map = load_json(self.voices_json, {}).get("moss", {})
        self.rows = self._rows()

    def group_of(self, name, voice):
        """The speaker's casting group, from cast.py or the census, else the
        pool voice they are on."""
        if name in self.cast_groups:
            return self.cast_groups[name]
        race = (self.census.get(name) or {}).get("race")
        if race in casting.RACE_GROUPS:
            return casting.RACE_GROUPS[race]
        return self.designs.voices.get(voice, {}).get("group")   # a pool voice knows its group

    def voice_of(self, name):
        voice = self.designs.voice_for_speaker(name) or self.voice_map.get(name)
        if voice:
            return voice
        census = (self.census.get(name) or {}).get("race")
        if census in casting.RACE_GROUPS:
            return casting.voice_for_group(casting.RACE_GROUPS[census], name, "moss", designs=self.designs)
        return self.voice_map.get("default") or self.designs.pools["hume_m_adult"][0]

    def meta_of(self, name, voice):
        """(race, gender, age) for a speaker: their own design's meta, else
        their casting group's race and gender and their pool voice's age."""
        v = self.designs.voices.get(voice, {})
        if v.get("kind") == "cast":
            return v["race"], v["gender"], v["age"]
        race, gender = designs_module.race_gender(self.group_of(name, voice))
        return race, gender, v.get("age", "")

    def cast_via(self, name, voice, group):
        """How a speaker got their voice: a word for the list's `via` column
        and a sentence for the pane."""
        v = self.designs.voices.get(voice, {})
        label = name or NARRATION
        if v.get("kind") == "cast":
            first = v["names"][0]
            if first != name:
                return "shares", f"{label} shares {first}'s own voice, {voice} (an `also` name in the cast section)."
            if self.drafts.get(voice, {}).get("new"):
                return "own (new)", f"{label} is getting a voice of their own, {voice}; not accepted yet."
            return "own", f"{label} has a voice of their own, {voice}, in the cast section."
        census = (self.census.get(name) or {}).get("race")
        if name in self.cast_groups:
            source, why = "cast.py", f"cast.py puts {label} in group {group}"
        elif census in casting.RACE_GROUPS:
            source, why = "census", f"the census gives {label} race {census}, group {group}"
        elif name in self.voice_map:
            source, why = "voices.json", f"voices.json puts {label} in group {group}"
        else:
            return "default", (f"{label} is in neither cast.py nor the census, so they get the default voice, "
                               f"{voice}. Add them to cast.py, or give them a voice of their own.")
        pool = self.designs.pools.get(group) or []
        n = pool.index(voice) + 1 if voice in pool else 0
        return source, f"{why}; a hash of the name picks voice {n} of {len(pool)} in that pool, {voice}."

    def _rows(self):
        """Every row of the tree: races, their pool voices and the speakers
        on each, and own voices under their race. `parent` is the row
        above; a row's `lines` and `stories` include its children's."""
        names = set(self.speaker_stories) | set(self.designs.speakers) | (set(self.voice_map) - {"default"})
        rows, users = [], {}
        for name in names:
            voice = self.voice_of(name)
            group = self.group_of(name, voice)
            race, gender, age = self.meta_of(name, voice)
            stories = self.speaker_stories.get(name, {})
            own = self.designs.voices.get(voice, {}).get("kind") == "cast"
            via, how = self.cast_via(name, voice, group)
            rows.append({"key": f"speaker:{name}", "label": name or NARRATION, "name": name, "voice": voice,
                         "kind": "own voice" if own else "on a pool voice", "group": group, "race": race,
                         "gender": gender, "age": age, "stories": stories, "lines": sum(stories.values()),
                         "via": via, "how": how, "parent": f"race:{race}" if own else f"voice:{voice}"})
            users.setdefault(voice, []).append(rows[-1])
        for group, voices in self.designs.pools.items():
            race, gender = designs_module.race_gender(group)
            for voice in voices:
                who = users.get(voice, [])
                rows.append({"key": f"voice:{voice}", "name": None, "voice": voice,
                             "label": "pool " + designs_module.pool_label(group, voices.index(voice), len(voices)),
                             "kind": "pool voice", "group": group, "race": race, "gender": gender,
                             "age": self.designs.voices[voice]["age"], "stories": self._sum(who),
                             "lines": sum(r["lines"] for r in who), "via": group, "how": "",
                             "parent": f"race:{race}"})
        for race in sorted(set(self.designs.races) | {r["race"] for r in rows}):
            under = [r for r in rows if r["parent"] == f"race:{race}"]
            rows.append({"key": f"race:{race}", "label": race, "name": None, "voice": "", "kind": "race",
                         "group": None, "race": race, "gender": "", "age": "", "stories": self._sum(under),
                         "lines": sum(r["lines"] for r in under), "via": "", "how": "", "parent": "",
                         "phrase": self.designs.races.get(race)})
        self.users = {v: sorted((r["label"] for r in rs), key=str.lower) for v, rs in users.items()}
        return sorted(rows, key=lambda r: (-r["lines"], r["label"].lower()))

    @staticmethod
    def _sum(rows):
        stories = {}
        for r in rows:
            for code, n in r["stories"].items():
                stories[code] = stories.get(code, 0) + n
        return stories

    def pool_name(self, voice):
        """A pool voice as people read it: "mithra female adult 1 of 2"."""
        group = self.designs.voices[voice]["group"]
        pool = self.designs.pools[group]
        return designs_module.pool_label(group, pool.index(voice), len(pool))

    def with_parents(self, rows):
        """`rows` and every row above them, in the catalogue's order, as
        key -> row; a tree cannot show a row without its parent."""
        by_key = {r["key"]: r for r in self.rows}
        keep = set()
        for r in rows:
            while r is not None and r["key"] not in keep:
                keep.add(r["key"])
                r = by_key.get(r["parent"])
        return {r["key"]: r for r in self.rows if r["key"] in keep}

    def options(self):
        """The filter menus' choices, (value, label) pairs, and the meta
        fields' suggestions."""
        races = sorted({r["race"] for r in self.rows if r["race"]})
        genders = sorted({r["gender"] for r in self.rows if r["gender"]})   # a race row has none
        return {"race": [("all", "all races")] + [(r, r) for r in races],
                "gender": [("all", "all genders")] + [(g, g) for g in genders],
                "story": [("all", "all storylines")] + list(self.story_names.items()),
                "kind": [("all", "all kinds")] + [(k, k) for k in KINDS if k != "all"],
                "meta_gender": list(designs_module.GENDERS), "meta_age": list(designs_module.AGES),
                "meta_race": list(self.designs.races)}

    def filter(self, query="", race="all", gender="all", story="all", kind="all"):
        q = query.strip().lower()
        out = []
        for r in self.rows:
            if q and q not in r["label"].lower() and q not in r["voice"].lower():
                continue
            if race != "all" and r["race"] != race:
                continue
            if gender != "all" and r["gender"] != gender:
                continue
            if story != "all" and story not in r["stories"]:
                continue
            if kind != "all" and r["kind"] != kind:
                continue
            out.append(r)
        if story != "all":   # most-spoken in that storyline first
            out.sort(key=lambda r: (-r["stories"].get(story, 0), r["label"].lower()))
        return out

    # -- one race ------------------------------------------------------

    def race_state(self, race):
        """A race row: its phrase as it stands (the draft, else saved), the
        voices it is composed into, and whether a draft is pending. A race
        the races section does not list (a design's `unknown`) has none."""
        voices = [v for v, d in self.designs.voices.items() if d["race"] == race]
        saved = self.designs.races.get(race)
        return {"race": race, "phrase": self.race_drafts.get(race, saved), "saved_phrase": saved,
                "voices": voices, "editable": race in self.designs.races, "pending": race in self.race_drafts}

    def races(self):
        """The phrases as they stand: saved, with the drafts over them."""
        return {**self.designs.races, **self.race_drafts}

    def edit_race(self, race, phrase):
        """A phrase typed for a race: kept as a draft until saved or reset,
        so it survives moving to another row and is heard when a voice of
        the race is auditioned."""
        if race not in self.designs.races:
            raise ValueError(f"race {race!r} is not in the races section")
        phrase = " ".join(phrase.split())
        if phrase == self.designs.races[race]:
            self.race_drafts.pop(race, None)
        else:
            self.race_drafts[race] = phrase

    def save_race(self, race, phrase=None):
        """The race's phrase (the draft unless given), straight to the YAML:
        there is no take to accept, a phrase alone cannot be auditioned.
        Returns the voices whose saved clip is now from an older design."""
        if phrase is None:
            phrase = self.race_drafts.get(race, self.designs.races.get(race, ""))
        fresh = designs_module.Designs.load(self.designs_path, groups=set(casting.POOLS))
        fresh.update_race(race, phrase)
        fresh.dump(self.designs_path)
        self.race_drafts.pop(race, None)
        self.reload()
        return [v for v in self.race_state(race)["voices"]
                if self.clip(v) and v in self.index and dv.is_changed(self.designs, v, self.index[v])]

    def reset_race(self, race):
        self.race_drafts.pop(race, None)

    def row(self, key):
        return next(r for r in self.rows if r["key"] == key)

    # -- one voice ------------------------------------------------------

    def clip(self, voice):
        path = self.refs / f"{voice}.wav"
        return path if path.exists() else None

    def state(self, voice):
        """What the editor shows for a voice. `instruct` is what an
        audition would give Qwen; `clip_instruct` what the saved clip was
        made with, and `stale` whether that differs from the saved design."""
        v = self.designs.voices[voice]
        draft = self.drafts.get(voice, {})
        typed = draft.get("typed", {})
        meta = {k: v[k] for k in ("gender", "age", "race")}
        meta.update(draft.get("meta", {}))
        meta.update(typed.get("meta", {}))
        description = typed.get("description", draft.get("description", v["description"]))
        cand = draft.get("candidate")
        rec = self.index.get(voice)
        clip = self.clip(voice)
        return {"voice": voice, "kind": v["kind"], "group": v.get("group"),
                "saved_description": v["description"], "saved_seed": v["seed"],
                "saved_meta": {k: v[k] for k in ("gender", "age", "race")},
                "description": description, "seed": draft.get("seed", v["seed"]), **meta,
                "instruct": self.compose(voice, description, **meta), "saved_instruct": self.designs.compose(voice),
                "clip": clip, "clip_instruct": dv.made_with(rec) if clip and rec else None,
                "stale": bool(clip and rec and dv.is_changed(self.designs, voice, rec)),
                "candidate": cand if cand and Path(cand).exists() else None,
                "edited_since_take": bool(cand and typed),
                "new": bool(draft.get("new")), "users": self.users.get(voice, []),
                "calibration": self.designs.calibration, "pending": voice in self.drafts}

    def edit_voice(self, voice, description, gender=None, age=None, race=None):
        """The fields as typed for a voice, kept as a draft so moving to
        another row does not lose them. Typed back to the take's values (or
        the saved ones when there is no take) they are no draft at all. A
        take heard before the edit cannot be accepted: audition again."""
        v = self.designs.voices[voice]
        draft = self.drafts.setdefault(voice, {})
        base_meta = {k: v[k] for k in ("gender", "age", "race")}
        base_meta.update(draft.get("meta", {}))
        if v["kind"] == "pool":
            gender = race = None
        meta = {"gender": gender, "age": age, "race": race}
        typed = {"description": " ".join(description.split()),
                 "meta": {k: base_meta[k] if val is None else val.strip() for k, val in meta.items()}}
        base = {"description": draft.get("description", v["description"]), "meta": base_meta}
        if typed == base:
            draft.pop("typed", None)
        else:
            draft["typed"] = typed
        if not draft:
            del self.drafts[voice]

    def compose(self, voice, description, gender=None, age=None, race=None):
        """The instruction a design would give Qwen. A pool voice's gender
        and race are its group's whatever is passed."""
        if self.designs.voices[voice]["kind"] == "pool":
            gender = race = None
        return self.designs.compose(voice, races=self.races(), gender=gender, age=age, race=race,
                                    description=description)

    def engine(self):
        if self._engine is None:
            self._engine = self.engine_factory()
        return self._engine

    def audition(self, voice, description, seed=None, gender=None, age=None, race=None):
        """Design a take from `description` and the meta (each, when None,
        the draft's or the saved one) with `seed`, likewise, into
        refs/.candidates/; returns its path."""
        description = " ".join(description.split())
        if not description:
            raise ValueError("the description is empty")
        st = self.state(voice)
        seed = st["seed"] if seed is None else seed
        meta = {"gender": st["gender"] if gender is None else gender, "age": st["age"] if age is None else age,
                "race": st["race"] if race is None else race}
        if self.designs.voices[voice]["kind"] == "pool":
            meta.update(gender=st["saved_meta"]["gender"], race=st["saved_meta"]["race"])
        if meta["race"] not in self.designs.races:
            raise ValueError(f"race {meta['race']!r} is not in the races section")
        instruct = self.compose(voice, description, **meta)
        out = self.refs / ".candidates" / f"{voice}.{seed}.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        engine = self.engine()
        engine.manual_seed(seed)
        samples, rate = engine.design(self.designs.calibration, instruct)
        sh.write_wav(out, samples, rate)
        draft = self.drafts.setdefault(voice, {})
        draft.pop("typed", None)   # the take is from the fields as they stand
        draft.update(description=description, seed=seed, meta=meta, candidate=out, instruct=instruct)
        return out

    def regenerate(self, voice, description, **meta):
        """A take with the same design and a new seed."""
        return self.audition(voice, description, seed=self.rng.randrange(1, 1 << 31), **meta)

    def give_own_voice(self, name):
        """A voice of `name`'s own, starting from their current voice's
        description and seed. Pending until accepted."""
        current = self.voice_of(name)
        if self.designs.voices.get(current, {}).get("kind") == "cast":
            raise ValueError(f"{name or NARRATION} already has a voice of their own")
        src = self.designs.voices[current]
        race, gender, age = self.meta_of(name, current)
        meta = {"gender": gender, "age": age, "race": race if race in self.designs.races else "hume"}
        voice = self.designs.add_cast(name, src["description"], src["seed"], **meta)
        self.drafts[voice] = {"description": src["description"], "seed": src["seed"], "new": name, "meta": meta}
        self.rows = self._rows()
        return voice

    def accept(self, voice):
        """Keep the auditioned take: clip, index, YAML, and for a new voice
        voices.json; a draft phrase for the voice's race goes in first,
        since the take was heard with it. The YAML is re-read first so only
        this voice changes."""
        draft = self.drafts.get(voice)
        if not draft or not draft.get("candidate") or not Path(draft["candidate"]).exists():
            raise ValueError("audition a take before accepting it")
        if draft.get("typed"):
            raise ValueError("the fields changed since the take: audition again before accepting")
        race = draft.get("meta", {}).get("race") or self.designs.voices[voice]["race"]
        if race in self.race_drafts:   # the take was heard with the draft phrase, so that is saved too
            self.save_race(race)
        fresh = designs_module.Designs.load(self.designs_path, groups=set(casting.POOLS))
        if draft.get("new"):
            fresh.add_cast(draft["new"], draft["description"], draft["seed"], **draft["meta"])
        else:
            fresh.update(voice, draft["description"], draft["seed"], **draft.get("meta", {}))
        fresh.dump(self.designs_path)
        self.refs.mkdir(parents=True, exist_ok=True)
        clip = self.refs / f"{voice}.wav"
        shutil.copyfile(draft["candidate"], clip)
        index = dv.load_index(self.refs)
        index[voice] = dv.record(fresh, voice, clip, "voice review")
        dv.write_index(self.refs, index, fresh.calibration)
        if draft.get("new"):
            import cast
            self.voices_json.write_text(json.dumps(cast.build(fresh), indent=2, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
        del self.drafts[voice]
        self.reload()
        return clip

    def reset(self, voice):
        """Back to what is saved: the draft and its take are dropped, and a
        voice given in this session and never accepted goes away."""
        draft = self.drafts.pop(voice, None)
        if draft and draft.get("new"):
            self.reload()
        return voice


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--designs", default=str(designs_module.DEFAULT_PATH))
    ap.add_argument("--refs", default=str(dv.REFS))
    ap.add_argument("--cpu", action="store_true", help="run Qwen on the CPU (slow)")
    args = ap.parse_args(argv)
    import voice_review_gui
    if not voice_review_gui.available():
        sys.exit("the voice review needs a display and Tk (run it under .venv-qwen)")
    session = Session(args.designs, args.refs,
                      engine_factory=lambda: dv.Qwen("cpu" if args.cpu else "cuda:0"))
    voice_review_gui.run(session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
