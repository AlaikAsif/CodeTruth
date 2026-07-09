"""Core data models shared by every layer and every language plugin."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SymbolType(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"


class EdgeKind(str, Enum):
    CALL = "call"
    IMPORT = "import"
    INHERIT = "inherit"
    ATTRIBUTE = "attribute"
    REFERENCE = "reference"
    STRING_REF = "string_ref"
    DYNAMIC = "dynamic"


class EdgeStrength(str, Enum):
    STRONG = "strong"
    WEAK = "weak"


class Status(str, Enum):
    SAFE_TO_DELETE = "safe_to_delete"
    LIKELY_DEAD = "likely_dead"
    UNCERTAIN_DYNAMIC_RISK = "uncertain_dynamic_risk"
    DEFINITELY_USED = "definitely_used"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Action(str, Enum):
    DELETE = "delete"
    REVIEW_REQUIRED = "review_required"
    KEEP = "keep"


class MarkerKind(str, Enum):
    # Proof of use: framework entry point, dunder, test, runtime observation.
    ENTRYPOINT = "entrypoint"
    RUNTIME_USED = "runtime_used"
    # Not proof of use, but a reason deletion is riskier than the graph shows.
    CAUTION = "caution"
    # Module contains non-literal reflection/eval — nothing in it is provably dead.
    DYNAMIC_MODULE = "dynamic_module"
    # Runtime tracking registered the symbol and observed zero calls.
    RUNTIME_ZERO = "runtime_zero"


@dataclass
class Symbol:
    """One node in the graph. id format: 'pkg.mod:Qual.name' ('pkg.mod' for modules)."""
    id: str
    name: str
    qualname: str
    type: SymbolType
    file: str          # repo-relative path, forward slashes
    line: int
    end_line: int
    module: str        # dotted module name the symbol lives in
    parent: Optional[str] = None   # enclosing symbol id (module id for top-level)
    exported: bool = False         # in __all__, or public top-level when no __all__
    is_public: bool = False        # top-level or method, name has no leading underscore
    is_test: bool = False          # defined in a test file
    decorators: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)  # dotted base names (classes only)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "qualname": self.qualname,
            "type": self.type.value, "file": self.file, "line": self.line,
            "end_line": self.end_line, "module": self.module, "parent": self.parent,
            "exported": self.exported, "is_public": self.is_public,
            "is_test": self.is_test, "decorators": self.decorators, "bases": self.bases,
        }


@dataclass
class Edge:
    """A directed usage edge: src uses dst. Strength is assigned at creation time."""
    src: str           # symbol id, or pseudo-node like 'file:config/app.yaml'
    dst: str           # symbol id
    kind: EdgeKind
    strength: EdgeStrength
    file: str
    line: int
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "src": self.src, "dst": self.dst, "kind": self.kind.value,
            "strength": self.strength.value, "file": self.file,
            "line": self.line, "detail": self.detail,
        }

    def describe(self) -> str:
        base = f"{self.kind.value} from {self.src} ({self.file}:{self.line})"
        return f"{base} — {self.detail}" if self.detail else base


@dataclass
class Marker:
    """A rule-produced fact about a symbol that isn't a graph edge."""
    symbol: str
    kind: MarkerKind
    reason: str
    rule: str = ""
    file: str = ""
    line: int = 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "kind": self.kind.value, "reason": self.reason,
            "rule": self.rule, "file": self.file, "line": self.line,
        }


@dataclass
class EvidenceRecord:
    """Layer 4 output: the structured record an agent reads before deciding."""
    symbol: str
    name: str
    type: SymbolType
    file: str
    line: int
    status: Status
    risk_level: RiskLevel
    recommended_action: Action
    evidence_for_deletion: list[str] = field(default_factory=list)
    evidence_against_deletion: list[str] = field(default_factory=list)
    inbound_strong: int = 0
    inbound_weak: int = 0
    exported: bool = False
    # Deterministic ordering heuristic for the review queue, in [0, 1]:
    # higher = weaker evidence of use = review/delete first. NOT a calibrated
    # probability (see PLAN.md §4) — it exists only to rank candidates within
    # a status so the strongest deletion targets surface first.
    rank_score: float = 0.0
    # Advisory description of what a deletion would involve (exact span,
    # orphaned imports, __all__ entry). Attached to safe_to_delete records;
    # never applied by the tool.
    deletion_plan: Optional[dict] = None
    # Ids of the unreachable clump this symbol belongs to (itself included)
    # when it is part of a dead cluster — reviewable/deletable as a group.
    cluster: Optional[list[str]] = None

    def to_dict(self) -> dict:
        d = {
            "symbol": self.symbol, "name": self.name, "type": self.type.value,
            "file": self.file, "line": self.line, "status": self.status.value,
            "risk_level": self.risk_level.value,
            "recommended_action": self.recommended_action.value,
            "rank_score": self.rank_score,
            "evidence_for_deletion": self.evidence_for_deletion,
            "evidence_against_deletion": self.evidence_against_deletion,
            "inbound_strong": self.inbound_strong, "inbound_weak": self.inbound_weak,
            "exported": self.exported,
        }
        if self.deletion_plan is not None:
            d["deletion_plan"] = self.deletion_plan
        if self.cluster:
            d["cluster"] = self.cluster
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "EvidenceRecord":
        return cls(
            symbol=d["symbol"], name=d["name"], type=SymbolType(d["type"]),
            file=d["file"], line=d["line"], status=Status(d["status"]),
            risk_level=RiskLevel(d["risk_level"]),
            recommended_action=Action(d["recommended_action"]),
            evidence_for_deletion=list(d.get("evidence_for_deletion", [])),
            evidence_against_deletion=list(d.get("evidence_against_deletion", [])),
            inbound_strong=d.get("inbound_strong", 0),
            inbound_weak=d.get("inbound_weak", 0),
            exported=d.get("exported", False),
            rank_score=d.get("rank_score", 0.0),
            deletion_plan=d.get("deletion_plan"),
            cluster=d.get("cluster"),
        )
