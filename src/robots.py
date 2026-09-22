"""robots.txt gate: ask the host before fetching one of its feeds.

Why this file exists
--------------------
The approved proposal of this business promises in writing that the Actor
respects robots.txt, and until now nothing in `src/` read a robots.txt at all:
there was a user agent and a delay between requests, and that was all. This
module closes that gap.

How it behaves, and why
-----------------------
* one robots.txt per host, fetched once per run and cached, so watching ten
  feeds of the same site costs one extra request, not ten;
* the question is asked with the same user agent string the feed request sends,
  so what we promise the host and what we obey are the same identity;
* **a robots.txt we cannot read never blocks the feed.** A 404, a 500, a
  timeout or a connection error all mean "the host stated no restriction we can
  read", which is the standard reading, and the run says so in the log. Only an
  explicit `Disallow` that matches our user agent skips a source. Treating a
  failed fetch as a block would let one flaky request silently turn a paid
  watch into an empty dataset, which is the very defect this round is fixing.

Nothing here charges anything: this module has no Apify import, it is pure
fetching and parsing, and the events this Actor charges are decided in
`src/main.py`.
"""

from __future__ import annotations

import urllib.robotparser
from urllib.parse import urlparse

import requests

# Status of one robots.txt decision, as it ends up in the log and the report.
ALLOWED = "allowed"
BLOCKED = "blocked"
UNREADABLE = "unreadable"


class RobotsGate:
    """Caches one robots.txt per host and answers "may we fetch this URL?"."""

    def __init__(
        self,
        user_agent: str,
        timeout_seconds: float = 10.0,
        getter=None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        # Injectable so the offline tests can drive it without a network.
        self._getter = getter if getter is not None else requests.get
        self._parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.fetches = 0
        self.unreadable_hosts: set[str] = set()

    @staticmethod
    def host_key(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _parser(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        key = self.host_key(url)
        if key in self._parsers:
            return self._parsers[key]
        parser: urllib.robotparser.RobotFileParser | None = None
        self.fetches += 1
        try:
            response = self._getter(
                key + "/robots.txt",
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout_seconds,
                allow_redirects=True,
            )
            if response.status_code == 200:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(response.text.splitlines())
            else:
                # 4xx and 5xx: the standard reading is "no restrictions stated".
                self.unreadable_hosts.add(key)
        except Exception:  # noqa: BLE001 - any failure means "no rule we can read"
            self.unreadable_hosts.add(key)
        self._parsers[key] = parser
        return parser

    def check(self, url: str) -> tuple[bool, str]:
        """Return (may_fetch, status). Only an explicit Disallow returns False.

        `status` is one of ALLOWED, BLOCKED or UNREADABLE, so the caller can
        log the difference between "the host said yes" and "the host said
        nothing we could read", which are the same decision but not the same
        fact.
        """
        parser = self._parser(url)
        if parser is None:
            return True, UNREADABLE
        try:
            # `can_fetch` splits the agent at "/" and matches case-insensitively,
            # so the full product string of the feed request can be passed here
            # unchanged.
            if parser.can_fetch(self.user_agent, url):
                return True, ALLOWED
        except Exception:  # noqa: BLE001 - a malformed robots.txt is not a block
            return True, UNREADABLE
        return False, BLOCKED


__all__ = ("ALLOWED", "BLOCKED", "UNREADABLE", "RobotsGate")
