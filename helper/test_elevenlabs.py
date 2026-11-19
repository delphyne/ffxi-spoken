"""elevenlabs.py without a network or a keyring: where the key comes from,
paging, retries, redaction, the account check, and a scan of the tree for
anything shaped like a key."""

import json
import random
import re
import subprocess
import sys
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import elevenlabs as el  # noqa: E402

KEY = "test-key-not-a-real-one"


class FakeTransport:
    """Replies from a queue per path; records what was asked."""

    def __init__(self, replies):
        self.replies = {path: list(queue) for path, queue in replies.items()}
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        path = url[len(el.BASE):].split("?")[0]
        status, payload, *rest = self.replies[path].pop(0)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return status, (rest[0] if rest else {}), raw


def client(replies, **kw):
    transport = FakeTransport(replies)
    waits = []
    return el.Client(KEY, transport=transport, sleep=waits.append, rng=random.Random(1), **kw), transport, waits


def keyring(stdout="", returncode=0):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(stdout=stdout, returncode=returncode)
    return run, calls


class KeyTest(unittest.TestCase):
    def test_the_environment_wins_and_the_keyring_is_not_asked(self):
        run, calls = keyring("from-keyring\n")
        self.assertEqual(el.api_key({el.ENV: "  from-env "}, run=run), "from-env")
        self.assertEqual(calls, [])

    def test_the_keyring_is_read_per_platform(self):
        for platform, tool in (("darwin", "security"), ("linux", "secret-tool")):
            run, calls = keyring("from-keyring\n")
            self.assertEqual(el.api_key({}, run=run, platform=platform, which=lambda t: "/bin/" + t), "from-keyring")
            self.assertEqual(calls[0][0], tool)
            self.assertIn(el.SERVICE, calls[0])
            self.assertIn(el.ACCOUNT, calls[0])

    def test_no_key_says_how_to_store_one(self):
        cases = [dict(run=keyring("", 44)[0], platform="darwin", which=lambda t: "/bin/" + t),   # not in the keyring
                 dict(run=keyring("x")[0], platform="linux", which=lambda t: None),              # no keyring tool
                 dict(run=keyring("x")[0], platform="win32", which=lambda t: "/bin/" + t)]       # no keyring we know
        for case in cases:
            with self.assertRaisesRegex(el.KeyMissing, "make elevenlabs-key"):
                el.api_key({}, **case)

    def test_storing_hands_over_to_the_keyrings_own_prompt(self):
        for platform in ("darwin", "linux"):
            run, calls = keyring()
            with unittest.mock.patch("builtins.print"):
                self.assertEqual(el.store_key(run=run, platform=platform, which=lambda t: "/bin/" + t), 0)
            self.assertIn(el.SERVICE, calls[0])
            self.assertEqual(calls[0][-1], "-w" if platform == "darwin" else el.ACCOUNT,
                             "the command ends where the tool prompts; the key is never an argument")
        with self.assertRaises(el.KeyMissing):
            el.store_key(run=keyring()[0], platform="win32")


class ClientTest(unittest.TestCase):
    def test_the_key_goes_in_the_header_and_nowhere_else(self):
        c, transport, _ = client({"/v1/user/subscription": [(200, {"tier": "pro"})]})
        self.assertEqual(c.subscription(), {"tier": "pro"})
        method, url, headers, body = transport.calls[0]
        self.assertEqual((method, url, body), ("GET", el.BASE + "/v1/user/subscription", None))
        self.assertEqual(headers["xi-api-key"], KEY)
        self.assertNotIn(KEY, url)
        self.assertEqual(set(headers), {"xi-api-key", "Accept", "User-Agent"}, "nothing about the user goes out")
        self.assertEqual(headers["User-Agent"], el.USER_AGENT)

    def test_voices_follow_the_pages(self):
        c, transport, _ = client({"/v2/voices": [
            (200, {"voices": [{"name": "a"}, {"name": "b"}], "has_more": True, "next_page_token": "t 2"}),
            (200, {"voices": [{"name": "c"}], "has_more": False, "next_page_token": None})]})
        self.assertEqual([v["name"] for v in c.voices(page_size=2)], ["a", "b", "c"])
        self.assertEqual([u.split("?")[1] for _, u, _, _ in transport.calls],
                         ["page_size=2", "page_size=2&next_page_token=t+2"])

    def test_rate_limits_and_server_errors_are_retried_with_a_growing_wait(self):
        c, transport, waits = client({"/v1/models": [(429, {}), (503, {}), (429, {}, {"Retry-After": "7"}),
                                                     (200, [{"model_id": "m"}])]})
        self.assertEqual(c.models(), [{"model_id": "m"}])
        self.assertEqual(len(transport.calls), 4)
        self.assertEqual([int(w) for w in waits], [1, 2, 7], "1, 2, then what Retry-After asks, plus jitter")
        self.assertTrue(all(w != int(w) for w in waits))

    def test_retries_end_and_other_errors_are_not_retried(self):
        c, transport, waits = client({"/v1/models": [(429, {"detail": {"message": "slow down"}})] * 9})
        with self.assertRaisesRegex(el.ApiError, "429: slow down") as cm:
            c.models()
        self.assertEqual((cm.exception.status, len(transport.calls), len(waits)), (429, el.RETRIES + 1, el.RETRIES))
        c, transport, waits = client({"/v1/models": [(401, {"detail": {"status": "x", "message": "missing permission"}})]})
        with self.assertRaisesRegex(el.ApiError, "401: missing permission"):
            c.models()
        self.assertEqual((len(transport.calls), waits), (1, []))

    def test_errors_never_carry_the_key(self):
        c, _, _ = client({"/v1/models": [(400, {"detail": f"bad key {KEY} given"})]})
        with self.assertRaises(el.ApiError) as cm:
            c.models()
        self.assertNotIn(KEY, str(cm.exception))
        self.assertIn("[api key]", str(cm.exception))

        def down(method, url, headers, body, timeout):
            raise OSError(f"could not reach host with {headers['xi-api-key']}")
        with self.assertRaises(el.ApiError) as cm:
            el.Client(KEY, transport=down).models()
        self.assertNotIn(KEY, str(cm.exception))
        self.assertEqual(el.error_message(b"<html>\nBad gateway"), "<html>")
        self.assertEqual(el.error_message(b""), "no message")
        with self.assertRaises(el.KeyMissing):
            el.Client("")


SUB = {"tier": "pro", "status": "active", "character_count": 238000, "character_limit": 1238000,
       "next_character_count_reset_unix": 1796083200, "voice_slots_used": 3, "voice_limit": 160}
VOICES = {"voices": [{"name": "ffxi/tenzen", "category": "generated"}, {"name": "ffxi/iroha", "category": "generated"},
                     {"name": "George", "category": "premade"}], "has_more": False}
MODELS = [{"model_id": "eleven_v4", "can_do_text_to_speech": True, "maximum_text_length_per_request": 5000,
           "model_rates": {"character_cost_multiplier": 1.0}},
          {"model_id": "scribe", "can_do_text_to_speech": False}]


class CheckTest(unittest.TestCase):
    def test_the_report(self):
        c, transport, _ = client({"/v1/user/subscription": [(200, SUB)], "/v2/voices": [(200, VOICES)],
                                  "/v1/models": [(200, MODELS)]})
        lines = el.check(c, now=datetime(2026, 11, 19, tzinfo=timezone.utc))
        self.assertEqual(lines, [
            "plan            pro (active)",
            "characters      238,000 of 1,238,000 used; 1,000,000 left",
            "resets          2026-12-01 (12 days)",
            "voice slots     3 of 160 used",
            "voices          3 in the account: 2 generated, 1 premade",
            "ffxi/ voices    2: ffxi/iroha, ffxi/tenzen",
            "speech models   eleven_v4",
            "  eleven_v4                    5000 characters a request, cost x1.0"])
        self.assertTrue(all(method == "GET" for method, *_ in transport.calls), "a check only reads")

    def test_a_read_the_key_may_not_make_is_reported_in_its_place(self):
        denied = (401, {"detail": {"message": "The API key you used is missing the permission user_read"}})
        c, _, _ = client({"/v1/user/subscription": [denied], "/v2/voices": [(200, VOICES)],
                          "/v1/models": [(200, MODELS)]})
        lines = el.check(c)
        self.assertIn("missing the permission user_read", lines[0])
        self.assertTrue(lines[0].startswith("plan            not read: "))
        self.assertEqual(lines[1], "voices          3 in the account: 2 generated, 1 premade")

    def test_main_reports_a_missing_key_without_a_traceback(self):
        import contextlib
        import io
        err = io.StringIO()
        with unittest.mock.patch.object(el, "api_key", side_effect=el.KeyMissing("no ElevenLabs API key: ...")), \
                contextlib.redirect_stderr(err):
            self.assertEqual(el.main(["check"]), 1)
        self.assertIn("no ElevenLabs API key", err.getvalue())


class KeyScanTest(unittest.TestCase):
    """Nothing committed may hold a key. ElevenLabs keys are sk_ and a long
    run of hex."""
    SHAPE = re.compile(rb"\bsk_[0-9a-f]{32,}\b")

    def test_no_tracked_file_holds_anything_shaped_like_a_key(self):
        repo = HERE.parent
        try:
            listed = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        found = []
        for name in filter(None, listed.split(b"\0")):
            path = repo / name.decode("utf-8")
            if path.is_file() and path.stat().st_size < 5_000_000 and self.SHAPE.search(path.read_bytes()):
                found.append(name.decode("utf-8"))
        self.assertEqual(found, [], "these files hold what looks like an API key")

    def test_the_shape_matches_a_key(self):
        self.assertTrue(self.SHAPE.search(b'key = "sk_' + b"0a" * 24 + b'"'))
        self.assertFalse(self.SHAPE.search(b"task_0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a"))


if __name__ == "__main__":
    unittest.main()
