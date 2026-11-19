"""Who really says a line whose on-screen name is blank, "???", or a
placeholder: helper/cast_as.yaml, read by the pack builder.

A cutscene names its speaker by an entity in the zone's entity list, and
some entities have no usable name. Every zone keeps one or two actors
literally called "???", placed beside the scene's named cast, that speak
for whoever is still off screen; a few scenes have actors with an empty
name (Cait Sith through the Beaucedine traitor hunt) or a placeholder
("1", "2", "4" in the Throne Room [S] finale). The addon sees only the
name the game shows, so the pack key keeps it; the voice is baked at
build time, and that is what this table decides.

An entry names a zone and an entity id (hex, as tools/xievent.py prints
them) and `as`, the character whose voice to use: a name voices.json or
the census knows. Because a "???" slot is reused within a zone, `lines`
(text id -> the line's first words) or `events` narrows an entry to some
of the entity's lines; the first words are checked against the decoded
text so a stale id is caught. An entry whose `as` is blank is a line still
to be cast; it matches, so it is not reported as unused, and changes
nothing. `note` is for the reader.

    - zone: 84                 # Batallia Downs [S]
      entity: 0x1054374
      lines: {8374: "Found ya at long last."}
      as: Larzos
"""

from pathlib import Path

DEFAULT = Path(__file__).resolve().parent / "cast_as.yaml"
KEYS = {"zone", "entity", "as", "lines", "events", "note"}


class Entry:
    def __init__(self, data, where):
        if not isinstance(data, dict):
            raise ValueError(f"{where}: an entry is a mapping with zone, entity, and as")
        extra = set(data) - KEYS
        if extra:
            raise ValueError(f"{where}: unknown key(s) {', '.join(sorted(extra))}")
        for key in ("zone", "entity"):
            if not isinstance(data.get(key), int) or isinstance(data.get(key), bool):
                raise ValueError(f"{where}: {key} must be an integer")
        self.zone, self.entity = data["zone"], data["entity"]
        who = data.get("as")
        if who is not None and not isinstance(who, str):
            raise ValueError(f"{where}: as must be a name or left blank")
        self.who = who.strip() if who and who.strip() else None
        events = data.get("events")
        if events is not None and (not isinstance(events, list) or not events
                                   or not all(isinstance(e, int) for e in events)):
            raise ValueError(f"{where}: events must be a list of event numbers")
        self.events = set(events) if events else None
        lines = data.get("lines")
        if lines is not None and (not isinstance(lines, dict) or not lines
                                  or not all(isinstance(k, int) and isinstance(v, str) for k, v in lines.items())):
            raise ValueError(f"{where}: lines must map text ids to the line's first words")
        self.lines = lines
        self.note = data.get("note") or ""
        self.where = where
        self.used = 0

    @property
    def specificity(self):
        return (self.lines is not None) * 2 + (self.events is not None)

    def matches(self, row):
        if row.get("zone") != self.zone or row.get("speaker_id") != self.entity:
            return False
        if self.events is not None and row.get("event") not in self.events:
            return False
        return self.lines is None or row.get("text_id") in self.lines

    def check(self, row):
        """The entry's first words against the row's cleaned text."""
        if self.lines is None:
            return
        says = self.lines[row["text_id"]].strip()
        texts = row.get("variants") or []
        if says and texts and not any(t.startswith(says) for t in texts):
            raise ValueError(f"{self.where}: text {row['text_id']} in zone {self.zone} no longer starts with "
                             f"{says!r}; it is {texts[0]!r}")

    def __str__(self):
        narrow = (f" lines {sorted(self.lines)}" if self.lines else "") + \
                 (f" events {sorted(self.events)}" if self.events else "")
        return f"zone {self.zone} entity {self.entity:#x}{narrow} as {self.who or '(blank)'}"


class CastAs:
    def __init__(self, entries, source="cast_as"):
        if not isinstance(entries, list):
            raise ValueError(f"{source}: expected a list of entries")
        self.entries = [Entry(e, f"{source} entry {i + 1}") for i, e in enumerate(entries)]
        self.spoke, self.visited, self.texts = set(), set(), set()   # what the rows seen so far covered

    @classmethod
    def load(cls, path=DEFAULT):
        import yaml  # the venv has it; the helper itself stays stdlib
        path = Path(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(data if data is not None else [], source=path.name)

    def who(self, row):
        """The character to voice `row` as, or None: the row is not in the
        table, or its entry is still blank."""
        self.spoke.add((row.get("zone"), row.get("speaker_id")))
        self.visited.add((row.get("zone"), row.get("event")))
        self.texts.add((row.get("zone"), row.get("text_id")))
        found = [e for e in self.entries if e.matches(row)]
        if not found:
            return None
        found.sort(key=lambda e: -e.specificity)
        if len(found) > 1 and found[0].specificity == found[1].specificity:
            raise ValueError(f"{found[0].where} and {found[1].where} both cover zone {row['zone']} "
                             f"event {row['event']} text {row['text_id']}")
        entry = found[0]
        entry.check(row)
        entry.used += 1
        return entry.who

    def names(self):
        return sorted({e.who for e in self.entries if e.who})

    def unused(self):
        """Entries the rows reached but that matched nothing: a whole-entity
        entry whose entity spoke, an `events` entry whose event was visited,
        a `lines` entry whose text id came up. One table covers every
        storyline, so an entry a build never came near is not reported."""
        def reached(e):
            if e.lines is not None:
                return any((e.zone, t) in self.texts for t in e.lines)
            if e.events is not None:
                return any((e.zone, ev) in self.visited for ev in e.events)
            return (e.zone, e.entity) in self.spoke
        return [e for e in self.entries if not e.used and reached(e)]
