#!/usr/bin/env python3
"""Build an audio pack: docs/Audio-Packs.md made real.

    .venv/bin/python -I tools/build_pack.py rhapsodies 0.1 [--missions rov] [--dry-run] [--limit N]

Takes every line a storyline's cutscenes can show (tools/xievent.py over
LandSandBoat's mission scripts), cleans it the way the addon will
(tools/ffxitext.py), and renders one file per unique speaker and text:
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
be enumerated and are skipped; a line naming the player is rendered with
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


def corpus(rows, narration=True):
    """Unique (speaker, text) keys from decoder rows, in first-seen order,
    each with its sources; plus counts of what was left out and why."""
    lines, order, skipped = {}, [], {}
    for row in rows:
        if row["kind"] == "menu" or (row["kind"] == "narration" and not narration):
            skipped[row["kind"]] = skipped.get(row["kind"], 0) + 1
            continue
        if row.get("text_id") is None:
            skipped["variable text id"] = skipped.get("variable text id", 0) + 1
            continue
        if not row.get("variants"):
            live = sorted(set(row.get("live_only") or []))
            why = "live-only: " + ", ".join(live) if live else "empty text"
            skipped[why] = skipped.get(why, 0) + 1
            continue
        source = {"zone": row["zone"], "event": row["event"], "text_id": row["text_id"]}
        for text in row["variants"]:
            key = (row["speaker"], text)
            if key not in lines:
                lines[key] = {"speaker": row["speaker"], "text": text, "sources": []}
                order.append(key)
            if source not in lines[key]["sources"]:
                lines[key]["sources"].append(source)
    return [lines[k] for k in order], skipped


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
                 source=None):
        self.root = Path(out_root) / pack
        self.pack, self.version = pack, version
        self.synth, self.encode, self.lexicon, self.voice_for = synth, encode, lexicon, voice_for
        self.cache = Path(cache_dir)
        self.player_word, self.phonemes, self.game_text, self.log = player_word, phonemes, game_text, log
        self.bitrate, self.engine, self.source = bitrate, engine or {"name": "?"}, source or provenance()
        self.part = self.root / f"{version}.part"
        self.final = self.root / version

    def spoken(self, text):
        """What the engine is given: the player's slot filled, then the lexicon."""
        return self.lexicon.apply(text.replace(ffxitext.PLAYER, self.player_word), phonemes=self.phonemes)

    def plan(self, lines):
        """Rows before any audio exists: key, file, voice, spoken text, sources."""
        taken, rows = {}, []
        for line in lines:
            speaker, text = line["speaker"], line["text"]
            rows.append({"speaker": speaker, "text": text, "file": file_name(speaker, text, taken),
                         "voice": self.voice_for(speaker), "spoken": self.spoken(text),
                         "sources": [dict(s, game_text=self.game_text(s)) if self.game_text else dict(s)
                                     for s in line["sources"]]})
        return rows

    def render(self, row):
        wav = self.cache / f"{sh.cache_key(row['speaker'], row['spoken'], row['voice'])}.wav"
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
        started = time.time()
        for n, row in enumerate(rows, 1):
            self.render(row)
            if n % 25 == 0 or n == len(rows):
                self.log(f"  {n}/{len(rows)} rendered, {time.time() - started:.0f}s")
        manifest = {
            "pack": self.pack, "version": self.version,
            "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "format": {"codec": "opus", "bitrate_kbps": self.bitrate, "channels": 1},
            "engine": self.engine, "source": self.source, "player_word": self.player_word,
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
    same pack can be rebuilt from the same weights."""
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
    speakers = {}
    for r in rows:
        speakers[r["speaker"]] = speakers.get(r["speaker"], 0) + 1
    log(f"{len(rows)} lines from {len(speakers)} speakers; skipped: "
        + (", ".join(f"{k} {v}" for k, v in sorted(skipped.items())) or "nothing"))
    with_player = sum(ffxitext.PLAYER in r["text"] for r in rows)
    if with_player:
        log(f"{with_player} lines name the player")
    for name, n in sorted(speakers.items(), key=lambda kv: (-kv[1], kv[0])):
        voice = rows[next(i for i, r in enumerate(rows) if r["speaker"] == name)]["voice"]
        log(f"  {n:5}  {name or '<narration>':28} {voice}")


def main(argv=None):
    here = HERE.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pack", help="pack name, e.g. rhapsodies")
    ap.add_argument("version", help="pack version, e.g. 0.1")
    ap.add_argument("--missions", default="rov", help="LandSandBoat scripts/missions folder the pack covers")
    ap.add_argument("--out", default=str(here / "audio"), help="where <pack>/<version>/ goes")
    ap.add_argument("--client", default=str(xidat.CLIENT_DEFAULT))
    ap.add_argument("--lsb", default=str(xievent.LSB_DEFAULT))
    ap.add_argument("--backend", default="auto", choices=["auto", *sh.BACKENDS])
    ap.add_argument("--voices", default=str(here / "helper/voices"))
    ap.add_argument("--voice-map", default=str(here / "helper/voices.json"))
    ap.add_argument("--lexicon", default=str(here / "helper/lexicon.yaml"))
    ap.add_argument("--census", default=str(here / "helper/census.json"))
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
    rows = list(xievent.rov_lines(client, args.lsb, args.missions))
    lines, skipped = corpus(rows, narration=not args.no_narration)

    backend_cls = sh.pick_backend(args.backend, args.voices)
    backend = backend_cls(args.voices, sh.load_voice_map(args.voice_map, backend_cls.name),
                          fallback=sh.census_fallback(args.census, backend_cls.name))
    lexicon = sh.Lexicon.load(args.lexicon)
    lexicon.path = args.lexicon
    builder = Builder(args.out, args.pack, args.version, backend.synthesise, Encoder(args.bitrate), lexicon,
                      backend.voice_for, args.cache, player_word=args.player_word, phonemes=backend.phonemes,
                      game_text=game_text_hasher(client), bitrate=args.bitrate, engine=engine_info(backend))
    if args.dry_run:
        plan = builder.plan(lines[:args.limit] if args.limit else lines)
        summarise(plan, skipped)
        for r in plan[:10]:
            print(f"  {r['file']}  <- {r['spoken']!r}")
        return 0
    if backend_cls.name == "none":
        sys.exit("no engine available (make venv, make voices); --dry-run lists the lines without one")
    warm = getattr(backend, "warm_up", None)
    if warm:
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
