"""FFXI dialogue text, the Python side (docs/Event-Decoding-Handoff.md, step 5).

Two jobs, both on raw Shift-JIS bytes:

- `clean(raw)` is `text.lua`'s `text.clean` ported step for step, so a
  line taken from the client's dialogue DAT cleans to the same string the
  addon writes to its queue when the chat log shows that line. That
  equality is what lets a pack file be found by speaker plus text.
  Substitution order mirrors text.lua and Balloon: prompt bytes, element
  symbols and private Shift-JIS codes while still bytes, then decode, then
  colour codes, line breaks, other control bytes, and whitespace.

- `expand(raw)` turns a DAT entry, which still holds the client's parameter
  codes, into every rendering the chat log can show. `0x0C n [a/b]` and
  `0x7F 0x85 [his/her]` (gender) and `0x7F 0x92 n [has/have]` (plural)
  become one variant per alternative; `0x08` (the player's name) becomes
  PLAYER so the addon can put the same token back before hashing; numbers
  (`0x0A n`) and item or key item names (`0x01 len bytes`) cannot be
  enumerated and make the entry live-only.

Codes come from the client's `0x24` menu handler and from reading the DATs
(see the handoff). `variants(raw)` chains the two: cleaned text per
rendering, or none when the entry is live-only.
"""

import re
from itertools import product

PLAYER = "<player>"

# 0x7F 0x31..0x33, 0x37: the game waits for the player; 0x7F 0x34..0x36 s: continues after s seconds
PROMPT = re.compile(rb"\x7f[\x31-\x33\x37]")
PROMPT_END = re.compile(rb"\x7f[\x31-\x33\x37]$")
AUTO_PROMPT = re.compile(rb"\x7f[\x34-\x36].", re.S)
# the "Name : " prefix of a chat-log line; Lua's `^.- : ` with the match ending before column 32
SPEAKER = re.compile(rb"^(.*?) : ", re.S)

ELEMENTS = {0x1F: "Fire", 0x20: "Ice", 0x21: "Wind", 0x22: "Earth",
            0x23: "Lightning", 0x24: "Water", 0x25: "Light", 0x26: "Dark"}

# Balloon's SubCharactersPreShift; Windower's chat/chars has no names for
# the note, sharp, flat and stars, so these are text.lua's fallbacks too.
SJIS_SUBS = [
    (b"\x81\x40", b" "),    # ideographic space / tab
    (b"\x81\xf4", b""),     # standard music note
    (b"\x81\xed", b""),     # the game's music note
    (b"\x81\xeb", b""),     # sharp
    (b"\x81\xec", b""),     # flat
    (b"\x81\x99", b""),     # black star
    (b"\x81\x9a", b""),     # white star
    (b"\x81\x60", b"~"),    # wave dash
    (b"\x87\xb2", b'"'),    # centred left double quote
    (b"\x87\xb3", b'"'),    # centred right double quote
    (b"\x88\x69", b"\xf0\x40"),  # the game's é: unassigned in cp932, so park it in the user area
]
# ... and turn the parked code into é once decoded (0xF040 is U+E000 in
# cp932). text.lua inserts UTF-8 "é" into the Shift-JIS string instead,
# which Windower's converter may or may not pass through: check in game.
UNICODE_SUBS = [("\ue000", "é")]

# 0x7F followed by a byte >= 0x80: sizes from the 0x24 menu handler's skip table.
SEVEN_F_3 = {0x80, 0x84, 0x86, 0x8C, 0x92}
SEVEN_F_4 = {0x38}


def _decode(b):
    # Windower's from_shift_jis for the characters dialogue actually uses;
    # cp932 is the codec the client's tables were written against.
    return b.decode("cp932", "replace")


def clean(raw, with_speaker=True):
    """text.clean: returns (speaker, line, prompted) for a Shift-JIS line.

    with_speaker=False skips the "Name : " prefix rule, for DAT entries
    whose speaker comes from the event decoder."""
    if isinstance(raw, str):
        raw = raw.encode("cp932", "replace")
    prompted = PROMPT_END.search(raw) is not None
    line = PROMPT.sub(b"", raw)
    line = AUTO_PROMPT.sub(b"", line)

    speaker = ""
    if with_speaker:
        m = SPEAKER.match(line)
        if m and m.end() < 32:
            speaker = _decode(m.group(1).lstrip(b" \t\n\v\f\r"))
            line = line[m.end():]

    def element(m):
        name = ELEMENTS.get(m.group(1)[0])
        return f" {name} ".encode() if name else b""
    line = re.sub(rb"\xef(.)", element, line, flags=re.S)

    for old, new in SJIS_SUBS:
        line = line.replace(old, new)

    text = _decode(line)
    for old, new in UNICODE_SUBS:
        text = text.replace(old, new)

    text = re.sub("[\x1e\x1f].", "", text, flags=re.S)
    text = text.replace("\x07", " ")
    text = re.sub("[\x00-\x1f\x7f]", "", text)
    text = re.sub("[ \t\n\v\f\r]+", " ", text).strip(" \t\n\v\f\r")
    return speaker, text, prompted


class Expansion:
    """`variants`: every byte rendering of a DAT entry; `live_only`: why it
    cannot be enumerated (empty when it can)."""

    def __init__(self):
        self.variants = [b""]
        self.live_only = []

    def append(self, piece):
        self.variants = [v + piece for v in self.variants]

    def choose(self, alternatives):
        self.variants = [v + a for v, a in product(self.variants, alternatives)]


def _alternatives(raw, i):
    """At raw[i] == b'[': the alternatives up to ']' and the index after."""
    j = raw.find(b"]", i)
    if j < 0:
        return None, i
    return raw[i + 1:j].split(b"/"), j + 1


def expand(raw, max_variants=32):
    out = Expansion()
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c == 0x0C and i + 2 < n and raw[i + 2] == 0x5B:          # 0C n [a/b]: choice n
            alts, i = _alternatives(raw, i + 2)
            if alts is None:
                out.live_only.append("unterminated choice")
                break
            out.choose(alts)
        elif c == 0x7F and i + 1 < n and raw[i + 1] >= 0x80:
            hi = raw[i + 1]
            size = 4 if hi in SEVEN_F_4 else 3 if hi in SEVEN_F_3 else 2
            if i + size < n and raw[i + size] == 0x5B:              # 7F 85 [his/her], 7F 92 n [has/have]
                alts, i = _alternatives(raw, i + size)
                if alts is None:
                    out.live_only.append("unterminated choice")
                    break
                out.choose(alts)
            else:
                out.live_only.append(f"7f{hi:02x}")
                i += size
        elif c == 0x08:                                              # the player's name
            out.append(PLAYER.encode())
            i += 1
        elif c == 0x0A and i + 1 < n:                                # 0A n: number n
            out.live_only.append("number")
            i += 2
        elif c == 0x01 and i + 1 < n:                                # 01 len bytes: item, key item
            out.live_only.append("item")
            i += 2 + raw[i + 1]
        else:
            out.append(raw[i:i + 1])
            i += 1
        if len(out.variants) > max_variants:
            out.live_only.append("too many variants")
            break
    if out.live_only:
        out.variants = []
    return out


def variants(raw):
    """Cleaned text for each rendering of a DAT entry; [] when live-only."""
    seen, out = set(), []
    for v in expand(raw).variants:
        text = clean(v, with_speaker=False)[1]
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
