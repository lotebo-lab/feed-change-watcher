"""What a run really charges, read from the SDK's own charging log.

Why this file exists
--------------------
tests/test_schemas.py checks the gate `should_charge_report()` as a function and
checks that src/main.py calls it. That is a model of the run, not the run. The
cloud run Y8drvcQt2D4h6a5EF read 1 feed, found 0 new items and charged 2 events
(one `source-checked` at US$ 0.05 plus one `change-report` at US$ 0.50, about
US$ 0.55 to learn that nothing had changed), and no test in this folder would
have caught it, because the plain local run reports `chargedEvents: 0` whatever
the Actor decides.

So this test drives the whole Actor three times through
tests/run_local_e2e_ppe.py, which turns pay-per-event on locally, and then reads
the dataset named `charging-log` that the SDK itself writes for every
`Actor.charge()` call. The number asserted here is the number the charging
manager recorded, not a number this Actor printed about itself.

The three runs share one state store, which is what makes the second one the
case that matters: the same feeds, unchanged, nothing new, and the buyer must
pay only for the two feeds that were read.

Usage:
    python tests/test_charging_e2e.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACTOR_DIR = HERE.parent
RUNNER = HERE / "run_local_e2e_ppe.py"

# Events the platform charges by itself (dataset writes, Actor start). They are
# not decided by src/main.py and they are not priced in .actor/actor.json, so
# they do not belong in the bill this test is about.
PLATFORM_EVENT_PREFIX = "apify-"

PRICES_USD = {"source-checked": 0.05, "change-report": 0.5}

failures: list[str] = []
checks_run = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checks_run
    checks_run += 1
    print(("ok   " if ok else "FAIL ") + name + (("  <- " + detail) if not ok else ""))
    if not ok:
        failures.append(name)


def charged_events(run_dir: Path) -> dict[str, int]:
    """Read the SDK's charging log for the last run and count events by name."""
    log_dir = run_dir / "storage" / "datasets" / "charging-log"
    counts: dict[str, int] = {}
    if not log_dir.is_dir():
        return counts
    for entry in sorted(log_dir.glob("[0-9]*.json")):
        record = json.loads(entry.read_text())
        name = record.get("event_name", "")
        if not name or name.startswith(PLATFORM_EVENT_PREFIX):
            continue
        counts[name] = counts.get(name, 0) + int(record.get("charged_count", 0))
    return counts


def report_of(run_dir: Path) -> dict:
    kvs = run_dir / "storage" / "key_value_stores" / "default"
    for candidate in (kvs / "REPORT", kvs / "REPORT.json"):
        if candidate.is_file():
            return json.loads(candidate.read_text())
    return {}


def run_phase(site: str, run_dir: Path, phase: str) -> tuple[dict[str, int], dict]:
    result = subprocess.run(
        [sys.executable, str(RUNNER), str(HERE / "fixtures" / site), str(run_dir), phase],
        cwd=str(ACTOR_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stdout[-2000:])
        print(result.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"the local run failed with exit code {result.returncode}")
    return charged_events(run_dir), report_of(run_dir)


def bill_usd(counts: dict[str, int]) -> float:
    return round(sum(PRICES_USD.get(name, 0.0) * n for name, n in counts.items()), 4)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="vigia-charging-"))
    try:
        # --- 1. first run of these feeds: everything is new, there is a report
        counts, report = run_phase("run1", work, "1")
        check(
            "first_run_reads_both_feeds",
            report.get("sourcesRead") == 2,
            "sourcesRead=%r" % report.get("sourcesRead"),
        )
        check(
            "first_run_finds_new_items",
            report.get("newItems", 0) > 0,
            "newItems=%r" % report.get("newItems"),
        )
        check(
            "first_run_charges_one_source_checked_per_feed",
            counts.get("source-checked") == 2,
            "charging log: %r" % counts,
        )
        check(
            "first_run_charges_the_report_once",
            counts.get("change-report") == 1,
            "charging log: %r" % counts,
        )
        check(
            "first_run_bill_is_0_60",
            bill_usd(counts) == 0.6,
            "US$ %s from %r" % (bill_usd(counts), counts),
        )

        # --- 2. THE CASE THAT MATTERS: same feeds, nothing changed.
        # This is the normal answer of a watcher, and run Y8drvcQt2D4h6a5EF
        # charged the US$ 0.50 report for exactly this.
        counts, report = run_phase("run1", work, "2")
        check(
            "quiet_run_still_reads_both_feeds",
            report.get("sourcesRead") == 2,
            "sourcesRead=%r" % report.get("sourcesRead"),
        )
        check(
            "quiet_run_finds_nothing_new",
            report.get("newItems") == 0,
            "newItems=%r" % report.get("newItems"),
        )
        check(
            "quiet_run_charges_one_source_checked_per_feed",
            counts.get("source-checked") == 2,
            "charging log: %r" % counts,
        )
        check(
            "quiet_run_does_not_charge_the_report",
            "change-report" not in counts,
            "charging log: %r" % counts,
        )
        check(
            "quiet_run_charges_only_the_source_event",
            set(counts) == {"source-checked"},
            "charging log: %r" % counts,
        )
        check(
            "quiet_run_bill_is_0_10",
            bill_usd(counts) == 0.1,
            "US$ %s from %r" % (bill_usd(counts), counts),
        )
        check(
            "quiet_run_reports_two_charged_events",
            report.get("chargedEvents") == 2,
            "chargedEvents=%r" % report.get("chargedEvents"),
        )
        check(
            "quiet_run_still_writes_the_summary_row",
            _summary_rows(work) == 1,
            "summary rows=%r" % _summary_rows(work),
        )

        # --- 3. one feed changed: the report is worth paying for again
        counts, report = run_phase("run2", work, "2")
        check(
            "changed_run_finds_the_new_item",
            report.get("newItems") == 1,
            "newItems=%r" % report.get("newItems"),
        )
        check(
            "changed_run_charges_the_report_once",
            counts.get("change-report") == 1,
            "charging log: %r" % counts,
        )
        check(
            "changed_run_charges_one_source_checked_per_feed",
            counts.get("source-checked") == 2,
            "charging log: %r" % counts,
        )
        check(
            "changed_run_bill_is_0_60",
            bill_usd(counts) == 0.6,
            "US$ %s from %r" % (bill_usd(counts), counts),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("%d/%d passed" % (checks_run - len(failures), checks_run))
    return 1 if failures else 0


def _summary_rows(run_dir: Path) -> int:
    dataset = run_dir / "storage" / "datasets" / "default"
    if not dataset.is_dir():
        return 0
    rows = 0
    for entry in dataset.glob("[0-9]*.json"):
        if json.loads(entry.read_text()).get("rowType") == "summary":
            rows += 1
    return rows


if __name__ == "__main__":
    sys.exit(main())
