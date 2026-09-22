"""What this Actor already saw, per feed.

The state is one record per source in the Actor's key-value store: the list of
`item_id` values already returned for that feed. On the next run, an item whose
id is in the list is not returned again.

The decision itself ("which of these are new?") is a pure function, so the
tests can exercise it without network and without the Apify platform. Only
`load_seen` and `save_seen` touch the store, and they receive the store as an
argument instead of importing the SDK.

First run of a feed: there is no record yet, so every item the feed currently
publishes is new. That run is the baseline.
"""

from __future__ import annotations

import hashlib
import re

# How many ids we keep per source. Feeds usually publish 10 to 100 items, so
# this is many runs of history; the oldest ids fall off first.
MAX_SEEN_IDS_PER_SOURCE = 5000

STATE_KEY_PREFIX = "SEEN--"

_UNSAFE_KEY_CHARS = re.compile(r"[^a-zA-Z0-9!\-_.'()]")


def state_key(source_url: str) -> str:
    """Key-value store key for one feed.

    The readable part helps a human browsing the store; the hash makes the key
    unique and safe, because feed URLs contain characters a key may not have.
    """
    source_url = (source_url or "").strip()
    digest = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:16]
    readable = _UNSAFE_KEY_CHARS.sub("-", source_url)[-40:].strip("-")
    return STATE_KEY_PREFIX + (readable + "-" if readable else "") + digest


def normalize_seen(record) -> list[str]:
    """Accept whatever the store gives back and return a list of ids.

    Handles the record this module writes ({"ids": [...]}), a bare list, and
    a missing or damaged record (empty list).
    """
    if record is None:
        return []
    if isinstance(record, dict):
        record = record.get("ids") or record.get("seen") or []
    if isinstance(record, (str, bytes)):
        return []
    try:
        values = list(record)
    except TypeError:
        return []
    ids: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            ids.append(text)
    return ids


def select_new(items: list[dict], seen) -> tuple[list[dict], list[str]]:
    """Split parsed items into the ones we never returned before.

    `seen` is anything `normalize_seen` accepts. Returns the new items in feed
    order and their ids. An id repeated inside the same feed is returned once.
    """
    known = set(normalize_seen(seen))
    new_items: list[dict] = []
    new_ids: list[str] = []
    for item in items or []:
        item_id = str((item or {}).get("item_id") or "").strip()
        if not item_id or item_id in known:
            continue
        known.add(item_id)
        new_items.append(item)
        new_ids.append(item_id)
    return new_items, new_ids


def merge_seen(seen, new_ids, max_ids: int = MAX_SEEN_IDS_PER_SOURCE) -> list[str]:
    """Old ids plus the new ones, without duplicates, capped at `max_ids`.

    The most recent ids are kept: the cap drops from the front of the list.
    """
    merged: list[str] = []
    known: set[str] = set()
    for item_id in normalize_seen(seen) + normalize_seen(new_ids):
        if item_id in known:
            continue
        known.add(item_id)
        merged.append(item_id)
    if max_ids and len(merged) > max_ids:
        merged = merged[-max_ids:]
    return merged


def build_record(source_url: str, seen_ids: list[str], last_run_at: str = "") -> dict:
    """The record shape written to the store."""
    return {
        "source_url": source_url,
        "ids": list(seen_ids),
        "count": len(seen_ids),
        "last_run_at": last_run_at,
    }


async def load_seen(store, source_url: str) -> list[str]:
    """Read the ids already returned for one feed. `store` is a KV store."""
    record = await store.get_value(state_key(source_url))
    return normalize_seen(record)


async def save_seen(store, source_url: str, seen_ids: list[str], last_run_at: str = "") -> None:
    """Write the ids back for one feed."""
    await store.set_value(
        state_key(source_url), build_record(source_url, seen_ids, last_run_at)
    )
