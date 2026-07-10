"""Layer 2 — relationship graph construction for Python.

Second pass over each module's AST with the full cross-repo symbol table
available. Produces directed edges (call / import / inherit / attribute /
reference) classified strong or weak at creation time:

- strong: the reference resolves deterministically to a repo symbol
  (direct call, explicit import, inheritance, `self.method()`).
- weak: name-based matching only (attribute access on an unresolved object
  matches every symbol with that name — like `vulture`'s conservative model).
"""
from __future__ import annotations

import ast
from collections import defaultdict
from typing import Optional

from ...core.graph import CodeGraph
from ...core.models import (Edge, EdgeKind, EdgeStrength, Marker, MarkerKind,
                            Symbol, SymbolType)
from .extractor import ModuleInfo

MAX_NAME_MATCHES = 60  # skip weak fan-out beyond this (too generic to mean anything)

# External bases that don't imply framework callbacks on subclass methods.
BENIGN_BASES = {
    "object", "Exception", "BaseException", "ValueError", "TypeError",
    "RuntimeError", "KeyError", "AttributeError", "OSError", "IOError",
    "NotImplementedError", "StopIteration", "Enum", "IntEnum", "StrEnum",
    "enum.Enum", "enum.IntEnum", "enum.StrEnum", "str", "int", "float",
    "dict", "list", "set", "tuple", "frozenset", "bytes", "NamedTuple",
    "typing.NamedTuple", "TypedDict", "typing.TypedDict", "Protocol",
    "typing.Protocol", "ABC", "abc.ABC", "Generic", "typing.Generic",
    "BaseModel", "pydantic.BaseModel",  # pydantic models: fields, not callbacks
}


class SymbolIndex:
    """Cross-repo lookup tables built once from all extracted modules."""

    def __init__(self, modules: list[ModuleInfo]):
        self.modules: dict[str, ModuleInfo] = {m.name: m for m in modules}
        self.by_id: dict[str, Symbol] = {}
        self.toplevel: dict[str, dict[str, str]] = defaultdict(dict)
        self.by_name: dict[str, list[str]] = defaultdict(list)
        self.class_members: dict[str, dict[str, str]] = defaultdict(dict)
        self._module_prefixes: set[str] = set()
        for m in modules:
            for part_count in range(1, len(m.name.split(".")) + 1):
                self._module_prefixes.add(".".join(m.name.split(".")[:part_count]))
            for s in m.symbols:
                self.by_id[s.id] = s
                if s.type is SymbolType.MODULE:
                    continue
                self.by_name[s.name].append(s.id)
                if s.parent == m.name:
                    self.toplevel[m.name][s.name] = s.id
                elif s.parent and s.parent in self.by_id \
                        and self.by_id[s.parent].type is SymbolType.CLASS:
                    self.class_members[s.parent][s.name] = s.id

    def is_internal_module_prefix(self, name: str) -> bool:
        return name in self._module_prefixes

    def all_symbols(self) -> list[Symbol]:
        return list(self.by_id.values())


def _relative_base(mi: ModuleInfo, level: int) -> Optional[str]:
    """Package that a relative import of the given level resolves against."""
    parts = mi.name.split(".")
    if not mi.is_package:
        parts = parts[:-1]
    drop = level - 1
    if drop > len(parts):
        return None
    return ".".join(parts[:len(parts) - drop]) if parts[:len(parts) - drop] else None


def build_scope(mi: ModuleInfo, idx: SymbolIndex, graph: CodeGraph) -> dict:
    """Map local names -> ('symbol', id) | ('module', name) | ('ext', None).
    Emits strong import edges as a side effect."""
    scope: dict[str, tuple[str, Optional[str]]] = {}
    for name, sid in idx.toplevel.get(mi.name, {}).items():
        scope[name] = ("symbol", sid)

    for imp in mi.imports:
        if imp.kind == "import":
            target = imp.module
            internal = idx.is_internal_module_prefix(target)
            if imp.asname:
                scope[imp.asname] = ("module", target) if internal else ("ext", None)
            else:
                top = target.split(".")[0]
                scope[top] = ("module", top) if idx.is_internal_module_prefix(top) \
                    else ("ext", None)
            if internal and target in idx.modules:
                graph.add_edge(Edge(mi.name, target, EdgeKind.IMPORT,
                                    EdgeStrength.STRONG, mi.rel_path, imp.lineno,
                                    f"import {target}"))
            continue

        # from-import
        if imp.level > 0:
            base = _relative_base(mi, imp.level)
            if base is None:
                continue
            base = f"{base}.{imp.module}" if imp.module else base
        else:
            base = imp.module

        if imp.name == "*":
            src_mod = idx.modules.get(base)
            if src_mod is not None:
                graph.add_edge(Edge(mi.name, base, EdgeKind.IMPORT,
                                    EdgeStrength.STRONG, mi.rel_path, imp.lineno,
                                    f"from {base} import *"))
                for name, sid in idx.toplevel.get(base, {}).items():
                    sym = idx.by_id[sid]
                    if src_mod.all_names is not None:
                        if sym.name in src_mod.all_names:
                            scope[name] = ("symbol", sid)
                    elif not name.startswith("_"):
                        scope[name] = ("symbol", sid)
            continue

        bound = imp.asname or imp.name
        sid = idx.toplevel.get(base, {}).get(imp.name)
        if sid:
            scope[bound] = ("symbol", sid)
            graph.add_edge(Edge(mi.name, sid, EdgeKind.IMPORT,
                                EdgeStrength.STRONG, mi.rel_path, imp.lineno,
                                f"from {base} import {imp.name}"))
            # Importing a name from a module is also a use of the module.
            if base in idx.modules and base != mi.name:
                graph.add_edge(Edge(mi.name, base, EdgeKind.IMPORT,
                                    EdgeStrength.STRONG, mi.rel_path,
                                    imp.lineno, f"from {base} import ..."))
        elif f"{base}.{imp.name}" in idx.modules:
            sub = f"{base}.{imp.name}"
            scope[bound] = ("module", sub)
            graph.add_edge(Edge(mi.name, sub, EdgeKind.IMPORT,
                                EdgeStrength.STRONG, mi.rel_path, imp.lineno,
                                f"from {base} import {imp.name}"))
        elif base in idx.modules:
            # Name lives in an internal module but wasn't extracted (e.g.
            # re-export chain). Credit the module; keeps it provably used.
            graph.add_edge(Edge(mi.name, base, EdgeKind.IMPORT,
                                EdgeStrength.STRONG, mi.rel_path, imp.lineno,
                                f"from {base} import {imp.name}"))
            scope[bound] = ("ext", None)
        else:
            scope[bound] = ("ext", None)
    return scope


def _collect_chain(node: ast.AST) -> tuple[list[str], Optional[ast.AST]]:
    """Split `a.b.c` into (['a','b','c'], None), or (['send'], base_expr)
    for `expr().send` where the base is not a plain name chain."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        parts.reverse()
        return parts, None
    parts.reverse()
    return parts, cur


class EdgeVisitor(ast.NodeVisitor):
    def __init__(self, mi: ModuleInfo, scope: dict, idx: SymbolIndex,
                 graph: CodeGraph):
        self.mi = mi
        self.scopes: list[dict] = [scope]  # innermost last
        self.idx = idx
        self.graph = graph
        self.src_stack: list[str] = [mi.name]
        self.qual_stack: list[str] = []
        self.class_stack: list[str] = []   # class symbol ids
        self._handled: set[int] = set()

    def _lookup(self, name: str) -> tuple[Optional[str], Optional[str]]:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None, None

    def _local_defs(self, body) -> dict:
        """Names of defs nested directly in this body (incl. conditional
        blocks) — they are referenced by bare name in the enclosing scope."""
        local: dict = {}
        prefix = ".".join(self.qual_stack)

        def collect(stmts):
            for node in stmts:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)):
                    qual = f"{prefix}.{node.name}" if prefix else node.name
                    sid = f"{self.mi.name}:{qual}"
                    if sid in self.idx.by_id:
                        local[node.name] = ("symbol", sid)
                elif isinstance(node, ast.If):
                    collect(node.body)
                    collect(node.orelse)
                elif isinstance(node, ast.Try):
                    collect(node.body)
                    collect(node.orelse)
                    collect(node.finalbody)
                    for h in node.handlers:
                        collect(h.body)
                elif isinstance(node, ast.With):
                    collect(node.body)

        collect(body)
        return local

    # -- helpers ------------------------------------------------------------

    @property
    def src(self) -> str:
        return self.src_stack[-1]

    def _emit(self, dst: str, kind: EdgeKind, strength: EdgeStrength,
              node: ast.AST, detail: str = "") -> None:
        self.graph.add_edge(Edge(self.src, dst, kind, strength,
                                 self.mi.rel_path, getattr(node, "lineno", 0),
                                 detail))

    def _resolve_parts(self, parts: list[str]) -> tuple[Optional[str], bool]:
        """Resolve a dotted name chain against scope + index.
        Returns (deepest resolved symbol id or None, fully_resolved)."""
        kind, target = self._lookup(parts[0])
        if kind is None or kind == "ext":
            return None, False
        i = 1
        cur_id: Optional[str] = None
        if kind == "symbol":
            cur_id = target
        else:  # module
            cur_mod = target
            while i < len(parts):
                nxt_sym = self.idx.toplevel.get(cur_mod, {}).get(parts[i])
                if nxt_sym:
                    cur_id = nxt_sym
                    i += 1
                    break
                sub = f"{cur_mod}.{parts[i]}"
                if sub in self.idx.modules:
                    cur_mod = sub
                    i += 1
                else:
                    return (cur_mod if cur_mod in self.idx.by_id else None), False
            else:
                return (cur_mod if cur_mod in self.idx.by_id else None), True
        # Descend through class members.
        while i < len(parts) and cur_id:
            sym = self.idx.by_id.get(cur_id)
            if sym and sym.type is SymbolType.CLASS:
                member = self.idx.class_members.get(cur_id, {}).get(parts[i])
                if member:
                    cur_id = member
                    i += 1
                    continue
            return cur_id, False
        return cur_id, i >= len(parts)

    def _weak_name_edges(self, attr: str, node: ast.AST, kind: EdgeKind) -> None:
        if attr.startswith("__") or len(attr) < 2:
            return
        matches = self.idx.by_name.get(attr, [])
        if not matches or len(matches) > MAX_NAME_MATCHES:
            return
        for sid in matches:
            if sid == self.src:
                continue
            self._emit(sid, kind, EdgeStrength.WEAK, node,
                       f"attribute name match '.{attr}'")

    def _mark_handled_chain(self, node: ast.AST) -> Optional[ast.AST]:
        """Mark the Attribute/Name chain as handled; return the non-chain
        base expression (still needs visiting), if any."""
        cur = node
        while isinstance(cur, ast.Attribute):
            self._handled.add(id(cur))
            cur = cur.value
        if isinstance(cur, ast.Name):
            self._handled.add(id(cur))
            return None
        return cur

    def _handle_ref(self, node: ast.AST, kind: EdgeKind) -> None:
        """Shared logic for Name / Attribute loads and call targets."""
        parts, base_expr = _collect_chain(node)
        rest = self._mark_handled_chain(node)
        if base_expr is None:
            # Chain rooted at a plain name: self/cls handling first.
            if parts[0] in ("self", "cls") and self.class_stack and len(parts) >= 2:
                member = self.idx.class_members.get(self.class_stack[-1], {}) \
                    .get(parts[1])
                if member:
                    self._emit(member, kind, EdgeStrength.STRONG, node,
                               f"{parts[0]}.{parts[1]}")
                else:
                    self._weak_name_edges(parts[1], node, EdgeKind.ATTRIBUTE)
                return
            resolved, full = self._resolve_parts(parts)
            if resolved:
                self._emit(resolved, kind, EdgeStrength.STRONG, node,
                           ".".join(parts))
            if not full:
                # Weak name-match every unresolved attribute segment, not
                # just the last — `ctx.index.by_name` must keep `.index`
                # looking alive too.
                for attr in parts[1:]:
                    self._weak_name_edges(attr, node, EdgeKind.ATTRIBUTE)
            return
        # Attribute on a computed expression. If the expression is a direct
        # constructor call of a known class — `Extractor(x).run()` — resolve
        # the attribute as a strong member access.
        resolved_member = False
        if parts and isinstance(base_expr, ast.Call) \
                and isinstance(base_expr.func, (ast.Name, ast.Attribute)):
            fparts, fbase = _collect_chain(base_expr.func)
            if fbase is None:
                cls_id, full = self._resolve_parts(fparts)
                if cls_id and full:
                    cls = self.idx.by_id.get(cls_id)
                    if cls and cls.type is SymbolType.CLASS:
                        member = self.idx.class_members.get(cls_id, {}) \
                            .get(parts[0])
                        if member:
                            self._emit(member, kind, EdgeStrength.STRONG, node,
                                       f"{'.'.join(fparts)}(...).{parts[0]}")
                            resolved_member = True
        if parts and not resolved_member:
            for attr in parts:
                self._weak_name_edges(attr, node, EdgeKind.ATTRIBUTE)
        if rest is not None:
            self.visit(rest)

    # -- definition scoping ---------------------------------------------------

    def _current_def_id(self) -> str:
        return f"{self.mi.name}:{'.'.join(self.qual_stack)}"

    def visit_FunctionDef(self, node):
        self._visit_def(node, is_class=False)

    def visit_AsyncFunctionDef(self, node):
        self._visit_def(node, is_class=False)

    def visit_ClassDef(self, node):
        self._visit_def(node, is_class=True)

    def _visit_def(self, node, is_class: bool):
        # Decorators, defaults, annotations, and bases execute in the
        # enclosing scope — visit them before pushing the new scope.
        for dec in node.decorator_list:
            self.visit(dec)
        if is_class:
            pass  # bases handled below with the class as src
        else:
            for d in list(node.args.defaults) + [d for d in node.args.kw_defaults if d]:
                self.visit(d)
            # Signature annotations are real usage: `def f(u: User) -> Order`
            # keeps User and Order alive (FastAPI-style code often references
            # a model *only* in annotations / response_model chains).
            args = node.args
            for a in (args.posonlyargs + args.args + args.kwonlyargs
                      + ([args.vararg] if args.vararg else [])
                      + ([args.kwarg] if args.kwarg else [])):
                if a.annotation is not None:
                    self.visit(a.annotation)
            if node.returns is not None:
                self.visit(node.returns)

        self.qual_stack.append(node.name)
        sym_id = self._current_def_id()
        known = sym_id in self.idx.by_id
        self.src_stack.append(sym_id if known else self.src_stack[-1])
        # Defs nested in this body are referenced by bare name from here.
        self.scopes.append(self._local_defs(node.body))
        if is_class and known:
            self.class_stack.append(sym_id)
            for base in node.bases:
                parts, base_expr = _collect_chain(base)
                if base_expr is None and parts:
                    self._mark_handled_chain(base)
                    resolved, full = self._resolve_parts(parts)
                    if resolved and full:
                        self._emit(resolved, EdgeKind.INHERIT,
                                   EdgeStrength.STRONG, base, ".".join(parts))
        for child in node.body:
            self.visit(child)
        self.scopes.pop()
        if is_class and known:
            self.class_stack.pop()
        self.src_stack.pop()
        self.qual_stack.pop()

    # -- reference sites ------------------------------------------------------

    def visit_Call(self, node):
        if isinstance(node.func, (ast.Name, ast.Attribute)):
            self._handle_ref(node.func, EdgeKind.CALL)
        for arg in node.args:
            self.visit(arg)
        for kw in node.keywords:
            self.visit(kw.value)
        if not isinstance(node.func, (ast.Name, ast.Attribute)):
            self.visit(node.func)

    def visit_Attribute(self, node):
        if id(node) in self._handled:
            return
        if isinstance(node.ctx, ast.Load):
            self._handle_ref(node, EdgeKind.ATTRIBUTE)
        else:
            self.generic_visit(node)

    def visit_Name(self, node):
        if id(node) in self._handled:
            return
        if isinstance(node.ctx, ast.Load):
            kind, target = self._lookup(node.id)
            if kind == "symbol" and target != self.src:
                self._emit(target, EdgeKind.REFERENCE, EdgeStrength.STRONG,
                           node, node.id)
            elif kind == "module" and target in self.idx.by_id:
                self._emit(target, EdgeKind.REFERENCE, EdgeStrength.STRONG,
                           node, node.id)


def build_edges(modules: list[ModuleInfo], idx: SymbolIndex, graph: CodeGraph,
                markers: list[Marker]) -> None:
    for sym in idx.all_symbols():
        graph.add_symbol(sym)

    for mi in modules:
        if mi.tree is None:
            continue
        scope = build_scope(mi, idx, graph)
        EdgeVisitor(mi, scope, idx, graph).visit(mi.tree)

    # Methods on classes with unresolvable (external) bases may satisfy an
    # interface the framework calls — flag them so they never look provably dead.
    for mi in modules:
        for sym in mi.symbols:
            if sym.type is not SymbolType.CLASS or not sym.bases:
                continue
            external = [b for b in sym.bases
                        if b.split(".")[-1] not in {x.split(".")[-1] for x in BENIGN_BASES}
                        and b not in BENIGN_BASES
                        and not _base_is_internal(b, sym, mi, idx)]
            if not external:
                continue
            for member_id in idx.class_members.get(sym.id, {}).values():
                member = idx.by_id[member_id]
                if member.type is SymbolType.METHOD and not member.name.startswith("__"):
                    markers.append(Marker(
                        member_id, MarkerKind.CAUTION,
                        f"class {sym.qualname} inherits external base "
                        f"{external[0]!r}; this method may implement its interface",
                        rule="external-base", file=sym.file, line=sym.line))


def _base_is_internal(base: str, cls: Symbol, mi: ModuleInfo,
                      idx: SymbolIndex) -> bool:
    """True if the base name resolves to a class defined in this repo.
    Must never match the subclass itself — `class TextWrapper(textwrap.
    TextWrapper)` extends the stdlib, not itself."""
    parts = base.split(".")
    if len(parts) > 1:
        # Dotted base: internal only when the root is an internal module
        # or a name defined in this module.
        return (idx.is_internal_module_prefix(parts[0])
                or parts[0] in idx.toplevel.get(mi.name, {}))
    for m in idx.modules.values():
        sid = idx.toplevel.get(m.name, {}).get(base)
        if sid and sid != cls.id and idx.by_id[sid].type is SymbolType.CLASS:
            return True
    return False
