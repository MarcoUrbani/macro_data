import logging
import time
from typing import Optional
import requests

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class HttpClient:
    """Reusable HTTP client with retry, exponential backoff, and timeout."""

    def __init__(
        self,
        timeout: float = 20.0,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.session = requests.Session()
        self.session.headers.update(headers or DEFAULT_HEADERS)

    def get_text(self, url: str) -> str:
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                return response.text
            except requests.RequestException as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                sleep_time = self.backoff_factor * (2 ** (attempt - 1))
                logger.warning(
                    "Attempt %d/%d failed for %s (%s). Retrying in %.1fs",
                    attempt,
                    self.max_retries,
                    url,
                    exc,
                    sleep_time,
                )
                time.sleep(sleep_time)

        raise RuntimeError(f"Failed to fetch {url} after {self.max_retries} attempts") from last_error
    
    
    
    
    