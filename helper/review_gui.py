"""A window for the lexicon review (review.py --gui, the default when a
display and Tk are available). One word at a time; a button for each of
the terminal loop's commands, which keep their keys; and, for building a
replacement, a palette of every IPA glyph the lexicon accepts, each with
a word that shows its sound. Tk comes with the venv's Python; the system
python3 may lack it, so tkinter is imported only when the window opens.

Keys outside the IPA field: a accept, r repeat, e example, s skip, b back
to the previous word (undoing its accept or skip), q quit, j jump to the
IPA field. In the field: Enter tries what is typed, Escape
goes back to the keys. Clicking a glyph inserts it at the cursor.
"""

import sys
import threading
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
COLUMNS = 14

KEYS = {"a": "accept", "r": "repeat", "e": "example", "s": "skip", "b": "back", "q": "quit", "j": "field"}


def available():
    """True when a window can open: a display, and tkinter importable."""
    import os
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


class Window:
    def __init__(self, session):
        import tkinter as tk
        from tkinter import ttk, font
        self.tk, self.ses = tk, session
        self.audio = threading.Lock()  # one playback at a time
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
        root.minsize(px(720), px(560))
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

        main = ttk.Frame(root, padding=px(12))
        main.pack(fill="both", expand=True)
        self.v_count = tk.StringVar()
        self.v_word = tk.StringVar()
        self.v_detail = tk.StringVar()
        self.v_example = tk.StringVar()
        self.v_engine = tk.StringVar()
        self.v_ipa = tk.StringVar()
        self.v_respell = tk.StringVar()
        self.v_status = tk.StringVar(value=session.intro())

        head = ttk.Frame(main)
        head.pack(fill="x")
        ttk.Label(head, textvariable=self.v_word, font=self.f_word).pack(side="left")
        ttk.Label(head, textvariable=self.v_count).pack(side="right")
        ttk.Label(main, textvariable=self.v_detail).pack(anchor="w")
        ttk.Label(main, textvariable=self.v_example, wraplength=px(680), foreground="#555").pack(anchor="w", pady=(px(4), px(8)))
        ttk.Label(main, textvariable=self.v_engine, font=self.f_ipa).pack(anchor="w")

        row = ttk.Frame(main)
        row.pack(fill="x", pady=(px(8), px(2)))
        ttk.Label(row, text="yours:").pack(side="left")
        self.entry = ttk.Entry(row, textvariable=self.v_ipa, font=self.f_ipa, width=28)
        self.entry.pack(side="left", padx=px(6), fill="x", expand=True)
        ttk.Button(row, text="Try  ⏎", command=self.try_ipa).pack(side="left")
        ttk.Button(row, text="Reset", command=self.reset_ipa, takefocus=0).pack(side="left", padx=(px(6), 0))
        ttk.Label(main, textvariable=self.v_respell, foreground="#555").pack(anchor="w")
        self.v_ipa.trace_add("write", lambda *_: self.show_respell())

        buttons = ttk.Frame(main)
        buttons.pack(fill="x", pady=px(10))
        for text, cmd in [("Accept  a", self.accept), ("Repeat  r", self.repeat), ("Example  e", self.example),
                          ("Skip  s", self.skip), ("Back  b", self.back), ("Quit  q", self.quit)]:
            ttk.Button(buttons, text=text, command=cmd, takefocus=0).pack(side="left", padx=(0, px(6)))

        pal = ttk.Frame(main)
        pal.pack(fill="both", expand=True)
        groups = dict(PALETTE)
        rows = [[("Vowels", groups["Vowels"])], [("Diphthongs", groups["Diphthongs"]), ("Marks", groups["Marks"])],
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
        ttk.Label(main, textvariable=self.v_status, foreground="#555").pack(anchor="w", pady=(px(8), 0))

        root.bind("<Key>", self.on_key)
        self.entry.bind("<Return>", lambda e: self.try_ipa())
        self.entry.bind("<Escape>", lambda e: root.focus_set())
        root.protocol("WM_DELETE_WINDOW", self.quit)

    # --- words ---

    def show(self):
        ses = self.ses
        if ses.done:
            self.v_word.set("done")
            self.v_count.set("")
            self.v_detail.set(ses.farewell())
            self.v_example.set("")
            self.v_engine.set("")
            self.v_ipa.set("")
            self.entry.state(["disabled"])
            return
        self.entry.state(["!disabled"])
        self.v_count.set(f"{ses.pos + 1} / {len(ses.todo)}")
        self.v_word.set(ses.word)
        self.v_detail.set(ses.detail())
        self.v_example.set(f"“{ses.example}”" if ses.example else "(no example line)")
        self.v_engine.set(f"engine: {ses.engine_ipa}   ({respell.respell(ses.engine_ipa)})")
        self.v_ipa.set(ses.current)
        self.entry.icursor("end")
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

    def accept(self):
        if self.ses.done:
            return
        word, ipa = self.ses.word, self.ses.current
        self.ses.accept()
        self.status(f"added {word}: {ipa}")
        self.show()

    def skip(self):
        if self.ses.done:
            return
        word = self.ses.word
        self.ses.skip()
        self.status(f"skipped {word}; the engine's reading stands")
        self.show()

    def back(self):
        if not self.ses.back():
            self.status("nothing before this word")
            return
        self.status(f"back to {self.ses.word}; its earlier choice is undone")
        self.show()

    def repeat(self):
        if not self.ses.done:
            self.play(self.ses.play)

    def example(self):
        if self.ses.done:
            return
        if not self.ses.example:
            self.status("no example line for this word")
            return
        self.play(self.ses.play_example)

    def try_ipa(self):
        if self.ses.done:
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
        if not self.ses.done:
            self.v_ipa.set(self.ses.engine_ipa)
            self.entry.icursor("end")
            self.entry.focus_set()

    def insert(self, glyph):
        """At the cursor when the field is being edited, else at the end."""
        if self.root.focus_get() is not self.entry:
            self.entry.icursor("end")
            self.entry.focus_set()
        self.entry.insert(self.tk.INSERT, glyph)

    def quit(self):
        self.root.destroy()

    def on_key(self, event):
        if self.root.focus_get() is self.entry:
            return None
        cmd = KEYS.get(event.char.lower()) if event.char else None
        if cmd == "field":
            self.entry.focus_set()
            self.entry.icursor("end")
        elif cmd:
            getattr(self, cmd)()
        return "break" if cmd else None

    # --- audio off the Tk thread ---

    def play(self, fn):
        def work():
            with self.audio:
                self.root.after(0, self.status, "playing…")
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    self.root.after(0, self.status, f"playback failed: {e}")
                else:
                    self.root.after(0, self.status, "")
        threading.Thread(target=work, daemon=True).start()

    def run(self):
        self.ses.advance()
        self.show()
        self.root.mainloop()
        print(self.ses.farewell())
        return self.ses.covered


def run(session):
    return Window(session).run()
