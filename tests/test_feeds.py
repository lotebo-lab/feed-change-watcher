"""Tests for the feed parser and for the "what is new" state, with no network.

pytest is not installed in the lab sandbox and `pip install` has no network
there, so this file is a runner of its own, in plain Python. Run it from the
Actor directory:

    ../../.venv/bin/python tests/test_feeds.py

The two feeds below are written out in full inside this file, so the test never
touches the internet. They carry on purpose everything the Actor must throw
away: <author>, <dc:creator>, <managingEditor>, <webMaster>, an <email>, an
e-mail address inside the description, and a full <content> body.

What it proves:

1. RSS 2.0 and Atom are both parsed, with title, link, id, date and summary;
2. no author name and no e-mail address survives into the output;
3. an item already seen is not returned again, and a fresh item is;
4. every row has exactly the seven allowed fields and nothing else.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.feed_parser import (  # noqa: E402
    ALLOWED_FIELDS,
    FeedParseError,
    enforce_allowed_fields,
    normalize_date,
    parse_feed,
    scrub,
)
from src.state import (  # noqa: E402
    merge_seen,
    normalize_seen,
    select_new,
    state_key,
)

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


RSS_URL = "https://example.com/feed.xml"
RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Example Blog</title>
    <link>https://example.com/</link>
    <description>Notes from the Example team</description>
    <managingEditor>editor@example.com (The Editor)</managingEditor>
    <webMaster>webmaster@example.com</webMaster>
    <item>
      <title>Release 2.1 is out</title>
      <link>https://example.com/posts/2</link>
      <guid isPermaLink="false">example-post-2</guid>
      <pubDate>Sat, 19 Sep 2026 10:30:00 +0000</pubDate>
      <description>&lt;p&gt;The new release adds the export button. Write to support@example.com for help.&lt;/p&gt;</description>
      <author>ana@example.com (Ana Dev)</author>
      <dc:creator>Ana Dev</dc:creator>
      <content:encoded>Full article body that must never be copied into our dataset.</content:encoded>
    </item>
    <item>
      <title>Release 2.0 is out</title>
      <link>https://example.com/posts/1</link>
      <guid isPermaLink="false">example-post-1</guid>
      <pubDate>Mon, 01 Sep 2026 08:00:00 +0000</pubDate>
      <description>The first release.</description>
      <dc:creator>Ana Dev</dc:creator>
    </item>
  </channel>
</rss>
"""

ATOM_URL = "https://example.org/atom.xml"
ATOM_XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom Feed</title>
  <link rel="alternate" type="text/html" href="https://example.org/"/>
  <updated>2026-09-18T09:00:00Z</updated>
  <author>
    <name>Bruno Writer</name>
    <email>bruno@example.org</email>
  </author>
  <entry>
    <title>Atom entry one</title>
    <link rel="alternate" type="text/html" href="https://example.org/entries/1"/>
    <link rel="self" href="https://example.org/entries/1.atom"/>
    <id>tag:example.org,2026:entry-1</id>
    <published>2026-09-18T08:00:00Z</published>
    <updated>2026-09-18T09:00:00Z</updated>
    <summary>Short summary for entry one.</summary>
    <author>
      <name>Bruno Writer</name>
      <email>bruno@example.org</email>
    </author>
    <content type="html">Full article body that must never be copied into our dataset.</content>
  </entry>
  <entry>
    <title>Atom entry two</title>
    <link rel="alternate" type="text/html" href="https://example.org/entries/2"/>
    <id>tag:example.org,2026:entry-2</id>
    <published>2026-09-17T12:00:00Z</published>
    <summary>Short summary for entry two.</summary>
  </entry>
</feed>
"""

MINIMAL_RSS = """<rss version="2.0"><channel><title>No ids here</title>
  <item><title>An item with no guid and no link</title>
  <pubDate>Tue, 15 Sep 2026 07:00:00 +0000</pubDate></item>
</channel></rss>"""

FORBIDDEN_SUBSTRINGS = (
    "@",
    "Ana Dev",
    "Bruno Writer",
    "The Editor",
    "Full article body",
)

# --------------------------------------------------------------- 1. RSS 2.0
rss = parse_feed(RSS_URL, RSS_XML)
check("rss_source_title", rss["source_title"] == "Example Blog", repr(rss["source_title"]))
check("rss_source_url", rss["source_url"] == RSS_URL, repr(rss["source_url"]))
check("rss_item_count_is_two", len(rss["items"]) == 2, str(len(rss["items"])))

rss_first = rss["items"][0] if rss["items"] else {}
check(
    "rss_item_title",
    rss_first.get("item_title") == "Release 2.1 is out",
    repr(rss_first.get("item_title")),
)
check(
    "rss_item_link",
    rss_first.get("item_link") == "https://example.com/posts/2",
    repr(rss_first.get("item_link")),
)
check(
    "rss_item_id_from_guid",
    rss_first.get("item_id") == "example-post-2",
    repr(rss_first.get("item_id")),
)
check(
    "rss_pubdate_normalized_to_utc",
    rss_first.get("item_published_at") == "2026-09-19T10:30:00Z",
    repr(rss_first.get("item_published_at")),
)
check(
    "rss_summary_is_plain_text_from_description",
    rss_first.get("item_summary", "").startswith("The new release adds the export button."),
    repr(rss_first.get("item_summary")),
)

# ------------------------------------------------------------------ 2. Atom
atom = parse_feed(ATOM_URL, ATOM_XML)
check("atom_source_title", atom["source_title"] == "Example Atom Feed", repr(atom["source_title"]))
check("atom_item_count_is_two", len(atom["items"]) == 2, str(len(atom["items"])))

atom_first = atom["items"][0] if atom["items"] else {}
check(
    "atom_item_title",
    atom_first.get("item_title") == "Atom entry one",
    repr(atom_first.get("item_title")),
)
check(
    "atom_item_link_prefers_alternate",
    atom_first.get("item_link") == "https://example.org/entries/1",
    repr(atom_first.get("item_link")),
)
check(
    "atom_item_id_from_id_tag",
    atom_first.get("item_id") == "tag:example.org,2026:entry-1",
    repr(atom_first.get("item_id")),
)
check(
    "atom_published_preferred_over_updated",
    atom_first.get("item_published_at") == "2026-09-18T08:00:00Z",
    repr(atom_first.get("item_published_at")),
)
check(
    "atom_summary",
    atom_first.get("item_summary") == "Short summary for entry one.",
    repr(atom_first.get("item_summary")),
)

check("parse_accepts_bytes", len(parse_feed(RSS_URL, RSS_XML.encode("utf-8"))["items"]) == 2)

# -------------------------------------- 3. no people, no e-mail, no full body
all_items = rss["items"] + atom["items"]
for index, item in enumerate(all_items):
    blob = " || ".join(str(value) for value in item.values())
    for forbidden in FORBIDDEN_SUBSTRINGS:
        check(
            "item_%d_has_no_%s" % (index, forbidden.replace(" ", "_").replace("@", "at_sign")),
            forbidden not in blob,
            blob,
        )

check("scrub_drops_a_bare_email", scrub("ana@example.com") == "", repr(scrub("ana@example.com")))
check(
    "scrub_keeps_the_rest_of_the_sentence",
    scrub("Write to support@example.com for help.") == "Write to for help.",
    repr(scrub("Write to support@example.com for help.")),
)
check("scrub_strips_html", scrub("<p>Hi <b>there</b></p>") == "Hi there", repr(scrub("<p>Hi <b>there</b></p>")))
check(
    "scrub_truncates",
    len(scrub("x " * 800)) <= 603 and scrub("x " * 800).endswith("..."),
    str(len(scrub("x " * 800))),
)
check(
    "enforce_allowed_fields_drops_extras_and_at_signs",
    enforce_allowed_fields({"item_title": "ok", "author": "ana@example.com", "item_link": "a@b"})
    == {
        "source_url": "",
        "source_title": "",
        "item_id": "",
        "item_title": "ok",
        "item_link": "",
        "item_published_at": "",
        "item_summary": "",
    },
    repr(enforce_allowed_fields({"item_title": "ok", "author": "ana@example.com", "item_link": "a@b"})),
)

# ------------------------------------------- 4. exactly the seven fields
for index, item in enumerate(all_items):
    check(
        "item_%d_has_exactly_the_seven_fields" % index,
        tuple(item.keys()) == ALLOWED_FIELDS,
        repr(sorted(item.keys())),
    )
check("allowed_fields_are_seven", len(ALLOWED_FIELDS) == 7, str(len(ALLOWED_FIELDS)))

# ----------------------------------------------------- 5. ids and odd feeds
minimal = parse_feed("https://example.net/rss", MINIMAL_RSS)
check("minimal_feed_has_one_item", len(minimal["items"]) == 1, str(len(minimal["items"])))
check(
    "id_falls_back_to_a_hash",
    minimal["items"][0]["item_id"].startswith("sha1:"),
    repr(minimal["items"][0]["item_id"]),
)
check(
    "hash_id_is_stable",
    parse_feed("https://example.net/rss", MINIMAL_RSS)["items"][0]["item_id"]
    == minimal["items"][0]["item_id"],
)
check("normalize_date_passes_through_unparsable", normalize_date("someday") == "someday")
check("normalize_date_empty", normalize_date(None) == "")

for bad_name, bad_body in (
    ("empty", ""),
    ("not_xml", "this is not a feed"),
    ("html_page", "<html><body><p>not a feed</p></body></html>"),
):
    try:
        parse_feed("https://example.net/x", bad_body)
        check("rejects_" + bad_name, False, "no error raised")
    except FeedParseError:
        check("rejects_" + bad_name, True)
    except Exception as exc:  # noqa: BLE001
        check("rejects_" + bad_name, False, repr(exc))

# -------------------------------------------------- 6. what is new, per feed
first_run_new, first_run_ids = select_new(rss["items"], [])
check("first_run_returns_everything", len(first_run_new) == 2, str(len(first_run_new)))
check(
    "first_run_ids",
    first_run_ids == ["example-post-2", "example-post-1"],
    repr(first_run_ids),
)

seen = merge_seen([], first_run_ids)
second_run_new, second_run_ids = select_new(rss["items"], seen)
check("seen_items_do_not_come_back", second_run_new == [] and second_run_ids == [], repr(second_run_new))

fresh_item = dict(rss["items"][0])
fresh_item["item_id"] = "example-post-3"
fresh_item["item_title"] = "Release 2.2 is out"
third_run_new, third_run_ids = select_new([fresh_item] + rss["items"], seen)
check("only_the_new_item_comes_back", len(third_run_new) == 1, str(len(third_run_new)))
check("new_item_is_the_right_one", third_run_ids == ["example-post-3"], repr(third_run_ids))
check(
    "new_item_keeps_the_seven_fields",
    tuple(third_run_new[0].keys()) == ALLOWED_FIELDS,
    repr(sorted(third_run_new[0].keys())),
)

check(
    "state_is_kept_per_source",
    select_new(atom["items"], merge_seen([], first_run_ids))[1]
    == ["tag:example.org,2026:entry-1", "tag:example.org,2026:entry-2"],
    "ids of one feed must not hide the items of another",
)

duplicated = select_new(rss["items"] + rss["items"], [])[0]
check("duplicate_ids_inside_one_feed_return_once", len(duplicated) == 2, str(len(duplicated)))

check("merge_seen_has_no_duplicates", merge_seen(["a", "b"], ["b", "c"]) == ["a", "b", "c"])
check("merge_seen_caps_and_keeps_the_newest", merge_seen(["a", "b", "c"], ["d"], 2) == ["c", "d"])
check("normalize_seen_reads_the_record", normalize_seen({"ids": ["a", "b"]}) == ["a", "b"])
check("normalize_seen_reads_a_bare_list", normalize_seen(["a"]) == ["a"])
check("normalize_seen_survives_garbage", normalize_seen("a,b") == [] and normalize_seen(None) == [])

key_rss = state_key(RSS_URL)
key_atom = state_key(ATOM_URL)
check("state_key_is_stable", key_rss == state_key(RSS_URL), key_rss)
check("state_key_differs_per_source", key_rss != key_atom, key_rss + " " + key_atom)
check(
    "state_key_is_store_safe",
    all(character.isalnum() or character in "!-_.'()" for character in key_rss),
    key_rss,
)

TOTAL = PASSED + len(FAILURES)
if FAILURES:
    print("\nfailed: " + ", ".join(FAILURES))
print("%d/%d passed" % (PASSED, TOTAL))
sys.exit(1 if FAILURES else 0)
