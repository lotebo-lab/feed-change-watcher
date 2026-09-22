"""The one dataset row every successful run writes, new items or none.

Why this file exists
--------------------
This Actor watches feeds. From the second run on, "nothing new" is not the
exception, it is the normal answer: a feed that publishes once a week gives the
buyer six empty datasets out of seven. An empty file is indistinguishable from
an Actor that crashed, so the buyer who paid for the watch cannot tell the two
apart. The summary row says, inside the dataset itself, how many sources were
checked, how many were read, how many failed and how many new items appeared,
even when that number is zero.

Nothing here charges anything. The module has no Apify import on purpose: it is
pure data, it is called after the work is done, and the events this Actor
charges (`source-checked` per feed read, `change-report` once per run when at
least one feed was read) are decided in `src/main.py` and were not touched.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Every row carries this field so a reader can tell a feed item from the run
# summary with one comparison, and drop the summary if it only wants items.
ROW_TYPE_FIELD = "rowType"
ITEM_ROW_TYPE = "feed-item"
SUMMARY_ROW_TYPE = "summary"

# Fields that exist only on the summary row. Declared here so the schema test
# can check the dataset schema against the code instead of against a list typed
# by hand twice.
SUMMARY_ONLY_FIELDS = (
    "runStartedAt",
    "finishedAt",
    "sourcesGiven",
    "sourcesRead",
    "sourcesFailed",
    "sourcesSkipped",
    "sourcesSkippedByRobots",
    "newItems",
    "chargedEvents",
    "chargeLimitReached",
    "chargeFailures",
    "message",
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tag_items(items: list[dict]) -> list[dict]:
    """Stamp `rowType` on the feed items, without touching their content.

    A copy is returned: `parse_feed` builds the item with exactly the seven
    allowed fields and that allow list is the LGPD guard of this Actor, so the
    row type is added here, at the door of the dataset, and never inside the
    parser.
    """
    return [{ROW_TYPE_FIELD: ITEM_ROW_TYPE, **item} for item in items]


def summary_message(report: dict) -> str:
    """One plain sentence with the counts. No promise, no advice, no guess."""
    given = report.get("sourcesGiven", 0)
    read = report.get("sourcesRead", 0)
    failed = report.get("sourcesFailed", 0)
    skipped = report.get("sourcesSkipped", 0)
    by_robots = report.get("sourcesSkippedByRobots", 0)
    new_items = report.get("newItems", 0)

    if new_items:
        text = (
            f"{new_items} new item(s) found in {read} of {given} feed(s) checked."
        )
    else:
        text = (
            f"No new items: {read} of {given} feed(s) were read and every item "
            "they list had already been reported by an earlier run."
        )
    if read == 0 and given:
        if by_robots == given:
            text = (
                f"Nothing was fetched: all {given} feed(s) are disallowed by "
                "their host's robots.txt for this Actor's user agent, so this "
                "run says nothing about whether they changed."
            )
        else:
            text = (
                f"No feed could be read: {given} feed(s) were given and none "
                "answered, so this run says nothing about whether they changed."
            )
    if failed:
        text += f" {failed} feed(s) could not be read; see 'perSource' in the REPORT record."
    if by_robots and by_robots != given:
        text += (
            f" {by_robots} feed(s) were skipped because the host's robots.txt "
            "disallows them for this Actor's user agent."
        )
    if skipped:
        text += f" {skipped} feed(s) were skipped after the charge limit was reached."
    if report.get("chargeLimitReached"):
        text += (
            " The run stopped early because it reached its pay-per-event charge "
            "limit, so part of the list was not checked."
        )
    return text


def build_summary_row(report: dict) -> dict:
    """The summary row, built from the same numbers the REPORT record holds."""
    return {
        ROW_TYPE_FIELD: SUMMARY_ROW_TYPE,
        "runStartedAt": report.get("runStartedAt"),
        "finishedAt": report.get("finishedAt") or _now(),
        "sourcesGiven": report.get("sourcesGiven", 0),
        "sourcesRead": report.get("sourcesRead", 0),
        "sourcesFailed": report.get("sourcesFailed", 0),
        "sourcesSkipped": report.get("sourcesSkipped", 0),
        "sourcesSkippedByRobots": report.get("sourcesSkippedByRobots", 0),
        "newItems": report.get("newItems", 0),
        "chargedEvents": report.get("chargedEvents", 0),
        "chargeLimitReached": bool(report.get("chargeLimitReached", False)),
        "chargeFailures": report.get("chargeFailures", 0),
        "message": summary_message(report),
    }


__all__ = (
    "ROW_TYPE_FIELD",
    "ITEM_ROW_TYPE",
    "SUMMARY_ROW_TYPE",
    "SUMMARY_ONLY_FIELDS",
    "build_summary_row",
    "summary_message",
    "tag_items",
)
