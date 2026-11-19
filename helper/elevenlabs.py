#!/usr/bin/env python3
"""The ElevenLabs client (docs/ElevenLabs-Handoff.md): where the API key
lives, the requests, and an account check that spends nothing.

    make elevenlabs-key      store the key in the desktop keyring
    make elevenlabs-check    plan, characters left, voice slots, models
    python3 -I helper/elevenlabs.py --help

Standard library only, so it runs under the system Python and the venvs.

The key is looked for in ELEVENLABS_API_KEY, then the keyring: the login
Keychain on macOS (`security`), the Secret Service on Linux
(`secret-tool`), under service "ffxi-spoken-elevenlabs", account "api-key".
It is sent only in the xi-api-key header. It is never a command argument,
never logged, and `redact` takes it out of any error text; storing it is
left to the keyring's own prompt, so it never passes through this process.

`Client` takes a transport, `(method, url, headers, body, timeout) ->
(status, headers, bytes)`, so the tests run without a network. A 429 or a
5xx is retried with a growing, jittered wait; anything else that is not a
2xx raises `ApiError` with the service's own message.
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.elevenlabs.io"
SERVICE = "ffxi-spoken-elevenlabs"
ACCOUNT = "api-key"
ENV = "ELEVENLABS_API_KEY"
VOICE_PREFIX = "ffxi/"      # a voice named ffxi/<voice key> is one of ours
RETRIES = 5
USER_AGENT = "spoken-pack-builder"


class KeyMissing(RuntimeError):
    pass


class ApiError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(f"ElevenLabs answered {status}: {message}")
        self.status = status


def redact(text, key):
    """`text` with the key taken out, for anything a person might read."""
    text = str(text)
    return text.replace(key, "[api key]") if key else text


def keyring_commands(platform=sys.platform):
    """(read, store) command lines for this platform's keyring, or None.
    Each prompts for, or prints, the secret itself; the key is in neither."""
    if platform == "darwin":
        return (["security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"],
                ["security", "add-generic-password", "-U", "-s", SERVICE, "-a", ACCOUNT, "-w"])
    if platform.startswith("linux"):
        return (["secret-tool", "lookup", "service", SERVICE, "account", ACCOUNT],
                ["secret-tool", "store", "--label=ffxi-spoken ElevenLabs", "service", SERVICE, "account", ACCOUNT])
    return None


def api_key(env=None, run=subprocess.run, platform=sys.platform, which=shutil.which):
    """The API key: the environment's, else the keyring's."""
    env = os.environ if env is None else env
    key = (env.get(ENV) or "").strip()
    if key:
        return key
    commands = keyring_commands(platform)
    if commands and which(commands[0][0]):
        done = run(commands[0], capture_output=True, text=True)
        key = (done.stdout or "").strip() if done.returncode == 0 else ""
        if key:
            return key
    raise KeyMissing(f"no ElevenLabs API key: run make elevenlabs-key, or set {ENV}")


def store_key(run=subprocess.run, platform=sys.platform, which=shutil.which):
    """Hand over to the keyring's own prompt, which reads the key without
    echo; returns its exit status."""
    commands = keyring_commands(platform)
    if not commands or not which(commands[1][0]):
        raise KeyMissing(f"no keyring tool on this system (security or secret-tool); set {ENV} instead")
    print(f"Storing the ElevenLabs API key in the keyring as {SERVICE} / {ACCOUNT}.\n"
          "Paste the key at the prompt; it is not shown.", flush=True)
    return run(commands[1]).returncode


def urllib_transport(method, url, headers, body, timeout):
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status, dict(reply.headers.items()), reply.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read()


def error_message(body):
    """What the service says went wrong: `detail` is a string, or a mapping
    with a message; else the body's first line."""
    text = body.decode("utf-8", "replace")
    try:
        detail = json.loads(text).get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("status")
    return str(detail or text.strip().splitlines()[0] if text.strip() else "no message")[:300]


class Client:
    def __init__(self, key, transport=urllib_transport, base=BASE, sleep=time.sleep, rng=None, timeout=60):
        if not key:
            raise KeyMissing("no ElevenLabs API key")
        self.key, self.transport, self.base = key, transport, base.rstrip("/")
        self.sleep, self.rng, self.timeout = sleep, rng or random.Random(), timeout

    def request(self, method, path, params=None, body=None):
        """One call; returns (headers, bytes). Rate limits and server
        errors are retried RETRIES times, waiting 1, 2, 4... seconds (or
        what Retry-After says) plus up to a second of jitter."""
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        headers = {"xi-api-key": self.key, "Accept": "application/json", "User-Agent": USER_AGENT}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        for attempt in range(RETRIES + 1):
            try:
                status, reply_headers, payload = self.transport(method, url, headers, data, self.timeout)
            except OSError as e:
                raise ApiError("no reply", redact(e, self.key)) from None
            if 200 <= status < 300:
                return reply_headers, payload
            if (status == 429 or status >= 500) and attempt < RETRIES:
                self.sleep(self.wait(attempt, reply_headers))
                continue
            raise ApiError(status, redact(error_message(payload), self.key))

    def wait(self, attempt, headers):
        asked = {k.lower(): v for k, v in headers.items()}.get("retry-after", "")
        base = float(asked) if asked.replace(".", "", 1).isdigit() else 2.0 ** attempt
        return min(base, 60.0) + self.rng.random()

    def json(self, method, path, params=None, body=None):
        return json.loads(self.request(method, path, params, body)[1].decode("utf-8"))

    # -- reads that cost nothing ------------------------------------------

    def subscription(self):
        return self.json("GET", "/v1/user/subscription")

    def models(self):
        return self.json("GET", "/v1/models")

    def voices(self, page_size=100):
        """Every voice in the account, following the pages."""
        out, token = [], None
        while True:
            page = self.json("GET", "/v2/voices", {"page_size": page_size, "next_page_token": token})
            out += page.get("voices") or []
            token = page.get("next_page_token")
            if not page.get("has_more") or not token:
                return out


def check(client, now=None):
    """The account as the pack work needs to see it: lines of a report.
    Reads only; nothing here is billed. A read the key has no permission
    for is reported in its place, so one report says everything a
    restricted key still lacks."""
    lines = []

    def read(label, call):
        try:
            return call()
        except ApiError as e:
            lines.append(f"{label:15} not read: {e}")
            return None

    sub = read("plan", client.subscription)
    if sub is not None:
        used, limit = sub.get("character_count") or 0, sub.get("character_limit") or 0
        lines += [f"plan            {sub.get('tier', '?')} ({sub.get('status', '?')})",
                  f"characters      {used:,} of {limit:,} used; {max(limit - used, 0):,} left"]
        reset = sub.get("next_character_count_reset_unix")
        if reset:
            when = datetime.fromtimestamp(reset, timezone.utc)
            days = (when - (now or datetime.now(timezone.utc))).days
            lines.append(f"resets          {when:%Y-%m-%d} ({days} days)")
        slots = sub.get("voice_slots_used")
        lines.append(f"voice slots     {'?' if slots is None else slots} of {sub.get('voice_limit', '?')} used")
    voices = read("voices", client.voices)
    if voices is not None:
        ours = sorted(v.get("name", "") for v in voices if (v.get("name") or "").startswith(VOICE_PREFIX))
        kinds = {}
        for v in voices:
            kinds[v.get("category") or "?"] = kinds.get(v.get("category") or "?", 0) + 1
        lines.append(f"voices          {len(voices)} in the account"
                     + (": " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) if kinds else ""))
        lines.append(f"{VOICE_PREFIX} voices    {len(ours)}"
                     + (": " + ", ".join(ours[:12]) + (f" and {len(ours) - 12} more" if len(ours) > 12 else "")
                        if ours else ""))
    models = read("speech models", client.models)
    if models is not None:
        speakers = [m for m in models if m.get("can_do_text_to_speech")]
        lines.append("speech models   " + ", ".join(m.get("model_id", "?") for m in speakers))
        for m in speakers:
            rate = (m.get("model_rates") or {}).get("character_cost_multiplier")
            lines.append(f"  {m.get('model_id', '?'):28} {m.get('maximum_text_length_per_request', '?')} "
                         "characters a request" + (f", cost x{rate}" if rate is not None else ""))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("key", "check"),
                    help="key: store the API key in the keyring; check: read the account, spending nothing")
    args = ap.parse_args(argv)
    try:
        if args.command == "key":
            return store_key()
        print("\n".join(check(Client(api_key()))))
    except (KeyMissing, ApiError) as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
