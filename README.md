# Spoken for FFXI

> **Work in progress. Not stable, not ready to use.** Nothing here is
> finished; most of it is scaffolding and notes. Expect it to break, change
> shape, or do nothing at all. There is no release.

A [Windower 4](https://www.windower.net/) addon for Final Fantasy XI that
reads NPC dialogue aloud with AI-generated voices, inspired by the
[Spoken](https://www.curseforge.com/wow/addons/spoken) addon for World of
Warcraft by rustykey ([source](https://github.com/rusty-key/spoken-wow),
[site](https://spoken.quest/)). Spoken gives every WoW quest giver a voice
matched to who they are. The aim here is the same for Vana'diel: when an NPC
talks, you hear them.

## How it is meant to work

FFXI dialogue is static text from the game's data files, and the chat log
receives every line with the speaker's name. The addon captures those lines
and hands them to a helper process on the host, which generates or fetches
the audio and plays it. Controller input is observed natively so the reader
knows when you have moved on. The design is in
[docs/Voice-Handoff.md](docs/Voice-Handoff.md) and the addon-to-helper
contract in [docs/Protocol.md](docs/Protocol.md).

Two parts:

- **The addon** (`Spoken.lua`) runs in Windower. It cleans each dialogue line
  (Shift-JIS, colour codes, prompt markers, element symbols) and appends it
  to a queue file. It can auto-advance a prompted line when the helper says
  the audio has finished.
- **The helper** (`helper/spoken_helper.py`) runs on the Linux host, watches
  the queue, synthesises speech with a local engine (Piper first; espeak-ng
  as a last resort so the pipeline can be tested without downloading a
  voice), caches it, plays it, and reports back.

Why a helper rather than doing it all in Lua: Windower's Lua can make HTTPS
calls but they block the game thread, it can play a WAV but cannot stop one,
and the Wine prefix is a plain host directory, so a file queue is the
simplest bridge.

## Status

| Piece | State |
| --- | --- |
| Dialogue capture and cleanup | drafted, tested outside the game |
| Queue file protocol | drafted |
| Host helper: watch, synthesise, play, stop | drafted, untested against the game |
| Auto-advance via Windower `setkey` | unverified under Wine |
| Voice casting per NPC | not started |
| Pre-generated voice packs | not started |

## Related

- [Balloon](https://github.com/delphyne/Balloon) (our fork): the on-screen
  dialogue balloon this grew out of. Its native gamepad module is how a
  controller advance becomes visible to an addon.
- [Spoken for WoW](https://github.com/rusty-key/spoken-wow): the inspiration,
  including how it casts voices and ships pre-generated packs.
- [Lyre](https://www.curseforge.com/wow/addons/lyre): the live-generation
  alternative for WoW, closer to the helper design used here.

## Development

```
make install        # symlink this checkout into Windower's addons folder
make check          # luacheck plus the Lua and Python tests
make helper         # run the host helper against ./data
```

In game: `//lua load spoken`, then `//spoken help`.

Windower is found automatically under the Lumoria flatpak; see the Makefile
for overriding that. `CLAUDE.md` has the layout and conventions.
