# RSS Feed Monitor: New Items Only, From Changelog, Release and Status Feeds

**Run it on the Apify Store: https://apify.com/lotebo-lab/feed-change-watcher**

This repository holds the source code of the RSS feed monitor published at that link. The Actor
itself runs on the Apify platform, so there is nothing to install and nothing to host.

## What it does, who runs it, what it does not do

- **What it does:** give it a list of RSS or Atom feed URLs and every run returns **only the new
  items** — the entries it has never handed you before, one row per item — because it remembers
  the item ids it already returned for each feed.
- **Who runs it:** people who follow a dozen changelog, release, status or supplier feeds on a
  schedule and do not want to reread the same entries every week to work out which ones are new.
- **What it does not do:** it does not watch pages that have no feed, does not open the item link
  to fetch the full article, does not send e-mail or Slack, and does not run itself on a schedule.
  The full list is under [What this Actor does not do](#what-this-actor-does-not-do).

The first run on a feed is the baseline and returns everything the feed carries at that moment.
The value is in the run after that, which is usually a short list and sometimes an empty one.

## Run it on a schedule (this is the point)

What this Actor sells is the difference between two runs, so one run on its own is half of it.
**The first run on a feed returns the feed as it stands today**: every item the feed carries at
that moment, marked `first_run: true` in the `REPORT`. It is the run after that which does the job
you came for, returning only what appeared since the previous run.

So set it to run again by itself. On the Apify platform you schedule the Actor directly, with no
task to create first: in Schedules, "Click on the Add dropdown and select whether you want to
schedule an Actor or task", pick this Actor, and write the interval as a cron expression with six
positions. There is one prerequisite: "To schedule an Actor, you need to have run it at least once
before". So press Start once, let that run be the baseline, then schedule it. The platform's floor
is that "The minimum interval between runs is 10 seconds"; how often you actually poll is your call
and your publisher's. The steps are in the Apify documentation:
https://docs.apify.com/platform/schedules

Schedules are not a paid extra. The Apify account limits page lists "Maximum number of schedules
per user: 100", and the number is the same in every plan column, including Free:
https://docs.apify.com/account/limits

One thing to know about the memory. The list of item ids already returned is not stored inside the
Actor: it is a named key-value store record in **your own account**, keyed by feed URL. It survives
between runs and it is yours to read or delete. If that store is deleted, or if you change the feed
URL, the next run is a first run again for that feed and hands you everything it carries.

## Who runs it, and when

- **Engineering and integration teams** watching vendor changelogs and release feeds, to catch the
  version that breaks an integration;
- **operations and support**, keeping a record of incidents on the status feeds of the services
  they depend on;
- **procurement, legal and policy people**, following supplier blogs, standards bodies and
  regulators whose posts change how they work;
- **anyone who has to be able to say what changed last week** and does not want to read every feed
  daily.

The usual moment is a weekly or nightly schedule on the Apify platform: the run writes the new
items to a dataset, and you connect that dataset to whatever you already read.

## What comes out, field by field

The dataset has two kinds of row, told apart by the `rowType` field: `feed-item` for a new item,
and `summary` for the single run summary row that every successful run writes, new items or none.
Filter on `rowType` to keep only the items.

One `feed-item` row per new item. Besides `rowType`, an item row has exactly these seven fields,
and nothing else; the same names are declared in
[`.actor/dataset_schema.json`](.actor/dataset_schema.json) and checked by `tests/test_schemas.py`.

| field | type | what it holds |
|---|---|---|
| `source_url` | string | the feed this item came from, as given in the input |
| `source_title` | string | the title the feed publishes for itself, empty when it publishes none |
| `item_id` | string | the identity of the item: the feed's `guid` or `id`, the link when there is none, and a `sha1:` hash of feed URL, title and date when there is neither. This is the value the Actor remembers between runs |
| `item_title` | string | the item title, as plain text |
| `item_link` | string | the item URL on the publisher's site, empty when the feed gives none |
| `item_published_at` | string | the publication date in UTC, `YYYY-MM-DDTHH:MM:SSZ`. A date the parser cannot read is kept as the feed wrote it |
| `item_summary` | string | the short description the feed itself publishes (`description` in RSS, `summary` in Atom), as plain text, cut at 600 characters (`MAX_SUMMARY_CHARS` in `src/feed_parser.py`) |

The `summary` row carries the counts of the run: `runStartedAt`, `finishedAt`, `sourcesGiven`,
`sourcesRead`, `sourcesFailed`, `sourcesSkipped`, `sourcesSkippedByRobots`, `newItems`,
`chargedEvents`, `chargeLimitReached`, `chargeFailures` and a one-sentence `message`. It exists so
a quiet run does not look like a crash: an empty dataset and a broken Actor read the same, a
dataset with one summary row saying "no new items" does not.

Every run also writes a `REPORT` record in the key-value store with the same counts plus
`robotsFetches` and a `perSource` list (`source_url`, `source_title`, `status`, `error`,
`items_in_feed`, `new_items`, `first_run`).

`items_in_feed` next to `new_items` is the part that matters when the answer is "nothing new": it
shows the feed was really read and was really quiet, instead of leaving you to guess whether the
run failed.

### When a feed is not read

`status` in `perSource` is one of four values, and the last three mean this run says nothing about
whether that feed changed:

| status | what happened | charged |
|---|---|---|
| `ok` | fetched, parsed and compared | yes, one `source-checked` |
| `failed` | the reason is in `error` | no |
| `skipped_by_robots` | the host's `robots.txt` disallows this Actor's user agent, so nothing was fetched | no |
| `skipped` | the run had already hit its pay-per-event charge limit | no |

A feed is `failed` when any of these happens: the host answers an HTTP error status (4xx or 5xx),
the request times out or the connection fails, the body is empty, the body is not valid XML, or the
XML root element is not a feed. The failure is written to the log and to the report, the other
feeds in the list keep going, and nothing is charged for it.

One limit worth knowing: **the Actor does not check the `Content-Type` header.** It tries to parse
whatever the URL returns. A page that is valid XML but is not a feed is rejected by the
root-element check, and an HTML page is normally rejected as invalid XML, but the decision is made
by the parser, not by the content type the server declares.

### Real output

The row below comes from an end-to-end run of `src/main.py` recorded on 2026-09-20: the second run
of a pair, with the feeds served by a **local test server on `127.0.0.1`** from `tests/fixtures/run2`,
not by a publisher. That is why the URLs below say `127.0.0.1:8099`. Between the two runs one post
was added to the blog feed and nothing changed in the status feed. The blog feed carried four items
at that moment, and this is the only row the run returned:

```json
{"item_id": "post-release-2-2", "item_link": "http://127.0.0.1:8099/posts/release-2-2", "item_published_at": "2026-09-20T09:15:00Z", "item_summary": "Exported timestamps are now written in UTC instead of the browser timezone.", "item_title": "Release 2.2 fixes the timezone of the export", "source_title": "Example Machines Blog", "source_url": "http://127.0.0.1:8099/blog.xml"}
```

The `REPORT` record of that same run:

```json
{
  "runStartedAt": "2026-09-20T06:00:00Z",
  "sourcesGiven": 2,
  "sourcesRead": 2,
  "sourcesFailed": 0,
  "newItems": 1,
  "perSource": [
    {"source_url": "http://127.0.0.1:8099/blog.xml", "source_title": "Example Machines Blog", "status": "ok", "error": "", "items_in_feed": 4, "new_items": 1, "first_run": false},
    {"source_url": "http://127.0.0.1:8099/status.atom", "source_title": "Example Machines Status", "status": "ok", "error": "", "items_in_feed": 2, "new_items": 0, "first_run": false}
  ],
  "chargedEvents": 0,
  "chargeLimitReached": false,
  "chargeFailures": 0
}
```

The first run of the same pair returned five rows: everything the two feeds carried. That is the
baseline run.

Both runs were recorded before the `rowType` field and the summary row were added, so neither
appears in the snippets above. The same pair of runs today returns the same item rows, each with
`"rowType": "feed-item"`, plus one `summary` row per run. The log files of both runs are kept in
our build workspace and are **not** published in this repository: `logs/` is in `.gitignore`.

## Input

The example below is the input this Actor is prefilled with, copied from the `prefill` and
`default` values in [`.actor/input_schema.json`](.actor/input_schema.json), so you can press Start
and read a real result before pointing it at your own feeds.

```json
{
  "feedUrls": ["https://news.ycombinator.com/rss"],
  "maxSources": 20,
  "requestDelaySeconds": 2,
  "requestTimeoutSeconds": 20
}
```

| field | type | required | default | range |
|---|---|---|---|---|
| `feedUrls` | array of feed URLs | yes | `[]` | RSS 2.0, RSS 1.0 or Atom |
| `maxSources` | integer | no | 20 | 1 to 500 feeds read in this run |
| `requestDelaySeconds` | integer | no | 2 | 0 to 60 seconds between two feed requests |
| `requestTimeoutSeconds` | integer | no | 20 | 3 to 120 seconds before a feed is reported as failed |

Notes on the input, as the code handles it:

- a URL without `http://` or `https://` gets `https://` added, and the same URL given twice is read
  once;
- entries may also be objects with a `url` key, which is what the Apify "Link list" style input
  produces;
- `maxSources` cuts the list for this run: feeds beyond that number are simply not read, and the
  run says so in the log;
- the first request does not wait; the delay applies between requests.

The list of item ids already returned is kept per feed URL in a named key-value store that belongs
to your account and survives between runs. Up to 5000 ids are kept per feed, oldest dropped first
(`MAX_SEEN_IDS_PER_SOURCE` in `src/state.py`). Change a feed URL and the next run treats it as a
first run and returns everything again.

## What this Actor does not do

- **It does not open the item link**, so it never returns the full article text. You get the
  summary the feed publishes, and a feed that publishes only a title gives you only a title.
- **It does not watch pages that have no feed**, and it does not build a feed out of an HTML page.
- **It does not detect an edit** to an item it already sent. A changed item is not a new item here.
- **It does not send e-mail, Slack or any other notification.** It writes a dataset, and you
  connect that to whatever you already use.
- **It does not filter by keyword, tag or author**, and it does not rank, translate or summarise
  anything.
- **It does not run on a schedule by itself.** You set the schedule on the Apify platform.
- **It does not deduplicate across feeds**: the same post published in two feeds comes back once
  per feed.
- **It does not get past a login, a paywall or a captcha**, and it does not run JavaScript.
- **It does not return who wrote an item.** The parser has an allow list of seven fields
  (`ALLOWED_FIELDS` in `src/feed_parser.py`) and nothing else reaches the dataset: author elements,
  `dc:creator`, `managingEditor`, `webMaster`, `contributor`, `name`, `uri` and `email` are never
  read, and any value that still contains an "@" after cleaning is dropped.

## Manners, `robots.txt` and your responsibility

- **A feed is fetched only when you list it.** This Actor makes one HTTP GET per feed URL in your
  input, follows redirects, reads at most 10 MB (`MAX_RESPONSE_BYTES` in `src/main.py`), and never
  crawls, follows links or discovers feeds on its own.
- **It identifies itself** on every request as
  `FeedChangeWatcher/0.1 (Apify Actor; +https://apify.com/lotebo-lab/feed-change-watcher)`, and
  keeps `requestDelaySeconds` between requests so the servers you follow are not hit hard.
- **It reads `robots.txt` before it reads a feed.** One `robots.txt` per host, fetched once per run
  and reused, asked with the same user agent the feed request sends. An explicit `Disallow` that
  matches that user agent skips the feed: nothing is fetched, nothing is charged, and the feed
  appears as `skipped_by_robots` in the report. A `robots.txt` that cannot be read, because of a
  404, a server error or a timeout, states no restriction, so the feed is fetched and the log says
  so.
- **`robots.txt` is not the whole answer.** You are still responsible for the terms of each site
  you list: a feed can be open in `robots.txt` and closed to automated clients by the site's terms
  or by your agreement with it.
- **You are responsible for having the right to read every feed you list.** Check the terms of the
  sites that publish them, and check whether your own agreement with them allows automated access.

## Price

Pay per event, two events, exactly as declared in [`.actor/actor.json`](.actor/actor.json):

| event | price | when it is charged |
|---|---|---|
| `source-checked` | US$ 0.05 | once per feed that was fetched, parsed and compared. A feed that failed to load, that `robots.txt` disallowed, or that was skipped after the charge limit, is not charged |
| `change-report` | US$ 0.50 | once per run, and only when the run found at least one new item |

**A run that finds nothing new costs only the feeds it read.** Watching a changelog means most runs
have nothing to report, and you are not charged for the report on those runs: ten feeds with no new
item cost ten `source-checked` events and nothing else. Ten feeds with at least one new item cost
ten `source-checked` events plus one `change-report`. The summary row is written to the dataset
either way, free. The first run of a feed is charged like any other read, and it returns everything
the feed carries. Apify charges its own Actor start event and the platform usage of the run on top
of this; those are not set by this Actor.

## How this was checked

**The offline test suite.** Four files, run with the plain interpreter and no network. Last run on
2026-09-23, from the root of this repository:

| command | checks | result |
|---|---|---|
| `python tests/test_feeds.py` | 70 | all passed |
| `python tests/test_robots.py` | 26 | all passed |
| `python tests/test_schemas.py` | 104 | all passed |
| `python tests/test_charging_e2e.py` | 17 | all passed |

What they cover: `test_feeds.py` parses RSS 2.0, RSS 1.0 and Atom documents and checks the
seven-field allow list, the date normalisation and the "which of these are new?" decision;
`test_robots.py` drives the `robots.txt` gate with an injected fetcher, including a malformed file,
a 404 and a connection error, and checks that none of them blocks a feed; `test_schemas.py`
compares `.actor/input_schema.json`, `.actor/dataset_schema.json` and `.actor/actor.json` against
what the code actually does, field by field and default by default; `test_charging_e2e.py` runs the
whole flow twice over a fixture and checks the bill of each run.

**Against real feeds, on the Apify platform.** These are runs on Apify against public feeds, not
local fixtures:

- run `Y8drvcQt2D4h6a5EF`, the second run in a row on `https://news.ycombinator.com/rss`:
  `30 item(s) in the feed, 0 new`, and one summary row saying `No new items: 1 of 1 feed(s) were
  read and every item they list had already been reported by an earlier run`. That is the whole
  point of the Actor, measured;
- run `EsSLbti3Ge74zFGNH` on the same feed found 10 new items and wrote 11 rows;
- runs `OljBI69qNps2VoaRX` and `nXD1GPHaPMMmgWqDq` both returned `newItems: 0` with
  `chargedEvents: 1`, which is the proof that a quiet run pays for the feed it read and **not** for
  the change report;
- run `Ve4KLyVc0wMGrxlLk` read 2 feeds with 0 failures and pushed 850 new items in one pass.

**What has never been tested, stated plainly:**

- **No paid bill has ever come out of this Actor.** Charging has been exercised on the platform and
  the event counts above were read back from the run's own dataset, but no invoice has ever been
  produced by it. A charge that fails or times out is a warning in the log, never the end of the
  run.
- **No publisher has ever refused us in `robots.txt`.** The gate is covered offline, with an
  injected fetcher, and no real feed we have run against disallowed this Actor's user agent. So
  `skipped_by_robots` is proven in test, not in the wild.
- **No long watch.** The longest sequence we measured on one feed is a handful of consecutive runs
  inside a single day. This Actor has never watched a feed for weeks, so the 5000-id ceiling per
  feed and the drop of the oldest ids have never been reached by a real run.
- **Not every feed dialect in the wild.** The parser was exercised against the documents in
  `tests/` and the public feeds above. A feed it cannot read is reported as `failed`, with the
  reason, and never silently dropped, but we cannot claim it parses everything that calls itself a
  feed.
- **No `Content-Type` check**, as stated above: the decision is made by the XML parser, not by the
  header the server declares.

## For developers

```
.actor/              actor.json, input, output and dataset schemas
src/main.py          the run: reads input, fetches feeds, writes rows, charges events
src/feed_parser.py   RSS 2.0, RSS 1.0 and Atom parsing, and the seven-field allow list
src/robots.py        the robots.txt gate, one fetch per host per run
src/state.py         the ids already returned, per feed, in your own key-value store
src/summary_row.py   the run summary row and its message
tests/               the offline suite, four files, no network
```

The Actor is written in Python. Parsing uses the Python standard library (`xml.etree`), not
`feedparser`, so there is no extra dependency to break, and a failing feed never stops the run: it
is reported and the other feeds keep going. This Actor was built with the help of AI, and every
module has an offline automated test in `tests/`.

## Ready-made example runs

Each page below is a published task of this Actor: a ready-made run that shows the input, the
fields that come back, and a Run button. The same page is served as Markdown by adding `.md` to the
URL.

- [Track new security advisories across vendor feeds](https://apify.com/lotebo-lab/feed-change-watcher/examples/watch-vendor-security-advisory-feeds-for-new-entries): polls every vendor advisory feed you follow and returns only the entries published since your last run, normalised into one format.
- [See only new tender notices from many agency feeds](https://apify.com/lotebo-lab/feed-change-watcher/examples/read-many-tender-feeds-and-see-only-new-notices): reads the feed of every agency you follow and returns the notices that appeared since the previous run, in one table.
- [Monitor regulator news feeds without rereading old items](https://apify.com/lotebo-lab/feed-change-watcher/examples/monitor-regulator-news-feeds-without-rereading-old-items): checks the news feed of each agency you watch on a polite delay and returns only the items published since the last run.
- [How do I get only new items from an RSS feed?](https://apify.com/lotebo-lab/feed-change-watcher/examples/get-only-new-items-from-an-rss-feed): give one or more RSS or Atom feeds and each run returns only the items no earlier run returned, normalised into one format, plus how many items each source had.
- [What is new across several release feeds today?](https://apify.com/lotebo-lab/feed-change-watcher/examples/watch-several-release-feeds-in-one-run): reads a list of feeds in one run and returns only the items that no earlier run reported, grouped by the feed they came from.
- [Send only new feed items to n8n or Make, never duplicates](https://apify.com/lotebo-lab/feed-change-watcher/examples/send-only-new-feed-items-to-n8n-or-make): returns only the items published since the last run, as flat JSON an automation step consumes without extra parsing.
- [Watch release feeds and get only new versions](https://apify.com/lotebo-lab/feed-change-watcher/examples/watch-release-feeds-for-new-versions): one row per version you have not seen before, grouped by the feed it came from.
- [Which of my feeds stopped working this run?](https://apify.com/lotebo-lab/feed-change-watcher/examples/check-which-of-my-feeds-stopped-working): reads the whole list and reports how many feeds answered, how many failed and how many robots.txt refused to serve.

---

**Actor page on the Apify Store: https://apify.com/lotebo-lab/feed-change-watcher**
