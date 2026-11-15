# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Spoken is a Windower4 addon for Final Fantasy XI that reads NPC dialogue aloud with generated voices, inspired by the WoW addon of the same name (https://github.com/rusty-key/spoken-wow). It is a work in progress and not usable yet; `README.md` says so and must keep saying so until there is a release. `docs/Voice-Handoff.md` is the design and research record; `docs/Protocol.md` is the addon-to-helper file contract.

It is a sibling of `../Balloon` (the on-screen dialogue balloon) and shares its scaffolding. Keep the Makefile, `.luacheckrc`, and test harness in step with `../Balloon`, `../ahbuddy`, and `../randommount`.

## Install

Windower runs under the Lumoria flatpak at `~/.var/app/net.windower.Lumoria/data/prefixes/prefix-1/pfx/drive_c/Windower4/`. This repo is symlinked into its `addons/` folder so edits are live after a reload:

```
make install        # find Windower and symlink this repo
make where          # print the folder it would use
```

In game: `//lua load spoken` the first time, `//lua reload spoken` after editing.

## Lint and tests

```
make check                     # luacheck, the Lua tests, and the Python tests
luajit test/test_Spoken.lua    # Lua tests alone; Windower is stubbed
python3 -I -m unittest discover -s helper -p 'test_*.py'
```

Windower's Lua accepts `"fmt":format(x)`; luacheck and luajit do not, so write `("fmt"):format(x)`. `.luacheckrc` whitelists the luau globals; add to it rather than using inline `-- luacheck:` comments.

The Lua harness stubs `luau`, `config`, `chat.chars`, and `windower`, then `dofile`s `Spoken.lua` and drives the `incoming text`, `incoming chunk`, `addon command`, and `prerender` handlers. File writes go through `io`, which the harness redirects into an in-memory table so tests can read the queue the addon wrote.

## Architecture

- **`Spoken.lua`** owns the event handlers and settings. `incoming text` filters by chat mode (150 NPC dialogue with prompt, 144 promptless NPC text, 151 system narration when enabled), runs the line through `text.clean`, and appends a queue entry. `incoming chunk` writes a stop entry on NPC release (`0x52`) and zoning (`0x0B`). `prerender` polls the helper's status file a few times a second and, when auto-advance is on and the helper reports the current prompted line finished, sends Enter through Windower's `setkey`.
- **`text.lua`** is pure: `text.clean(raw)` returns `speaker, line, prompted`. It strips prompt and auto-prompt bytes, colour codes, line breaks, and turns FFXI's private glyph codes (element symbols, music notes, stars) into words or nothing. It mirrors the substitution order in Balloon: raw Shift-JIS byte substitutions first, then `windower.from_shift_jis`, then Unicode-level cleanup.
- **`defaults.lua`** holds user settings; Windower persists them to `data/settings.xml` (gitignored).
- **`helper/spoken_helper.py`** is the host side, stdlib only. It tails `data/queue.jsonl`, synthesises each line through a backend (`piper`, `espeak`, or `none` for dry runs), caches WAVs by a hash of speaker and text, plays them with `paplay`/`ffplay`/`aplay`, kills playback on a stop entry or a newer line, and writes `data/status.json`.

## Conventions

- `data/` is gitignored and shared with the helper: queue, status, and settings all live there.
- Do not add anything to the addon that blocks the game thread (no synchronous HTTP, no sleeping in handlers). Anything slow belongs in the helper.
- Bump `_addon.version` in `Spoken.lua` and note user-visible changes in `README.md`'s status table while there is no changelog.
- Keep FFXI byte-level knowledge (prompt bytes, element symbols, private Shift-JIS codes) in `text.lua`, with a test for each code.
