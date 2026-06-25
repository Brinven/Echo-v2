"""
Ib-Lite — Echo's self-contained SQLite memory subsystem.

Replaces the external OpenMemory/Hindsight HTTP backend. Five typed memory
tables (Core, Policy, Preference, Fact, Episodic), FTS5 keyword search,
sqlite-vec cosine search, and a 12B-model-driven significance gate.

The pipeline only ever imports `IbLite`.
"""

from .ib_lite import IbLite

__all__ = ["IbLite"]
