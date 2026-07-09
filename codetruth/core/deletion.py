"""Advisory deletion plans: *describe* exactly what removing a symbol would
involve — the tool never applies any of it (advisory-only by design).

A plan answers the three questions a reviewer has to work out by hand:
1. What is the exact span to remove (decorators through end of block,
   including trailing blank lines)?
2. Which imports in the same file become orphaned — their only remaining
   user is the symbol being removed?
3. Does anything else reference the name administratively (an `__all__`
   entry) that would need cleanup?
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional

from .models import Symbol, SymbolType


def build_deletion_plan(repo: Path, sym: Symbol) -> Optional[dict]:
    """Compute an advisory plan for one symbol. Returns None when the file
    can't be parsed or the definition can't be located (never raises)."""
    if sym.type is SymbolType.MODULE:
        return {
            "symbol": sym.id, "file": sym.file, "kind": "module",
            "note": "whole-file removal candidate — review the module, then "
                    "remove the file and any references to the module name",
        }
    try:
        source = (repo / sym.file).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError, ValueError):
        return None

    node = _locate(tree, sym.qualname)
    if node is None:
        return None

    lines = source.splitlines()
    start = node.lineno
    if getattr(node, "decorator_list", None):
        start = min(d.lineno for d in node.decorator_list)
    end = node.end_lineno or node.lineno
    # Consume trailing blank lines so the removal leaves no gap.
    end_with_blanks = end
    while end_with_blanks < len(lines) and not lines[end_with_blanks].strip():
        end_with_blanks += 1

    plan = {
        "symbol": sym.id,
        "file": sym.file,
        "kind": sym.type.value,
        "span": {"start_line": start, "end_line": end},
        "span_with_trailing_blanks": {"start_line": start,
                                      "end_line": end_with_blanks},
        "orphaned_imports": _orphaned_imports(tree, start, end),
        "note": "advisory only — CodeTruth never applies deletions",
    }

    dunder_all = _all_entry_line(tree, sym.name)
    if dunder_all is not None:
        plan["dunder_all_entry"] = {
            "file": sym.file, "line": dunder_all,
            "note": f"remove '{sym.name}' from __all__",
        }
    return plan


def _locate(tree: ast.Module, qualname: str) -> Optional[ast.AST]:
    """Walk the def/class nesting to the node matching a dotted qualname.
    Variables resolve to their (first) assignment statement."""
    parts = qualname.split(".")
    body = tree.body
    node: Optional[ast.AST] = None
    for i, part in enumerate(parts):
        node = _find_in_body(body, part)
        if node is None:
            return None
        if i < len(parts) - 1:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)):
                return None
            body = node.body
    return node


def _find_in_body(body, name: str) -> Optional[ast.AST]:
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and stmt.name == name:
            return stmt
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return stmt
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) \
                and stmt.target.id == name:
            return stmt
        if isinstance(stmt, (ast.If, ast.Try, ast.With)):
            blocks = [getattr(stmt, "body", [])] + \
                     [getattr(stmt, "orelse", [])] + \
                     [getattr(stmt, "finalbody", [])] + \
                     [h.body for h in getattr(stmt, "handlers", [])]
            for blk in blocks:
                found = _find_in_body(blk, name)
                if found is not None:
                    return found
    return None


def _orphaned_imports(tree: ast.Module, start: int, end: int) -> list[dict]:
    """Imports whose bound name is used ONLY inside the [start, end] span —
    they become dead once the span is removed."""
    # bound name -> import statement line
    bound: dict[str, int] = {}
    import_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_lines.add(node.lineno)
            for alias in node.names:
                bound[alias.asname or alias.name.split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            import_lines.add(node.lineno)
            for alias in node.names:
                if alias.name != "*":
                    bound[alias.asname or alias.name] = node.lineno

    if not bound:
        return []

    # name -> line numbers of every load-context usage outside import stmts
    usages: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) \
                and node.lineno not in import_lines:
            usages.setdefault(node.id, []).append(node.lineno)
        elif isinstance(node, ast.Attribute):
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.lineno not in import_lines:
                usages.setdefault(root.id, []).append(root.lineno)

    orphaned = []
    for name, imp_line in sorted(bound.items(), key=lambda kv: kv[1]):
        lines = usages.get(name, [])
        inside = [ln for ln in lines if start <= ln <= end]
        outside = [ln for ln in lines if not (start <= ln <= end)]
        if inside and not outside:
            orphaned.append({"name": name, "import_line": imp_line,
                             "note": "only used by the symbol being removed"})
    return orphaned


def _all_entry_line(tree: ast.Module, name: str) -> Optional[int]:
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name) and t.id == "__all__":
                    if isinstance(stmt.value, (ast.List, ast.Tuple)):
                        for e in stmt.value.elts:
                            if isinstance(e, ast.Constant) and e.value == name:
                                return stmt.lineno
    return None
