"""Same end-to-end run as `run_local_e2e.py`, but with pay-per-event turned on.

Why this file exists: the plain local run reports `chargedEvents: 0` no matter
what the Actor decides, because locally the SDK says the run is not billed per
event and `src/main.py` skips every charge. So the plain run cannot prove
anything about the bill. The cloud run Y8drvcQt2D4h6a5EF read 1 feed, found 0
new items and still charged 2 events (about US$ 0.55 to learn that nothing
changed), and the plain local run could not have caught it.

This script sets the two environment variables the SDK reads for local
pay-per-event development, the same way `actor-auditoria-paginas` does, so the
offline run takes the same code path the cloud run takes and the number in
`chargedEvents` is a number the Actor really decided.

Expected, with the two fixture feeds (asserted by tests/test_charging_e2e.py):
    fixtures/run1, empty state:  2 feeds read, 5 new items
        -> 2 source-checked + 1 change-report = 3 charged events
    fixtures/run1 again, state kept: 2 feeds read, 0 new items
        -> 2 source-checked + 0 change-report = 2 charged events
    fixtures/run2, state kept:   2 feeds read, 1 new item
        -> 2 source-checked + 1 change-report = 3 charged events

Usage:
    python tests/run_local_e2e_ppe.py <site-dir> <run-dir> [phase]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PRICING_INFO = {
    "pricingModel": "PAY_PER_EVENT",
    "pricingPerEvent": {
        "actorChargeEvents": {
            "source-checked": {"eventTitle": "Source checked", "eventPriceUsd": 0.05},
            "change-report": {"eventTitle": "Change report", "eventPriceUsd": 0.5},
        }
    },
}

os.environ["ACTOR_TEST_PAY_PER_EVENT"] = "1"
os.environ["APIFY_ACTOR_PRICING_INFO"] = json.dumps(PRICING_INFO)
# Both env vars, or neither counts. apify 4.0.2 only reads the pricing above in
# `ChargingManager._fetch_pricing_info` when `charged_event_counts` is also set
# (validation alias `apify_charged_actor_event_counts`); with only one of them
# it falls through to the "local development without environment variables"
# branch, where `_pricing_info` stays empty and every event, including the
# synthetic `apify-default-dataset-item`, is priced at US$ 1. That made the run
# hit `max_total_charge_usd` after a handful of dataset rows and report a
# `chargedEvents` that had nothing to do with this Actor's prices.
# An empty dict does not work: the field validator is `data or None`, so `{}`
# lands back on None and the whole pricing block is ignored again. Zeroed counts
# for the two real events are the smallest value that parses.
os.environ["APIFY_CHARGED_ACTOR_EVENT_COUNTS"] = json.dumps(
    {"source-checked": 0, "change-report": 0}
)
# High on purpose. Pushing to the default dataset makes the SDK charge a
# synthetic `apify-default-dataset-item` event; on the platform an event absent
# from the Actor's pricing costs 0, but locally `_get_event_price` prices any
# unknown event at US$ 1, so a US$ 5 ceiling was reached after five rows and the
# run reported `chargeLimitReached: true` for reasons that do not exist in the
# cloud. The charge-limit path itself is covered by unit cases in
# tests/test_schemas.py, which do not need this fake dollar.
os.environ.setdefault("ACTOR_MAX_TOTAL_CHARGE_USD", "1000")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_local_e2e import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
