"""Layer 1 — symbol extraction for Python via the ast module.

Walks every .py file in the repo and produces a flat symbol table:
modules, classes, functions, methods, and module-level variables, plus the
raw import statements and __all__ needed by the edge builder (Layer 2).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ...core.models import Symbol, SymbolType

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env", ".env",
    "node_modules", ".tox", ".nox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "build", "dist", ".eggs", "site-packages", ".idea", ".vscode",
    ".codetruth",  # CodeTruth's own cache — must never be scanned or fingerprinted
}

CONFIG_EXTS = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}


@dataclass
class ImportRec:
    kind: str          # "import" | "from"
    module: str        # dotted module ('' for `from . import x`)
    name: str          # imported name ('' for plain `import x`, '*' for star)
    asname: Optional[str]
    level: int         # relative-import level
    lineno: int


@dataclass
class ModuleInfo:
    name: str                       # dotted module name
    rel_path: str                   # repo-relative path, forward slashes
    abs_path: str
    is_test: bool
    is_package: bool                # file is an __init__.py
    tree: Optional[ast.Module] = None
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportRec] = field(default_factory=list)
    all_names: Optional[list[str]] = None   # contents of __all__ if present
    has_main_guard: bool = False


def is_test_path(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    base = parts[-1]
    if base == "conftest.py" or base.startswith("test_") or base.endswith("_test.py"):
        return True
    return any(p in ("tests", "test", "testing") for p in parts[:-1])


def _ignored(rel_posix: str, ignores: tuple[str, ...]) -> bool:
    if not ignores:
        return False
    from ...core.config import RepoConfig
    return RepoConfig(ignore_paths=list(ignores)).is_ignored(rel_posix)


def iter_py_files(root: Path, ignores: tuple[str, ...] = ()):
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if _ignored(rel.as_posix(), ignores):
            continue
        yield path


def iter_config_files(root: Path, ignores: tuple[str, ...] = ()):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in CONFIG_EXTS:
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if _ignored(rel.as_posix(), ignores):
            continue
        yield path


def module_name_for(path: Path, root: Path) -> tuple[str, bool]:
    """Dotted module name from the file path; returns (name, is_package)."""
    rel = path.relative_to(root)
    parts = list(rel.parts)
    is_package = parts[-1] == "__init__.py"
    if is_package:
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]  # strip .py
    if not parts:  # __init__.py at repo root
        parts = [root.name]
    return ".".join(parts), is_package


def dotted_name(node: ast.AST) -> Optional[str]:
    """Best-effort dotted name for decorators / base classes / call targets."""
    if isinstance(node, ast.Call):
        return dotted_name(node.func)
    if isinstance(node, ast.Attribute):
        base = dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_main_guard(node: ast.If) -> bool:
    t = node.test
    if isinstance(t, ast.Compare) and len(t.comparators) == 1:
        left, right = t.left, t.comparators[0]
        for a, b in ((left, right), (right, left)):
            if (isinstance(a, ast.Name) and a.id == "__name__"
                    and isinstance(b, ast.Constant) and b.value == "__main__"):
                return True
    return False


class _Extractor:
    def __init__(self, mi: ModuleInfo):
        self.mi = mi

    def run(self) -> None:
        mi = self.mi
        mod_sym = Symbol(
            id=mi.name, name=mi.name.rsplit(".", 1)[-1], qualname=mi.name,
            type=SymbolType.MODULE, file=mi.rel_path, line=1,
            end_line=getattr(mi.tree, "end_lineno", 1) or 1,
            module=mi.name, parent=None, exported=True, is_public=True,
            is_test=mi.is_test,
        )
        mi.symbols.append(mod_sym)
        self._walk_body(mi.tree.body, prefix="", parent=mi.name, in_class=False, depth=0)
        # Resolve exported flags now that __all__ is known.
        for s in mi.symbols:
            if s.type is SymbolType.MODULE or s.parent != mi.name:
                continue
            if mi.all_names is not None:
                s.exported = s.name in mi.all_names
            else:
                s.exported = not s.name.startswith("_")

    def _walk_body(self, body, prefix: str, parent: str, in_class: bool, depth: int):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_def(node, prefix, parent, in_class, depth, is_class=False)
            elif isinstance(node, ast.ClassDef):
                self._add_def(node, prefix, parent, in_class, depth, is_class=True)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                if depth == 0 or in_class:
                    self._add_variables(node, prefix, parent, depth)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                self._add_import(node)
            elif isinstance(node, ast.If):
                if depth == 0 and _is_main_guard(node):
                    self.mi.has_main_guard = True
                # Descend into conditional blocks at the same scope (covers
                # TYPE_CHECKING imports, platform-conditional defs, etc.).
                self._walk_body(node.body, prefix, parent, in_class, depth)
                self._walk_body(node.orelse, prefix, parent, in_class, depth)
            elif isinstance(node, ast.Try):
                for blk in (node.body, node.orelse, node.finalbody):
                    self._walk_body(blk, prefix, parent, in_class, depth)
                for handler in node.handlers:
                    self._walk_body(handler.body, prefix, parent, in_class, depth)
            elif isinstance(node, ast.With):
                self._walk_body(node.body, prefix, parent, in_class, depth)
            else:
                # Function-level imports still create aliases; catch them.
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        self._add_import(sub)

    def _add_def(self, node, prefix, parent, in_class, depth, is_class: bool):
        mi = self.mi
        qual = f"{prefix}{node.name}"
        sym_id = f"{mi.name}:{qual}"
        if is_class:
            stype = SymbolType.CLASS
        else:
            stype = SymbolType.METHOD if in_class else SymbolType.FUNCTION
        decorators = [d for d in (dotted_name(x) for x in node.decorator_list) if d]
        bases = []
        if is_class:
            bases = [b for b in (dotted_name(x) for x in node.bases) if b]
        sym = Symbol(
            id=sym_id, name=node.name, qualname=qual, type=stype,
            file=mi.rel_path, line=node.lineno,
            end_line=node.end_lineno or node.lineno, module=mi.name,
            parent=parent, exported=False,
            is_public=(depth == 0 or in_class) and not node.name.startswith("_"),
            is_test=mi.is_test, decorators=decorators, bases=bases,
        )
        mi.symbols.append(sym)
        # Function-level imports inside the def still matter for edges.
        self._walk_body(node.body, prefix=f"{qual}.", parent=sym_id,
                        in_class=is_class, depth=depth + 1)

    def _add_variables(self, node, prefix, parent, depth):
        mi = self.mi
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in targets:
            names = []
            if isinstance(t, ast.Name):
                names = [t.id]
            elif isinstance(t, (ast.Tuple, ast.List)):
                names = [e.id for e in t.elts if isinstance(e, ast.Name)]
            for name in names:
                if name == "__all__" and depth == 0:
                    mi.all_names = self._literal_str_list(node.value)
                    continue
                if name.startswith("__") and name.endswith("__"):
                    continue  # dunder module metadata (__version__, etc.)
                qual = f"{prefix}{name}"
                sym_id = f"{mi.name}:{qual}"
                if any(s.id == sym_id for s in mi.symbols):
                    continue  # reassignment — keep first definition site
                mi.symbols.append(Symbol(
                    id=sym_id, name=name, qualname=qual, type=SymbolType.VARIABLE,
                    file=mi.rel_path, line=node.lineno,
                    end_line=node.end_lineno or node.lineno, module=mi.name,
                    parent=parent, exported=False,
                    is_public=not name.startswith("_"), is_test=mi.is_test,
                ))

    @staticmethod
    def _literal_str_list(value) -> list[str]:
        out = []
        if isinstance(value, (ast.List, ast.Tuple)):
            for e in value.elts:
                if isinstance(e, ast.Constant) and isinstance(e.value, str):
                    out.append(e.value)
        return out

    def _add_import(self, node) -> None:
        mi = self.mi
        if isinstance(node, ast.Import):
            for alias in node.names:
                mi.imports.append(ImportRec("import", alias.name, "",
                                            alias.asname, 0, node.lineno))
        else:
            for alias in node.names:
                mi.imports.append(ImportRec("from", node.module or "", alias.name,
                                            alias.asname, node.level, node.lineno))


def extract_repo(repo_path: Path,
                 ignores: tuple[str, ...] = ()) -> tuple[list[ModuleInfo], list[str]]:
    """Parse every Python file under repo_path. Returns (modules, warnings)."""
    modules: list[ModuleInfo] = []
    warnings: list[str] = []
    for path in iter_py_files(repo_path, ignores):
        rel = path.relative_to(repo_path).as_posix()
        name, is_package = module_name_for(path, repo_path)
        mi = ModuleInfo(name=name, rel_path=rel, abs_path=str(path),
                        is_test=is_test_path(rel), is_package=is_package)
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            mi.tree = ast.parse(source, filename=rel)
        except (SyntaxError, ValueError) as exc:
            warnings.append(f"parse error in {rel}: {exc}")
            modules.append(mi)
            continue
        _Extractor(mi).run()
        modules.append(mi)
    return modules, warnings
