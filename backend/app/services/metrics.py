"""Metrics for FindBack (Phase 18).

Two kinds of measurement, deliberately kept apart.

**In-process counters** cover the work the API itself does: captures, dedupe
lookups, searches, AI and embedding calls. They are counted in memory and
rendered at scrape time, so they describe the process that served the scrape.

**Queue metrics are derived from the database at scrape time.** A Celery worker
is a different OS process from the API, so a counter incremented inside a worker
would never appear in the API's `/metrics`. Deriving them from `processing_jobs`
instead means they cover *every* worker, survive a restart, and cannot silently
reset -- which is the whole point of asking how deep the queue is.

The exposition format is Prometheus text format, implemented here with the
standard library. Adding a metrics client library would mean adding a
dependency to a project whose pinned requirements are load-bearing (the
embedding dimension, the httpx version FastAPI's test client needs), and the
format is small enough to render directly.

Nothing here is allowed to identify anybody
-------------------------------------------
Label values are drawn from a fixed vocabulary of statuses and stage names.
`user_id` and `content_id` are deliberately NOT labels: they are unbounded, so
they would grow the time series without limit, and they are personal data.
Both are log fields instead, where they are hashed on the way in. See
`app/services/observability.py`.
"""
from __future__ import annotations

import math
import threading
import time

# Rendered as `# HELP findback_<name>`, so the phase's names stay recognisable.
PREFIX = "findback_"

# Buckets in seconds. Chosen around the numbers the product cares about: a save
# must feel instant, so a pipeline over ~5 minutes is already an incident.
PROCESSING_DURATION_BUCKETS = (0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)
QUEUE_WAIT_BUCKETS = (0.05, 0.1, 0.5, 1, 5, 15, 60, 300, 900)
SEARCH_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5)

# A metric family whose value comes from the database rather than from an
# accumulator in this process. Rendering must be idempotent: two scrapes must
# produce the same numbers, so these are SET rather than observed.
SOURCE_PROCESS = "process"


def _escape(value: str) -> str:
    """Escape a label value for the text format."""
    return (str(value).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n"))


def _labels(pairs: dict | None) -> str:
    if not pairs:
        return ""
    body = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(pairs.items()))
    return "{" + body + "}"


class _Family:
    """Base for the three metric types. Not used directly."""

    kind = "untyped"

    def __init__(self, name: str, help_text: str, labelnames: tuple = ()):
        self.name = name
        self.help_text = help_text
        self.labelnames = labelnames
        self.source = SOURCE_PROCESS
        self._lock = threading.Lock()

    @property
    def full_name(self) -> str:
        return PREFIX + self.name

    def _check(self, pairs: dict | None) -> dict:
        pairs = pairs or {}
        if set(pairs) != set(self.labelnames):
            raise ValueError(
                f"{self.name} expects labels {self.labelnames}, got "
                f"{tuple(pairs)}")
        return pairs

    def reset(self) -> None:
        raise NotImplementedError


class Counter(_Family):
    """Monotonic. Only ever goes up, which is what makes a rate meaningful."""

    kind = "counter"

    def __init__(self, name: str, help_text: str, labelnames: tuple = ()):
        super().__init__(name, help_text, labelnames)
        self._values: dict[tuple, float] = {}

    def inc(self, amount: float = 1, labels: dict | None = None) -> None:
        if amount < 0:
            raise ValueError("a counter cannot decrease")
        pairs = self._check(labels)
        key = tuple(pairs.get(name) for name in self.labelnames)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, labels: dict | None = None) -> float:
        pairs = self._check(labels)
        key = tuple(pairs.get(name) for name in self.labelnames)
        with self._lock:
            return self._values.get(key, 0.0)

    def set_from_db(self, total: float, labels: dict | None = None) -> None:
        """Replace the value wholesale. Used by the DB-derived families."""
        pairs = self._check(labels)
        key = tuple(pairs.get(name) for name in self.labelnames)
        with self._lock:
            self._values = {key: float(total)}

    def reset(self) -> None:
        with self._lock:
            self._values = {}

    def render(self) -> list[str]:
        with self._lock:
            items = sorted(self._values.items())
        lines = []
        for key, total in items:
            pairs = dict(zip(self.labelnames, key))
            lines.append(f"{self.full_name}{_labels(pairs)} {_num(total)}")
        return lines


class Gauge(_Family):
    """A value that goes up and down, read as a level rather than a rate."""

    kind = "gauge"

    def __init__(self, name: str, help_text: str, labelnames: tuple = ()):
        super().__init__(name, help_text, labelnames)
        self._values: dict[tuple, float] = {}

    def set(self, value: float, labels: dict | None = None) -> None:
        pairs = self._check(labels)
        key = tuple(pairs.get(name) for name in self.labelnames)
        with self._lock:
            self._values[key] = float(value)

    def value(self, labels: dict | None = None) -> float | None:
        pairs = self._check(labels)
        key = tuple(pairs.get(name) for name in self.labelnames)
        with self._lock:
            return self._values.get(key)

    def set_from_db(self, value: float, labels: dict | None = None) -> None:
        self.set(value, labels)

    def reset(self) -> None:
        with self._lock:
            self._values = {}

    def render(self) -> list[str]:
        with self._lock:
            items = sorted(self._values.items())
        lines = []
        for key, value in items:
            pairs = dict(zip(self.labelnames, key))
            lines.append(f"{self.full_name}{_labels(pairs)} {_num(value)}")
        return lines


class Histogram(_Family):
    """Cumulative buckets, plus the sum and count a rate is computed from."""

    kind = "histogram"

    def __init__(self, name: str, help_text: str, buckets: tuple,
                 labelnames: tuple = ()):
        super().__init__(name, help_text, labelnames)
        self.buckets = tuple(sorted(buckets))
        # key -> [per-bucket counts, total observations, sum of observations]
        #
        # The observation count is kept separately from the bucket counts on
        # purpose. An observation lands in EVERY bucket it is <=, so summing
        # the buckets would count one 0.2s sample nine times and answer "23"
        # for four observations. Prometheus's own _count is the number of
        # observations, so that is what this has to report.
        self._state: dict[tuple, list] = {}

    def _blank(self) -> list:
        return [[0] * len(self.buckets), 0, 0.0]

    def _slot(self, key: tuple) -> list:
        state = self._state.get(key)
        if state is None:
            state = self._blank()
            self._state[key] = state
        return state

    def observe(self, value: float, labels: dict | None = None) -> None:
        pairs = self._check(labels)
        key = tuple(pairs.get(name) for name in self.labelnames)
        with self._lock:
            state = self._slot(key)
            state[1] += 1
            state[2] += value
            # Every bucket the value fits in is incremented, so each bucket
            # holds the cumulative figure Prometheus expects.
            for index, bound in enumerate(self.buckets):
                if value <= bound:
                    state[0][index] += 1

    def count(self, labels: dict | None = None) -> int:
        pairs = self._check(labels)
        key = tuple(pairs.get(name) for name in self.labelnames)
        with self._lock:
            return self._state[key][1] if key in self._state else 0

    def total_sum(self, labels: dict | None = None) -> float:
        pairs = self._check(labels)
        key = tuple(pairs.get(name) for name in self.labelnames)
        with self._lock:
            return self._state[key][2] if key in self._state else 0.0

    def bucket_counts(self, labels: dict | None = None) -> list[int]:
        """Per-bucket counts, NOT cumulative -- the raw form."""
        pairs = self._check(labels)
        key = tuple(pairs.get(name) for name in self.labelnames)
        with self._lock:
            return (list(self._state[key][0]) if key in self._state
                    else [0] * len(self.buckets))

    def set_from_db(self, values, labels: dict | None = None):
        """Install observations computed elsewhere, e.g. read from the database.

        `values` is one entry per observation rather than per bucket, so the
        caller hands over raw samples and this buckets them. Setting rather than
        accumulating is what keeps two scrapes idempotent.
        """
        pairs = self._check(labels)
        key = tuple(pairs.get(name) for name in self.labelnames)
        state = self._blank()
        for raw in values:
            value = float(raw)
            state[1] += 1
            state[2] += value
            for index, bound in enumerate(self.buckets):
                if value <= bound:
                    state[0][index] += 1
        with self._lock:
            self._state = {key: state}

    def reset(self) -> None:
        with self._lock:
            self._state = {}

    def render(self) -> list[str]:
        with self._lock:
            keys = sorted(self._state)
            snapshot = {k: ([c for c in self._state[k][0]],
                            self._state[k][1], self._state[k][2]) for k in keys}
        lines = []
        for key in keys:
            counts, observed, total = snapshot[key]
            pairs = dict(zip(self.labelnames, key))
            for index, bound in enumerate(self.buckets):
                # `counts[index]` already means "observations <= this bound",
                # because observe() increments every bucket the value fits in.
                # It IS the cumulative figure, so it must not be summed again:
                # doing so reports 8 for four observations of 0.2/0.7/3.0/400.
                bucket_pairs = dict(pairs)
                bucket_pairs["le"] = _num(bound)
                lines.append(
                    f"{self.full_name}_bucket{_labels(bucket_pairs)} "
                    f"{counts[index]}")
            inf_pairs = dict(pairs)
            inf_pairs["le"] = "+Inf"
            lines.append(
                f"{self.full_name}_bucket{_labels(inf_pairs)} {observed}")
            lines.append(f"{self.full_name}_sum{_labels(pairs)} {_num(total)}")
            lines.append(f"{self.full_name}_count{_labels(pairs)} {observed}")
        return lines


def _num(value) -> str:
    """Render a number the way the text format wants: no trailing `.0`."""
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return repr(round(float(value), 6))


# --- the metrics of this phase -------------------------------------------

# Labels are drawn from a closed vocabulary only. See the module docstring on
# why no user or content id appears here.
OUTCOME_LABELS = ("outcome",)

captures_total = Counter(
    "captures_total",
    "Saves accepted by the API, including repeat saves and sync replays.")
dedupe_hits = Counter(
    "dedupe_hits",
    "Saves that resolved to content this user had already saved.",
    OUTCOME_LABELS)
dedupe_misses = Counter(
    "dedupe_misses",
    "Saves that did not match existing content and created a new asset.",
    OUTCOME_LABELS)
ai_requests = Counter(
    "ai_requests", "Outbound calls to the chat provider.", OUTCOME_LABELS)
ai_failures = Counter(
    "ai_failures",
    "Outbound chat calls that exhausted their retries and failed.",
    OUTCOME_LABELS)
embedding_requests = Counter(
    "embedding_requests",
    "Outbound calls to the embedding provider.", OUTCOME_LABELS)
search_requests = Counter(
    "search_requests", "Searches served.", OUTCOME_LABELS)
processing_jobs_total = Counter(
    "processing_jobs_total",
    "Processing jobs ever recorded. Derived from processing_jobs.")
processing_success_total = Counter(
    "processing_success_total",
    "Processing jobs that reached READY. Derived from processing_jobs.")
processing_failures_total = Counter(
    "processing_failures_total",
    "Processing jobs that reached FAILED. Derived from processing_jobs.")
queue_depth = Gauge(
    "queue_depth",
    "Jobs waiting to be processed. Derived from processing_jobs.")

processing_duration = Histogram(
    "processing_duration",
    "Seconds from a job being claimed to it reaching a terminal state.",
    PROCESSING_DURATION_BUCKETS)
queue_wait_time = Histogram(
    "queue_wait_time",
    "Seconds from a job being recorded to a worker claiming it.",
    QUEUE_WAIT_BUCKETS)
search_latency = Histogram(
    "search_latency", "Seconds to serve one search.", SEARCH_LATENCY_BUCKETS)

# Everything the database answers for: from processing_jobs_total down, each is
# set from a row count rather than accumulated in this process.
FAMILIES = (captures_total, dedupe_hits, dedupe_misses, ai_requests,
            ai_failures, embedding_requests, search_requests,
            processing_jobs_total, processing_success_total,
            processing_failures_total, queue_depth, processing_duration,
            queue_wait_time, search_latency)


def reset() -> None:
    """Forget every process-local value. Used between tests."""
    for family in FAMILIES:
        family.reset()


# --- the queue, read from the database -----------------------------------

def collect_queue_metrics(db) -> dict:
    """Fill the queue families from `processing_jobs`. Returns a small summary.

    Idempotent on purpose: the values are SET from a query, so scraping twice
    yields the same answer instead of double-counting.

    A database that cannot be reached is not an observability failure. The
    families are simply left untouched and the reason is returned, so
    `/metrics` keeps answering with whatever it can rather than 500ing -- a
    broken scrape must not be mistaken for a broken service.
    """
    from sqlalchemy import text

    summary = {"ok": False, "reason": None, "counts": {}}
    try:
        rows = db.execute(text("""
            SELECT status,
                   extract(epoch FROM (claimed_at - created_at)) AS wait_s,
                   extract(epoch FROM (updated_at - claimed_at)) AS run_s
              FROM processing_jobs
        """)).all()
    except Exception as exc:  # noqa: BLE001 - a scrape must never raise
        summary["reason"] = f"{type(exc).__name__}: {exc}"
        return summary

    counts = {"total": 0, "ready": 0, "failed": 0, "pending": 0,
              "processing": 0}
    waits: list[float] = []
    durations: list[float] = []
    for status, wait_s, run_s in rows:
        counts["total"] += 1
        key = (status or "").lower()
        if key in counts:
            counts[key] += 1
        if wait_s is not None:
            waits.append(max(0.0, float(wait_s)))
        if run_s is not None:
            durations.append(max(0.0, float(run_s)))

    processing_jobs_total.set_from_db(counts["total"])
    processing_success_total.set_from_db(counts["ready"])
    processing_failures_total.set_from_db(counts["failed"])
    queue_depth.set_from_db(counts["pending"])
    processing_duration.set_from_db(durations)
    queue_wait_time.set_from_db(waits)

    summary["ok"] = True
    summary["counts"] = counts
    return summary


# --- rendering ------------------------------------------------------------

def render() -> str:
    """The whole registry as Prometheus text format."""
    lines: list[str] = []
    for family in FAMILIES:
        lines.append(f"# HELP {family.full_name} {family.help_text}")
        lines.append(f"# TYPE {family.full_name} {family.kind}")
        lines.extend(family.render())
    lines.append("")
    return "\n".join(lines)


# --- timers ---------------------------------------------------------------

class Timer:
    """Context manager that observes a histogram and reports the duration.

    Returns the measured seconds from `elapsed_ms` so a caller can log the same
    number it recorded, rather than measuring twice and getting two answers.
    """

    def __init__(self, histogram: Histogram, labels: dict | None = None):
        self.histogram = histogram
        self.labels = labels
        self.seconds = 0.0
        self._start = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc_info) -> None:
        self.seconds = time.monotonic() - self._start
        self.histogram.observe(self.seconds, self.labels)

    @property
    def elapsed_ms(self) -> int:
        return int(round(self.seconds * 1000))


def observe_ms(histogram: Histogram, milliseconds: float,
               labels: dict | None = None) -> None:
    """Observe a duration that was measured somewhere else."""
    histogram.observe(max(0.0, milliseconds) / 1000.0, labels)


def quantiles(samples: list[float]) -> dict:
    """A p50/p95/p99 summary, for the log line a test can assert on.

    Prometheus computes quantiles from the buckets at query time; this is only
    for putting a human-readable number in a log record.
    """
    if not samples:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "count": 0}
    ordered = sorted(samples)

    def at(fraction: float) -> float:
        index = min(len(ordered) - 1,
                    max(0, math.ceil(fraction * len(ordered)) - 1))
        return ordered[index]

    return {"p50": at(0.50), "p95": at(0.95), "p99": at(0.99),
            "count": len(ordered)}
