"""Per-backend bulkhead and circuit breaker for community backends (Federation Standard v1 §6).

At most ``MAX_INFLIGHT`` concurrent calls per backend; excess calls fail fast. Each call is
bounded by the backend's total timeout. ``FAILURES_TO_OPEN`` consecutive failures or timeouts
open the breaker for ``OPEN_SECONDS``; then one half-open probe decides: success closes it,
failure reopens it. Only the call path feeds the breaker, so a healthy ``tools/list`` cannot
hold it closed. Core backends never reach this module.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from genefoundry_router.observability import COMMUNITY_BREAKER_STATE

MAX_INFLIGHT = 4  # GF_COMMUNITY_MAX_INFLIGHT; see community_guard.configure()
TOTAL_TIMEOUT = 12.0  # unless the entry sets ``timeout`` (10-15 s)
FAILURES_TO_OPEN = 5
OPEN_SECONDS = 60.0
_GAUGE = {"closed": 0, "open": 1, "half_open": 2}

_now = time.monotonic


@dataclass
class Breaker:
    timeout: float = field(default_factory=lambda: TOTAL_TIMEOUT)
    inflight: int = 0
    failures: int = 0
    opened_at: float | None = None
    probing: bool = False

    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if self.probing or _now() - self.opened_at >= OPEN_SECONDS:
            return "half_open"
        return "open"

    def gauge(self) -> int:
        return _GAUGE[self.state()]

    def admit(self) -> bool | None:
        """``None``: fail fast, no upstream request. Else whether this call is the probe."""
        state = self.state()
        if (
            self.inflight >= MAX_INFLIGHT
            or state == "open"
            or (state == "half_open" and self.probing)
        ):
            return None
        self.inflight += 1
        self.probing = state == "half_open"
        return self.probing

    def release(self, ok: bool | None, probe: bool | None) -> None:
        """``ok`` is ``None`` for a cancelled call, which neither closes nor counts."""
        self.inflight -= 1
        if probe:
            self.probing = False
        if ok:
            self.failures = 0
            if probe:
                self.opened_at = None
        elif ok is False:
            self.failures += 1
            if probe or (self.opened_at is None and self.failures >= FAILURES_TO_OPEN):
                self.opened_at = _now()


_BREAKERS: dict[str, Breaker] = {}


def get(namespace: str, timeout: float | None = None) -> Breaker:
    """The namespace's breaker, created (and exported as a metric) on first use."""
    breaker = _BREAKERS.get(namespace)
    if breaker is None:
        breaker = _BREAKERS[namespace] = Breaker()
        COMMUNITY_BREAKER_STATE.labels(namespace=namespace).set_function(breaker.gauge)
    if timeout is not None:
        breaker.timeout = timeout
    return breaker
