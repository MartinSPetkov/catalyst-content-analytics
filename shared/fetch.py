from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

_BLOCKED_DOMAINS = {"linkedin.com", "google.com"}
_TIMEOUT = 15


def _guard_url(url: str) -> None:
    domain = urlparse(url).netloc.lower().lstrip("www.")
    for blocked in _BLOCKED_DOMAINS:
        if blocked in domain:
            raise ValueError(f"Fetching {domain} is not permitted.")


def fetch_page(url: str) -> str:
    _guard_url(url)
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)
    except Exception as e:
        print(f"[fetch] Warning: could not fetch {url}: {e}")
        return ""


def extract_links(url: str) -> list[str]:
    _guard_url(url)
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        return [a["href"] for a in soup.find_all("a", href=True)]
    except Exception as e:
        print(f"[fetch] Warning: could not extract links from {url}: {e}")
        return []
