#!/usr/bin/env python3
"""Review and edit the designed voices (helper/voice_designs.yaml) by ear.

    make voice-review
    .venv-qwen/bin/python -I tools/voice_review.py

A window lists every speaker the storylines have and every pool voice, with
filters for race, gender, storyline, and kind, and a search box that
matches any part of a name. For the selected voice you can edit its meta
(gender, age, race) and description, see the instruction they compose into
(designs.compose), audition it (Qwen3 VoiceDesign speaks the calibration
sentence with that instruction and seed), regenerate with a new seed, then
accept the take or reset to what is saved. A speaker on a shared pool voice
can be given a voice of their own, starting from the pool's design.

Accepting writes the clip to helper/voices/moss/refs/, its record to the
clips' index, and the description and seed to voice_designs.yaml; a new
voice also regenerates helper/voices.json. The YAML is re-read from disk
before each write and only the accepted voice changes, so edits made in an
editor meanwhile survive. Takes not accepted stay in refs/.candidates/.

Race and gender come from a design's meta when the speaker has one, else
from their casting group (helper/cast.py's CAST, else the census race).
Storylines come from helper/voice_stories.json (make voice-stories).

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
KINDS = ("all", "own voice", "pool voice", "pools")


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
        return self.voice_map.get("default") or self.designs.pools["hume_m"][0]

    def meta_of(self, name, voice):
        """(race, gender, age) for a speaker: their own design's meta, else
        their casting group's race and gender and their pool voice's age."""
        v = self.designs.voices.get(voice, {})
        if v.get("kind") == "cast":
            return v["race"], v["gender"], v["age"]
        race, gender = designs_module.race_gender(self.group_of(name, voice))
        return race, gender, v.get("age", "")

    def _rows(self):
        names = set(self.speaker_stories) | set(self.designs.speakers) | (set(self.voice_map) - {"default"})
        rows, users = [], {}
        for name in names:
            voice = self.voice_of(name)
            group = self.group_of(name, voice)
            race, gender, age = self.meta_of(name, voice)
            stories = self.speaker_stories.get(name, {})
            own = self.designs.voices.get(voice, {}).get("kind") == "cast"
            rows.append({"key": f"speaker:{name}", "label": name or NARRATION, "name": name, "voice": voice,
                         "kind": "own voice" if own else "pool voice", "group": group, "race": race,
                         "gender": gender, "age": age, "stories": stories, "lines": sum(stories.values())})
            users.setdefault(voice, []).append(rows[-1])
        for group, voices in self.designs.pools.items():
            race, gender = designs_module.race_gender(group)
            for voice in voices:
                age = self.designs.voices[voice]["age"]
                who = users.get(voice, [])
                stories = {}
                for r in who:
                    for code, n in r["stories"].items():
                        stories[code] = stories.get(code, 0) + n
                rows.append({"key": f"voice:{voice}", "label": f"pool {voice}", "name": None, "voice": voice,
                             "kind": "pools", "group": group, "race": race, "gender": gender, "age": age,
                             "stories": stories,
                             "lines": sum(stories.values())})
        self.users = {v: sorted((r["label"] for r in rs), key=str.lower) for v, rs in users.items()}
        return sorted(rows, key=lambda r: (-r["lines"], r["label"].lower()))

    def options(self):
        """The filter menus' choices, (value, label) pairs, and the meta
        fields' suggestions."""
        races = sorted({r["race"] for r in self.rows})
        genders = sorted({r["gender"] for r in self.rows})
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
        meta = {k: v[k] for k in ("gender", "age", "race")}
        meta.update(draft.get("meta", {}))
        description = draft.get("description", v["description"])
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
                "new": bool(draft.get("new")), "users": self.users.get(voice, []),
                "calibration": self.designs.calibration, "pending": voice in self.drafts}

    def compose(self, voice, description, gender=None, age=None, race=None):
        """The instruction a design would give Qwen. A pool voice's gender
        and race are its group's whatever is passed."""
        if self.designs.voices[voice]["kind"] == "pool":
            gender = race = None
        return self.designs.compose(voice, gender=gender, age=age, race=race, description=description)

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
        voices.json. The YAML is re-read first so only this voice changes."""
        draft = self.drafts.get(voice)
        if not draft or not draft.get("candidate") or not Path(draft["candidate"]).exists():
            raise ValueError("audition a take before accepting it")
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
