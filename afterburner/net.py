"""Public read-only HTTP client: retry, bounded timeouts, and rate-limit backoff."""
import logging
import time
import requests

LOG = logging.getLogger(__name__)


class DataError(RuntimeError):
    pass


class HTTP:
    def __init__(self, timeout=10, retries=2, pace=1.15, session=None):
        self.timeout, self.retries, self.pace = timeout, retries, pace
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "AfterburnerIntel/0.1 (public research; no trading)",
                                     "Accept": "application/json, application/xml, application/rss+xml, */*"})
        self.last_request = 0.0

    def get(self, url, params=None, response_type="json"):
        for attempt in range(self.retries + 1):
            wait = self.pace - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            self.last_request = time.monotonic()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code in (429, 500, 502, 503, 504):
                    raise DataError(f"HTTP {response.status_code} from {response.url.split('?')[0]}")
                response.raise_for_status()
                return response.json() if response_type == "json" else response.content
            except (requests.RequestException, DataError, ValueError) as exc:
                if attempt == self.retries:
                    raise DataError(f"{url.split('?')[0]}: {str(exc)[:180]}") from exc
                LOG.warning("Retry %s for %s: %s", attempt + 1, url.split('?')[0], exc)
                time.sleep(min(2 ** attempt, 10))
        raise DataError("unreachable")
