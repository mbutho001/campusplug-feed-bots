"""Shared HTTP helpers for every source adapter."""
import time
import requests

BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


class Fetcher:
    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.timeout = cfg["run"]["http_timeout"]
        self.ua = cfg["run"]["user_agent"]
        self.session = requests.Session()

    def get(self, url, *, ua=None, headers=None, retries=2, backoff=5, stream=False):
        """GET with retry/backoff. Returns Response or None. 429 is respected."""
        hdrs = {"User-Agent": ua or self.ua, "Accept-Language": "en-ZA,en;q=0.9"}
        if headers:
            hdrs.update(headers)
        delay = backoff
        for attempt in range(1, retries + 2):
            try:
                r = self.session.get(url, headers=hdrs, timeout=self.timeout, stream=stream)
            except requests.RequestException as exc:
                self.log(f"  ! {url} attempt {attempt}: {exc}")
                r = None
            else:
                if r.status_code == 200:
                    return r
                if r.status_code not in (429, 500, 502, 503, 504):
                    self.log(f"  ! {url} -> HTTP {r.status_code} (giving up)")
                    return None
                self.log(f"  . {url} -> HTTP {r.status_code}, retry {attempt}")
            if attempt <= retries:
                time.sleep(delay)
                delay *= 2
        return None
