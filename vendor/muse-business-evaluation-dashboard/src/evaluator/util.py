"""HTTP fetching with the SEC-declared User-Agent and a small disk cache.
Stdlib only. Fails loud: callers decide whether a failure is fatal."""
import gzip
import hashlib
import json
import time
import urllib.request
from pathlib import Path

from .config import OUTPUT_DIR

_last_request = [0.0]


def fetch_bytes(url, user_agent, timeout=60, delay=0.2, cache_hours=12):
    cache_dir = OUTPUT_DIR / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    cache_file = cache_dir / key
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < cache_hours * 3600:
        return cache_file.read_bytes()
    wait = delay - (time.time() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent,
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    _last_request[0] = time.time()
    cache_file.write_bytes(raw)
    return raw


def fetch_json(url, user_agent, timeout=60, delay=0.2):
    return json.loads(fetch_bytes(url, user_agent, timeout, delay).decode("utf-8", "replace"))


def fetch_text(url, user_agent, timeout=60, delay=0.2):
    return fetch_bytes(url, user_agent, timeout, delay).decode("utf-8", "replace")
