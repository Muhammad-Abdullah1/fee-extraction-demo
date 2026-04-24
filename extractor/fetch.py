"""Fetch helpers.

The demo prefers cached fixtures so it is fully reproducible offline.
If a fixture is missing, we attempt a plain HTTP GET. A Playwright
fallback hook is left in place but disabled by default — the spec
allows it for JS-heavy pages, but neither of the working sources in
this demo requires it.
"""
import os
import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def ensure_local(url: str, local_path: str, timeout: int = 30) -> str:
    """Return a local file path for the given URL.

    Reads from cache if present. Otherwise fetches over HTTP.
    Raises if neither path works — we never fabricate content.
    """
    if os.path.exists(local_path):
        return local_path

    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        f.write(resp.content)
    return local_path


def fetch_html(url: str, timeout: int = 30) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.text
