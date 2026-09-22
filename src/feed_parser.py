"""RSS 2.0 and Atom parser, standard library only.

`feedparser` is not installed in the lab sandbox and `pip install` has no
network there, so parsing is done with `xml.etree.ElementTree`, which ships
with Python.

Two rules shape this file:

1. **Allow list of output fields.** A parsed item carries exactly the seven
   fields in `ALLOWED_FIELDS` and nothing else. Whatever else the feed carries
   is dropped before it can reach the dataset.
2. **No people.** `author`, `dc:creator`, `managingEditor`, `webMaster` and
   friends are never read, and any value that still contains an "@" after
   token cleaning is discarded. This is the same hard rule the TED Actor uses:
   we publish what an item is about, never who wrote it or how to mail them.

The summary is the short text the feed itself publishes (`description` in RSS,
`summary` in Atom). Full article bodies (`content:encoded`, `atom:content`) are
never read.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

# The only fields that may leave this module.
ALLOWED_FIELDS = (
    "source_url",
    "source_title",
    "item_id",
    "item_title",
    "item_link",
    "item_published_at",
    "item_summary",
)

# Element names (lower case, namespace stripped) that this parser must never
# read, because they carry a person or a mailbox.
DENIED_TAGS = frozenset(
    {
        "author",
        "creator",
        "managingeditor",
        "webmaster",
        "contributor",
        "owner",
        "email",
        "name",
        "uri",
    }
)

ITEM_TAGS = frozenset({"item", "entry"})

TITLE_TAGS = ("title",)
LINK_TAGS = ("link",)
ID_TAGS = ("guid", "id")
DATE_TAGS = ("pubdate", "published", "updated", "issued", "date", "modified")
SUMMARY_TAGS = ("description", "summary", "subtitle")

MAX_SUMMARY_CHARS = 600
MAX_TITLE_CHARS = 300
MAX_RAW_DATE_CHARS = 64

_XML_DECLARATION = re.compile(r"(?s)^\s*<\?xml.*?\?>")
_SCRIPT_OR_STYLE = re.compile(r"(?is)<(script|style)\b.*?</\1\s*>")
_TAG = re.compile(r"(?s)<[^>]*>")
_WHITESPACE = re.compile(r"\s+")


class FeedParseError(ValueError):
    """The bytes we received are not a feed we can read."""


def local_name(tag: str) -> str:
    """Element tag without the XML namespace, lower case."""
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        tag = tag.rsplit("}", 1)[1]
    if ":" in tag:
        tag = tag.rsplit(":", 1)[1]
    return tag.strip().lower()


def scrub(value: str | None, max_chars: int = MAX_SUMMARY_CHARS) -> str:
    """Plain text, with anything that looks like a mailbox removed.

    Tokens containing "@" are dropped one by one; if an "@" survives anywhere,
    the whole value is discarded. A value can never leave this function with
    an "@" in it.
    """
    if not value:
        return ""
    text = _SCRIPT_OR_STYLE.sub(" ", str(value))
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _TAG.sub(" ", text)  # entities may have hidden a tag
    text = text.replace(" ", " ")
    tokens = [token for token in text.split() if "@" not in token]
    text = _WHITESPACE.sub(" ", " ".join(tokens)).strip()
    if "@" in text:  # belt and braces: never let one through
        return ""
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text


def normalize_date(raw: str | None) -> str:
    """RFC 822 (RSS) or ISO 8601 (Atom) in, UTC ISO 8601 out.

    A date we cannot parse is kept as the feed wrote it, trimmed, so the user
    still sees something instead of a blank.
    """
    if not raw:
        return ""
    raw = raw.strip()
    if not raw:
        return ""
    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(raw)
    except Exception:  # noqa: BLE001 - malformed dates are normal in feeds
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            parsed = None
    if parsed is None:
        return scrub(raw, MAX_RAW_DATE_CHARS)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _children(element) -> list:
    return [child for child in element if local_name(child.tag) not in DENIED_TAGS]


def _first_text(element, names: tuple[str, ...]) -> str:
    """Text of the first direct child whose local name is in `names`."""
    for name in names:
        for child in _children(element):
            if local_name(child.tag) == name:
                text = child.text or ""
                if text.strip():
                    return text
    return ""


def _item_link(element) -> str:
    """Item URL: RSS puts it in the text, Atom in a href attribute."""
    best = ""
    for child in _children(element):
        if local_name(child.tag) not in LINK_TAGS:
            continue
        href = (child.get("href") or "").strip()
        rel = (child.get("rel") or "alternate").strip().lower()
        if href:
            if rel == "alternate":
                return href
            if rel in {"", "self"} and not best:
                best = href
            continue
        text = (child.text or "").strip()
        if text and not best:
            best = text
    return best


def _item_id(element, source_url: str, title: str, published: str, link: str) -> str:
    """Stable identity for an item, in the order feeds make it available."""
    for name in ID_TAGS:
        for child in _children(element):
            if local_name(child.tag) == name:
                candidate = scrub((child.text or ""), 512)
                if candidate:
                    return candidate
    if link:
        return link
    seed = "|".join([source_url, title, published])
    return "sha1:" + hashlib.sha1(seed.encode("utf-8")).hexdigest()


def _feed_root_and_title(root) -> tuple[object, str]:
    """The element that holds the items, and the feed title."""
    root_name = local_name(root.tag)
    if root_name == "rss":
        for child in _children(root):
            if local_name(child.tag) == "channel":
                return child, _first_text(child, TITLE_TAGS)
        return root, ""
    if root_name in {"feed", "rdf"}:
        return root, _first_text(root, TITLE_TAGS)
    # Some feeds are served with <channel> as the root.
    return root, _first_text(root, TITLE_TAGS)


def _iter_items(container, root) -> list:
    items = [child for child in container if local_name(child.tag) in ITEM_TAGS]
    if items:
        return items
    # RSS 1.0 puts <item> next to <channel>, not inside it.
    return [element for element in root.iter() if local_name(element.tag) in ITEM_TAGS]


def parse_feed(source_url: str, xml_text: str | bytes) -> dict:
    """Parse one feed. Accepts the response bytes or already decoded text.

    Returns {"source_url", "source_title", "items": [item, ...]} where every
    item has exactly the keys in ALLOWED_FIELDS.
    """
    if not xml_text or not str(xml_text).strip():
        raise FeedParseError("empty response")
    if isinstance(xml_text, bytes):
        # Bytes keep the <?xml encoding="..."?> declaration meaningful, so the
        # parser decodes the feed the way the feed says it is encoded.
        data: str | bytes = xml_text.lstrip(b"\xef\xbb\xbf \t\r\n")
    else:
        # ElementTree refuses a str that still declares an encoding, so the
        # declaration goes away: the text is already decoded.
        data = _XML_DECLARATION.sub("", xml_text.lstrip("﻿ \t\r\n")).lstrip()
    try:
        root = ElementTree.fromstring(data)
    except (ElementTree.ParseError, ValueError) as exc:
        raise FeedParseError("not valid XML: %s" % exc) from exc

    container, raw_title = _feed_root_and_title(root)
    source_title = scrub(raw_title, MAX_TITLE_CHARS)
    source_url = scrub(source_url, 2048)

    items: list[dict] = []
    for element in _iter_items(container, root):
        title = scrub(_first_text(element, TITLE_TAGS), MAX_TITLE_CHARS)
        published = normalize_date(_first_text(element, DATE_TAGS))
        link = scrub(_item_link(element), 2048)
        item_id = _item_id(element, source_url, title, published, link)
        summary = scrub(_first_text(element, SUMMARY_TAGS), MAX_SUMMARY_CHARS)
        item = {
            "source_url": source_url,
            "source_title": source_title,
            "item_id": item_id,
            "item_title": title,
            "item_link": link,
            "item_published_at": published,
            "item_summary": summary,
        }
        items.append(enforce_allowed_fields(item))

    if not items and local_name(root.tag) not in {"rss", "feed", "rdf", "channel"}:
        raise FeedParseError("root element <%s> is not a feed" % local_name(root.tag))

    return {"source_url": source_url, "source_title": source_title, "items": items}


def enforce_allowed_fields(item: dict) -> dict:
    """Last gate: exactly the seven allowed fields, all of them strings."""
    clean = {}
    for field in ALLOWED_FIELDS:
        value = item.get(field, "")
        value = "" if value is None else str(value)
        if "@" in value:
            value = ""
        clean[field] = value
    return clean
