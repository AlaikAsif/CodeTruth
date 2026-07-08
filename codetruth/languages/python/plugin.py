"""The v1 language plugin: full Python support."""
from __future__ import annotations

from pathlib import Path

from ...core.graph import CodeGraph
from ...core.models import Marker, Symbol
from ...core.plugin import LanguagePlugin, Rule
from . import edges as edges_mod
from . import rules as rules_mod
from .extractor import ModuleInfo, extract_repo, iter_config_files


class PythonPlugin(LanguagePlugin):
    name = "python"

    def extract(self, repo_path: Path) -> tuple[list[ModuleInfo], list[str]]:
        return extract_repo(repo_path)

    def build_index(self, modules: list[ModuleInfo]) -> edges_mod.SymbolIndex:
        return edges_mod.SymbolIndex(modules)

    def build_edges(self, repo_path: Path, modules: list[ModuleInfo],
                    index: edges_mod.SymbolIndex, graph: CodeGraph,
                    markers: list[Marker]) -> None:
        edges_mod.build_edges(modules, index, graph, markers)

    def rules(self) -> list[Rule]:
        return rules_mod.default_rules()

    def symbols(self, modules: list[ModuleInfo]) -> list[Symbol]:
        return [s for m in modules for s in m.symbols]

    def config_files(self, repo_path: Path) -> list[Path]:
        return list(iter_config_files(repo_path))
