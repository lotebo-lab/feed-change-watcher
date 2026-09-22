"""Apify Actor entry point: Feed Change Watcher.

Input is a list of RSS or Atom feed URLs. Every run fetches each feed, keeps
only the items whose id was never returned before, pushes those to the dataset
and writes one report per run with the count per source.

What the run does, in order, for each feed:

1. waits the configured delay since the previous request (polite by default);
2. fetches the feed with a user agent that says who we are;
3. parses it with the standard library (src/feed_parser.py), which keeps only
   the seven allowed fields and drops anything that names a person;
4. compares the ids against the ids stored for that feed (src/state.py);
5. pushes the new items and stores the ids.

Charging notes, learned the hard way: every charge call has a timeout, and a
charge that fails is a warning in the log, never the end of the run. A run that
stops because charging hung returns nothing to the user and still costs them.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import requests
from apify import Actor

try:  # running inside the Actor image (python src/main.py)
    from feed_parser import FeedParseError, parse_feed
    from robots import BLOCKED, UNREADABLE, RobotsGate
    from state import load_seen, merge_seen, save_seen, select_new
    from summary_row import SUMMARY_ROW_TYPE, build_summary_row, tag_items
except ImportError:  # running as a package (python -m src.main)
    from .feed_parser import FeedParseError, parse_feed
    from .robots import BLOCKED, UNREADABLE, RobotsGate
    from .state import load_seen, merge_seen, save_seen, select_new
    from .summary_row import SUMMARY_ROW_TYPE, build_summary_row, tag_items

# Pay-per-event. One event per feed actually read, plus one event for the run
# report. `actor-start` is charged by Apify itself, never in code.
SOURCE_EVENT = "source-checked"
REPORT_EVENT = "change-report"

# Hard ceiling for one charge round trip. Every charge is an HTTP call to the
# Apify API and it must never hold a run hostage: run dDAxrKSafjiCYssUa of
# page-audit-tool finished its work in 4.2 s and then burned 60.1 s on a single
# charge that never answered. Short ceiling, warning in the log, run goes on.
CHARGE_TIMEOUT_SECONDS = 5.0

# The state lives in a named key-value store so it survives between runs: the
# default store of a run is thrown away with the run.
STATE_STORE_NAME = "feed-change-watcher-state"

USER_AGENT = (
    "FeedChangeWatcher/0.1 (Apify Actor; +https://apify.com/lotebo-lab/feed-change-watcher)"
)

DEFAULTS = {
    "feedUrls": [],
    "maxSources": 20,
    "requestDelaySeconds": 2,
    "requestTimeoutSeconds": 20,
}

MAX_RESPONSE_BYTES = 10 * 1024 * 1024


def normalize_urls(raw) -> list[str]:
    """Accept a list of URLs, a list of {"url": ...} objects, or one string."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    urls: list[str] = []
    for entry in raw:
        if isinstance(entry, dict):
            entry = entry.get("url") or entry.get("requestUrl") or ""
        url = str(entry or "").strip()
        if not url:
            continue
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")
        if url not in urls:
            urls.append(url)
    return urls


def should_charge_report(
    total_new: int, is_pay_per_event: bool, limit_reached: bool
) -> bool:
    """Decide whether this run pays for the change report.

    The report costs US$ 0.50 against US$ 0.05 for reading a feed, so it is the
    whole bill of a quiet run. A feed watcher answers "nothing new" most of the
    time (run Y8drvcQt2D4h6a5EF read 1 feed, found 0 new items and charged 2
    events, about US$ 0.55 to learn that nothing changed). From here on the
    report is only charged when there is a change to report; reading the feed
    is real work and stays charged per source, whatever the answer is.
    """
    return bool(total_new) and is_pay_per_event and not limit_reached


def fetch_feed(url: str, timeout_seconds: float) -> bytes:
    """Fetch one feed. Raises on any HTTP or network problem."""
    response = requests.get(
        url,
        timeout=timeout_seconds,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
        allow_redirects=True,
    )
    response.raise_for_status()
    return response.content[:MAX_RESPONSE_BYTES]


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        options = {**DEFAULTS, **{k: v for k, v in actor_input.items() if v is not None}}

        feed_urls = normalize_urls(options["feedUrls"])
        if not feed_urls:
            raise ValueError("Input field 'feedUrls' is required: give at least one feed URL.")

        max_sources = max(1, int(options["maxSources"]))
        delay_seconds = max(0.0, float(options["requestDelaySeconds"]))
        timeout_seconds = max(1.0, float(options["requestTimeoutSeconds"]))

        if len(feed_urls) > max_sources:
            Actor.log.info(
                f"{len(feed_urls)} feeds given, reading the first {max_sources} "
                "(raise 'maxSources' to read more)."
            )
            feed_urls = feed_urls[:max_sources]

        charge_state = {"limit_reached": False, "charged": 0, "failures": 0}
        pricing = Actor.get_charging_manager().get_pricing_info()
        is_pay_per_event = pricing.is_pay_per_event
        if not is_pay_per_event:
            Actor.log.info(
                "This run is not billed per event (pricing model: "
                f"{pricing.pricing_model}). Reading the feeds without charging."
            )

        async def charge(event_name: str) -> bool:
            """Charge one event. False means: the run's budget is spent."""
            if not is_pay_per_event or charge_state["limit_reached"]:
                return not charge_state["limit_reached"]
            try:
                charge_result = await asyncio.wait_for(
                    Actor.charge(event_name=event_name), timeout=CHARGE_TIMEOUT_SECONDS
                )
            except Exception as exc:  # noqa: BLE001 - never kill a paid run
                charge_state["failures"] += 1
                Actor.log.warning(f"Could not charge {event_name}: {exc}")
                return True
            if charge_result.event_charge_limit_reached:
                charge_state["limit_reached"] = True
                Actor.log.info(
                    "Charge limit reached, stopping after this feed and keeping "
                    "everything found so far."
                )
                return False
            if charge_result.charged_count < 1:
                charge_state["failures"] += 1
                return True
            charge_state["charged"] += charge_result.charged_count
            return True

        state_store = await Actor.open_key_value_store(name=STATE_STORE_NAME)
        started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        report_sources: list[dict] = []
        sources_read = 0
        sources_failed = 0
        sources_skipped = 0
        sources_skipped_by_robots = 0
        total_new = 0
        first_request = True

        # The approved proposal of this Actor promises that robots.txt is
        # respected. One robots.txt per host, fetched once, and a host that
        # does not answer never blocks the feed (see src/robots.py).
        robots = RobotsGate(user_agent=USER_AGENT, timeout_seconds=timeout_seconds)

        for url in feed_urls:
            if charge_state["limit_reached"]:
                sources_skipped += 1
                Actor.log.info(f"Skipping {url}: the charge limit was reached.")
                report_sources.append(
                    {
                        "source_url": url,
                        "source_title": "",
                        "status": "skipped",
                        "error": "charge limit reached",
                        "items_in_feed": 0,
                        "new_items": 0,
                    }
                )
                continue

            if delay_seconds and not first_request:
                await asyncio.sleep(delay_seconds)
            first_request = False

            # Asked before the feed request, and inside the same pacing, because
            # fetching robots.txt is itself a request to the host. It is a
            # synchronous call, so it goes to a worker thread like the feed.
            may_fetch, robots_status = await asyncio.to_thread(robots.check, url)
            if not may_fetch:
                sources_skipped_by_robots += 1
                Actor.log.info(
                    f"[robots] skipping {url}: the host's robots.txt disallows "
                    f"'{USER_AGENT.split('/')[0]}' on this path. Nothing was "
                    "fetched and nothing was charged for this feed."
                )
                report_sources.append(
                    {
                        "source_url": url,
                        "source_title": "",
                        "status": "skipped_by_robots",
                        "error": "disallowed by the host's robots.txt",
                        "items_in_feed": 0,
                        "new_items": 0,
                    }
                )
                continue
            if robots_status == UNREADABLE:
                Actor.log.info(
                    f"[robots] {robots.host_key(url)}/robots.txt could not be read; "
                    "no restriction is stated, so the feed is fetched."
                )

            try:
                body = await asyncio.to_thread(fetch_feed, url, timeout_seconds)
                feed = parse_feed(url, body)
            except (requests.RequestException, FeedParseError, ValueError) as exc:
                sources_failed += 1
                Actor.log.warning(f"Could not read {url}: {exc}")
                report_sources.append(
                    {
                        "source_url": url,
                        "source_title": "",
                        "status": "failed",
                        "error": str(exc)[:300],
                        "items_in_feed": 0,
                        "new_items": 0,
                    }
                )
                continue

            seen = await load_seen(state_store, url)
            first_time = not seen
            new_items, new_ids = select_new(feed["items"], seen)

            if new_items:
                # Tagged at the door of the dataset, never inside the parser:
                # `parse_feed` keeps its seven-field allow list untouched.
                await Actor.push_data(tag_items(new_items))
            await save_seen(state_store, url, merge_seen(seen, new_ids), started_at)

            sources_read += 1
            total_new += len(new_items)
            report_sources.append(
                {
                    "source_url": url,
                    "source_title": feed["source_title"],
                    "status": "ok",
                    "error": "",
                    "items_in_feed": len(feed["items"]),
                    "new_items": len(new_items),
                    "first_run": first_time,
                }
            )
            Actor.log.info(
                f"{url}: {len(feed['items'])} item(s) in the feed, "
                f"{len(new_items)} new" + (" (first run, baseline)" if first_time else "")
            )

            # Charged only after the feed was really read and stored.
            await charge(SOURCE_EVENT)

        report = {
            "runStartedAt": started_at,
            "sourcesGiven": len(feed_urls),
            "sourcesRead": sources_read,
            "sourcesFailed": sources_failed,
            "sourcesSkipped": sources_skipped,
            "sourcesSkippedByRobots": sources_skipped_by_robots,
            "robotsFetches": robots.fetches,
            "newItems": total_new,
            "perSource": report_sources,
            "chargedEvents": charge_state["charged"],
            "chargeLimitReached": charge_state["limit_reached"],
            "chargeFailures": charge_state["failures"],
        }

        # The gate lives in should_charge_report() so a test can read it. It
        # reads `total_new`, never the dataset, so the summary row below cannot
        # make a run charge an event it would not have charged before.
        if should_charge_report(total_new, is_pay_per_event, charge_state["limit_reached"]):
            await charge(REPORT_EVENT)
            report["chargedEvents"] = charge_state["charged"]
            report["chargeFailures"] = charge_state["failures"]

        # A successful run always writes at least this row, free of charge.
        # "No new items" is the normal answer of a feed watcher from the second
        # run on, and an empty dataset reads to the buyer as a broken Actor.
        await Actor.push_data(build_summary_row(report))
        Actor.log.info(
            f"Wrote {total_new + 1} row(s) to dataset "
            f"{Actor.configuration.default_dataset_id} "
            f"({total_new} new item row(s) plus 1 '{SUMMARY_ROW_TYPE}' row)"
        )

        await Actor.set_value("REPORT", report)
        Actor.log.info(
            f"Finished: {sources_read} feed(s) read, {sources_failed} failed, "
            f"{sources_skipped_by_robots} skipped by robots.txt, "
            f"{total_new} new item(s) pushed."
        )


if __name__ == "__main__":
    asyncio.run(main())
