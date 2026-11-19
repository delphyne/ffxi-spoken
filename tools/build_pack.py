#!/usr/bin/env python3
"""Build an audio pack: docs/Audio-Packs.md made real.

    .venv/bin/python -I tools/build_pack.py rhapsodies 0.1 [--missions rov] [--dry-run] [--limit N]
    .venv/bin/python -I tools/build_pack.py jeuno 0.1 --source quests/jeuno --source missions/rotz

Takes every line its sources can show and a pack may voice (tools/corpus.py:
a mission folder, a quest region, or a companion, decoded by
tools/xievent.py, minus what helper/exclude.yaml drops), cleaned the way
the addon will (tools/ffxitext.py), and renders one file per unique speaker and text:
lexicon applied, the speaker's cast voice (voices.json, else the census
race pool), synthesised through the helper's backend into the helper's WAV
cache, then encoded to Opus. Writes

    <out>/<pack>/<version>.part/      while building
    <out>/<pack>/<version>/           when done: manifest.json and
        Iroha/i-bid-you-finish-your-tasks.7f3a.opus ...
    <out>/<pack>-<version>.zip        the same, for a GitHub Release
    packs/<pack>/<version>/manifest.json   a copy to commit
    packs/index.json                  current version, URL, and sha256 of each pack

The manifest row for a line carries the lookup key (speaker, cleaned
text), the file, its sha256 and duration, and provenance: the voice, the
text the engine was given, and every zone, event, and text id the line
came from with a hash of the game's bytes there. The manifest itself
records the engine and the hashes of its model files, the repo commit,
the lexicon hash, and what was skipped. Audio is a build product, so it
is never committed: the manifest and index are, and the archive goes to
a Release (`make release`). Lines with an item name or a number cannot
be enumerated and are skipped, as are menus, system text, stock speech,
and lines with nothing to say; a line of dialogue nobody is named for
stops the build until helper/cast_as.yaml says who speaks it (a dry run
lists them and goes on). A line naming the player is rendered with
--player-word in that slot and keyed on the `<player>` token the addon
substitutes. The WAV cache makes a rebuild cost only what changed. Needs
opusenc (opus-tools) or ffmpeg.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import wave
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "helper"))
import corpus as extraction  # noqa: E402
import ffxitext  # noqa: E402
import spoken_helper as sh  # noqa: E402
import xidat  # noqa: E402
import xievent  # noqa: E402

PLAYER_WORD = "adventurer"
BITRATE = 48
UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # not allowed in a Windows file name


def slug(text, limit=40):
    """A readable file name stem from a line's text."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) > limit:
        s = s[:limit].rsplit("-", 1)[0] or s[:limit]
    return s or "line"


def dir_name(speaker):
    """The speaker's folder: the name as it is, minus characters Windows
    refuses; narration and names with nothing left get fixed folders."""
    if not speaker:
        return "narration"
    return UNSAFE.sub("", speaker).strip(" .") or "unnamed"


def key_hash(speaker, text):
    return hashlib.sha256(f"{speaker}\0{text}".encode("utf-8")).hexdigest()


def file_name(speaker, text, taken):
    """<speaker>/<slug>.<hash>.opus, the hash just long enough to be unique
    in this pack (`taken` maps paths already given out to their key)."""
    h = key_hash(speaker, text)
    for n in (4, 8, 16):
        path = f"{dir_name(speaker)}/{slug(text)}.{h[:n]}.opus"
        if taken.get(path, (speaker, text)) == (speaker, text):
            taken[path] = (speaker, text)
            return path
    raise RuntimeError(f"cannot name {speaker!r} {text!r}")


def corpus(rows, narration=True, cast_as=None, exclude=None):
    """(lines, skipped, blocked) from decoder rows, through tools/corpus.py.
    `lines` are the unique (speaker, text) keys a pack may voice, in
    first-seen order, each with its sources; `skipped` counts what was left
    out by reason; `blocked` are lines of dialogue nobody is named for,
    which a pack must not ship. `cast_as` (helper/cast_as.py) names who
    really says a line whose shown name is blank, "???", or a placeholder:
    the line then carries `as`, and one key cast two ways is an error."""
    records, _ = extraction.extract(rows, exclude, cast_as)
    lines, order, skipped, blocked = {}, [], {}, []

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1

    for r in records:
        cls = r["class"]
        if cls == "unattributed":
            blocked.append(r)
        elif cls == "narration" and not narration:
            skip("narration")
        elif cls in extraction.VOICED:
            key = (r["speaker"], r["text"])
            if key not in lines:
                lines[key] = {"speaker": r["speaker"], "text": r["text"], "sources": []}
                if r.get("as"):
                    lines[key]["as"] = r["as"]
                order.append(key)
            lines[key]["sources"] += [s for s in r["sources"] if s not in lines[key]["sources"]]
        else:
            skip(f"{cls}: {r['reason']}" if cls in extraction.EXCLUDED else r["reason"])
    return [lines[k] for k in order], skipped, blocked


def blocked_report(blocked, limit=10):
    """Where the unattributed lines are, without their text."""
    where = {}
    for r in blocked:
        at = r["sources"][0]
        where[(at["zone"], at["event"])] = where.get((at["zone"], at["event"]), 0) + 1
    out = [f"{len(blocked)} lines of dialogue have no speaker, in {len(where)} scenes; "
           "helper/cast_as.yaml must say who speaks them"]
    out += [f"  zone {z} event {e}: {n}" for (z, e), n in sorted(where.items())[:limit]]
    if len(where) > limit:
        out.append(f"  and {len(where) - limit} more scenes (make audit-corpus SOURCE=... SHOW=unattributed)")
    return out


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def wav_duration(path):
    with wave.open(str(path), "rb") as w:
        return round(w.getnframes() / w.getframerate(), 3)


class Encoder:
    """WAV to Opus, mono at `bitrate` kbit/s, through opusenc or ffmpeg."""

    def __init__(self, bitrate=BITRATE):
        self.bitrate = bitrate
        if shutil.which("opusenc"):
            self.tool = "opusenc"
        elif shutil.which("ffmpeg"):
            self.tool = "ffmpeg"
        else:
            raise RuntimeError("no Opus encoder: install opus-tools (opusenc) or ffmpeg")

    def __call__(self, wav, opus):
        Path(opus).parent.mkdir(parents=True, exist_ok=True)
        if self.tool == "opusenc":
            cmd = ["opusenc", "--quiet", "--bitrate", str(self.bitrate), "--downmix-mono", str(wav), str(opus)]
        else:
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-ac", "1", "-c:a", "libopus",
                   "-b:a", f"{self.bitrate}k", str(opus)]
        subprocess.run(cmd, check=True)


class Builder:
    """One pack version. `synth(spoken, voice, wav_path)` and
    `encode(wav, opus)` are injectable; `voice_for(speaker)` is the
    backend's casting; `game_text(source)` hashes the DAT bytes."""

    def __init__(self, out_root, pack, version, synth, encode, lexicon, voice_for, cache_dir,
                 player_word=PLAYER_WORD, phonemes=True, game_text=None, log=print, bitrate=BITRATE, engine=None,
                 source=None, two_pass=None):
        self.root = Path(out_root) / pack
        self.pack, self.version = pack, version
        self.synth, self.encode, self.lexicon, self.voice_for = synth, encode, lexicon, voice_for
        self.cache = Path(cache_dir)
        self.player_word, self.phonemes, self.game_text, self.log = player_word, phonemes, game_text, log
        self.bitrate, self.engine, self.source = bitrate, engine or {"name": "?"}, source or provenance()
        self.part = self.root / f"{version}.part"
        self.final = self.root / version
        # a backend whose model and codec take turns on the GPU (the moss
        # backend): every missing line goes through generate_codes first,
        # then decode_codes, before the usual loop finds the WAVs cached
        self.two_pass = two_pass

    def spoken(self, text):
        """What the engine is given: the player's slot filled, the stage
        directions dropped, then the lexicon."""
        plain = sh.strip_directions(text.replace(ffxitext.PLAYER, self.player_word))
        return self.lexicon.apply(plain, phonemes=self.phonemes)

    def plan(self, lines):
        """Rows before any audio exists: key, file, voice, spoken text, sources."""
        taken, rows = {}, []
        for line in lines:
            speaker, text = line["speaker"], line["text"]
            row = {"speaker": speaker, "text": text, "file": file_name(speaker, text, taken),
                   "voice": self.voice_for(line.get("as") or speaker), "spoken": self.spoken(text),
                   "sources": [dict(s, game_text=self.game_text(s)) if self.game_text else dict(s)
                               for s in line["sources"]]}
            if line.get("as"):
                row["as"] = line["as"]   # the shown name stays the key; the voice is this character's
            rows.append(row)
        return rows

    def wav_path(self, row):
        return self.cache / f"{sh.cache_key(row['speaker'], row['spoken'], row['voice'])}.wav"

    def passes(self, rows):
        """The two-pass backend's turn: codes for every line missing from the
        WAV cache with only the model loaded, then audio with only the codec.
        Codes are kept under cache/<backend>-codes/ until decoded, and both
        kinds of file are written whole, so an interrupted build picks up in
        whichever pass it stopped."""
        b = self.two_pass
        todo, seen = [], set()
        for row in rows:
            wav = self.wav_path(row)
            if not wav.exists() and wav not in seen:
                seen.add(wav)
                todo.append((row, wav, self.cache / f"{b.name}-codes" / f"{wav.stem}.pt"))
        if not todo:
            return
        self.log(f"  {len(todo)} lines to render in two passes")
        b.prepare(sorted({row["voice"] for row, _, _ in todo}))
        started, pending = time.time(), [t for t in todo if not t[2].exists()]
        if pending:
            b.begin_generate()
            for n, (row, _, codes) in enumerate(pending, 1):
                b.generate_codes(row["spoken"], row["voice"], codes)
                if n % 25 == 0 or n == len(pending):
                    self.log(f"  codes {n}/{len(pending)}, {time.time() - started:.0f}s")
        started = time.time()
        b.begin_decode()
        for n, (row, wav, codes) in enumerate(todo, 1):
            wav.parent.mkdir(parents=True, exist_ok=True)
            tmp = wav.with_name(wav.stem + ".tmp.wav")
            b.decode_codes(codes, tmp)
            tmp.replace(wav)
            codes.unlink()
            if n % 25 == 0 or n == len(todo):
                self.log(f"  audio {n}/{len(todo)}, {time.time() - started:.0f}s")

    def render(self, row):
        wav = self.wav_path(row)
        if not wav.exists():
            wav.parent.mkdir(parents=True, exist_ok=True)
            self.synth(row["spoken"], row["voice"], wav)
        opus = self.part / row["file"]
        self.encode(wav, opus)
        row["duration"] = wav_duration(wav)
        row["sha256"] = sha256_file(opus)
        row["bytes"] = opus.stat().st_size
        return row

    def build(self, lines, skipped, limit=0, force=False):
        if self.final.exists() and not force:
            raise FileExistsError(f"{self.final} exists; bump the version or pass --force")
        if self.part.exists():
            shutil.rmtree(self.part)
        self.part.mkdir(parents=True)
        rows = self.plan(lines[:limit] if limit else lines)
        if self.two_pass:
            self.passes(rows)
        started = time.time()
        for n, row in enumerate(rows, 1):
            self.render(row)
            if n % 25 == 0 or n == len(rows):
                self.log(f"  {n}/{len(rows)} rendered, {time.time() - started:.0f}s")
        manifest = {
            "pack": self.pack, "version": self.version,
            "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "format": {"codec": "opus", "bitrate_kbps": self.bitrate, "channels": 1},
            "engine": self.engine() if callable(self.engine) else self.engine,
            "source": self.source, "player_word": self.player_word,
            "lexicon_sha256": self.lexicon_sha256(),
            "lines": rows, "skipped": skipped,
            "duration": round(sum(r["duration"] for r in rows), 1),
            "bytes": sum(r["bytes"] for r in rows),
        }
        (self.part / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                                                 encoding="utf-8")
        if self.final.exists():
            shutil.rmtree(self.final)
        self.part.rename(self.final)
        return manifest

    def lexicon_sha256(self):
        path = getattr(self.lexicon, "path", None)
        return sha256_file(path) if path and Path(path).exists() else None


def provenance(repo=None):
    """The commit the pack was built from, and whether the tree was clean."""
    repo = Path(repo or HERE.parent)
    try:
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                                check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True,
                               check=True).stdout.strip() != ""
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None, "builder": "tools/build_pack.py"}
    return {"commit": commit, "dirty": dirty, "builder": "tools/build_pack.py"}


def engine_info(backend):
    """Which engine, on what, with the hashes of its model files, so the
    same pack can be rebuilt from the same weights. A backend with its own
    `engine_record` (moss: model revisions, settings, reference clips) says
    so itself."""
    record = getattr(backend, "engine_record", None)
    if record:
        return record()
    info = {"name": backend.name, "device": getattr(backend, "device", None), "models": {}}
    for attr in ("model_file", "voices_file"):
        name = getattr(backend, attr, None)
        if name and (backend.model_dir / name).exists():
            info["models"][name] = sha256_file(backend.model_dir / name)
    return info


def write_archive(final, zip_path):
    """The version directory as <pack>/<version>/... in a zip. Opus does
    not compress, so entries are stored; returns (sha256, bytes)."""
    final, zip_path = Path(final), Path(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
        for f in sorted(final.rglob("*")):
            if f.is_file():
                z.write(f, f"{final.parent.name}/{final.name}/{f.relative_to(final).as_posix()}")
    return sha256_file(zip_path), zip_path.stat().st_size


def write_voices_archive(engine, refs_dir, zip_path):
    """The reference clips a moss pack used, with their index entries, as
    voices/<voice>.wav plus voices/index.json; None when the engine used no
    clips. Each clip's sha256 is checked against the manifest's record so
    the archive holds exactly the voices the pack was rendered with."""
    clips = (engine or {}).get("clips") or {}
    if not clips:
        return None
    refs_dir = Path(refs_dir)
    index = json.loads((refs_dir / "index.json").read_text(encoding="utf-8"))
    entries = {}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
        for voice, sha in sorted(clips.items()):
            clip = refs_dir / f"{voice}.wav"
            if sha256_file(clip) != sha:
                raise RuntimeError(f"{clip} changed since the pack was rendered; redesigning it needs a rebuild")
            z.write(clip, f"voices/{voice}.wav")
            entries[voice] = index["voices"].get(voice, {})
        z.writestr("voices/index.json", json.dumps({"calibration": index.get("calibration"),
                                                    "model": index.get("model"), "voices": entries},
                                                   indent=1, ensure_ascii=False) + "\n")
    return sha256_file(zip_path), Path(zip_path).stat().st_size


def download_url(repo, pack, version):
    return f"https://github.com/{repo}/releases/download/{pack}-{version}/{pack}-{version}.zip" if repo else None


def update_index(index_path, manifest, archive_sha256, archive_bytes, url):
    """packs/index.json: the current version of each pack and where its
    archive is. Written in full each time; one pack per entry."""
    index_path = Path(index_path)
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"packs": {}}
    index["packs"][manifest["pack"]] = {
        "version": manifest["version"], "built": manifest["built"], "url": url,
        "sha256": archive_sha256, "bytes": archive_bytes,
        "files": len(manifest["lines"]), "duration": manifest["duration"],
        "manifest": f"packs/{manifest['pack']}/{manifest['version']}/manifest.json",
    }
    index["packs"] = dict(sorted(index["packs"].items()))
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    return index


def release_notes(manifest, archive_sha256=None):
    speakers = {}
    for r in manifest["lines"]:
        speakers[r["speaker"]] = speakers.get(r["speaker"], 0) + 1
    eng = manifest["engine"]
    lines = [f"{manifest['pack']} {manifest['version']}: {len(manifest['lines'])} lines from {len(speakers)} speakers, "
             f"{manifest['duration'] / 60:.0f} minutes of speech, {manifest['bytes'] / 1e6:.1f} MB of Opus "
             f"at {manifest['format']['bitrate_kbps']} kbps.",
             "",
             f"Rendered with {eng['name']} on {eng.get('device') or 'cpu'} from commit "
             f"{(manifest.get('source') or {}).get('commit') or 'unknown'}"
             + (" (dirty tree)" if (manifest.get('source') or {}).get('dirty') else "") + ".",
             f"Skipped: " + ", ".join(f"{v} {k}" for k, v in sorted(manifest["skipped"].items())) + ".",
             "",
             "Unzip into Spoken's audio folder so the pack sits at "
             f"audio/{manifest['pack']}/{manifest['version']}/manifest.json."]
    if archive_sha256:
        lines += ["", f"sha256: {archive_sha256}"]
    return "\n".join(lines) + "\n"


def cast_as_table(path):
    """helper/cast_as.yaml, or None when there is none."""
    path = Path(path)
    if not path.exists():
        return None
    import cast_as  # helper/, on sys.path above
    return cast_as.CastAs.load(path)


def cast_as_warnings(table, voice_map_path, census_path):
    """What is off in the cast-as table after the corpus has been through
    it: entries the build reached that matched no line, and names no
    casting knows (any engine's section of voices.json, or the census)."""
    if table is None:
        return []
    out = [f"cast_as entry matched no line: {e} ({e.where})" for e in table.unused()]
    known = set()
    if Path(voice_map_path).exists():
        data = json.loads(Path(voice_map_path).read_text(encoding="utf-8"))
        for section in (data.values() if any(isinstance(v, dict) for v in data.values()) else [data]):
            known.update(section)
    census = {}
    if Path(census_path).exists():
        census = json.loads(Path(census_path).read_text(encoding="utf-8"))
    for name in table.names():
        if name not in known and not census.get(name, {}).get("race"):
            out.append(f"cast_as name {name!r} is in neither voices.json nor the census; it gets the default voice")
    return out


def game_text_hasher(client):
    """source -> short hash of the DAT entry's bytes, so a rebuild can see
    that the game changed a line since the pack was built."""
    tables = {}

    def hash_of(source):
        zone = source["zone"]
        if zone not in tables:
            tables[zone] = client.dialog_bytes(zone)
        raw = tables[zone][source["text_id"]]
        return hashlib.sha256(raw).hexdigest()[:16]
    return hash_of


def summarise(rows, skipped, log=print):
    def label(r):
        return (r["speaker"] or "<narration>") + (f" as {r['as']}" if r.get("as") else "")
    speakers = {}
    for r in rows:
        speakers[label(r)] = speakers.get(label(r), 0) + 1
    log(f"{len(rows)} lines from {len(speakers)} speakers; skipped: "
        + (", ".join(f"{k} {v}" for k, v in sorted(skipped.items())) or "nothing"))
    with_player = sum(ffxitext.PLAYER in r["text"] for r in rows)
    if with_player:
        log(f"{with_player} lines name the player")
    for name, n in sorted(speakers.items(), key=lambda kv: (-kv[1], kv[0])):
        voice = rows[next(i for i, r in enumerate(rows) if label(r) == name)]["voice"]
        log(f"  {n:5}  {name:28} {voice}")


def main(argv=None):
    here = HERE.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pack", help="pack name, e.g. rhapsodies")
    ap.add_argument("version", help="pack version, e.g. 0.1")
    ap.add_argument("--missions", default="rov", help="LandSandBoat scripts/missions folder the pack covers")
    ap.add_argument("--source", action="append", default=[],
                    help="a source the pack covers instead: missions/<folder>, quests/<region>, companions/<name> "
                         "(tools/corpus.py list); repeat for several")
    ap.add_argument("--exclude", default=str(here / "helper/exclude.yaml"), help="what is never voiced")
    ap.add_argument("--companions", default=str(here / "helper/companions.yaml"))
    ap.add_argument("--out", default=str(here / "audio"), help="where <pack>/<version>/ goes")
    ap.add_argument("--client", default=str(xidat.CLIENT_DEFAULT))
    ap.add_argument("--lsb", default=str(xievent.LSB_DEFAULT))
    ap.add_argument("--backend", default="auto", choices=["auto", *sh.BACKENDS])
    ap.add_argument("--voices", default=str(here / "helper/voices"))
    ap.add_argument("--voice-map", default=str(here / "helper/voices.json"))
    ap.add_argument("--lexicon", default=str(here / "helper/lexicon.yaml"))
    ap.add_argument("--census", default=str(here / "helper/census.json"))
    ap.add_argument("--cast-as", default=str(here / "helper/cast_as.yaml"),
                    help="who says the lines whose shown name is blank, ???, or a placeholder")
    ap.add_argument("--cache", default=str(here / "helper/cache"), help="the helper's WAV cache; shared with live play")
    ap.add_argument("--player-word", default=PLAYER_WORD, help="what is said where the player's name goes")
    ap.add_argument("--bitrate", type=int, default=BITRATE, help="Opus kbit/s")
    ap.add_argument("--no-narration", action="store_true", help="leave out lines with no speaker")
    ap.add_argument("--limit", type=int, default=0, help="render only the first N lines (a test build)")
    ap.add_argument("--dry-run", action="store_true", help="list the lines and voices; render nothing")
    ap.add_argument("--force", action="store_true", help="replace an existing <pack>/<version>/")
    ap.add_argument("--repo", default=None, help="GitHub owner/name the archive will be released under")
    ap.add_argument("--packs", default=str(here / "packs"), help="where the committed manifests and index live")
    ap.add_argument("--notes", action="store_true", help="print release notes for an already built version and exit")
    args = ap.parse_args(argv)

    if args.notes:
        manifest = json.loads((Path(args.packs) / args.pack / args.version / "manifest.json").read_text(encoding="utf-8"))
        entry = json.loads((Path(args.packs) / "index.json").read_text(encoding="utf-8"))["packs"].get(args.pack, {})
        sha = entry.get("sha256") if entry.get("version") == args.version else None
        print(release_notes(manifest, sha), end="")
        return 0

    client = xidat.Client(args.client)
    rows = []
    for source in args.source or [f"missions/{args.missions}"]:
        rows += extraction.rows_of(client, args.lsb, source, args.companions)[0]
    cast_as = cast_as_table(args.cast_as)
    lines, skipped, blocked = corpus(rows, narration=not args.no_narration, cast_as=cast_as,
                                     exclude=extraction.Exclude.load(args.exclude))

    for warning in cast_as_warnings(cast_as, args.voice_map, args.census):
        print(f"warning: {warning}", file=sys.stderr)

    backend_cls = sh.pick_backend(args.backend, args.voices)
    backend = backend_cls(args.voices, sh.load_voice_map(args.voice_map, backend_cls.name),
                          fallback=sh.census_fallback(args.census, backend_cls.name))
    lexicon = sh.Lexicon.load(args.lexicon, backend=backend_cls.name)
    lexicon.path = args.lexicon
    two_pass = backend if getattr(backend, "two_pass", False) else None
    builder = Builder(args.out, args.pack, args.version, backend.synthesise, Encoder(args.bitrate), lexicon,
                      backend.voice_for, args.cache, player_word=args.player_word, phonemes=backend.phonemes,
                      game_text=game_text_hasher(client), bitrate=args.bitrate,
                      engine=(lambda: engine_info(backend)) if two_pass else engine_info(backend),
                      two_pass=two_pass)
    if args.dry_run:
        plan = builder.plan(lines[:args.limit] if args.limit else lines)
        summarise(plan, skipped)
        for r in plan[:10]:
            print(f"  {r['file']}  <- {r['spoken']!r}")
        for line in blocked_report(blocked) if blocked else []:
            print(line)
        return 0
    if blocked:
        sys.exit("\n".join(blocked_report(blocked)))
    if backend_cls.name == "none":
        sys.exit("no engine available (make venv, make voices); --dry-run lists the lines without one")
    warm = getattr(backend, "warm_up", None)
    if warm and not two_pass:   # a two-pass backend loads each model in its own pass
        warm()
        builder.engine = engine_info(backend)  # the device is known once the model is loaded
    manifest = builder.build(lines, skipped, limit=args.limit, force=args.force)
    summarise(manifest["lines"], skipped)
    print(f"wrote {builder.final}: {len(manifest['lines'])} files, {manifest['duration'] / 60:.1f} min, "
          f"{manifest['bytes'] / 1e6:.1f} MB")
    if args.limit:
        print("a --limit build is a test: no archive, manifest copy, or index entry")
        return 0
    archive = Path(args.out) / f"{args.pack}-{args.version}.zip"
    sha, size = write_archive(builder.final, archive)
    print(f"wrote {archive}: {size / 1e6:.1f} MB, sha256 {sha[:12]}")
    voices = write_voices_archive(manifest["engine"], Path(args.voices) / "moss" / "refs",
                                  Path(args.out) / f"{args.pack}-{args.version}-voices.zip")
    if voices:
        print(f"wrote {Path(args.out) / f'{args.pack}-{args.version}-voices.zip'}: the reference clips, "
              f"{voices[1] / 1e6:.1f} MB; make release uploads it too")
    keep = Path(args.packs) / args.pack / args.version / "manifest.json"
    keep.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(builder.final / "manifest.json", keep)
    update_index(Path(args.packs) / "index.json", manifest, sha, size, download_url(args.repo, args.pack, args.version))
    print(f"wrote {keep} and {Path(args.packs) / 'index.json'}: commit them; `make release` uploads the archive")
    if manifest["source"].get("dirty"):
        print("note: the tree had uncommitted changes; the manifest's commit does not describe them", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
