"""Language-plugin and rule interfaces. The core engine only talks to these."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .graph import CodeGraph
from .models import Edge, Marker, Symbol

if TYPE_CHECKING:
    pass


@dataclass
class RuleContext:
    """Everything a Layer 3 rule may inspect, plus sinks for its findings."""
    repo_path: Path
    modules: list[Any]           # language-specific ModuleInfo objects
    index: Any                   # language-specific symbol index
    graph: CodeGraph
    markers: list[Marker]
    config_files: list[Path] = field(default_factory=list)

    def add_edge(self, edge: Edge) -> None:
        self.graph.add_edge(edge)

    def add_marker(self, marker: Marker) -> None:
        self.markers.append(marker)


class Rule(ABC):
    """One Layer 3 pattern detector. Emits weak edges and/or markers."""
    id: str = "rule"

    @abstractmethod
    def apply(self, ctx: RuleContext) -> None: ...


class LanguagePlugin(ABC):
    """A language implementation: extraction (L1), edges (L2), rules (L3)."""
    name: str = ""

    @abstractmethod
    def extract(self, repo_path: Path,
                ignores: tuple[str, ...] = ()) -> tuple[list[Any], list[str]]:
        """Return (modules, warnings). Each module carries its symbols.
        `ignores` are user-configured path globs to skip entirely."""

    @abstractmethod
    def build_index(self, modules: list[Any]) -> Any: ...

    @abstractmethod
    def build_edges(self, repo_path: Path, modules: list[Any], index: Any,
                    graph: CodeGraph, markers: list[Marker]) -> None: ...

    @abstractmethod
    def rules(self) -> list[Rule]: ...

    @abstractmethod
    def symbols(self, modules: list[Any]) -> list[Symbol]: ...


_PLUGINS: dict[str, LanguagePlugin] = {}


def register_plugin(plugin: LanguagePlugin) -> None:
    _PLUGINS[plugin.name] = plugin


def get_plugin(name: str) -> LanguagePlugin:
    if name not in _PLUGINS:
        # Lazy-load built-in plugins on first request.
        if name == "python":
            from ..languages.python.plugin import PythonPlugin
            register_plugin(PythonPlugin())
        elif name in ("javascript", "typescript", "js", "ts"):
            from ..languages.javascript.plugin import JavaScriptPlugin
            plugin = JavaScriptPlugin()
            for alias in ("javascript", "typescript", "js", "ts"):
                _PLUGINS[alias] = plugin
        elif name == "go":
            raise NotImplementedError(
                "The 'go' plugin is a stub — python and javascript ship today.")
        else:
            raise ValueError(f"Unknown language plugin: {name!r}")
    return _PLUGINS[name]
