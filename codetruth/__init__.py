"""CodeTruth — a verification layer that lets AI agents safely delete code.

    from codetruth import scan, check_deletion_safety, track
"""
from .api import check_deletion_safety, plan_deletion, scan, scan_repos
from .core.models import (Action, Edge, EvidenceRecord, RiskLevel, Status,
                          Symbol)
from .runtime import track

__version__ = "0.6.1"

__all__ = [
    "scan", "scan_repos", "check_deletion_safety", "plan_deletion", "track",
    "Status", "RiskLevel", "Action", "Symbol", "Edge", "EvidenceRecord",
    "__version__",
]
