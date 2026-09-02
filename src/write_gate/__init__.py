"""写库前门禁 (sql-write-gate): deterministic pre-write gate for SQL INSERTs."""

from write_gate.wrapper import WriteGate, Evidence

__all__ = ["WriteGate", "Evidence"]
__version__ = "0.1.0"
