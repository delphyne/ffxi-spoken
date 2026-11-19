#!/usr/bin/env python3
"""Review and edit the designed voices (helper/voice_designs.yaml) by ear.

    make review-voices
    .venv-qwen/bin/python -I tools/voice_review.py

A window shows the voices as a tree, grouped by race:

    race            a heading only: race is not part of a prompt
      pool voice    one design (age, description, seed) per voice of a
                    casting group (race and gender), shared by the
                    speakers hashed onto it
        speaker     everyone on that pool voice, with how they got there
      own voice     a named character's design from the cast section

with filters for race, gender, storyline, and kind, and a search box that
matches any part of a name. Selecting a speaker on a pool voice edits that
pool voice, and the pane says so. For a voice you can edit its meta
(gender, age, race, and for an own voice accent, persona, and emotion) and
description, see the prompt they compose into (designs.compose, the form
ElevenLabs asks for, with the run's quality level), audition it (Qwen3 VoiceDesign speaks the calibration
sentence with that instruction and seed), regenerate with a new seed, then
accept the take or reset to what is saved. What is typed for a voice is a
draft that moving to another row keeps, as is its take; a take heard
before a later edit cannot be accepted until the voice is auditioned
again. A speaker on a shared pool voice can be given a voice of their own,
starting from the pool's design. A voice with a take or a saved clip can be
heard saying one of its speakers' own lines (helper/voice_stories.json
keeps a few per speaker) or anything typed, rendered by MOSS the way a
pack line is: that runs helper/moss_say.py under .venv-moss, and since
Qwen and MOSS do not share the card, the window lets go of one before it
loads the other.

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
import hashlib
import json
import os
import random
import shutil
import subprocess
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
PLAYER_WORD = "adventurer"   # what a pack says for <player> (tools/build_pack.py)
MOSS_PYTHON = REPO / ".venv-moss" / "bin" / "python"
MOSS_HF = REPO / "helper" / "voices" / "moss" / "hf"
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


class MossWorker:
    """helper/moss_say.py running under .venv-moss: one line at a time,
    rendered by MOSS continuing a clip, the model kept loaded in between.
    `say(request)` returns the WAV path; `close()` ends the process."""

    def __init__(self, python=MOSS_PYTHON, hf_home=MOSS_HF, script=REPO / "helper" / "moss_say.py"):
        if not Path(python).exists():
            raise RuntimeError(f"{python} is missing: run make venv-moss to hear lines through MOSS")
        env = {k: v for k, v in os.environ.items() if k != "MOSS_CODEC"}   # one-off lines decode on the CPU
        env.update(HF_HOME=str(hf_home), PYTORCH_ALLOC_CONF="expandable_segments:True")
        self.proc = subprocess.Popen([str(python), "-I", str(script)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, encoding="utf-8", env=env)
        if not self._read().get("ready"):
            raise RuntimeError("the MOSS worker did not start; see the terminal")

    def _read(self):
        line = self.proc.stdout.readline()
        if not line:
            code = self.proc.wait()
            raise RuntimeError(f"the MOSS worker ended (exit {code}); see the terminal")
        return json.loads(line)

    def say(self, request):
        self.proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        reply = self._read()
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "MOSS failed"))
        return Path(reply["out"])

    def close(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
                self.proc.wait()


class Session:
    """Everything the window shows and does, without the window.

    `engine` designs a clip: `manual_seed(seed)` then `design(text,
    description) -> (samples, rate)`; it is made on first use by
    `engine_factory`, because loading Qwen takes a while. `worker_factory`
    makes the MOSS worker that `hear` renders a line through; the card
    holds one of the two at a time, so each is let go before the other
    loads."""

    def __init__(self, designs_path=designs_module.DEFAULT_PATH, refs=dv.REFS,
                 voices_json=REPO / "helper" / "voices.json", census=REPO / "helper" / "census.json",
                 stories=REPO / "helper" / "voice_stories.json", engine_factory=None, rng=None,
                 worker_factory=None, lexicon=REPO / "helper" / "lexicon.yaml",
                 lines=REPO / "data" / "voice_lines.json", quality=designs_module.DEFAULT_QUALITY):
        self.designs_path, self.refs = Path(designs_path), Path(refs)
        self.quality = quality
        self.voices_json = Path(voices_json)
        self.census = load_json(census, {})
        data = load_json(stories, {"stories": {}, "speakers": {}})
        self.story_names, self.speaker_stories = data["stories"], data["speakers"]
        # speaker -> a few lines they say, to hear a voice say: the game's text, so kept in data/
        self.lines = load_json(lines, {})
        self.cast_groups = cast_groups()
        self.engine_factory = engine_factory or (lambda: dv.Qwen())
        self.worker_factory = worker_factory or MossWorker
        self.lexicon_path = Path(lexicon)
        self._lexicon = None
        self._engine = None
        self._worker = None
        self.rng = rng or random.Random()
        self.drafts = {}   # voice -> {"meta", "description", "seed", "candidate", "new": speaker or None}
        self.reload()

    # -- catalogue -------------------------------------------------------

    def load(self):
        """The YAML as it is on disk now."""
        return designs_module.Designs.load(self.designs_path, groups=set(casting.POOLS), quality=self.quality)

    def reload(self):
        self.designs = self.load()
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
        for race in sorted(set(designs_module.RACES) | {r["race"] for r in rows}):
            under = [r for r in rows if r["parent"] == f"race:{race}"]
            rows.append({"key": f"race:{race}", "label": race, "name": None, "voice": "", "kind": "race",
                         "group": None, "race": race, "gender": "", "age": "", "stories": self._sum(under),
                         "lines": sum(r["lines"] for r in under), "via": "", "how": "", "parent": ""})
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
                "meta_race": list(designs_module.RACES)}

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

    def race_voices(self, race):
        """The voices of a race; a race row is a heading, with nothing of
        its own to edit."""
        return [v for v, d in self.designs.voices.items() if d["race"] == race]

    def row(self, key):
        return next(r for r in self.rows if r["key"] == key)

    # -- one voice ------------------------------------------------------

    def clip(self, voice):
        path = self.refs / f"{voice}.wav"
        return path if path.exists() else None

    def state(self, voice):
        """What the editor shows for a voice. `instruct` is the prompt as
        the fields stand, what an audition would give Qwen; `clip_instruct` what the saved clip was
        made with, and `stale` whether that differs from the saved design."""
        v = self.designs.voices[voice]
        draft = self.drafts.get(voice, {})
        typed = draft.get("typed", {})
        meta = self._meta(v)
        meta.update(draft.get("meta", {}))
        meta.update(typed.get("meta", {}))
        description = typed.get("description", draft.get("description", v["description"]))
        cand = draft.get("candidate")
        rec = self.index.get(voice)
        clip = self.clip(voice)
        return {"voice": voice, "kind": v["kind"], "group": v.get("group"),
                "saved_description": v["description"], "saved_seed": v["seed"],
                "saved_meta": self._meta(v),
                "description": description, "seed": draft.get("seed", v["seed"]), **meta,
                "instruct": self.compose(voice, description, **meta), "saved_instruct": self.designs.compose(voice),
                "clip": clip, "clip_instruct": dv.made_with(rec) if clip and rec else None,
                "stale": bool(clip and rec and dv.is_changed(self.designs, voice, rec)),
                "candidate": cand if cand and Path(cand).exists() else None,
                "edited_since_take": bool(cand and typed),
                "new": bool(draft.get("new")), "users": self.users.get(voice, []),
                "calibration": self.designs.calibration, "pending": voice in self.drafts}

    @staticmethod
    def _meta(v):
        return {k: v[k] for k in designs_module.META}

    @staticmethod
    def _pool_meta(v, meta):
        """`meta` as a pool voice may have it: only its age is its own."""
        return {k: (val if k == "age" else None) for k, val in meta.items()} if v["kind"] == "pool" else meta

    def edit_voice(self, voice, description, **meta):
        """The fields as typed for a voice (`meta` is any of designs.META),
        kept as a draft so moving to another row does not lose them. Typed
        back to the take's values (or the saved ones when there is no take)
        they are no draft at all. A take heard before the edit cannot be
        accepted: audition again."""
        v = self.designs.voices[voice]
        draft = self.drafts.setdefault(voice, {})
        base_meta = self._meta(v)
        base_meta.update(draft.get("meta", {}))
        meta = self._pool_meta(v, meta)
        typed = {"description": " ".join(description.split()),
                 "meta": {k: base if meta.get(k) is None else " ".join(meta[k].split())
                          for k, base in base_meta.items()}}
        base = {"description": draft.get("description", v["description"]), "meta": base_meta}
        if typed == base:
            draft.pop("typed", None)
        else:
            draft["typed"] = typed
        if not draft:
            del self.drafts[voice]

    def compose(self, voice, description, **meta):
        """The prompt a design would compose into. A pool voice's gender
        and race are its group's whatever is passed, and it has no accent,
        persona, or emotion."""
        meta = self._pool_meta(self.designs.voices[voice], meta)
        return self.designs.compose(voice, description=description, **meta)

    def engine(self):
        if self._engine is None:
            self.close_worker()   # the card holds one engine at a time
            self._engine = self.engine_factory()
        return self._engine

    def free_engine(self):
        """Let go of Qwen, so MOSS can have the card."""
        if self._engine is None:
            return
        self._engine = None
        import gc
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def worker(self):
        if self._worker is None:
            self.free_engine()
            self._worker = self.worker_factory()
        return self._worker

    def close_worker(self):
        if self._worker is not None:
            self._worker.close()
            self._worker = None

    def close(self):
        self.close_worker()

    # -- hearing a line through MOSS --------------------------------------

    def lines_of(self, voice, name=None):
        """Lines to hear the voice say: the speaker `name`'s first, then
        those of everyone the voice belongs to."""
        names = [name] if name else []
        names += self.designs.voices.get(voice, {}).get("names") or []
        names += self.users.get(voice, [])
        out = []
        for n in names:
            for line in self.lines.get(n, []):
                if line not in out:
                    out.append(line)
        return out

    def lexicon(self):
        if self._lexicon is None:
            self._lexicon = sh.Lexicon.load(self.lexicon_path, backend="moss")
        return self._lexicon

    def hear(self, voice, text):
        """`text` said by the voice: MOSS continues its clip, the take when
        there is one the fields still match, else the saved clip, the way a
        pack line is made (directions dropped, the player named, the
        lexicon applied). Returns (wav path, what was continued); the WAV
        is kept under refs/.candidates/hear/ and not made twice."""
        st = self.state(voice)
        if st["candidate"] and not st["edited_since_take"]:
            ref, ref_text, how = st["candidate"], self.designs.calibration, "take"
        elif st["clip"]:
            ref, how = st["clip"], "saved clip"
            ref_text = (self.index.get(voice) or {}).get("calibration") or self.designs.calibration
        else:
            raise ValueError("nothing to continue: audition a take, or accept one, first")
        plain = " ".join(sh.strip_directions(text.replace("<player>", PLAYER_WORD)).split())
        if not plain:
            raise ValueError("nothing to say")
        spoken = self.lexicon().apply(plain, phonemes=True)
        tag = hashlib.sha1(f"{dv.sha256_file(ref)}|{ref_text}|{spoken}|{st['seed']}".encode("utf-8")).hexdigest()[:10]
        out = self.refs / ".candidates" / "hear" / f"{voice}.{tag}.wav"
        if not out.exists():
            self.worker().say({"ref": str(ref), "ref_text": ref_text, "seed": st["seed"], "text": spoken,
                               "out": str(out)})
        return out, how

    def audition(self, voice, description, seed=None, **meta):
        """Design a take from `description` and the meta (each field, when
        None or not given, the draft's or the saved one) with `seed`,
        likewise, into refs/.candidates/; returns its path."""
        description = " ".join(description.split())
        if not description:
            raise ValueError("the description is empty")
        st = self.state(voice)
        seed = st["seed"] if seed is None else seed
        v = self.designs.voices[voice]
        meta = self._pool_meta(v, meta)
        meta = {k: st[k] if meta.get(k) is None else meta[k] for k in designs_module.META}
        if v["kind"] == "pool":
            meta.update(gender=st["saved_meta"]["gender"], race=st["saved_meta"]["race"])
        if meta["race"] not in designs_module.RACES and meta["race"] != "unknown":
            raise ValueError(f"race {meta['race']!r} is not a casting group's ({', '.join(designs_module.RACES)})")
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
        meta = {"gender": gender, "age": age, "race": race if race in designs_module.RACES else "hume",
                "accent": "", "persona": "", "emotion": ""}
        voice = self.designs.add_cast(name, src["description"], src["seed"], **meta)
        self.drafts[voice] = {"description": src["description"], "seed": src["seed"], "new": name, "meta": meta}
        self.rows = self._rows()
        return voice

    def accept(self, voice):
        """Keep the auditioned take: clip, index, YAML, and for a new voice
        voices.json. The YAML is re-read first so only this voice
        changes."""
        draft = self.drafts.get(voice)
        if not draft or not draft.get("candidate") or not Path(draft["candidate"]).exists():
            raise ValueError("audition a take before accepting it")
        if draft.get("typed"):
            raise ValueError("the fields changed since the take: audition again before accepting")
        fresh = self.load()
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
    ap.add_argument("--quality", default=designs_module.DEFAULT_QUALITY,
                    help="the quality level the prompts ask for (default: %(default)s)")
    args = ap.parse_args(argv)
    import voice_review_gui
    if not voice_review_gui.available():
        sys.exit("the voice review needs a display and Tk (run it under .venv-qwen)")
    session = Session(args.designs, args.refs, quality=args.quality,
                      engine_factory=lambda: dv.Qwen("cpu" if args.cpu else "cuda:0"))
    voice_review_gui.run(session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
