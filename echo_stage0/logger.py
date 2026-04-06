"""
JSONL session logger for Echo.

Appends one JSON record per pipeline run to logs/stage0_log.jsonl.
Compatible with both Stage 0 and Stage 1 record formats.
Tracks session stats (min/max/avg) for the summary report.
"""

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path


LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "stage0_log.jsonl"

# Budget: first audio within 1.5s (Stage 1 target)
FIRST_AUDIO_BUDGET_S = 1.5


class SessionLogger:
    """Logs pipeline runs to JSONL and computes session statistics."""

    def __init__(self):
        LOG_DIR.mkdir(exist_ok=True)
        self._runs: list[dict] = []

    def log_run(self, **kwargs) -> dict:
        """
        Append one run record to the log file and session history.

        Accepts any keyword arguments. Stage 1 fields:
            stage, model, stt_backend, tts_backend, vad_mode,
            input_duration_s, stt_latency_s, llm_first_token_s,
            llm_full_response_s, tts_first_chunk_s, first_audio_s,
            total_latency_s, passed_budget, transcript, response_full
        """
        record = {"timestamp": datetime.now(timezone.utc).isoformat()}
        record.update(kwargs)

        # Round all float values
        for key, val in record.items():
            if isinstance(val, float):
                record[key] = round(val, 4)

        # Auto-compute passed_budget if not provided
        if "passed_budget" not in record:
            first_audio = record.get("first_audio_s")
            if first_audio is not None:
                record["passed_budget"] = first_audio < FIRST_AUDIO_BUDGET_S
            else:
                total = record.get("total_latency_s", 999)
                record["passed_budget"] = total < 3.0

        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        self._runs.append(record)
        return record

    def run_count(self) -> int:
        return len(self._runs)

    def get_session_stats(self) -> dict | None:
        """Compute min/max/avg for key latencies across all runs in this session."""
        if not self._runs:
            return None

        # Support both Stage 0 and Stage 1 field names
        keys = [
            "stt_latency_s", "first_audio_s", "total_latency_s",
            "llm_first_token_s", "llm_full_response_s", "tts_first_chunk_s",
        ]

        stats = {}
        for key in keys:
            values = [r[key] for r in self._runs if key in r and r[key] is not None]
            if values:
                stats[key] = {
                    "avg": round(statistics.mean(values), 4),
                    "min": round(min(values), 4),
                    "max": round(max(values), 4),
                }

        passed = sum(1 for r in self._runs if r.get("passed_budget", False))
        stats["pass_rate"] = f"{passed}/{len(self._runs)}"
        stats["run_count"] = len(self._runs)
        return stats
