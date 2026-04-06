"""
Pipeline timer for Echo Stage 0.

Captures high-resolution timestamps at each pipeline stage
and computes latencies between them.
"""

import time


class PipelineTimer:
    """Captures timestamps at named events and computes latencies."""

    def __init__(self):
        self._marks: dict[str, float] = {}

    def mark(self, name: str) -> float:
        """Record a timestamp for the named event. Returns the timestamp."""
        ts = time.perf_counter()
        self._marks[name] = ts
        return ts

    def latency(self, start: str, end: str) -> float:
        """Compute seconds between two named marks."""
        return self._marks[end] - self._marks[start]

    def report(self) -> dict:
        """Return a dict of all computed latencies for logging."""
        r = {}
        pairs = [
            ("stt_latency_s", "t1", "t2"),
            ("llm_latency_s", "t3", "t4"),
            ("tts_latency_s", "t5", "t6"),
            ("total_latency_s", "t0", "t7"),
        ]
        for key, start, end in pairs:
            if start in self._marks and end in self._marks:
                r[key] = round(self.latency(start, end), 4)
        return r

    def reset(self):
        """Clear all marks for the next run."""
        self._marks.clear()
