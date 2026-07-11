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
        self._base_cache: dict[str, tuple[list[str], bool]] = {}
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

    # -- inheritance-aware member lookup ------------------------------------

    def _base_ids(self, class_id: str) -> tuple[list[str], bool]:
        """Internal base-class ids for a class, and whether every non-benign
        base was resolved (False => an external/unknown base could define
        members we can't see)."""
        cached = self._base_cache.get(class_id)
        if cached is not None:
            return cached
        cls = self.by_id.get(class_id)
        ids: list[str] = []
        complete = True
        benign_leaves = {x.split(".")[-1] for x in BENIGN_BASES}
        for base in (cls.bases if cls else ()):
            parts = base.split(".")
            leaf = parts[-1]
            if base in BENIGN_BASES or leaf in benign_leaves:
                continue
            resolved = None
            if len(parts) == 1:
                resolved = self.toplevel.get(cls.module, {}).get(leaf)
                if resolved is None:
                    cands = [s for s in self.by_name.get(leaf, ())
                             if s != class_id
                             and self.by_id[s].type is SymbolType.CLASS]
                    if len(cands) == 1:
                        resolved = cands[0]
            else:
                mod = ".".join(parts[:-1])
                if mod in self.modules:
                    resolved = self.toplevel.get(mod, {}).get(leaf)
                elif not self.is_internal_module_prefix(parts[0]):
                    resolved = None  # dotted external (e.g. textwrap.X)
            if resolved and resolved != class_id \
                    and self.by_id[resolved].type is SymbolType.CLASS:
                ids.append(resolved)
            else:
                complete = False
        self._base_cache[class_id] = (ids, complete)
        return ids, complete

    def returns_classes(self, func_id: str) -> frozenset:
        """Class ids named by a function/method's return annotation —
        `def make() -> Session` types `x = make()` as Session."""
        sym = self.by_id.get(func_id)
        if sym is None or not sym.returns:
            return frozenset()
        name = sym.returns.strip().strip("'\"").split("[")[0]
        parts = name.split(".")
        leaf = parts[-1]
        if not leaf or not leaf[0].isupper():
            return frozenset()   # cheap filter: classes are CamelCase
        sid = self.toplevel.get(sym.module, {}).get(leaf)
        if sid and self.by_id[sid].type is SymbolType.CLASS:
            return frozenset([sid])
        if len(parts) > 1:
            mod = ".".join(parts[:-1])
            sid = self.toplevel.get(mod, {}).get(leaf)
            if sid and self.by_id[sid].type is SymbolType.CLASS:
                return frozenset([sid])
        cands = [s for s in self.by_name.get(leaf, ())
                 if self.by_id[s].type is SymbolType.CLASS]
        if len(cands) == 1:
            return frozenset(cands)
        return frozenset()

    def resolve_member(self, class_id: str, name: str,
                       _seen: Optional[set] = None) -> tuple[Optional[str], bool]:
        """Look `name` up on a class and its internal base chain (MRO-ish).
        Returns (member_id | None, complete). complete=False means an
        unresolved base could define the member outside our view — callers
        must stay conservative on a miss."""
        seen = _seen or set()
        if class_id in seen:
            return None, True
        seen.add(class_id)
        member = self.class_members.get(class_id, {}).get(name)
        if member:
            return member, True
        bases, complete = self._base_ids(class_id)
        for base_id in bases:
            found, sub_complete = self.resolve_member(base_id, name, seen)
            complete = complete and sub_complete
            if found:
                return found, complete
        return None, complete


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


# Sentinel: a name was assigned something we can't type — never resolve
# member access on it through the type env (fall back to name-match fanout).
UNKNOWN = object()

_REFLECT_FUNCS = {"getattr", "setattr", "hasattr", "delattr"}

_ANNOTATION_WRAPPERS = {"Optional", "Annotated", "Final", "ClassVar"}


class EdgeVisitor(ast.NodeVisitor):
    def __init__(self, mi: ModuleInfo, scope: dict, idx: SymbolIndex,
                 graph: CodeGraph, markers: Optional[list] = None):
        self.mi = mi
        # Scope frames: (kind, names). Python rule: class-body names are NOT
        # visible inside methods — _lookup skips 'class' frames unless the
        # class body is the innermost frame (i.e. we're executing it).
        self.scopes: list[tuple[str, dict]] = [("module", scope)]
        self.idx = idx
        self.graph = graph
        self.markers = markers if markers is not None else []
        self.src_stack: list[str] = [mi.name]
        self.qual_stack: list[str] = []
        self.class_stack: list[str] = []   # class symbol ids
        self.self_types_stack: list[dict] = []  # instance-attr types per class
        self._handled: set[int] = set()
        # Module-level type env (function envs stack on top of it).
        self.type_envs: list[dict] = [
            self._collect_types(mi.tree.body if mi.tree else [])]

    # -- receiver typing ------------------------------------------------------

    def _annotation_classes(self, node) -> frozenset:
        """Class ids named by a type annotation (Optional/Annotated unwrapped,
        string forward-refs resolved through the current scope)."""
        if node is None:
            return frozenset()
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            name = node.value.strip().split("[")[0]
            node = ast.Name(id=name.split(".")[-1], ctx=ast.Load()) \
                if name.isidentifier() else None
            if node is None:
                return frozenset()
        if isinstance(node, ast.Subscript):
            head = node.value
            head_name = head.id if isinstance(head, ast.Name) else \
                (head.attr if isinstance(head, ast.Attribute) else "")
            if head_name in _ANNOTATION_WRAPPERS:
                inner = node.slice
                if isinstance(inner, ast.Tuple) and inner.elts:
                    inner = inner.elts[0]
                return self._annotation_classes(inner)
            return frozenset()
        if isinstance(node, (ast.Name, ast.Attribute)):
            parts, base = _collect_chain(node)
            if base is None:
                resolved, full = self._resolve_parts(parts)
                if resolved and full \
                        and self.idx.by_id[resolved].type is SymbolType.CLASS:
                    return frozenset([resolved])
        return frozenset()

    def _infer_value_classes(self, value):
        """Type of an assigned value: `Foo()` / a bare class reference ->
        {Foo}; `factory()` with `-> Session` -> {Session}; else UNKNOWN."""
        is_call = isinstance(value, ast.Call)
        if is_call:
            value = value.func
        if isinstance(value, (ast.Name, ast.Attribute)):
            parts, base = _collect_chain(value)
            if base is None:
                resolved, full = self._resolve_parts(parts)
                if resolved and full:
                    sym = self.idx.by_id[resolved]
                    if sym.type is SymbolType.CLASS:
                        return frozenset([resolved])
                    if is_call and sym.type in (SymbolType.FUNCTION,
                                                SymbolType.METHOD):
                        rclasses = self.idx.returns_classes(resolved)
                        if rclasses:
                            return rclasses
        return UNKNOWN

    def _collect_types(self, body, args: Optional[ast.arguments] = None) -> dict:
        """Flow-insensitive local type env for one scope: name -> frozenset of
        class ids, or UNKNOWN. Bindings *accumulate* (a name assigned Foo in
        one branch and Bar in another maps to {Foo, Bar} — over-approximate
        liveness, the safe direction); any un-typable assignment poisons the
        name to UNKNOWN."""
        env: dict = {}

        def bind(name: str, classes) -> None:
            if env.get(name) is UNKNOWN:
                return
            if classes is UNKNOWN or not classes:
                env[name] = UNKNOWN
            else:
                env[name] = env.get(name, frozenset()) | classes

        if args is not None:
            for a in (args.posonlyargs + args.args + args.kwonlyargs):
                if a.arg in ("self", "cls"):
                    continue
                classes = self._annotation_classes(a.annotation)
                if classes:
                    env[a.arg] = classes
            for a in (args.vararg, args.kwarg):
                if a is not None:
                    env[a.arg] = UNKNOWN

        def walk(stmts):
            for node in stmts:
                t = type(node)
                if t in (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef):
                    continue  # nested scopes get their own env
                if t is ast.Assign:
                    names = []
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name):
                            names.append(tgt.id)
                        elif isinstance(tgt, (ast.Tuple, ast.List)):
                            for e in tgt.elts:
                                if isinstance(e, ast.Name):
                                    bind(e.id, UNKNOWN)
                    if len(names) == 1:
                        bind(names[0], self._infer_value_classes(node.value))
                    else:
                        for n in names:
                            bind(n, UNKNOWN)
                elif t is ast.AnnAssign and isinstance(node.target, ast.Name):
                    classes = self._annotation_classes(node.annotation)
                    if not classes and node.value is not None:
                        classes = self._infer_value_classes(node.value)
                    bind(node.target.id, classes)
                elif t is ast.AugAssign and isinstance(node.target, ast.Name):
                    bind(node.target.id, UNKNOWN)
                elif t in (ast.For, ast.AsyncFor):
                    for e in ast.walk(node.target):
                        if isinstance(e, ast.Name):
                            bind(e.id, UNKNOWN)
                    walk(node.body)
                    walk(node.orelse)
                elif t in (ast.With, ast.AsyncWith):
                    for item in node.items:
                        if item.optional_vars is not None:
                            for e in ast.walk(item.optional_vars):
                                if isinstance(e, ast.Name):
                                    bind(e.id, UNKNOWN)
                    walk(node.body)
                elif t is ast.If:
                    walk(node.body)
                    walk(node.orelse)
                elif t is ast.Try:
                    walk(node.body)
                    walk(node.orelse)
                    walk(node.finalbody)
                    for h in node.handlers:
                        if h.name:
                            bind(h.name, UNKNOWN)
                        walk(h.body)
                elif t in (ast.While,):
                    walk(node.body)
                    walk(node.orelse)
                elif t in (ast.Global, ast.Nonlocal):
                    for n in node.names:
                        bind(n, UNKNOWN)
                else:
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.NamedExpr) \
                                and isinstance(sub.target, ast.Name):
                            bind(sub.target.id, UNKNOWN)

        walk(body)
        return env

    def _collect_self_types(self, class_node) -> dict:
        """Instance-attribute types for a class: `self.x = Foo()` anywhere in
        its methods -> x: {Foo}; conflicting/un-typable assignments poison."""
        env: dict = {}
        for stmt in class_node.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(stmt):
                targets = []
                value = None
                if isinstance(node, ast.Assign):
                    targets, value = node.targets, node.value
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    targets, value = [node.target], node.value
                for tgt in targets:
                    if isinstance(tgt, ast.Attribute) \
                            and isinstance(tgt.value, ast.Name) \
                            and tgt.value.id == "self":
                        if env.get(tgt.attr) is UNKNOWN:
                            continue
                        ann = self._annotation_classes(
                            node.annotation) if isinstance(
                            node, ast.AnnAssign) else frozenset()
                        classes = ann or self._infer_value_classes(value)
                        if classes is UNKNOWN:
                            env[tgt.attr] = UNKNOWN
                        else:
                            env[tgt.attr] = env.get(tgt.attr,
                                                    frozenset()) | classes
        return env

    def _typed_lookup(self, name: str):
        """Type-env stack lookup: frozenset of class ids, UNKNOWN, or None
        (name never typed in any visible scope)."""
        for env in reversed(self.type_envs):
            if name in env:
                return env[name]
        return None

    def _typed_member_edges(self, classes: frozenset, attr: str, node,
                            kind: EdgeKind) -> bool:
        """Emit strong member edges for a typed receiver. Returns True when
        the access is fully accounted for (member found on every class, or
        provably absent from a complete internal hierarchy) — in which case
        the caller must NOT fall back to name-match fanout."""
        accounted = True
        emitted = False
        for cls_id in classes:
            member, complete = self.idx.resolve_member(cls_id, attr)
            if member:
                self._emit(member, kind, EdgeStrength.STRONG, node,
                           f"typed receiver {self.idx.by_id[cls_id].name}"
                           f".{attr}")
                emitted = True
            elif not complete:
                accounted = False   # external base may define it
        return emitted or accounted

    def _lookup(self, name: str) -> tuple[Optional[str], Optional[str]]:
        top = len(self.scopes) - 1
        for i in range(top, -1, -1):
            kind, names = self.scopes[i]
            if kind == "class" and i != top:
                continue  # class bodies don't scope into nested functions
            if name in names:
                return names[name]
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
        # Descend through class members (inheritance-aware).
        while i < len(parts) and cur_id:
            sym = self.idx.by_id.get(cur_id)
            if sym and sym.type is SymbolType.CLASS:
                member, _complete = self.idx.resolve_member(cur_id, parts[i])
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
                cls_id = self.class_stack[-1]
                member, complete = self.idx.resolve_member(cls_id, parts[1])
                if member:
                    self._emit(member, kind, EdgeStrength.STRONG, node,
                               f"{parts[0]}.{parts[1]}")
                    return
                self_types = self.self_types_stack[-1] \
                    if self.self_types_stack else {}
                attr_type = self_types.get(parts[1])
                if isinstance(attr_type, frozenset) and attr_type:
                    # typed instance attribute: resolve the next hop on its
                    # class instead of fanning out on both segment names
                    if len(parts) >= 3:
                        if not self._typed_member_edges(attr_type, parts[2],
                                                        node, kind):
                            self._weak_name_edges(parts[2], node,
                                                  EdgeKind.ATTRIBUTE)
                    return
                if attr_type is UNKNOWN or complete:
                    # a known-but-untypable instance attr, or a fully-visible
                    # internal hierarchy with no such member: this access is
                    # instance data, not a reference to some same-named
                    # symbol elsewhere — no fanout
                    return
                self._weak_name_edges(parts[1], node, EdgeKind.ATTRIBUTE)
                return
            resolved, full = self._resolve_parts(parts)
            if resolved:
                self._emit(resolved, kind, EdgeStrength.STRONG, node,
                           ".".join(parts))
            if not full:
                # Typed receiver? `x = Foo(); x.method()` (or a module-level
                # variable with an inferred class) resolves through the class
                # instead of fanning out to every symbol named `method`.
                if len(parts) >= 2:
                    stopped_at_root = resolved is None or (
                        self.idx.by_id[resolved].type is SymbolType.VARIABLE
                        and self.idx.by_id[resolved].name == parts[0])
                    if stopped_at_root:
                        classes = self._typed_lookup(parts[0])
                        if isinstance(classes, frozenset) and classes \
                                and self._typed_member_edges(
                                    classes, parts[1], node, kind):
                            return
                # Fallback: weak name-match every unresolved attribute
                # segment, not just the last — `ctx.index.by_name` must keep
                # `.index` looking alive too.
                for attr in parts[1:]:
                    self._weak_name_edges(attr, node, EdgeKind.ATTRIBUTE)
            return
        # Attribute on a computed expression. If the expression is a direct
        # constructor call of a known class — `Extractor(x).run()` — resolve
        # the attribute as a strong member access (inheritance-aware).
        resolved_member = False
        if parts and isinstance(base_expr, ast.Call) \
                and isinstance(base_expr.func, (ast.Name, ast.Attribute)):
            fparts, fbase = _collect_chain(base_expr.func)
            if fbase is None:
                cls_id, full = self._resolve_parts(fparts)
                if cls_id and full:
                    callee = self.idx.by_id.get(cls_id)
                    classes = frozenset()
                    if callee and callee.type is SymbolType.CLASS:
                        classes = frozenset([cls_id])
                    elif callee and callee.type in (SymbolType.FUNCTION,
                                                    SymbolType.METHOD):
                        # `factory().method()` via the return annotation
                        classes = self.idx.returns_classes(cls_id)
                    if classes:
                        resolved_member = self._typed_member_edges(
                            classes, parts[0], node, kind)
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
        self.scopes.append(("class" if is_class else "function",
                            self._local_defs(node.body)))
        if is_class:
            self.type_envs.append({})   # class body: no local var typing
            self.self_types_stack.append(self._collect_self_types(node))
        else:
            self.type_envs.append(self._collect_types(node.body, node.args))
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
        self.type_envs.pop()
        if is_class:
            self.self_types_stack.pop()
        self.scopes.pop()
        if is_class and known:
            self.class_stack.pop()
        self.src_stack.pop()
        self.qual_stack.pop()

    # -- reference sites ------------------------------------------------------

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id in _REFLECT_FUNCS \
                and len(node.args) >= 2:
            self._handle_reflection(node)
            for arg in node.args:
                self.visit(arg)
            for kw in node.keywords:
                self.visit(kw.value)
            return
        if isinstance(node.func, (ast.Name, ast.Attribute)):
            self._handle_ref(node.func, EdgeKind.CALL)
        for arg in node.args:
            self.visit(arg)
        for kw in node.keywords:
            self.visit(kw.value)
        if not isinstance(node.func, (ast.Name, ast.Attribute)):
            self.visit(node.func)

    # -- reflection (getattr family), receiver-scoped ---------------------------

    def _receiver_classes(self, recv):
        """Classify a getattr/setattr receiver: frozenset of class ids,
        ('module', id), UNKNOWN, or None (no idea)."""
        if not isinstance(recv, (ast.Name, ast.Attribute)):
            return None
        parts, base = _collect_chain(recv)
        if base is not None:
            return None
        if parts == ["self"] and self.class_stack:
            return frozenset([self.class_stack[-1]])
        if parts[0] == "self" and len(parts) == 2 and self.self_types_stack:
            attr_type = self.self_types_stack[-1].get(parts[1])
            if isinstance(attr_type, frozenset) and attr_type:
                return attr_type
        if len(parts) == 1:
            typed = self._typed_lookup(parts[0])
            if isinstance(typed, frozenset) and typed:
                return typed
            if typed is UNKNOWN:
                return UNKNOWN
        resolved, full = self._resolve_parts(parts)
        if resolved and full:
            sym = self.idx.by_id[resolved]
            if sym.type is SymbolType.CLASS:
                return frozenset([resolved])
            if sym.type is SymbolType.MODULE:
                return ("module", resolved)
        return None

    def _all_member_ids(self, class_id: str, seen: Optional[set] = None) -> set:
        seen = seen or set()
        if class_id in seen:
            return set()
        seen.add(class_id)
        out = set(self.idx.class_members.get(class_id, {}).values())
        for base_id in self.idx._base_ids(class_id)[0]:
            out |= self._all_member_ids(base_id, seen)
        return out

    def _handle_reflection(self, node) -> None:
        """getattr/setattr/hasattr/delattr: literal names become precise weak
        edges; non-literal names poison only the receiver's namespace (the
        receiver's class members or the target module) instead of the whole
        containing module."""
        fname = node.func.id
        recv_type = self._receiver_classes(node.args[0])
        name_arg = node.args[1]
        literal = name_arg.value if isinstance(name_arg, ast.Constant) \
            and isinstance(name_arg.value, str) else None
        line = node.lineno

        if literal is not None:
            if isinstance(recv_type, frozenset):
                accounted = False
                for cls_id in recv_type:
                    member, complete = self.idx.resolve_member(cls_id, literal)
                    if member:
                        self._emit(member, EdgeKind.DYNAMIC, EdgeStrength.WEAK,
                                   node, f"{fname}(..., '{literal}')")
                        accounted = True
                    elif complete:
                        accounted = True
                if accounted:
                    return
            elif isinstance(recv_type, tuple):        # module receiver
                sid = self.idx.toplevel.get(recv_type[1], {}).get(literal)
                if sid:
                    self._emit(sid, EdgeKind.DYNAMIC, EdgeStrength.WEAK, node,
                               f"{fname}(..., '{literal}')")
                return
            # unknown receiver: conservative global name match
            for sid in self.idx.by_name.get(literal, ()):
                self._emit(sid, EdgeKind.DYNAMIC, EdgeStrength.WEAK, node,
                           f"{fname}(..., '{literal}')")
            return

        # non-literal attribute name
        if isinstance(recv_type, frozenset):
            for cls_id in recv_type:
                cls = self.idx.by_id[cls_id]
                for member_id in self._all_member_ids(cls_id):
                    self.markers.append(Marker(
                        member_id, MarkerKind.CAUTION,
                        f"non-literal {fname}() on a {cls.name} instance at "
                        f"{self.mi.rel_path}:{line} — this member may be "
                        "accessed dynamically", rule="python-reflection",
                        file=self.mi.rel_path, line=line))
            return
        if isinstance(recv_type, tuple):              # module receiver
            self.markers.append(Marker(
                recv_type[1], MarkerKind.DYNAMIC_MODULE,
                f"non-literal {fname}() on module at "
                f"{self.mi.rel_path}:{line}", rule="python-reflection",
                file=self.mi.rel_path, line=line))
            return
        # receiver unknown: poison the containing module (old behaviour)
        self.markers.append(Marker(
            self.mi.name, MarkerKind.DYNAMIC_MODULE,
            f"non-literal {fname}() at {self.mi.rel_path}:{line} — "
            "symbols in this module cannot be proved unreachable",
            rule="python-reflection", file=self.mi.rel_path, line=line))

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
        EdgeVisitor(mi, scope, idx, graph, markers).visit(mi.tree)

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
