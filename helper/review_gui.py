"""A window for the lexicon review (review.py, the default when a display
and Tk are available). Tk comes with the venv's Python; the system python3
may lack it, so tkinter is imported only when the window opens.

Left, a table of every word: the incoming proper nouns (data/proper_nouns
.tsv) and every lexicon entry, with its status (new, lexicon, skipped,
deleted, or covered by a longer entry), uses, storylines, speakers, and IPA.
A search box matches any part of a word; filters narrow it by status,
storyline, and speaker (data/word_stories.json, from make voice-stories);
a column header sorts by that column, again to reverse.

Right, the word in hand: the engine's reading, the lexicon's, an IPA field
to edit, a check box per engine for the entry's `backends:` limit (all
checked is no limit; the Interjection button keeps it from moss and qwen),
a button for each of the terminal loop's commands (which keep their keys),
and a palette of every IPA glyph the lexicon accepts, each with a word that
shows its sound. Accept writes the IPA and the limit (a new entry, or the
existing one in place), skip leaves the word, d deletes an entry; each then
moves to the next row of the table as filtered and sorted. Up and Down
move through the table without choosing anything; Back returns to the
word looked at before, even one the filters now hide, and a choice made
on a hidden word still moves on to the row after where it stood.

Keys outside the IPA field and the search: a accept, r repeat, e example,
s skip, b back, d delete, q quit, j jump to the IPA field, Up and Down the
table. In the field: Enter tries what is typed, Escape goes back to the
keys. Clicking a glyph inserts it at the cursor. The field shows ᵻ as a
blue ɨ (IpaField): no font the venv's Tk can see draws ᵻ at that size.

A key acts once per press (KeyGate): X autorepeat turns a held key into
tens of presses a second, and since accept moves to the next row, a key
held for 25 seconds once accepted 423 rows. Keys are also ignored for the
first second after the window appears, which is when it takes the focus
from whatever was being typed into while the engine warmed up.
"""

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import respell  # noqa: E402

# (glyph, a word with that sound). espeak-style IPA as Kokoro and Piper take
# it; ɡ is U+0261, not the ASCII letter. Grouped as the palette shows them.
PALETTE = [
    ("Vowels", [
        ("iː", "see"), ("ɪ", "bit"), ("i", "happy"), ("ɛ", "bed"), ("æ", "cat"),
        ("ɑː", "father"), ("ɑ", "hot (US)"), ("ɒ", "hot (GB)"), ("ɔː", "law"), ("ɔ", "north"),
        ("ʊ", "put"), ("uː", "too"), ("u", "influence"), ("ʌ", "cup"), ("ə", "about"),
        ("ɚ", "letter (US)"), ("ɜː", "bird"), ("ɐ", "comma (final a)"), ("ᵻ", "roses"),
    ]),
    ("Diphthongs", [
        ("eɪ", "face"), ("aɪ", "price"), ("ɔɪ", "choice"), ("oʊ", "goat (US)"), ("əʊ", "goat (GB)"),
        ("aʊ", "mouth"), ("ɪə", "near"), ("eə", "square"), ("ʊə", "cure"),
    ]),
    ("Consonants", [
        ("p", "pen"), ("b", "bad"), ("t", "tea"), ("d", "did"), ("k", "cat"), ("ɡ", "get"),
        ("f", "fall"), ("v", "van"), ("θ", "thin"), ("ð", "this"), ("s", "see"), ("z", "zoo"),
        ("ʃ", "ship"), ("ʒ", "measure"), ("h", "hat"), ("m", "man"), ("n", "no"), ("ŋ", "sing"),
        ("l", "leg"), ("ɹ", "red"), ("j", "yes"), ("w", "wet"), ("tʃ", "church"), ("dʒ", "judge"),
        ("ɾ", "butter (US flap)"), ("ʔ", "uh-oh"), ("x", "loch"),
    ]),
    ("Marks", [
        (respell.PRIMARY, "stress (before vowel)"), (respell.SECONDARY, "weak stress"),
        (respell.LENGTH, "long"), (respell.SYLLABLE, "syllable break"), (" ", "space"),
    ]),
]

# How a mark looks on its button: alone, a stress tick is a sliver, and the
# X core Liberation Sans drops the dotted circle at button sizes.
SHOWN = {respell.PRIMARY: "ˈa", respell.SECONDARY: "ˌa", respell.LENGTH: "aː", respell.SYLLABLE: "a.a", " ": "_"}
COLUMNS = 12

KEYS = {"a": "accept", "r": "repeat", "e": "example", "s": "skip", "b": "back", "q": "quit", "j": "field",
        "d": "delete"}


class KeyGate:
    """Which presses of a command key to act on. A press acts only when the
    key has been released since its last press and `GAP` seconds have gone
    by, which lets through anything a hand does and stops autorepeat (a
    press every 30–40 ms, each after a release): the window defers the
    release, so a repeat already queued still sees the key as held, and one
    that is not comes too soon. `arm` ignores presses for `SETTLE` seconds,
    for the window's arrival. `clock` is injectable for the tests."""

    GAP = 0.08
    SETTLE = 1.0

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.held = set()
        self.last = {}
        self.until = float("-inf")

    def arm(self):
        self.until = self.clock() + self.SETTLE

    def press(self, key):
        now = self.clock()
        since = now - self.last.get(key, float("-inf"))
        self.last[key] = now   # from the last press seen, so a steady stream never gets through
        if now < self.until or key in self.held or since < self.GAP:
            return False
        self.held.add(key)
        return True

    def release(self, key):
        self.held.discard(key)


def next_visible(context, shown, rows, step):
    """The row to move to from `shown`, `step` rows along `rows` (the table
    as filtered and sorted now). A word the filters hide, after a choice or
    coming back to it, is placed by `context`, the table as it stood when
    the word was opened: the nearest word in `rows` on that side of it.
    None at either end."""
    if shown in rows:
        i = rows.index(shown) + step
        return rows[i] if 0 <= i < len(rows) else None
    if shown not in context:
        return (rows[0] if step > 0 else rows[-1]) if rows else None
    i = context.index(shown) + step
    while 0 <= i < len(context):
        if context[i] in rows:
            return context[i]
        i += step
    return None


def available():
    """True when a window can open: a display, and tkinter importable."""
    import os
    import sys
    if sys.platform != "darwin" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False   # macOS always has a display for Tk
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


# storyline code -> name, in release order (tools/voice_stories.py has the same table)
STORY_NAMES = {
    "sandoria": "San d'Oria", "bastok": "Bastok", "windurst": "Windurst",
    "rotz": "Rise of the Zilart", "cop": "Chains of Promathia", "toau": "Treasures of Aht Urhgan",
    "wotg": "Wings of the Goddess", "acp": "A Crystalline Prophecy", "amk": "A Moogle Kupo d'Etat",
    "asa": "A Shantotto Ascension", "soa": "Seekers of Adoulin", "rov": "Rhapsodies of Vana'diel",
    "tvr": "The Voracious Resurgence",
}
STORY_ORDER = {c: i for i, c in enumerate(STORY_NAMES)}


STATUSES = ("new", "lexicon", "skipped", "deleted", "covered")


def load_word_stories(path):
    """data/word_stories.json, or {} when it has not been built."""
    p = Path(path) if path else None
    return json.loads(p.read_text(encoding="utf-8")) if p and p.exists() else {}


def catalogue(ses, word_stories):
    """The table's rows: the incoming words in their ranked order, then the
    lexicon entries that are not among them, in file order. A multi-word
    entry (Cait Sith) takes its storylines and speakers from its rarest
    word."""
    import spoken_helper as sh
    import review
    entries = sh.Lexicon.load(ses.lexicon_path).entries
    covered = review.covered_words(entries)
    words = list(ses.by_word) + [w for w in entries if w not in ses.by_word]
    rows = []
    for w in words:
        info = word_stories.get(w)
        if info is None and " " in w:
            parts = [word_stories[p] for p in w.split() if p in word_stories]
            info = min(parts, key=lambda e: sum(e["stories"].values())) if parts else None
        info = info or {"stories": {}, "speakers": {}}
        c = ses.by_word.get(w, {})
        e = entries.get(w)
        uses = c.get("uses")
        rows.append({"word": w, "status": ses.status_of(w, entries, covered),
                     "uses": int(uses) if str(uses or "").isdigit() else None,
                     "stories": info["stories"], "speakers": info["speakers"],
                     "ipa": (e["ipa_marked"] if e and e["ipa_marked"] else (c.get("kokoro_ipa") or "")),
                     "for": " ".join(sorted(e["backends"], key=backend_rank)) if e and e["backends"] else ""})
    return rows


def backend_rank(name):
    import review
    return (review.BACKEND_ORDER.index(name) if name in review.BACKEND_ORDER else 99, name)


def filter_rows(rows, query="", status="all", story="all", speaker="all"):
    q = query.strip().lower()
    return [r for r in rows
            if (not q or q in r["word"].lower())
            and (status == "all" or r["status"] == status)
            and (story == "all" or story in r["stories"])
            and (speaker == "all" or speaker in r["speakers"])]


def sort_key(col, r):
    if col == "#0":
        return (0, r["word"].lower())
    if col == "uses":
        return (0 if r["uses"] is not None else 1, r["uses"] or 0)
    if col == "stories":
        return (0, len(r["stories"]), " ".join(sorted(r["stories"])))
    if col == "speakers":
        top = next(iter(r["speakers"]), "")
        return (0 if top else 1, top.lower())
    if col == "status":
        return (0, STATUSES.index(r["status"]))
    return (0 if r[col] else 1, str(r[col]).lower())


def sort_rows(rows, col, descending=False):
    """Sorted by `col`, blanks last whichever way it runs."""
    keyed = [(sort_key(col, r), r) for r in rows]
    filled = sorted((kr for kr in keyed if kr[0][0] == 0), key=lambda kr: kr[0][1:], reverse=descending)
    blank = sorted((kr for kr in keyed if kr[0][0] != 0), key=lambda kr: kr[0][1:])
    return [r for _, r in filled + blank]


STAND_IN = {"ᵻ": "ɨ"}   # glyphs no font Tk can see draws at size -> what the field shows instead


class IpaField:
    """A one-line IPA edit field that draws every glyph the palette has.

    The venv's Tk sees only the X server's core fonts (fixed and the
    Liberation families). A shared font that lacks one glyph caches the
    fallback it found and then draws other glyphs blank (ᵻ blanked ŋ after
    it), so, like the palette buttons, every character gets a font object
    of its own. ᵻ has no scalable font at all, so it is shown as a blue ɨ,
    which Liberation draws and which looks nearly the same; a black ɨ is a
    real ɨ, which Kokoro also knows. `var` holds the IPA as it really is.
    """

    def __init__(self, parent, var, family, size, colour="#1a5fb4"):
        import tkinter as tk
        from tkinter import font
        self.tk, self.font, self.var = tk, font, var
        self.family, self.size, self.colour = family, size, colour
        self.text = tk.Text(parent, height=1, width=24, wrap="none", undo=True,
                            font=font.Font(family=family, size=size), padx=4, pady=2)
        self.fonts = {}
        self.syncing = False
        self.text.bind("<KeyPress>", self.on_key)
        self.text.bind("<<Modified>>", self.on_modified)
        self.text.bind("<<Paste>>", self.on_paste)
        var.trace_add("write", lambda *_: self.render())

    # -- the widget, for the window ----------------------------------

    def pack(self, **kw):
        self.text.pack(**kw)

    def bind(self, sequence, fn):
        self.text.bind(sequence, lambda e: (fn(e), "break")[1])

    def focus_set(self):
        self.text.focus_set()

    def has_focus(self):
        return self.text.focus_get() is self.text

    def icursor(self, where):
        self.text.mark_set("insert", "end-1c" if where == "end" else where)

    def insert(self, index, chars):
        """Insert real IPA at `index` (tk.INSERT), shown as the field shows it."""
        for ch in chars:
            self.text.insert(index, STAND_IN.get(ch, ch), self.tags_for(ch))
        self.sync()

    # -- display ------------------------------------------------------

    def tags_for(self, ch):
        """The tags a real character is shown with: a font object of its
        own, and for a stand-in also the stand-in tag that colours it."""
        shown = STAND_IN.get(ch, ch)
        key = f"g{ord(shown)}" + ("s" if ch in STAND_IN else "")
        if key not in self.fonts:
            self.fonts[key] = self.font.Font(family=self.family, size=self.size)
            self.text.tag_configure(key, font=self.fonts[key],
                                    foreground=self.colour if ch in STAND_IN else "")
        return (key, "standin") if ch in STAND_IN else (key,)

    def value(self):
        """The IPA as it is: stand-ins read back as what they stand for."""
        back = {v: k for k, v in STAND_IN.items()}
        out, index = [], "1.0"
        for ch in self.text.get("1.0", "end-1c"):
            out.append(back[ch] if ch in back and "standin" in self.text.tag_names(index) else ch)
            index = self.text.index(f"{index}+1c")
        return "".join(out)

    def render(self):
        """Redraw from `var` (set by code), keeping the cursor's offset."""
        if self.syncing:
            return
        cursor = len(self.text.get("1.0", "insert"))
        self.text.delete("1.0", "end")
        for ch in self.var.get():
            self.text.insert("end-1c", STAND_IN.get(ch, ch), self.tags_for(ch))
        self.text.mark_set("insert", f"1.0+{min(cursor, len(self.var.get()))}c")
        self.text.edit_modified(False)

    def sync(self):
        """Push the field's value into `var` without redrawing it."""
        self.syncing = True
        try:
            if self.var.get() != self.value():
                self.var.set(self.value())
        finally:
            self.syncing = False

    # -- typing -------------------------------------------------------

    def on_key(self, event):
        """Printable keys go in with their own tags, never a neighbour's,
        so a real ɨ typed beside a stand-in stays a real ɨ."""
        if event.char and event.char.isprintable() and not (event.state & 0x4):   # not with Ctrl
            if self.text.tag_ranges("sel"):
                self.text.delete("sel.first", "sel.last")
            self.insert("insert", event.char)
            self.text.see("insert")
            return "break"
        return None

    def on_paste(self, event):
        try:
            chars = self.text.clipboard_get()
        except self.tk.TclError:
            return "break"
        if self.text.tag_ranges("sel"):
            self.text.delete("sel.first", "sel.last")
        self.insert("insert", " ".join(chars.split()))   # one line
        return "break"

    def on_modified(self, event=None):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self.sync()


class Window:
    def __init__(self, session, word_stories=None):
        import tkinter as tk
        from tkinter import ttk, font
        self.tk, self.ttk, self.ses = tk, ttk, session
        self.audio = threading.Lock()  # one playback at a time
        self.word_stories = word_stories or {}
        root = self.root = tk.Tk()
        root.title("Spoken lexicon review")
        try:
            ttk.Style().theme_use("clam")
        except tk.TclError:
            pass
        # The venv's Tk has no Xft, so it sees only the X server's core fonts
        # and defaults to the bitmap "fixed", which ignores the desktop's
        # scale; Liberation Sans is scalable there and covers IPA. Tk's
        # scaling comes from Xft.dpi (KDE at 2.5x sets 240), so point sizes
        # follow the desktop and pixel paddings are scaled by hand with px().
        scale = float(root.tk.call("tk", "scaling")) / (96 / 72)
        px = self.px = lambda n: round(n * scale)
        root.minsize(px(900), px(560))
        base = font.nametofont("TkDefaultFont")
        size = 10
        families = {f.lower(): f for f in font.families()}
        family = next((families[f] for f in ("dejavu sans", "noto sans", "liberation sans") if f in families),
                      base.cget("family"))
        for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
            font.nametofont(name).configure(family=family, size=size)
        self.f_word = font.Font(family=family, size=size + 8, weight="bold")
        self.f_ipa = font.Font(family=family, size=size + 4)
        # Each glyph button gets its own font object: Tk caches fallback
        # fonts per font object, and once one glyph the font lacks has made it
        # search (ᵻ above 13pt, the dotted circle), a later θ draws blank.
        self.glyph_font = lambda: font.Font(family=family, size=size + 2)
        self.f_small = font.Font(family=family, size=max(size - 2, 7))
        ttk.Style().configure("Treeview", rowheight=font.nametofont("TkDefaultFont").metrics("linespace") + px(6))
        root.option_add("*TCombobox*Listbox.font", "TkDefaultFont")
        root.columnconfigure(0, weight=2)
        root.columnconfigure(1, weight=3)
        root.rowconfigure(0, weight=1)

        # -- left: the table ------------------------------------------
        left = ttk.Frame(root, padding=(px(10), px(10), px(4), px(10)))
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)
        bar = ttk.Frame(left)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(bar, text="Search").pack(side="left")
        self.v_query = tk.StringVar()
        self.search = ttk.Entry(bar, textvariable=self.v_query, width=18)
        self.search.pack(side="left", padx=(px(4), px(10)))
        self.v_query.trace_add("write", lambda *_: self.refresh())
        self.v_count = tk.StringVar()
        ttk.Label(bar, textvariable=self.v_count).pack(side="right")
        filters = ttk.Frame(left)
        filters.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(px(6), px(6)))
        self.rows = catalogue(session, self.word_stories)
        stories = {}
        speakers = {}
        for r in self.rows:
            stories.update({c: None for c in r["stories"]})
            for s, n in r["speakers"].items():
                speakers[s] = speakers.get(s, 0) + n
        self.filters = {}
        self.add_filter(filters, "status", [("all", "all statuses")] + [(s, s) for s in STATUSES], 12)
        self.add_filter(filters, "story", [("all", "all storylines")] + [(c, STORY_NAMES.get(c, c)) for c in
                                                                         sorted(stories, key=lambda c: STORY_ORDER.get(c, 99))], 22)
        speaker_choices = [("all", "all speakers")] + [(s, s or "(narration)") for s, _ in
                                                       sorted(speakers.items(), key=lambda kv: -kv[1])]
        self.add_filter(filters, "speaker", speaker_choices, 18, typing=True)
        cols = ("status", "uses", "stories", "speakers", "ipa", "for")
        tree = self.tree = ttk.Treeview(left, columns=cols, selectmode="browse")
        self.sort = None
        self.columns = ("#0",) + cols
        tree.heading("#0", text="word", command=lambda: self.sort_by("#0"))
        tree.column("#0", width=px(150))
        for col, width in zip(cols, (75, 55, 120, 140, 140, 90)):
            tree.heading(col, text=col, command=lambda c=col: self.sort_by(c))
            tree.column(col, width=px(width), anchor="e" if col == "uses" else "w")
        scroll = ttk.Scrollbar(left, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.grid(row=2, column=0, sticky="nsew")
        scroll.grid(row=2, column=1, sticky="ns")
        tree.bind("<<TreeviewSelect>>", lambda e: self.select())
        if not self.word_stories:
            ttk.Label(left, text="no data/word_stories.json: run make voice-stories for storylines and speakers",
                      foreground="#a33").grid(row=3, column=0, sticky="w", pady=(px(4), 0))

        # -- right: the word in hand ----------------------------------
        main = ttk.Frame(root, padding=(px(8), px(10), px(12), px(10)))
        main.grid(row=0, column=1, sticky="nsew")
        self.v_word = tk.StringVar()
        self.v_detail = tk.StringVar()
        self.v_example = tk.StringVar()
        self.v_engine = tk.StringVar()
        self.v_saved = tk.StringVar()
        self.v_ipa = tk.StringVar()
        self.v_respell = tk.StringVar()
        self.v_status = tk.StringVar(value="pick a word in the table")
        ttk.Label(main, textvariable=self.v_word, font=self.f_word).pack(anchor="w")
        ttk.Label(main, textvariable=self.v_detail, wraplength=px(640), justify="left").pack(anchor="w")
        ttk.Label(main, textvariable=self.v_example, wraplength=px(640), foreground="#555").pack(anchor="w", pady=(px(4), px(8)))
        ttk.Label(main, textvariable=self.v_engine, font=self.f_ipa).pack(anchor="w")
        ttk.Label(main, textvariable=self.v_saved, font=self.f_ipa).pack(anchor="w")

        row = ttk.Frame(main)
        row.pack(fill="x", pady=(px(8), px(2)))
        ttk.Label(row, text="yours:").pack(side="left")
        self.entry = IpaField(row, self.v_ipa, family, size + 4)
        self.entry.pack(side="left", padx=px(6), fill="x", expand=True)
        ttk.Button(row, text="Try  ⏎", command=self.try_ipa).pack(side="left")
        ttk.Button(row, text="Reset", command=self.reset_ipa, takefocus=0).pack(side="left", padx=(px(6), 0))
        ttk.Label(main, textvariable=self.v_respell, foreground="#555").pack(anchor="w")
        self.v_ipa.trace_add("write", lambda *_: self.show_respell())

        import review
        limit = ttk.Frame(main)
        limit.pack(fill="x", pady=(px(6), 0))
        ttk.Label(limit, text="for:").pack(side="left")
        self.v_backends = {}
        for name in review.BACKEND_ORDER:
            var = tk.BooleanVar(value=True)
            ttk.Checkbutton(limit, text=name, variable=var, takefocus=0,
                            command=self.set_backends).pack(side="left", padx=(px(6), 0))
            self.v_backends[name] = var
        ttk.Button(limit, text="Interjection", command=self.interjection, takefocus=0).pack(side="left", padx=(px(12), 0))
        ttk.Label(limit, text="(all: every engine)", foreground="#555").pack(side="left", padx=(px(8), 0))

        buttons = ttk.Frame(main)
        buttons.pack(fill="x", pady=px(10))
        self.buttons = {}
        for text, cmd in [("Accept  a", "accept"), ("Repeat  r", "repeat"), ("Example  e", "example"),
                          ("Skip  s", "skip"), ("Back  b", "back"), ("Delete  d", "delete"), ("Quit  q", "quit")]:
            b = ttk.Button(buttons, text=text, command=getattr(self, cmd), takefocus=0)
            b.pack(side="left", padx=(0, px(6)))
            self.buttons[cmd] = b

        pal = ttk.Frame(main)
        pal.pack(fill="both", expand=True)
        groups = dict(PALETTE)
        rows = [[("Vowels", groups["Vowels"])], [("Diphthongs", groups["Diphthongs"])], [("Marks", groups["Marks"])],
                [("Consonants", groups["Consonants"])]]
        for row_groups in rows:
            line = ttk.Frame(pal)
            line.pack(fill="x", pady=(px(3), 0))
            for group, glyphs in row_groups:
                box = ttk.LabelFrame(line, text=group, padding=(px(3), 0))
                box.pack(side="left", fill="y", padx=(0, px(6)))
                for n, (glyph, example) in enumerate(glyphs):
                    cell = ttk.Frame(box)
                    cell.grid(row=n // COLUMNS * 2, column=n % COLUMNS, padx=px(1), pady=(px(1), 0))
                    tk.Button(cell, text=SHOWN.get(glyph, glyph), font=self.glyph_font(), width=3, padx=px(2), pady=0,
                              takefocus=0, command=lambda g=glyph: self.insert(g)).pack()
                    ttk.Label(cell, text=example, font=self.f_small).pack()
        ttk.Label(main, textvariable=self.v_status, foreground="#555", wraplength=px(640)).pack(anchor="w", pady=(px(8), 0))

        width = min(px(1500), int(root.winfo_screenwidth() * 0.95))
        height = min(px(820), int(root.winfo_screenheight() * 0.9))
        root.geometry(f"{width}x{height}")
        self.gate = KeyGate()
        self.history = []   # words looked at, for Back
        self.context = []   # the table as it stood when the shown word was opened
        self.play_gen = 0
        root.bind("<Key>", self.on_key)
        root.bind("<Up>", lambda e: self.arrow(-1))
        root.bind("<Down>", lambda e: self.arrow(1))
        root.bind("<KeyRelease>", lambda e: root.after_idle(self.gate.release, e.keysym))
        root.bind("<Map>", lambda e: self.gate.arm() if e.widget is root else None)
        self.entry.bind("<Return>", lambda e: self.try_ipa())
        self.entry.bind("<Escape>", lambda e: root.focus_set())
        self.search.bind("<Escape>", lambda e: root.focus_set())
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.shown = None
        self.refresh()

    def add_filter(self, parent, name, choices, width, typing=False):
        """A labelled drop-down; `typing` lets you type to narrow it."""
        tk, ttk = self.tk, self.ttk
        var = tk.StringVar(value=choices[0][1])
        box = ttk.Combobox(parent, textvariable=var, values=[label for _, label in choices], width=width,
                           state="normal" if typing else "readonly")
        box.pack(side="left", padx=(0, self.px(6)))
        box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        if typing:
            def narrow(event):
                typed = var.get().strip().lower()
                box.configure(values=[label for _, label in choices if typed in label.lower()] or ["all speakers"])
                if event.keysym == "Return":
                    self.refresh()
            box.bind("<KeyRelease>", narrow)
            box.bind("<FocusOut>", lambda e: self.refresh())
        self.filters[name] = (var, box, choices)

    def chosen(self, name):
        var, _, choices = self.filters[name]
        return next((value for value, label in choices if label == var.get()), "all")

    # --- the table ---

    def visible(self):
        rows = filter_rows(self.rows, self.v_query.get(), **{n: self.chosen(n) for n in self.filters})
        if self.sort:
            rows = sort_rows(rows, *self.sort)
        return rows

    def refresh(self, keep=None):
        keep = keep or self.shown
        rows = self.visible()
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            top = list(r["speakers"])[:2]
            speakers = ", ".join(s or "(narration)" for s in top) + ("…" if len(r["speakers"]) > 2 else "")
            self.tree.insert("", "end", iid=r["word"], text=r["word"],
                             values=(r["status"], r["uses"] if r["uses"] is not None else "",
                                     " ".join(sorted(r["stories"])), speakers, r["ipa"], r["for"]))
        self.v_count.set(f"{len(rows)} of {len(self.rows)}")
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
            self.tree.see(keep)

    def sort_by(self, col):
        if self.sort and self.sort[0] == col:
            self.sort = (col, not self.sort[1])
        else:
            self.sort = (col, col == "uses")
        for c in self.columns:
            name = "word" if c == "#0" else c
            self.tree.heading(c, text=name + ((" ▼" if self.sort[1] else " ▲") if c == col else ""))
        self.refresh()

    def select(self):
        sel = self.tree.selection()
        if sel and sel[0] != self.shown:
            self.open(sel[0])

    def open(self, word, remember=True):
        self.ses.open_word(word)
        self.shown = word
        if remember and (not self.history or self.history[-1] != word):
            self.history.append(word)
        rows = [r["word"] for r in self.visible()]
        if word in rows:
            self.context = rows
        if self.tree.exists(word):
            self.tree.selection_set(word)
            self.tree.see(word)
        else:
            self.tree.selection_remove(*self.tree.selection())   # hidden by the filters: no row claims it
        self.show()

    def move(self, step):
        """To the next (or previous) row of the table as it stands, from
        the shown word even when the filters now hide it; False at the
        end. Looking is not choosing: nothing is recorded."""
        target = next_visible(self.context, self.shown, [r["word"] for r in self.visible()], step)
        if target is None:
            return False
        self.open(target)
        return True

    def arrow(self, step):
        """Up and Down from the keys: the table's own when it has the focus,
        else move; typing in the fields is left alone."""
        focus = self.root.focus_get()
        if focus is self.tree or self.entry.has_focus() or focus is self.search \
                or any(focus is box for _, box, _ in self.filters.values()):
            return None
        if not self.move(step):
            self.status("this is the first row" if step < 0 else "that was the last row")
        return "break"

    def update_row(self, word):
        """Recompute one word's row after it changed (status, IPA)."""
        fresh = {r["word"]: r for r in catalogue(self.ses, self.word_stories)}
        self.rows = [fresh.get(r["word"], r) for r in self.rows]
        self.refresh(keep=word)

    # --- the word in hand ---

    def show(self):
        ses = self.ses
        if ses.mode != "word" or ses.cand is None:
            for v in (self.v_word, self.v_detail, self.v_example, self.v_engine, self.v_saved, self.v_ipa):
                v.set("")
            return
        status = next((r["status"] for r in self.rows if r["word"] == ses.word), "")
        self.v_word.set(ses.word)
        info = self.word_stories.get(ses.word) or {}
        stories = ", ".join(f"{STORY_NAMES.get(c, c)} {n}" for c, n in info.get("stories", {}).items())
        who = ", ".join(f"{s or '(narration)'} {n}" for s, n in list(info.get("speakers", {}).items())[:6])
        detail = f"{status}. " + (ses.detail() if "uses" in ses.cand else "a lexicon entry not among the incoming words")
        self.v_detail.set(detail + (f"\nStorylines: {stories}" if stories else "") + (f"\nSpoken by: {who}" if who else ""))
        self.v_example.set(f"“{ses.example}”" if ses.example else "(no example line)")
        self.v_engine.set(f"engine: {ses.engine_ipa}   ({respell.respell(ses.engine_ipa)})" if ses.engine_ipa
                          else "engine: (no reading)")
        if ses.in_entry and ses.saved_ipa:
            self.v_saved.set(f"in the lexicon: {ses.saved_ipa}   ({respell.respell(ses.saved_ipa)})")
        elif ses.in_entry:
            self.v_saved.set(f"in the lexicon: the respelling {ses.saved_spelling()!r}; accept turns it into IPA")
        else:
            self.v_saved.set("not in the lexicon")
        if ses.in_entry and ses.saved_backends:
            self.v_saved.set(self.v_saved.get() + "   for " + ", ".join(sorted(ses.saved_backends, key=backend_rank)))
        self.v_ipa.set(ses.current)
        self.entry.icursor("end")
        for name, var in self.v_backends.items():
            var.set(ses.backends is None or name in ses.backends)
        self.buttons["delete"].state(["!disabled"] if ses.in_entry else ["disabled"])
        if self.root.focus_get() not in (self.search,):
            self.root.focus_set()
        self.play(ses.play)

    def show_respell(self):
        typed = self.v_ipa.get().strip()
        try:
            self.v_respell.set(respell.respell(typed) if typed else "")
        except Exception:  # noqa: BLE001  partial input while typing
            self.v_respell.set("")

    def status(self, text):
        self.v_status.set(text)

    # --- commands ---

    def ready(self):
        return self.ses.mode == "word" and self.ses.cand is not None

    def after(self, message):
        """Refresh the row, then go to the next one."""
        word = self.ses.word
        self.update_row(word)
        if not self.move(1):
            self.open(word)
            message += "; that was the last row"
        self.status(message)

    def accept(self):
        if not self.ready():
            return
        ses = self.ses
        word, ipa, existed = ses.word, ses.current, ses.in_entry
        changed = ipa != ses.saved_ipa or ses.backends != ses.saved_backends
        limit = f"  for {', '.join(sorted(ses.backends, key=backend_rank))}" if ses.backends else ""
        ses.accept()
        self.after(f"{'updated' if existed and changed else 'kept' if existed else 'added'} {word}: {ipa}{limit}")

    def set_backends(self):
        """The check boxes into the session; all checked is no limit."""
        if not self.ready():
            return
        self.ses.set_backends([n for n, v in self.v_backends.items() if v.get()])
        self.status("for every engine" if self.ses.backends is None
                    else "for " + ", ".join(sorted(self.ses.backends, key=backend_rank)) + "; accept writes it")

    def interjection(self):
        """The limit for a laugh or a grunt: the engines that read it as a
        word; moss and qwen say it right unaided."""
        for name, var in self.v_backends.items():
            var.set(name in ("kokoro", "piper", "espeak"))
        self.set_backends()

    def skip(self):
        if not self.ready():
            return
        word, existed = self.ses.word, self.ses.in_entry
        self.ses.skip()
        self.after(f"left {word} as it is" if existed else f"skipped {word}; the engine's reading stands")

    def delete(self):
        if not self.ready() or not self.ses.in_entry:
            return
        word = self.ses.word
        self.ses.delete()
        self.after(f"deleted {word}; the engine's reading stands")

    def back(self):
        """To the word looked at before this one, hidden by the filters or
        not; nothing is undone."""
        while self.history and self.history[-1] == self.shown:
            self.history.pop()
        if not self.history:
            self.history.append(self.shown)   # the first word stays the floor
            self.status("nothing to go back to")
            return
        self.open(self.history[-1], remember=False)   # it stays on top: Back again goes further

    def repeat(self):
        if self.ready():
            self.play(self.ses.play)

    def example(self):
        if not self.ready():
            return
        if not self.ses.example:
            self.status("no example line for this word")
            return
        self.play(self.ses.play_example)

    def try_ipa(self):
        if not self.ready():
            return
        typed = self.v_ipa.get().strip()
        if not typed:
            return
        try:
            current = self.ses.set_ipa(typed)
        except ValueError as e:
            self.status(str(e))
            return
        self.v_ipa.set(current)
        self.status(f"trying {current}  ({respell.respell(current)})")
        self.play(self.ses.play)

    def reset_ipa(self):
        """Back to the lexicon's IPA for an entry, else the engine's reading."""
        if self.ready():
            self.v_ipa.set(self.ses.saved_ipa or self.ses.engine_ipa)
            self.entry.icursor("end")
            self.entry.focus_set()

    def insert(self, glyph):
        """At the cursor when the field is being edited, else at the end."""
        if not self.entry.has_focus():
            self.entry.icursor("end")
            self.entry.focus_set()
        self.entry.insert(self.tk.INSERT, glyph)

    def quit(self):
        self.root.destroy()

    def on_key(self, event):
        focus = self.root.focus_get()
        if self.entry.has_focus() or focus is self.search or any(focus is box for _, box, _ in self.filters.values()):
            return None
        cmd = KEYS.get(event.char.lower()) if event.char else None
        if cmd and not self.gate.press(event.keysym):
            return "break"   # autorepeat, or the window has just appeared
        if cmd == "field":
            self.entry.focus_set()
            self.entry.icursor("end")
        elif cmd:
            getattr(self, cmd)()
        return "break" if cmd else None

    # --- audio off the Tk thread ---

    def play(self, fn):
        self.play_gen += 1
        gen = self.play_gen

        def work():
            with self.audio:
                if gen != self.play_gen:   # a newer word is in hand; skip this one
                    return
                self.root.after(0, self.status, "playing…")
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    self.root.after(0, self.status, f"playback failed: {e}")
                else:
                    self.root.after(0, self.status, "")
        threading.Thread(target=work, daemon=True).start()

    def run(self, entries=None):
        """Start at `entries` (a word) or on the lexicon (True), else on the
        first new word."""
        if isinstance(entries, str):
            target = entries
        else:
            if entries:
                self.filters["status"][0].set("lexicon")
                self.refresh()
            target = next((r["word"] for r in self.visible() if entries or r["status"] == "new"), None)
        if target:
            self.open(target)
        self.root.mainloop()
        self.ses.close_entries() if self.ses.mode == "entries" else None
        print(f"{self.ses.covered} words covered this run.")
        return self.ses.covered


def run(session, entries=None, word_stories=None):
    return Window(session, word_stories).run(entries)
