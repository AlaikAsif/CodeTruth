"""CodeTruth — a verification layer that lets AI agents safely delete code.

    from codetruth import scan, check_deletion_safety, track
"""
from .api import check_deletion_safety, scan
from .core.models import (Action, Edge, EvidenceRecord, RiskLevel, Status,
                          Symbol)
from .runtime import track

__version__ = "0.1.0"

__all__ = [
    "scan", "check_deletion_safety", "track",
    "Status", "RiskLevel", "Action", "Symbol", "Edge", "EvidenceRecord",
    "__version__",
]
