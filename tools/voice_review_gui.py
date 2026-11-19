"""The voice review's window (tools/voice_review.py). Tk, imported only when
the window opens, like the lexicon review's (helper/review_gui.py).

Left: a search box (any part of a name or voice), filters for race,
gender, storyline, and kind, and a tree of what matches, most-spoken
first: races, under each its pool voices with their speakers and its own
voices (a row shown only because a match sits under it is greyed). The
`via` column says how a speaker got their voice: cast.py, the census,
voices.json, or the default, else their own or a shared own voice. Right:
what the selected row edits, named in the title (a pool voice, even when
reached through one of its speakers; an own voice; a race's phrase), its
meta (gender, age, race) and description, and

    Play saved      the clip on file, if any
    Audition        design a take from the description as typed
    Regenerate      the same description with a new seed
    Play take       the latest take again
    Accept          keep the take: clip, index, and voice_designs.yaml
    Reset           drop the take and the edits

    Show prompt     a window with the exact instruction Qwen will be given,
                    composed from the fields as they stand, kept up to date
                    while it is open, beside what the saved clip was made with

Whatever is typed is kept until it is accepted or reset: moving to another
row and back shows it again, and the tree's state column says "edited",
"take", or "take, edited" (the fields changed after the take was heard, so
it must be auditioned again before Accept). A pool voice's gender and race
are its casting group's and cannot be changed here; its age can. A speaker
on a shared pool voice gets a "Give ... a voice of their own" button, which
starts a new design from the pool voice's. A race row edits only the race's
phrase. A phrase alone cannot be auditioned, so its draft is heard by
auditioning any voice of the race; Accept on the race row saves it with no
take, accepting a take of such a voice saves it too, and Reset on the race
row drops it.
Designing runs off the Tk thread; the first take loads Qwen3, which takes a
while, and a finished take does not move the selection. Ctrl+Enter
auditions, Ctrl+P plays the saved clip, Ctrl+T the take.
"""

import os
import subprocess
import threading


def available():
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


AGE_ORDER = {age: i for i, age in enumerate(__import__("designs").AGES)}


def sort_key(col, r, ses):
    """How a column sorts: lines by number, age from youngest, stories by
    how many, the rest as text without regard to case; blanks last."""
    if col == "#0":
        return (0, r["label"].lower())
    if col == "lines":
        return (0, r["lines"])
    if col == "age":
        return (0 if r["age"] else 1, AGE_ORDER.get(r["age"], len(AGE_ORDER)), r["age"])
    if col == "stories":
        return (0, len(r["stories"]), " ".join(sorted(r["stories"])))
    if col == "state":
        value = "" if not r["voice"] else ("take" if r["voice"] in ses.drafts
                                           else ("" if ses.clip(r["voice"]) else "no clip"))
        return (0 if value else 1, value)
    value = str(r.get(col) or "")
    return (0 if value else 1, value.lower())


class Window:
    def __init__(self, session, player=None):
        import tkinter as tk
        from tkinter import ttk
        import spoken_helper as sh
        self.tk, self.ttk = tk, ttk
        self.ses = session
        self.player = player if player is not None else sh.pick_player()
        self.playing = None
        self.busy = False
        self.voice = None
        self.row = None

        root = self.root = tk.Tk()
        root.title("Spoken: voice review")
        try:
            ttk.Style().theme_use("clam")
        except tk.TclError:
            pass
        # As in the lexicon review (helper/review_gui.py): the venv's Tk has
        # no Xft, so it sees only the X server's core fonts and defaults to
        # the bitmap "fixed", which ignores the desktop's scale. A scalable
        # family at point sizes follows Tk's scaling (from Xft.dpi, 240 under
        # KDE at 2.5x); pixel sizes are scaled by hand with px().
        from tkinter import font
        scale = float(root.tk.call("tk", "scaling")) / (96 / 72)
        px = self.px = lambda n: round(n * scale)
        size = 10
        families = {f.lower(): f for f in font.families()}
        family = next((families[f] for f in ("dejavu sans", "noto sans", "liberation sans") if f in families),
                      font.nametofont("TkDefaultFont").cget("family"))
        for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont", "TkFixedFont"):
            font.nametofont(name).configure(family=family, size=size)
        self.f_title = font.Font(family=family, size=size + 4, weight="bold")
        self.f_text = font.Font(family=family, size=size + 1)
        # a Treeview's rows do not grow with its font
        ttk.Style().configure("Treeview", rowheight=font.nametofont("TkDefaultFont").metrics("linespace") + px(6))
        root.option_add("*TCombobox*Listbox.font", "TkDefaultFont")   # the drop-down lists
        width = min(px(1280), int(root.winfo_screenwidth() * 0.9))
        height = min(px(760), int(root.winfo_screenheight() * 0.85))
        root.geometry(f"{width}x{height}")
        root.minsize(min(px(900), width), min(px(520), height))
        root.columnconfigure(0, weight=3)
        root.columnconfigure(1, weight=2)
        root.rowconfigure(1, weight=1)

        bar = ttk.Frame(root, padding=px(6))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(bar, text="Search").pack(side="left")
        self.query = tk.StringVar()
        search = ttk.Entry(bar, textvariable=self.query, width=24)
        search.pack(side="left", padx=(px(4), px(12)))
        self.query.trace_add("write", lambda *_: self.refresh())
        self.filters = {}
        for name, width in (("race", 12), ("gender", 10), ("story", 26), ("kind", 11)):
            var = tk.StringVar()
            box = ttk.Combobox(bar, textvariable=var, state="readonly", width=width)
            box.pack(side="left", padx=px(4))
            box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
            self.filters[name] = (var, box)
        self.count = ttk.Label(bar, text="")
        self.count.pack(side="left", padx=px(12))
        self.fill_filters()

        left = ttk.Frame(root, padding=(px(6), 0, px(3), px(6)))
        left.grid(row=1, column=0, sticky="nsew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        cols = ("voice", "via", "race", "gender", "age", "lines", "stories", "state")
        tree = self.tree = ttk.Treeview(left, columns=cols, selectmode="browse")
        self.sort = None          # (column, descending), or None for the session's order
        self.columns = ("#0",) + cols
        tree.heading("#0", text="speaker", command=lambda: self.sort_by("#0"))
        tree.column("#0", width=px(220))
        tree.tag_configure("context", foreground="#888")   # a parent shown only for what is under it
        for col, width in zip(cols, (120, 80, 80, 70, 90, 60, 180, 80)):
            tree.heading(col, text=col, command=lambda c=col: self.sort_by(c))
            tree.column(col, width=px(width), anchor="e" if col == "lines" else "w")
        scroll = ttk.Scrollbar(left, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        tree.bind("<<TreeviewSelect>>", lambda e: self.select())

        right = ttk.Frame(root, padding=(px(3), 0, px(6), px(6)))
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        self.title = ttk.Label(right, text="Select a race, a voice, or a speaker", font=self.f_title)
        self.title.grid(row=0, column=0, sticky="w")
        self.info = ttk.Label(right, text="", wraplength=px(480), justify="left")
        self.info.grid(row=1, column=0, sticky="w", pady=(px(2), px(6)))
        self.field_label = ttk.Label(right, text="Description")
        self.own = ttk.Button(right, text="", command=self.give_own)
        self.own.grid(row=2, column=0, sticky="w")
        self.own.grid_remove()
        meta = ttk.Frame(right)
        meta.grid(row=3, column=0, sticky="w", pady=(px(8), 0))
        self.meta = {}
        opts = self.ses.options()
        for col, (name, width, state) in enumerate((("gender", 9, "normal"), ("age", 13, "normal"),
                                                     ("race", 11, "readonly"))):
            ttk.Label(meta, text=name.capitalize()).grid(row=0, column=2 * col, sticky="w",
                                                         padx=(0 if col == 0 else px(12), px(4)))
            var = tk.StringVar()
            box = ttk.Combobox(meta, textvariable=var, values=opts[f"meta_{name}"], width=width, state=state)
            box.grid(row=0, column=2 * col + 1, sticky="w")
            var.trace_add("write", lambda *_: self.meta_modified())
            self.meta[name] = (var, box, state)
        self.field_label.grid(row=4, column=0, sticky="w", pady=(px(8), 0))
        self.text = tk.Text(right, wrap="word", height=7, font=self.f_text, undo=True, padx=px(4), pady=px(4))
        self.text.grid(row=5, column=0, sticky="ew")
        self.text.bind("<<Modified>>", self.text_modified)
        self.seed = ttk.Label(right, text="")
        self.seed.grid(row=6, column=0, sticky="w", pady=(px(2), px(8)))
        buttons = ttk.Frame(right)
        buttons.grid(row=7, column=0, sticky="w")
        self.buttons = {}
        self.prompt_window = None
        rows = ((("play_saved", "Play saved"), ("audition", "Audition"), ("regenerate", "Regenerate"),
                 ("play_take", "Play take")),
                (("accept", "Accept"), ("reset", "Reset"), ("show_prompt", "Show prompt")))
        for r, row in enumerate(rows):
            for c, (name, label) in enumerate(row):
                b = ttk.Button(buttons, text=label, command=getattr(self, name))
                b.grid(row=r, column=c, sticky="ew", padx=(0, px(6)), pady=(0, px(6)))
                self.buttons[name] = b
        self.calibration = ttk.Label(right, text="", wraplength=px(480), justify="left", foreground="#666")
        self.calibration.grid(row=8, column=0, sticky="w", pady=(px(10), 0))
        self.status_line = ttk.Label(root, text="", padding=px(6), anchor="w")
        self.status_line.grid(row=2, column=0, columnspan=2, sticky="ew")

        root.bind("<Control-Return>", lambda e: (self.audition(), "break")[1])
        root.bind("<Control-p>", lambda e: self.play_saved())
        root.bind("<Control-t>", lambda e: self.play_take())
        root.protocol("WM_DELETE_WINDOW", self.quit)
        search.focus_set()
        self.refresh()
        self.enable()

    # -- list ---------------------------------------------------------

    def fill_filters(self):
        self.choices = self.ses.options()
        for name, (var, box) in self.filters.items():
            labels = [label for _, label in self.choices[name]]
            box.configure(values=labels)
            if var.get() not in labels:
                var.set(labels[0])

    def chosen(self, name):
        var, _ = self.filters[name]
        return next((value for value, label in self.choices[name] if label == var.get()), "all")

    def refresh(self, keep=None):
        """The tree: the matching rows under their parents, siblings in the
        catalogue's order or the sorted column's."""
        rows = self.ses.filter(self.query.get(), **{n: self.chosen(n) for n in self.filters})
        matched = {r["key"] for r in rows}
        children = {}
        for r in self.ses.with_parents(rows).values():
            children.setdefault(r["parent"], []).append(r)
        self.tree.delete(*self.tree.get_children())

        def insert(parent):
            kids = children.get(parent, [])
            if self.sort:
                col, descending = self.sort
                kids = sorted(kids, key=lambda r: sort_key(col, r, self.ses), reverse=descending)
            for r in kids:
                self.tree.insert(parent, "end", iid=r["key"], text=r["label"], open=True,
                                 tags=() if r["key"] in matched else ("context",),
                                 values=(r["voice"], r["via"], r["race"], r["gender"], r["age"], r["lines"] or "",
                                         " ".join(sorted(r["stories"])), self.state_of(r)))
                insert(r["key"])
        insert("")
        self.count.configure(text=f"{len(rows)} of {len(self.ses.rows)}")
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
            self.tree.see(keep)

    def state_of(self, r):
        """The tree's state cell: a take or an edit waiting, else whether
        the voice has a clip."""
        voice = r["voice"]
        if not voice:
            return "edited" if r["race"] in self.ses.race_drafts else ""
        draft = self.ses.drafts.get(voice)
        if draft and draft.get("candidate"):
            return "take, edited" if draft.get("typed") else "take"
        if draft:
            return "edited"
        return "" if self.ses.clip(voice) else "no clip"

    def sort_by(self, col):
        """A header click: sort by that column, again to reverse. Lines start
        most first, everything else A to Z."""
        if self.sort and self.sort[0] == col:
            self.sort = (col, not self.sort[1])
        else:
            self.sort = (col, col == "lines")
        for c in self.columns:
            name = "speaker" if c == "#0" else c
            mark = (" ▼" if self.sort[1] else " ▲") if c == col else ""
            self.tree.heading(c, text=name + mark)
        self.refresh(keep=self.row["key"] if self.row else None)

    def select(self):
        sel = self.tree.selection()
        if not sel:
            return
        # refresh() reselects the current row after an audition, an accept,
        # or a filter change, and Tk delivers that selection event later;
        # the row is already shown
        if self.row is not None and sel[0] == self.row["key"]:
            return
        self.row = self.ses.row(sel[0])
        self.voice = self.row["voice"] or None   # a race row has no voice
        self.show()

    def show(self):
        r = self.row
        if r["kind"] == "race":
            return self.show_race()
        st = self.ses.state(self.voice)
        stories = ", ".join(f"{self.ses.story_names.get(c, c)} {n}" for c, n in sorted(r["stories"].items()))
        if st["kind"] == "pool":
            shared = st["users"]
            who = ", ".join(shared[:8]) + (f" and {len(shared) - 8} more" if len(shared) > 8 else "")
            self.title.configure(text=f"Pool voice: {self.ses.pool_name(self.voice)}")
            info = f"{self.voice}, one of group {st['group']}'s voices, shared by {len(shared)}: {who or 'nobody yet'}."
            if r["name"] is not None:
                info = (f"{r['how']}\nEditing here changes every speaker on {self.voice}; "
                        f"to change only {r['label']}, give them a voice of their own.\n" + info)
        else:
            self.title.configure(text=f"Own voice {self.voice}" + (" (new, not accepted yet)" if st["new"] else ""))
            info = r["how"] if r["name"] is not None else ""
        self.info.configure(text=info + (f"\nLines: {stories}" if stories else ""))
        if r["name"] is not None and st["kind"] == "pool":
            self.own.configure(text=f"Give {r['label']} a voice of their own")
            self.own.grid()
        else:
            self.own.grid_remove()
        self.loading = True   # filling the fields is not an edit
        pool = st["kind"] == "pool"
        for name, (var, box, state) in self.meta.items():
            var.set(st[name])
            box.configure(state="disabled" if pool and name in ("gender", "race") else state)
        self.field_label.configure(text="Description")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", st["description"])
        self.text.edit_modified(False)
        self.loading = False
        self.seed_line(st)
        self.update_prompt()
        self.calibration.configure(text=f"Every clip says: {st['calibration']}")
        self.enable()

    def seed_line(self, st=None):
        st = st or self.ses.state(self.voice)
        changed = st["seed"] != st["saved_seed"]
        self.seed.configure(text=f"seed {st['seed']}" + (f" (saved: {st['saved_seed']})" if changed else "")
                            + ("" if st["clip"] else "  ·  no clip yet")
                            + ("  ·  saved clip is from an older design" if st["stale"] else "")
                            + ("  ·  take ready" if st["candidate"] and not st["edited_since_take"] else "")
                            + ("  ·  edited since the take: audition again" if st["edited_since_take"] else "")
                            + ("  ·  edited, not auditioned" if st["pending"] and not st["candidate"]
                               and not st["new"] else "")
                            + (f"  ·  auditions use the unsaved {st['race']} phrase"
                               if st["race"] in self.ses.race_drafts else ""))

    def show_race(self):
        """A race row: its phrase in the text field, the meta blank."""
        st = self.ses.race_state(self.row["race"])
        self.title.configure(text=f"Race {st['race']}")
        voices = st["voices"]
        who = ", ".join(voices[:8]) + (f" and {len(voices) - 8} more" if len(voices) > 8 else "")
        if st["editable"]:
            info = (f"Its phrase is composed into every voice of this race, after gender and age and before "
                    f"the voice's own description: {len(voices)} voices: {who or 'none yet'}.\n"
                    "Blank for none. It cannot be auditioned by itself: a take needs a gender and an age. "
                    "What you type here is kept while you audition any voice of this race, which uses it; "
                    "Accept saves it (as does accepting such a take), Reset drops it. Every clip of the race "
                    "is then from an older design.")
        else:
            info = "Not in the races section, so no phrase: a design's race is unknown."
        self.info.configure(text=info)
        self.own.grid_remove()
        self.loading = True
        for name, (var, box, state) in self.meta.items():
            var.set("")
            box.configure(state="disabled")
        self.field_label.configure(text="Race phrase")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", st["phrase"] or "")
        self.text.edit_modified(False)
        self.loading = False
        self.seed.configure(text="edited, not saved yet" if st["pending"] else "")
        self.update_prompt()
        self.calibration.configure(text="")
        self.enable()

    # -- actions ------------------------------------------------------

    def description(self):
        return self.text.get("1.0", "end").strip()

    def meta_values(self):
        return {name: var.get().strip() for name, (var, _, _) in self.meta.items()}

    def text_modified(self, event=None):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self.edited()

    def meta_modified(self):
        if not getattr(self, "loading", False):
            self.edited()

    def edited(self):
        """A field changed by hand: kept as a draft until saved or reset, so
        moving to another row does not lose it."""
        self.update_prompt()
        if not self.row:
            return
        if self.row["kind"] == "race":
            if self.ses.race_state(self.row["race"])["editable"]:
                self.ses.edit_race(self.row["race"], self.description())
        elif self.voice:
            self.ses.edit_voice(self.voice, self.description(), **self.meta_values())
            self.seed_line()
        if self.tree.exists(self.row["key"]):
            self.tree.set(self.row["key"], "state", self.state_of(self.row))
        self.enable()

    # -- the prompt popout -------------------------------------------

    def show_prompt(self):
        tk, ttk = self.tk, self.ttk
        if self.prompt_window is not None and self.prompt_window.winfo_exists():
            self.prompt_window.lift()
            return self.update_prompt()
        px = self.px
        win = self.prompt_window = tk.Toplevel(self.root)
        win.title("Spoken: the instruction Qwen is given")
        win.geometry(f"{px(720)}x{px(360)}")
        win.columnconfigure(0, weight=1)
        win.rowconfigure(1, weight=1)
        win.rowconfigure(3, weight=1)
        self.prompt_title = ttk.Label(win, text="", padding=(px(8), px(8), px(8), px(2)), font=self.f_title)
        self.prompt_title.grid(row=0, column=0, sticky="w")
        self.prompt_text = tk.Text(win, wrap="word", height=5, font=self.f_text, padx=px(6), pady=px(6))
        self.prompt_text.grid(row=1, column=0, sticky="nsew", padx=px(8))
        self.prompt_saved_label = ttk.Label(win, text="", padding=(px(8), px(8), px(8), px(2)))
        self.prompt_saved_label.grid(row=2, column=0, sticky="w")
        self.prompt_saved = tk.Text(win, wrap="word", height=4, font=self.f_text, padx=px(6), pady=px(6),
                                    foreground="#555")
        self.prompt_saved.grid(row=3, column=0, sticky="nsew", padx=px(8), pady=(0, px(8)))
        ttk.Button(win, text="Copy", command=self.copy_prompt).grid(row=4, column=0, sticky="e",
                                                                     padx=px(8), pady=(0, px(8)))
        self.update_prompt()

    def prompt(self):
        if not self.voice:
            return ""
        return self.ses.compose(self.voice, self.description(), **self.meta_values())

    def update_prompt(self):
        if getattr(self, "loading", False) or self.prompt_window is None or not self.prompt_window.winfo_exists():
            return
        st = self.ses.state(self.voice) if self.voice else None
        self.prompt_title.configure(text=f"{self.row['label']} ({self.voice})" if st else "Nothing selected")
        for widget, text in ((self.prompt_text, self.prompt()),
                             (self.prompt_saved, (st or {}).get("clip_instruct") or "")):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
            widget.configure(state="disabled")
        if not st:
            label = ""
        elif not st["clip"]:
            label = "No saved clip yet."
        elif st["clip_instruct"] == self.prompt():
            label = "The saved clip was made with exactly this."
        else:
            label = "The saved clip was made with:"
        self.prompt_saved_label.configure(text=label)

    def copy_prompt(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.prompt())
        self.status("instruction copied")

    def give_own(self):
        try:
            self.voice = self.ses.give_own_voice(self.row["name"])
        except ValueError as e:
            return self.status(str(e))
        self.row = next(r for r in self.ses.rows if r["key"] == self.row["key"])
        self.refresh(keep=self.row["key"])
        self.show()
        self.status(f"new voice {self.voice}: edit the description, audition, then accept")

    def audition(self):
        description, meta = self.description(), self.meta_values()
        self.design(lambda: self.ses.audition(self.voice, description, **meta))

    def regenerate(self):
        description, meta = self.description(), self.meta_values()
        self.design(lambda: self.ses.regenerate(self.voice, description, **meta))

    def design(self, fn):
        if self.voice is None or self.busy:
            return
        self.busy = True
        self.enable()
        self.status("designing… (the first take loads Qwen3)" if self.ses._engine is None else "designing…")
        voice, key = self.voice, self.row["key"]

        def work():
            try:
                take = fn()
            except Exception as e:  # noqa: BLE001 - shown in the status line
                self.root.after(0, self.done, f"design failed: {e}")
            else:
                self.root.after(0, self.done, f"take for {voice} ready", key, take)
        threading.Thread(target=work, daemon=True).start()

    def done(self, message, key=None, take=None):
        self.busy = False
        self.status(message)
        if key:   # the take's row gets its mark; the row being edited stays selected
            self.refresh(keep=self.row["key"] if self.row else key)
            if self.row and self.row["key"] == key:
                self.show()
        if take:
            self.play(take)
        self.enable()

    def accept(self):
        if self.row and self.row["kind"] == "race":
            return self.accept_race()
        try:
            clip = self.ses.accept(self.voice)
        except (ValueError, OSError) as e:
            return self.status(str(e))
        self.after_change(f"saved {clip.name} and voice_designs.yaml")

    def accept_race(self):
        race = self.row["race"]
        try:
            stale = self.ses.save_race(race, self.description())
        except (ValueError, OSError) as e:
            return self.status(str(e))
        self.after_change(f"saved the {race} phrase to voice_designs.yaml"
                          + (f"; {len(stale)} clips are now from an older design (make design-voices CHANGED=1)"
                             if stale else ""))

    def reset(self):
        if self.row and self.row["kind"] == "race":
            self.ses.reset_race(self.row["race"])
            return self.after_change(f"{self.row['race']} phrase back to what is saved")
        self.ses.reset(self.voice)
        self.after_change(f"{self.voice} reset to what is saved")

    def after_change(self, message):
        key = self.row["key"]
        self.fill_filters()
        self.refresh(keep=key)
        if self.tree.exists(key):
            self.row = self.ses.row(key)
            self.voice = self.row["voice"] or None
            self.show()
        self.status(message)

    def play_saved(self):
        if self.voice and self.ses.clip(self.voice):
            self.play(self.ses.clip(self.voice))

    def play_take(self):
        if self.voice:
            take = self.ses.state(self.voice)["candidate"]
            if take:
                self.play(take)

    def play(self, path):
        self.stop()
        if not self.player:
            return self.status("no audio player found (paplay, ffplay, or aplay)")
        self.playing = subprocess.Popen(self.player + [str(path)], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)

    def stop(self):
        if self.playing and self.playing.poll() is None:
            self.playing.terminate()
        self.playing = None

    def enable(self):
        if self.row and self.row["kind"] == "race":
            st = self.ses.race_state(self.row["race"])
            edited = st["editable"] and (st["pending"] or self.description() != (st["phrase"] or ""))
            can = {"play_saved": False, "audition": False, "regenerate": False, "play_take": False,
                   "accept": edited, "reset": edited, "show_prompt": False}
            for name, b in self.buttons.items():
                b.state(["!disabled"] if can[name] else ["disabled"])
            return
        st = self.ses.state(self.voice) if self.voice else None
        can = {
            "play_saved": bool(st and st["clip"]),
            "audition": bool(st) and not self.busy, "regenerate": bool(st) and not self.busy,
            "play_take": bool(st and st["candidate"]),
            "accept": bool(st and st["candidate"] and not st["edited_since_take"]) and not self.busy,
            "reset": bool(st and st["pending"]) and not self.busy,
            "show_prompt": True,
        }
        for name, b in self.buttons.items():
            b.state(["!disabled"] if can[name] else ["disabled"])

    def status(self, text):
        self.status_line.configure(text=text)

    def quit(self):
        self.stop()
        pending = [v for v, d in self.ses.drafts.items() if d.get("candidate")]
        if pending:
            print("takes not accepted (still in refs/.candidates/): " + ", ".join(sorted(pending)))
        typed = [v for v, d in self.ses.drafts.items() if d.get("typed") or not d.get("candidate")]
        if typed:
            print("voices edited and not auditioned (lost): " + ", ".join(sorted(typed)))
        if self.ses.race_drafts:
            print("race phrases typed and not saved (lost): " + ", ".join(sorted(self.ses.race_drafts)))
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def run(session):
    Window(session).run()
