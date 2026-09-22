"""End-to-end run of the Feed Change Watcher against local feed files.

Why this script exists
----------------------
The lab sandbox denies every socket bind and reaches no feed on the internet,
so the Actor cannot be pointed at a real server. Everything else in the run is
the real thing: the real `src/main.py` entry point, the real Apify Actor
lifecycle (input, dataset, named key-value store), the real parser in
`src/feed_parser.py` and the real "what is new?" logic in `src/state.py`.

The only substitution is the transport underneath `requests`: an adapter
answers the fetch from files on disk, with a real status code, real headers and
a real body. Every request it serves is printed in the log.

Usage:
    python tests/run_local_e2e.py <site-dir> <run-dir> <phase>

`phase` is 1 (first run, baseline) or 2 (second run, after the feed changed).
Both phases share `<run-dir>`, so the state store survives between them, which
is the behaviour the Actor sells.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

HERE = Path(__file__).resolve().parent
ACTOR_DIR = HERE.parent
sys.path.insert(0, str(ACTOR_DIR / "src"))

BASE_URL = "http://127.0.0.1:8099/"


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%d/%b/%Y %H:%M:%S")


class LocalSiteAdapter(BaseAdapter):
    """Serves one directory over the `requests` API, without a socket."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root.resolve()
        self.served = 0

    def _resolve(self, path: str) -> Path | None:
        relative = unquote(path).lstrip("/")
        if not relative:
            return None
        candidate = (self.root / relative).resolve()
        if not str(candidate).startswith(str(self.root)):
            return None
        return candidate if candidate.is_file() else None

    def send(self, request, stream=False, timeout=None, verify=True, cert=None,
             proxies=None):  # noqa: D102 - requests adapter interface
        path = urlparse(request.url).path
        target = self._resolve(path)
        if target is None:
            status, reason = 404, "File not found"
            body = b"not found"
            content_type = "text/plain"
        else:
            status, reason = 200, "OK"
            body = target.read_bytes()
            content_type = "application/rss+xml; charset=utf-8"

        response = requests.Response()
        response.status_code = status
        response.reason = reason
        response.url = request.url
        response.request = request
        response.encoding = "utf-8"
        response.headers = CaseInsensitiveDict(
            {
                "Server": "local-file-adapter/1.0 (no socket: sandbox denies bind)",
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
            }
        )
        response.raw = io.BytesIO(b"" if request.method == "HEAD" else body)
        self.served += 1
        print(
            f'[local-site] 127.0.0.1 - - [{stamp()}] "{request.method} {path} '
            f'HTTP/1.1" {status} {len(body)}',
            flush=True,
        )
        return response

    def close(self) -> None:
        return None


def install_adapter(base_url: str, root: Path) -> LocalSiteAdapter:
    adapter = LocalSiteAdapter(root)
    prefix = base_url.rstrip("/")
    original_get_adapter = requests.Session.get_adapter

    def get_adapter(self, url):  # noqa: ANN001 - patching requests
        if url.startswith(prefix):
            return adapter
        return original_get_adapter(self, url)

    requests.Session.get_adapter = get_adapter
    return adapter


def main() -> int:
    site_dir = Path(sys.argv[1]).resolve()
    run_dir = Path(sys.argv[2]).resolve()
    phase = sys.argv[3] if len(sys.argv) > 3 else "1"

    actor_input = {
        "feedUrls": [BASE_URL + "blog.xml", BASE_URL + "status.atom"],
        "maxSources": 20,
        "requestDelaySeconds": 0,
        "requestTimeoutSeconds": 20,
    }

    input_dir = run_dir / "storage" / "key_value_stores" / "default"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "INPUT.json").write_text(json.dumps(actor_input, indent=2))

    # A new run gets a fresh dataset; the named state store is kept on purpose.
    dataset_dir = run_dir / "storage" / "datasets" / "default"
    if dataset_dir.is_dir():
        for leftover in dataset_dir.glob("*.json"):
            leftover.unlink()
    os.chdir(run_dir)

    print(f"Actor dir: {ACTOR_DIR}")
    print(f"Site dir:  {site_dir}")
    print(f"Run dir:   {run_dir}")
    print(f"Phase:     {phase}")
    print(f"Serving {BASE_URL} from disk (sandbox denies socket bind, so the "
          "HTTP transport under `requests` is a local file adapter).")
    print("Files on the test site: "
          + ", ".join(sorted(p.name for p in site_dir.iterdir() if p.is_file())))
    print(f"Actor input: {json.dumps(actor_input)}")
    print("-" * 72, flush=True)

    adapter = install_adapter(BASE_URL, site_dir)

    import main as actor_main  # noqa: PLC0415 - after sys.path setup

    actor_exit_code = 0
    try:
        asyncio.run(actor_main.main())
    except SystemExit as exc:
        actor_exit_code = 0 if exc.code is None else int(exc.code)

    print("-" * 72, flush=True)
    print(f"Actor lifecycle exit code: {actor_exit_code}")
    print(f"HTTP requests served by the local site: {adapter.served}")

    rows = []
    for item in sorted(dataset_dir.glob("[0-9]*.json")):
        rows.append(json.loads(item.read_text()))
    print(f"Dataset rows written: {len(rows)}")
    for row in rows:
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))

    kvs_dir = run_dir / "storage" / "key_value_stores" / "default"
    report_file = kvs_dir / "REPORT"
    if not report_file.is_file():
        report_file = kvs_dir / "REPORT.json"
    if report_file.is_file():
        print("REPORT record:")
        print(json.dumps(json.loads(report_file.read_text()), indent=2,
                         ensure_ascii=False))
    else:
        print(f"REPORT record not found at {report_file}")

    state_dir = run_dir / "storage" / "key_value_stores" / "feed-change-watcher-state"
    if state_dir.is_dir():
        keys = sorted(p.name for p in state_dir.glob("SEEN*") if p.suffix != ".json"
                      or True)
        print(f"State store keys: {keys}")
    print(f"exit_code {actor_exit_code}")
    return actor_exit_code


if __name__ == "__main__":
    sys.exit(main())
