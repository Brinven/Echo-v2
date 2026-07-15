"""
GPU / VRAM probe for Echo (Stage 8.3).

Why this exists: this machine almost always has something else on the GPU — Invoke generating
images, or a model Michael forgot he left loaded in LM Studio (his words, 2026-07-15). Echo's
12B is NOT resident at launch; LM Studio JIT-loads it on the first request, so a VRAM shortage
bites at the first thing Michael says, not at startup.

That failure is badly disguised. LM Studio drops the connection when a load OOMs, which surfaces
as an `APIConnectionError` — indistinguishable from "the server is down" — and Echo used to print
"LM Studio not detected", sending him to check a server that was running fine. Worse, the
OpenAI-compatible `/v1/models` keeps listing every model regardless of whether it is loaded, so
nothing in that view hints at the real problem. (Known cross-project gotcha; also in the Hindsight
`axly-infra` bank.)

So: read the actual numbers and say them. Windows-native via nvidia-smi (subprocess), no new
dependency — the same approach as location.py's probes. Fail-soft everywhere: any problem returns
None / "" and the caller simply loses the hint.
"""

import logging
import subprocess

logger = logging.getLogger(__name__)

# nvidia-smi is fast (~50ms) but this runs on a health poll, so cap it hard.
_PROBE_TIMEOUT_S = 2.0


def vram_usage() -> tuple[int, int] | None:
    """(used_mb, total_mb) for GPU 0, or None if nvidia-smi isn't available/parseable."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits", "--id=0"],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode != 0:
            return None
        used, total = (int(x.strip()) for x in out.stdout.strip().split(",")[:2])
        return used, total
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        logger.debug(f"nvidia-smi probe failed ({e})")
        return None


def vram_hint() -> str:
    """One line naming VRAM as a suspect, with real numbers. '' if the GPU can't be read.

    Deliberately reports the numbers instead of guessing a 'too full' threshold: what counts as
    tight depends on the model being loaded, and a wrong guess would be worse than no guess.
    """
    usage = vram_usage()
    if usage is None:
        return ""
    used, total = usage
    return (
        f"VRAM: {used / 1024:.1f} of {total / 1024:.1f} GB already in use. If that's most of the "
        "card, something else is holding it (Invoke? a model left loaded in LM Studio?) and the "
        "model can't load. `lms ps` lists what's resident — unload it, or set LM Studio's "
        "Max Loaded Models to 1."
    )
