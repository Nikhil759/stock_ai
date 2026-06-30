"""Rate-limited parallel fetch pool for yfinance."""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock


class ThrottledPool:
    """Run fetch jobs with bounded concurrency and pause between starts."""

    def __init__(self, max_workers: int = 6, delay_sec: float = 0.2, retries: int = 2):
        self.max_workers = max_workers
        self.delay_sec = delay_sec
        self.retries = retries
        self._lock = Lock()
        self._last_start = 0.0

    def _wait_turn(self):
        with self._lock:
            now = time.monotonic()
            wait = self.delay_sec - (now - self._last_start)
            if wait > 0:
                time.sleep(wait)
            self._last_start = time.monotonic()

    def _run_one(self, fn, item):
        last_err = None
        for attempt in range(self.retries + 1):
            self._wait_turn()
            try:
                return fn(item)
            except Exception as exc:
                last_err = exc
                if attempt < self.retries:
                    time.sleep(0.5 * (attempt + 1))
        raise last_err  # type: ignore[misc]

    def map(self, fn, items: list, on_progress=None) -> tuple[list, list[str]]:
        """Returns (results in input order where success, errors)."""
        if not items:
            return [], []
        results: dict = {}
        errors: list[str] = []
        done = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._run_one, fn, item): item for item in items}
            for future in as_completed(futures):
                item = futures[future]
                done += 1
                if on_progress:
                    on_progress(done, len(items))
                try:
                    val = future.result()
                    if val is not None:
                        results[item] = val
                except Exception as exc:
                    errors.append(f"{item}: {exc}")
        ordered = [results[i] for i in items if i in results]
        return ordered, errors[:10]
