"""Throwaway probe: does apify 4.0.2 parse the local pay-per-event env vars?"""

import json
import os

os.environ["ACTOR_TEST_PAY_PER_EVENT"] = "1"
os.environ["APIFY_ACTOR_PRICING_INFO"] = json.dumps(
    {
        "pricingModel": "PAY_PER_EVENT",
        "pricingPerEvent": {
            "actorChargeEvents": {
                "source-checked": {"eventTitle": "Source checked", "eventPriceUsd": 0.05},
                "change-report": {"eventTitle": "Change report", "eventPriceUsd": 0.5},
            }
        },
    }
)
os.environ["APIFY_CHARGED_ACTOR_EVENT_COUNTS"] = json.dumps({})
os.environ["ACTOR_MAX_TOTAL_CHARGE_USD"] = "5"

from apify import Configuration  # noqa: E402

c = Configuration.get_global_configuration()
print("pricing_info:", repr(c.actor_pricing_info))
print("charged_event_counts:", repr(c.charged_event_counts))
print("max_total_charge_usd:", repr(c.max_total_charge_usd))
print("test_pay_per_event:", repr(c.test_pay_per_event))
