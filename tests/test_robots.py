"""Tests for the robots.txt gate, with no network.

pytest is not installed in the lab sandbox and `pip install` has no network
there, so this file is a runner of its own, in plain Python. Run it from the
Actor directory:

    ../../.venv/bin/python tests/test_robots.py

Every robots.txt below is written out in full inside this file and handed to
the gate through its `getter` hook, so the test never touches the internet.

What it proves:

1. an explicit `Disallow` for our user agent blocks that feed, and only that
   feed: another path on the same host stays allowed;
2. a `Disallow` written for a different crawler does not block us;
3. a robots.txt that answers 404, answers 500, times out or refuses the
   connection NEVER blocks the feed. That is the rule that keeps one flaky
   request from silently turning a paid watch into an empty dataset;
4. one robots.txt is fetched per host, not per feed;
5. `User-agent: *` with `Disallow: /` blocks us;
6. the gate announces the same user agent the feed request announces.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.main import USER_AGENT  # noqa: E402
from src.robots import ALLOWED, BLOCKED, UNREADABLE, RobotsGate  # noqa: E402

FAILURES: list[str] = []
PASSED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print("ok   " + name)
    else:
        FAILURES.append(name)
        print("FAIL " + name + ((" :: " + detail) if detail else ""))


class FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def gate_for(bodies: dict, user_agent: str = USER_AGENT):
    """A gate whose robots.txt answers come from `bodies`, plus a call log."""
    calls: list[dict] = []

    def getter(url, headers=None, timeout=None, allow_redirects=None):
        calls.append({"url": url, "headers": headers or {}, "timeout": timeout})
        answer = bodies.get(url)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            return FakeResponse(404)
        if isinstance(answer, int):
            return FakeResponse(answer)
        return FakeResponse(200, answer)

    return RobotsGate(user_agent=user_agent, getter=getter), calls


# ------------------------------------------- 1. an explicit Disallow for us
ROBOTS_BLOCKS_FEEDS = """
User-agent: FeedChangeWatcher
Disallow: /private/

User-agent: *
Disallow:
"""

gate, calls = gate_for({"https://example.com/robots.txt": ROBOTS_BLOCKS_FEEDS})
allowed, status = gate.check("https://example.com/private/feed.xml")
check("explicit_disallow_blocks_that_feed", allowed is False, repr((allowed, status)))
check("blocked_status_is_reported", status == BLOCKED, repr(status))

allowed, status = gate.check("https://example.com/blog/feed.xml")
check("another_path_on_the_same_host_stays_allowed", allowed is True, repr((allowed, status)))
check("allowed_status_is_reported", status == ALLOWED, repr(status))

check(
    "one_robots_fetch_per_host_not_per_feed",
    len(calls) == 1 and gate.fetches == 1,
    "fetched %d time(s): %s" % (len(calls), [c["url"] for c in calls]),
)
check(
    "robots_is_fetched_from_the_host_root",
    calls[0]["url"] == "https://example.com/robots.txt",
    repr(calls[0]["url"]),
)
check(
    "robots_request_announces_our_user_agent",
    calls[0]["headers"].get("User-Agent") == USER_AGENT,
    repr(calls[0]["headers"]),
)
check(
    "robots_request_has_a_timeout",
    isinstance(calls[0]["timeout"], (int, float)) and calls[0]["timeout"] > 0,
    repr(calls[0]["timeout"]),
)

# --------------------------------- 2. a rule written for another crawler
ROBOTS_OTHER_CRAWLER = """
User-agent: SomeOtherBot
Disallow: /

User-agent: *
Allow: /
"""

gate, _ = gate_for({"https://other.example/robots.txt": ROBOTS_OTHER_CRAWLER})
allowed, _ = gate.check("https://other.example/feed.xml")
check("a_rule_for_another_crawler_does_not_block_us", allowed is True, repr(allowed))

# ------------------------------------------ 5. a wildcard Disallow blocks us
ROBOTS_BLOCKS_EVERYONE = """
User-agent: *
Disallow: /
"""

gate, _ = gate_for({"https://closed.example/robots.txt": ROBOTS_BLOCKS_EVERYONE})
allowed, status = gate.check("https://closed.example/feed.xml")
check("wildcard_disallow_blocks_us", allowed is False and status == BLOCKED, repr((allowed, status)))

# ------------- 3. a robots.txt we cannot read never blocks the feed
class FakeTimeout(Exception):
    pass


for label, answer in (
    ("404", 404),
    ("500", 500),
    ("timeout", FakeTimeout("read timed out")),
    ("connection_error", OSError("connection refused")),
):
    gate, _ = gate_for({"https://flaky.example/robots.txt": answer})
    allowed, status = gate.check("https://flaky.example/feed.xml")
    check(
        "robots_%s_does_not_block_the_feed" % label,
        allowed is True,
        repr((allowed, status)),
    )
    check(
        "robots_%s_is_reported_as_unreadable" % label,
        status == UNREADABLE,
        repr(status),
    )
    check(
        "robots_%s_host_is_recorded" % label,
        "https://flaky.example" in gate.unreadable_hosts,
        repr(gate.unreadable_hosts),
    )

# A robots.txt that is served but is garbage is not a block either.
gate, _ = gate_for({"https://garbage.example/robots.txt": "\x00 not robots at all <<<"})
allowed, _ = gate.check("https://garbage.example/feed.xml")
check("a_malformed_robots_does_not_block_the_feed", allowed is True, repr(allowed))

# An unreadable robots.txt is cached too: a dead host is asked once, not once
# per feed, or watching ten feeds of it doubles the requests we make to it.
gate, calls = gate_for({"https://flaky.example/robots.txt": 500})
gate.check("https://flaky.example/a.xml")
gate.check("https://flaky.example/b.xml")
check("an_unreadable_robots_is_cached_too", len(calls) == 1, repr(len(calls)))

# --------------------------------------- hosts are told apart, http vs https
gate, calls = gate_for(
    {
        "https://a.example/robots.txt": ROBOTS_BLOCKS_EVERYONE,
        "https://b.example/robots.txt": "User-agent: *\nAllow: /\n",
    }
)
blocked_a, _ = gate.check("https://a.example/feed.xml")
allowed_b, _ = gate.check("https://b.example/feed.xml")
check(
    "two_hosts_get_two_answers",
    blocked_a is False and allowed_b is True and len(calls) == 2,
    repr((blocked_a, allowed_b, len(calls))),
)
check(
    "host_key_keeps_scheme_and_port",
    RobotsGate.host_key("https://x.example:8443/feed.xml") == "https://x.example:8443",
    RobotsGate.host_key("https://x.example:8443/feed.xml"),
)

TOTAL = PASSED + len(FAILURES)
if FAILURES:
    print("\nfailed: " + ", ".join(FAILURES))
print("%d/%d passed" % (PASSED, TOTAL))
sys.exit(1 if FAILURES else 0)
