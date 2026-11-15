# Addon to helper protocol

Draft. Everything here may change.

The addon runs inside the game under Wine; the helper runs on the Linux
host. The Wine prefix is an ordinary host directory and the addon folder is
a symlink to this checkout, so both sides read and write `data/` in this
repo. No sockets.

## `data/queue.jsonl` (addon writes, helper reads)

One JSON object per line, appended. The helper tails the file and keeps its
own offset; if the file shrinks it starts over from the top. On start-up it
skips whatever is already in the file, so old dialogue is never replayed.

Speak entry:

```json
{"seq": 12, "speaker": "Iroha", "text": "Welcome, traveler.", "prompted": true, "mode": 150, "t": 1763200000}
```

- `seq` increases by one per entry for the life of the addon.
- `speaker` is the NPC name as shown in the log, or `""` for narration.
- `text` is cleaned UTF-8 with no control bytes.
- `prompted` is true when the game is waiting for the player to continue.
- `mode` is the chat mode the line arrived in.
- `t` is `os.time()` when the line arrived.

Stop entry:

```json
{"seq": 13, "stop": true, "reason": "release"}
```

`reason` is `release` (NPC release packet), `zone`, `command` (`//spoken
stop`), or `advance` (the player moved on, when that is observable).

A speak entry also implies "stop whatever is playing": only one line is ever
current.

## `data/status.json` (helper writes, addon reads)

Rewritten whole, atomically (write to a temp file, rename).

```json
{"seq": 12, "state": "speaking", "backend": "piper", "cached": true, "t": 1763200001.5}
```

`state` is one of:

- `idle`: nothing queued.
- `generating`: synthesising `seq`.
- `speaking`: playing `seq`.
- `done`: `seq` finished playing without being stopped.
- `error`: `seq` could not be spoken; `message` says why.

The addon polls this a few times a second. When auto-advance is on and it
sees `done` for the `seq` of the line currently on screen and that line was
prompted, it advances once.

## Pacing rules

- A new speak entry stops the current line even if it has not finished.
- A stop entry stops playback and sets `state` to `idle`.
- The helper never advances the game itself; only the addon sends input.
