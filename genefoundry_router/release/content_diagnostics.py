"""Deterministic count- and byte-bounded diagnostic collection."""

from __future__ import annotations

import json
from collections.abc import Iterable


class FindingBuffer:
    """Retain the lexicographically first findings without unbounded growth."""

    def __init__(self, count_limit: int, byte_limit: int) -> None:
        self._count_limit = count_limit
        self._byte_limit = byte_limit
        self._values: set[str] = set()
        self._bytes = 0
        self.truncated = False

    @staticmethod
    def _cost(value: str) -> int:
        return len(json.dumps(value, ensure_ascii=True).encode("utf-8")) + 1

    def add(self, value: str) -> None:
        if value in self._values:
            return
        cost = self._cost(value)
        self._values.add(value)
        self._bytes += cost
        while len(self._values) > self._count_limit or self._bytes > self._byte_limit:
            removed = max(self._values)
            self._values.remove(removed)
            self._bytes -= self._cost(removed)
            self.truncated = True

    def update(self, values: Iterable[str]) -> None:
        for value in values:
            self.add(value)

    @property
    def values(self) -> tuple[str, ...]:
        return tuple(sorted(self._values))
