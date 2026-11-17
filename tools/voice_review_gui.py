"""The voice review's window (tools/voice_review.py). Tk, imported only when
the window opens, like the lexicon review's (helper/review_gui.py).

Left: a search box (any part of a name or voice), filters for race,
gender, storyline, and kind, and the matching speakers and pool voices,
most-spoken first. Right: the selected voice's description to edit, and

    Play saved      the clip on file, if any
    Audition        design a take from the description as typed
    Regenerate      the same description with a new seed
    Play take       the latest take again
    Accept          keep the take: clip, index, and voice_designs.yaml
    Reset           drop the take and the edits

A speaker on a shared pool voice gets a "Give ... a voice of their own"
button, which starts a new design from the pool voice's description.
Designing runs off the Tk thread; the first take loads Qwen3, which takes a
while. Ctrl+Enter auditions, Ctrl+P plays the saved clip, Ctrl+T the take.
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
        root.geometry("1280x760")
        root.columnconfigure(0, weight=3)
        root.columnconfigure(1, weight=2)
        root.rowconfigure(1, weight=1)

        bar = ttk.Frame(root, padding=6)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(bar, text="Search").pack(side="left")
        self.query = tk.StringVar()
        search = ttk.Entry(bar, textvariable=self.query, width=24)
        search.pack(side="left", padx=(4, 12))
        self.query.trace_add("write", lambda *_: self.refresh())
        self.filters = {}
        for name, width in (("race", 12), ("gender", 10), ("story", 26), ("kind", 11)):
            var = tk.StringVar()
            box = ttk.Combobox(bar, textvariable=var, state="readonly", width=width)
            box.pack(side="left", padx=4)
            box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
            self.filters[name] = (var, box)
        self.count = ttk.Label(bar, text="")
        self.count.pack(side="left", padx=12)
        self.fill_filters()

        left = ttk.Frame(root, padding=(6, 0, 3, 6))
        left.grid(row=1, column=0, sticky="nsew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        cols = ("voice", "race", "gender", "lines", "stories", "state")
        tree = self.tree = ttk.Treeview(left, columns=cols, selectmode="browse")
        tree.heading("#0", text="speaker")
        tree.column("#0", width=200)
        for col, width in zip(cols, (120, 80, 70, 60, 200, 80)):
            tree.heading(col, text=col)
            tree.column(col, width=width, anchor="e" if col == "lines" else "w")
        scroll = ttk.Scrollbar(left, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        tree.bind("<<TreeviewSelect>>", lambda e: self.select())

        right = ttk.Frame(root, padding=(3, 0, 6, 6))
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        self.title = ttk.Label(right, text="Select a speaker or a voice", font=("TkDefaultFont", 14, "bold"))
        self.title.grid(row=0, column=0, sticky="w")
        self.info = ttk.Label(right, text="", wraplength=480, justify="left")
        self.info.grid(row=1, column=0, sticky="w", pady=(2, 6))
        self.own = ttk.Button(right, text="", command=self.give_own)
        self.own.grid(row=2, column=0, sticky="w")
        self.own.grid_remove()
        ttk.Label(right, text="Description").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.text = tk.Text(right, wrap="word", height=7, font=("TkDefaultFont", 11), undo=True)
        self.text.grid(row=4, column=0, sticky="ew")
        self.seed = ttk.Label(right, text="")
        self.seed.grid(row=5, column=0, sticky="w", pady=(2, 8))
        buttons = ttk.Frame(right)
        buttons.grid(row=6, column=0, sticky="w")
        self.buttons = {}
        for name, label in (("play_saved", "Play saved"), ("audition", "Audition"), ("regenerate", "Regenerate"),
                            ("play_take", "Play take"), ("accept", "Accept"), ("reset", "Reset")):
            b = ttk.Button(buttons, text=label, command=getattr(self, name))
            b.pack(side="left", padx=(0, 6))
            self.buttons[name] = b
        self.calibration = ttk.Label(right, text="", wraplength=480, justify="left", foreground="#666")
        self.calibration.grid(row=7, column=0, sticky="w", pady=(10, 0))
        self.status_line = ttk.Label(root, text="", padding=6, anchor="w")
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
        rows = self.ses.filter(self.query.get(), **{n: self.chosen(n) for n in self.filters})
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            st = "take" if r["voice"] in self.ses.drafts else ("" if self.ses.clip(r["voice"]) else "no clip")
            self.tree.insert("", "end", iid=r["key"], text=r["label"],
                             values=(r["voice"], r["race"], r["gender"], r["lines"] or "",
                                     " ".join(sorted(r["stories"])), st))
        self.count.configure(text=f"{len(rows)} of {len(self.ses.rows)}")
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
            self.tree.see(keep)

    def select(self):
        sel = self.tree.selection()
        if not sel:
            return
        self.row = self.ses.row(sel[0])
        self.voice = self.row["voice"]
        self.show()

    def show(self):
        st = self.ses.state(self.voice)
        r = self.row
        self.title.configure(text=r["label"])
        if r["kind"] == "pools" or st["kind"] == "pool":
            shared = st["users"]
            who = ", ".join(shared[:8]) + (f" and {len(shared) - 8} more" if len(shared) > 8 else "")
            info = f"Pool voice {self.voice} ({st['group']}), shared by {len(shared)}: {who or 'nobody yet'}."
        else:
            info = f"Own voice {self.voice}" + (" (new, not accepted yet)" if st["new"] else "") + "."
        stories = ", ".join(f"{self.ses.story_names.get(c, c)} {n}" for c, n in sorted(r["stories"].items()))
        self.info.configure(text=f"{info}\n{r['race']}, {r['gender']}" + (f". Lines: {stories}" if stories else ""))
        if r["name"] is not None and st["kind"] == "pool":
            self.own.configure(text=f"Give {r['label']} a voice of their own")
            self.own.grid()
        else:
            self.own.grid_remove()
        self.text.delete("1.0", "end")
        self.text.insert("1.0", st["description"])
        changed = st["description"] != st["saved_description"] or st["seed"] != st["saved_seed"]
        self.seed.configure(text=f"seed {st['seed']}" + (f" (saved: {st['saved_seed']})" if changed else "")
                            + ("" if st["clip"] else "  ·  no clip yet") + ("  ·  take ready" if st["candidate"] else ""))
        self.calibration.configure(text=f"Every clip says: {st['calibration']}")
        self.enable()

    # -- actions ------------------------------------------------------

    def description(self):
        return self.text.get("1.0", "end").strip()

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
        self.design(lambda: self.ses.audition(self.voice, self.description()))

    def regenerate(self):
        self.design(lambda: self.ses.regenerate(self.voice, self.description()))

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
        if key:
            self.refresh(keep=key)
            if self.row and self.row["key"] == key:
                self.show()
        if take:
            self.play(take)
        self.enable()

    def accept(self):
        try:
            clip = self.ses.accept(self.voice)
        except (ValueError, OSError) as e:
            return self.status(str(e))
        self.after_change(f"saved {clip.name} and voice_designs.yaml")

    def reset(self):
        self.ses.reset(self.voice)
        self.after_change(f"{self.voice} reset to what is saved")

    def after_change(self, message):
        key = self.row["key"]
        self.fill_filters()
        self.refresh(keep=key)
        if self.tree.exists(key):
            self.row = self.ses.row(key)
            self.voice = self.row["voice"]
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
        st = self.ses.state(self.voice) if self.voice else None
        can = {
            "play_saved": bool(st and st["clip"]),
            "audition": bool(st) and not self.busy, "regenerate": bool(st) and not self.busy,
            "play_take": bool(st and st["candidate"]),
            "accept": bool(st and st["candidate"]) and not self.busy,
            "reset": bool(st and st["pending"]) and not self.busy,
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
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def run(session):
    Window(session).run()
