"""Checks the .actor JSON files parse and agree with the code that runs.

pytest is not installed in the lab sandbox and `pip install` has no network
there, so this file is a runner of its own, in plain Python. Run it from the
Actor directory:

    ../../.venv/bin/python tests/test_schemas.py

What it verifies:

1. actor.json, dataset_schema.json, output_schema.json and input_schema.json
   are valid JSON;
2. actor.json points at the dataset schema through `storages.dataset`, at the
   output schema through `output` and at the input schema through `input`, and
   all three files exist;
3. the pay-per-event block has exactly two events, `source-checked` at
   US$ 0.05 and `change-report` at US$ 0.50, the same two names the charging
   code in src/main.py uses, with the report charged only when the run found at
   least one new item (a quiet run pays for the feeds it read and nothing more);
4. every field named in a dataset_schema view exists in the dataset row that
   src/main.py actually pushes. The row is built here by calling the same
   function main.py calls (`feed_parser.parse_feed`), and it must contain
   exactly the seven allowed fields;
5. charging has a timeout and a failed charge is a warning, not the end of the
   run. This is a regression guard: a charge call without a timeout is what
   broke the first Actor we ran in the cloud.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ACTOR_DIR = ROOT / ".actor"
sys.path.insert(0, str(ROOT))

from src.feed_parser import ALLOWED_FIELDS, parse_feed  # noqa: E402
from src.summary_row import (  # noqa: E402
    ITEM_ROW_TYPE,
    ROW_TYPE_FIELD,
    SUMMARY_ONLY_FIELDS,
    SUMMARY_ROW_TYPE,
    build_summary_row,
    tag_items,
)

FAILURES: list[str] = []
PASSED = 0

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Blog</title>
    <item>
      <title>Release 2.1 is out</title>
      <link>https://example.com/posts/2</link>
      <guid isPermaLink="false">example-post-2</guid>
      <pubDate>Sat, 19 Sep 2026 10:30:00 +0000</pubDate>
      <description>The new release adds the export button.</description>
    </item>
  </channel>
</rss>
"""

EXPECTED_EVENTS = {
    "source-checked": 0.05,
    "change-report": 0.5,
}


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print("ok   " + name)
    else:
        FAILURES.append(name)
        print("FAIL " + name + ((" :: " + detail) if detail else ""))


# --------------------------------------------------------------- 1. valid JSON
loaded: dict[str, dict] = {}
for filename in (
    "actor.json",
    "dataset_schema.json",
    "output_schema.json",
    "input_schema.json",
):
    path = ACTOR_DIR / filename
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        check("valid_json_" + filename, isinstance(parsed, dict), type(parsed).__name__)
        loaded[filename] = parsed if isinstance(parsed, dict) else {}
    except Exception as exc:  # noqa: BLE001 - the message is the report
        loaded[filename] = {}
        check("valid_json_" + filename, False, repr(exc))

actor = loaded["actor.json"]
dataset = loaded["dataset_schema.json"]
output = loaded["output_schema.json"]
inp = loaded["input_schema.json"]

check("actor_name", actor.get("name") == "feed-change-watcher", repr(actor.get("name")))
check(
    "actor_title",
    actor.get("title") == "RSS Feed Monitor: New Items Only",
    repr(actor.get("title")),
)
check("actor_version", actor.get("version") == "0.1", repr(actor.get("version")))
check(
    "actor_specification_1",
    actor.get("actorSpecification") == 1,
    repr(actor.get("actorSpecification")),
)
check("actor_has_description", bool(actor.get("description")), repr(actor.get("description")))
check(
    "actor_dockerfile_exists",
    (ACTOR_DIR / (actor.get("dockerfile") or "missing")).resolve().is_file(),
    repr(actor.get("dockerfile")),
)

# ------------------------------------------------- 2. the schemas are referenced
storages = actor.get("storages") or {}
check(
    "storages_dataset_reference",
    storages.get("dataset") == "./dataset_schema.json",
    repr(storages.get("dataset")),
)
check(
    "output_schema_reference",
    actor.get("output") == "./output_schema.json",
    repr(actor.get("output")),
)
check(
    "input_schema_reference",
    actor.get("input") == "./input_schema.json",
    repr(actor.get("input")),
)
for reference in (storages.get("dataset"), actor.get("output"), actor.get("input")):
    if isinstance(reference, str):
        target = (ACTOR_DIR / reference).resolve()
        check("referenced_file_exists_" + reference, target.is_file(), str(target))

check(
    "output_schema_version_1",
    output.get("actorOutputSchemaVersion") == 1,
    repr(output.get("actorOutputSchemaVersion")),
)
check(
    "output_schema_has_properties",
    isinstance(output.get("properties"), dict) and bool(output["properties"]),
    repr(list(output.get("properties", {}))),
)
for prop_name, prop in (output.get("properties") or {}).items():
    check(
        "output_property_has_template_" + prop_name,
        bool(isinstance(prop, dict) and prop.get("template")),
        repr(prop),
    )

main_source = (ROOT / "src" / "main.py").read_text(encoding="utf-8")
# Imported, not only read as text: the charging gate below is executed.
from src import main as main_module  # noqa: E402

check(
    "output_report_template_matches_the_key_main_writes",
    "records/REPORT" in json.dumps(output) and 'Actor.set_value("REPORT"' in main_source,
    "the output schema and src/main.py disagree on the report key",
)

# ------------------------------------------------------ 3. the charged events
events = (actor.get("pay_per_event") or {}).get("actorChargeEvents") or {}
check(
    "charge_events_are_exactly_two",
    set(events) == set(EXPECTED_EVENTS),
    repr(sorted(events)),
)
for event_name, price in EXPECTED_EVENTS.items():
    spec = events.get(event_name) or {}
    check(
        "charge_event_price_%s_is_%s" % (event_name, price),
        spec.get("eventPriceUsd") == price,
        repr(spec.get("eventPriceUsd")),
    )
    check(
        "charge_event_has_title_" + event_name,
        bool(spec.get("eventTitle")) and bool(spec.get("eventDescription")),
        repr(spec),
    )

check(
    "main_py_uses_source_checked_event",
    'SOURCE_EVENT = "source-checked"' in main_source,
    "constant not found in src/main.py",
)
check(
    "main_py_uses_change_report_event",
    'REPORT_EVENT = "change-report"' in main_source,
    "constant not found in src/main.py",
)
check(
    "main_py_charges_the_source_after_reading_it",
    "await charge(SOURCE_EVENT)" in main_source,
    "src/main.py does not charge the per-source event",
)
check(
    "main_py_charges_the_report_only_when_an_item_is_new",
    "if should_charge_report(total_new, is_pay_per_event, charge_state[\"limit_reached\"]):"
    in main_source
    and "await charge(REPORT_EVENT)" in main_source,
    "the report event must be charged once per run and only when the run found a new item",
)
check(
    "main_py_report_gate_does_not_read_sources_read",
    "if sources_read and is_pay_per_event" not in main_source,
    "a run that read a feed and found nothing new must not pay for the report",
)

# The gate itself, executed. A run that reads feeds and finds nothing new is
# the normal case of a watcher, and it must cost only the feeds it read.
_report_gate_cases = [
    # (total_new, is_pay_per_event, limit_reached, expected)
    (0, True, False, False),   # read a feed, nothing new: the quiet run
    (1, True, False, True),    # one new item: there is something to report
    (7, True, False, True),
    (0, False, False, False),  # not billed per event at all
    (3, False, False, False),
    (3, True, True, False),    # budget already spent
    (0, True, True, False),
]
for _new, _ppe, _limit, _expected in _report_gate_cases:
    check(
        "report_gate_new=%s_ppe=%s_limit=%s_is_%s" % (_new, _ppe, _limit, _expected),
        main_module.should_charge_report(_new, _ppe, _limit) is _expected,
        "should_charge_report(%s, %s, %s) must be %s" % (_new, _ppe, _limit, _expected),
    )
check(
    "main_py_pushes_parsed_items",
    "await Actor.push_data(tag_items(new_items))" in main_source
    and "parse_feed(url, body)" in main_source,
    "src/main.py no longer pushes parse_feed items; this test's model is stale",
)
check(
    "main_py_always_writes_the_summary_row",
    "await Actor.push_data(build_summary_row(report))" in main_source,
    "a successful run must write the summary row even when no item is new",
)
check(
    "summary_row_is_pushed_outside_any_charge_gate",
    main_source.index("await Actor.push_data(build_summary_row(report))")
    > main_source.index("await charge(REPORT_EVENT)"),
    "the summary row must be written after the charging decisions, never as one",
)
check(
    "summary_row_never_charges",
    "charge" not in main_source.split("await Actor.push_data(build_summary_row(report))")[1].split("set_value")[0],
    "no charge call may sit between the summary row and the end of the run",
)

# ------------------------------- 5. charging has a timeout and never kills the run
check(
    "charge_has_a_timeout",
    "CHARGE_TIMEOUT_SECONDS" in main_source
    and "asyncio.wait_for(" in main_source
    and "timeout=CHARGE_TIMEOUT_SECONDS" in main_source,
    "charge must be wrapped in asyncio.wait_for with CHARGE_TIMEOUT_SECONDS",
)
check(
    "charge_failure_is_only_a_warning",
    "Actor.log.warning(f\"Could not charge {event_name}: {exc}\")" in main_source,
    "a failed charge must be logged and the run must continue",
)
check(
    "request_has_a_timeout",
    "timeout=timeout_seconds" in main_source,
    "every feed request must carry the configured timeout",
)
check(
    "requests_are_spaced_by_the_delay",
    "await asyncio.sleep(delay_seconds)" in main_source,
    "src/main.py must wait between feed requests",
)
check(
    "requests_declare_a_user_agent",
    "USER_AGENT" in main_source and '"User-Agent": USER_AGENT' in main_source,
    "the fetcher must identify itself",
)
check(
    "state_store_is_named_so_it_survives_between_runs",
    'Actor.open_key_value_store(name=STATE_STORE_NAME)' in main_source,
    "the default key-value store dies with the run; the state needs a named store",
)

# ------------------------------------- 6. robots.txt, promised in the proposal
check(
    "main_py_asks_robots_before_fetching_a_feed",
    "RobotsGate" in main_source
    and "await asyncio.to_thread(robots.check, url)" in main_source,
    "the proposal promises robots.txt is respected; src/main.py must ask it",
)
check(
    "robots_check_comes_before_the_feed_request",
    main_source.index("await asyncio.to_thread(robots.check, url)")
    < main_source.index("await asyncio.to_thread(fetch_feed, url, timeout_seconds)"),
    "asking robots.txt after fetching the feed is not asking at all",
)
check(
    "a_blocked_feed_is_skipped_and_counted",
    "sources_skipped_by_robots += 1" in main_source
    and '"sourcesSkippedByRobots": sources_skipped_by_robots' in main_source,
    "a feed disallowed by robots.txt must be skipped and reach the summary row",
)
check(
    "robots_skip_is_logged",
    "[robots] skipping" in main_source,
    "the buyer must see in the log which feed was skipped and why",
)
check(
    "robots_uses_the_same_user_agent_as_the_feed_request",
    "RobotsGate(user_agent=USER_AGENT" in main_source,
    "we must obey the rules written for the identity we announce",
)

# ------------------------- 4. every view field exists in a row main.py pushes
#
# From the round that added the summary row, this dataset has TWO kinds of row
# and the model below has to know both, or it fails on fields that are correct.
# The two rows are built here by calling the very functions src/main.py calls,
# so this test can never drift into approving a schema the code does not write.
row = parse_feed("https://example.com/feed.xml", SAMPLE_FEED)["items"][0]
row_fields = set(row)
check("dataset_row_has_the_seven_fields", row_fields == set(ALLOWED_FIELDS), repr(sorted(row_fields)))

item_row = tag_items([row])[0]
check(
    "item_row_is_the_seven_fields_plus_the_row_type",
    set(item_row) == row_fields | {ROW_TYPE_FIELD},
    repr(sorted(item_row)),
)
check(
    "item_row_is_tagged_feed_item",
    item_row[ROW_TYPE_FIELD] == ITEM_ROW_TYPE,
    repr(item_row.get(ROW_TYPE_FIELD)),
)
check(
    "tagging_does_not_change_the_item_content",
    {k: v for k, v in item_row.items() if k != ROW_TYPE_FIELD} == row,
    "tag_items must only add the row type",
)

EMPTY_REPORT = {
    "runStartedAt": "2026-09-20T18:00:00Z",
    "sourcesGiven": 3,
    "sourcesRead": 3,
    "sourcesFailed": 0,
    "sourcesSkipped": 0,
    "sourcesSkippedByRobots": 0,
    "newItems": 0,
    "chargedEvents": 4,
    "chargeLimitReached": False,
    "chargeFailures": 0,
}
summary_row = build_summary_row(EMPTY_REPORT)
check(
    "summary_row_is_tagged_summary",
    summary_row[ROW_TYPE_FIELD] == SUMMARY_ROW_TYPE,
    repr(summary_row.get(ROW_TYPE_FIELD)),
)
check(
    "summary_row_has_exactly_the_declared_summary_fields",
    set(summary_row) == {ROW_TYPE_FIELD} | set(SUMMARY_ONLY_FIELDS),
    "row only: %s / declared only: %s"
    % (
        sorted(set(summary_row) - ({ROW_TYPE_FIELD} | set(SUMMARY_ONLY_FIELDS))),
        sorted(({ROW_TYPE_FIELD} | set(SUMMARY_ONLY_FIELDS)) - set(summary_row)),
    ),
)
# The point of the whole change: a run that found nothing still says, inside
# the dataset, how many sources were checked and that zero items were new.
check(
    "summary_row_of_a_run_with_no_new_items_states_the_counts",
    summary_row["sourcesGiven"] == 3
    and summary_row["sourcesRead"] == 3
    and summary_row["newItems"] == 0,
    repr(summary_row),
)
check(
    "summary_message_of_a_run_with_no_new_items_says_so_in_english",
    "No new items" in summary_row["message"]
    and "3 of 3 feed(s) were read" in summary_row["message"],
    repr(summary_row["message"]),
)
check(
    "summary_message_reports_feeds_skipped_by_robots",
    "robots.txt" in build_summary_row({**EMPTY_REPORT, "sourcesRead": 2, "sourcesSkippedByRobots": 1})["message"],
    repr(build_summary_row({**EMPTY_REPORT, "sourcesRead": 2, "sourcesSkippedByRobots": 1})["message"]),
)
check(
    "summary_message_when_every_feed_is_blocked_by_robots_does_not_claim_they_were_read",
    "robots.txt" in build_summary_row(
        {**EMPTY_REPORT, "sourcesRead": 0, "sourcesSkippedByRobots": 3}
    )["message"]
    and "none answered" not in build_summary_row(
        {**EMPTY_REPORT, "sourcesRead": 0, "sourcesSkippedByRobots": 3}
    )["message"],
    repr(build_summary_row({**EMPTY_REPORT, "sourcesRead": 0, "sourcesSkippedByRobots": 3})["message"]),
)

# Everything the dataset can hold: the tagged item row plus the summary row.
all_row_fields = set(item_row) | set(summary_row)

declared = set((dataset.get("fields") or {}).get("properties") or {})
check(
    "dataset_schema_fields_match_the_row",
    declared == all_row_fields,
    "declared only: %s / row only: %s"
    % (sorted(declared - all_row_fields), sorted(all_row_fields - declared)),
)

views = dataset.get("views") or {}
check("dataset_schema_has_views", bool(views), repr(sorted(views)))
for view_name, view in views.items():
    check("view_has_title_" + view_name, bool(view.get("title")), repr(view))
    fields = ((view.get("transformation") or {}).get("fields")) or []
    check("view_lists_fields_" + view_name, bool(fields), repr(view))
    missing = [field for field in fields if field not in all_row_fields]
    check(
        "view_fields_exist_in_output_" + view_name,
        not missing,
        "not written by src/main.py: " + repr(missing),
    )
    # The privacy allow list still rules the item fields: a view may show the
    # seven allowed item fields, the row type and the summary-only fields, and
    # nothing else ever reaches a buyer's screen.
    allowed_in_views = set(ALLOWED_FIELDS) | {ROW_TYPE_FIELD} | set(SUMMARY_ONLY_FIELDS)
    extra = [field for field in fields if field not in allowed_in_views]
    check(
        "view_lists_only_allowed_fields_" + view_name,
        not extra,
        "outside the allow list: " + repr(extra),
    )
    check(
        "view_shows_the_row_type_" + view_name,
        ROW_TYPE_FIELD in fields,
        "a buyer must be able to tell an item row from the summary row",
    )
    display_props = set(((view.get("display") or {}).get("properties") or {}))
    check(
        "view_display_matches_fields_" + view_name,
        display_props <= set(fields),
        "displayed but not selected: " + repr(sorted(display_props - set(fields))),
    )

# ----------------------------------------- extra: input form matches the code
expected_input = {
    "feedUrls": ("array", None),
    "maxSources": ("integer", 20),
    "requestDelaySeconds": ("integer", 2),
    "requestTimeoutSeconds": ("integer", 20),
}
props = inp.get("properties") or {}
check("input_schema_version_1", inp.get("schemaVersion") == 1, repr(inp.get("schemaVersion")))
check("input_requires_feed_urls", inp.get("required") == ["feedUrls"], repr(inp.get("required")))
check("input_fields_are_the_four", set(props) == set(expected_input), repr(sorted(props)))
for field, (ftype, default) in expected_input.items():
    spec = props.get(field) or {}
    check("input_type_" + field, spec.get("type") == ftype, repr(spec.get("type")))
    check("input_has_title_" + field, bool(spec.get("title")), repr(spec))
    check("input_has_description_" + field, bool(spec.get("description")), repr(spec))
    check("input_has_default_" + field, "default" in spec, repr(spec))
    if default is not None:
        check(
            "input_default_%s_is_%s" % (field, default),
            spec.get("default") == default,
            repr(spec.get("default")),
        )
        check(
            "main_default_%s_is_%s" % (field, default),
            '"%s": %s' % (field, default) in main_source
            or '"%s": %s.0' % (field, default) in main_source,
            "src/main.py DEFAULTS disagree with the input form",
        )

check(
    "readme_exists",
    (ROOT / "README.md").is_file() and (ROOT / "README.md").stat().st_size > 800,
    "README.md missing or too short for the Store page",
)
check(
    "requirements_has_no_feedparser",
    "feedparser" not in (ROOT / "requirements.txt").read_text(encoding="utf-8"),
    "the parser must stay on the standard library",
)

TOTAL = PASSED + len(FAILURES)
if FAILURES:
    print("\nfailed: " + ", ".join(FAILURES))
print("%d/%d passed" % (PASSED, TOTAL))
sys.exit(1 if FAILURES else 0)
