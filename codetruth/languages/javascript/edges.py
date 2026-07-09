"""Layer 2 — relationship graph construction for JavaScript/TypeScript.

Same contract as the Python edge builder: resolve what we can determinately
(imports, lexical references, namespace member access, `this.method`) into
STRONG edges; fall back to conservative name-matching WEAK edges for property
access on unresolved objects — JS is dynamic enough that the weak tier does a
lot of the safety work here.
"""
from __future__ import annotations

import posixpath
from collections import defaultdict
from typing import Optional

from ...core.graph import CodeGraph
from ...core.models import (Edge, EdgeKind, EdgeStrength, Marker, MarkerKind,
                            Symbol, SymbolType)
from .extractor import ModuleInfo, _text

MAX_NAME_MATCHES = 60

# Base classes that don't imply framework-invoked methods on subclasses.
BENIGN_BASES = {"Object", "Error", "TypeError", "RangeError", "Array", "Map",
                "Set", "Promise", "EventTarget"}

_INDEX_CANDIDATES = ("/index", "/index.js", "/index.ts", "/index.tsx",
                     "/index.jsx")


class SymbolIndex:
    def __init__(self, modules: list[ModuleInfo]):
        self.modules: dict[str, ModuleInfo] = {m.name: m for m in modules}
        self.by_id: dict[str, Symbol] = {}
        self.toplevel: dict[str, dict[str, str]] = defaultdict(dict)
        self.by_name: dict[str, list[str]] = defaultdict(list)
        self.children: dict[str, dict[str, str]] = defaultdict(dict)
        for m in modules:
            for s in m.symbols:
                self.by_id[s.id] = s
                if s.type is SymbolType.MODULE:
                    continue
                self.by_name[s.name].append(s.id)
                if s.parent == m.name:
                    self.toplevel[m.name][s.name] = s.id
                if s.parent:
                    self.children[s.parent][s.name] = s.id

    def all_symbols(self) -> list[Symbol]:
        return list(self.by_id.values())

    def resolve_source(self, importer: str, source: str) -> Optional[str]:
        """'./utils' relative to 'src/app' -> 'src/utils' (or None if
        external / not found)."""
        if not source.startswith("."):
            return None
        base_dir = posixpath.dirname(importer)
        target = posixpath.normpath(posixpath.join(base_dir, source))
        # strip a literal extension if the import included one
        for ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
            if target.endswith(ext):
                target = target[: -len(ext)]
                break
        if target in self.modules:
            return target
        for suffix in _INDEX_CANDIDATES:
            cand = posixpath.normpath(target + suffix)
            cand = cand.rsplit(".", 1)[0] if "." in cand.rsplit("/", 1)[-1] else cand
            if cand in self.modules:
                return cand
        if f"{target}/index" in self.modules:
            return f"{target}/index"
        return None


def build_scope(mi: ModuleInfo, idx: SymbolIndex, graph: CodeGraph) -> dict:
    scope: dict[str, tuple[str, Optional[str]]] = {}
    for name, sid in idx.toplevel.get(mi.name, {}).items():
        scope[name] = ("symbol", sid)

    for imp in mi.imports:
        target_mod = idx.resolve_source(mi.name, imp.source)
        if target_mod is None:
            if imp.alias:
                scope[imp.alias] = ("ext", None)
            continue
        graph.add_edge(Edge(mi.name, target_mod, EdgeKind.IMPORT,
                            EdgeStrength.STRONG, mi.rel_path, imp.line,
                            f"import from '{imp.source}'"))
        if imp.kind == "named" or imp.kind == "reexport":
            sid = idx.toplevel.get(target_mod, {}).get(imp.name)
            if sid:
                graph.add_edge(Edge(mi.name, sid, EdgeKind.IMPORT,
                                    EdgeStrength.STRONG, mi.rel_path, imp.line,
                                    f"import {{ {imp.name} }} from "
                                    f"'{imp.source}'"))
                if imp.alias:
                    scope[imp.alias] = ("symbol", sid)
            elif imp.alias:
                scope[imp.alias] = ("module", target_mod)
        elif imp.kind in ("default", "namespace"):
            if imp.alias:
                scope[imp.alias] = ("module", target_mod)
            # default import also credits an exported `default`-named symbol
            sid = idx.toplevel.get(target_mod, {}).get("default")
            if sid:
                graph.add_edge(Edge(mi.name, sid, EdgeKind.IMPORT,
                                    EdgeStrength.STRONG, mi.rel_path,
                                    imp.line, "default import"))
        elif imp.kind == "reexport_all":
            for sid in idx.toplevel.get(target_mod, {}).values():
                sym = idx.by_id[sid]
                if sym.exported:
                    graph.add_edge(Edge(mi.name, sid, EdgeKind.IMPORT,
                                        EdgeStrength.STRONG, mi.rel_path,
                                        imp.line,
                                        f"export * from '{imp.source}'"))
    return scope


class EdgeWalker:
    """Recursive tree walk emitting reference edges. Skips identifiers in
    declaration positions (names, params, import clauses, object keys)."""

    SKIP_SUBTREES = {"import_statement", "formal_parameters",
                     "type_parameters", "comment"}
    _DECL_NAME_PARENTS = {
        "function_declaration", "generator_function_declaration",
        "class_declaration", "abstract_class_declaration",
        "variable_declarator", "method_definition", "field_definition",
        "public_field_definition", "interface_declaration",
        "enum_declaration", "type_alias_declaration",
        "required_parameter", "optional_parameter",
    }

    def __init__(self, mi: ModuleInfo, scope: dict, idx: SymbolIndex,
                 graph: CodeGraph):
        self.mi = mi
        self.idx = idx
        self.graph = graph
        self.scopes: list[dict] = [scope]
        self.src_stack: list[str] = [mi.name]
        self.qual_stack: list[str] = []
        self.class_stack: list[str] = []

    # -- helpers ---------------------------------------------------------------

    def _lookup(self, name: str):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None, None

    def _emit(self, dst: str, kind: EdgeKind, strength: EdgeStrength, node,
              detail: str = "") -> None:
        src = self.src_stack[-1]
        self.graph.add_edge(Edge(src, dst, kind, strength, self.mi.rel_path,
                                 node.start_point[0] + 1, detail))

    def _weak_name_edges(self, name: str, node) -> None:
        if len(name) < 2:
            return
        matches = self.idx.by_name.get(name, [])
        if not matches or len(matches) > MAX_NAME_MATCHES:
            return
        for sid in matches:
            if sid != self.src_stack[-1]:
                self._emit(sid, EdgeKind.ATTRIBUTE, EdgeStrength.WEAK, node,
                           f"property name match '.{name}'")

    def _local_defs(self, sym_id: str) -> dict:
        return {name: ("symbol", cid)
                for name, cid in self.idx.children.get(sym_id, {}).items()}

    # -- walk --------------------------------------------------------------------

    def walk(self, node) -> None:
        t = node.type
        if t in self.SKIP_SUBTREES:
            return
        if t == "identifier" or t == "type_identifier":
            # handled by parent dispatch; identifiers reached here are loads
            self._ref(node)
            return
        if t == "member_expression":
            self._member(node)
            return
        if t == "subscript_expression":
            self._subscript(node)
            return

        entering = None
        if t in ("function_declaration", "generator_function_declaration",
                 "class_declaration", "abstract_class_declaration",
                 "method_definition"):
            entering = self._enter_named(node)
        elif t == "variable_declarator":
            entering = self._enter_declarator(node)

        for i, child in enumerate(node.children):
            if not child.is_named:
                continue
            field = node.field_name_for_child(i)
            if field == "name" and t in self._DECL_NAME_PARENTS:
                continue
            if field == "property" and t in ("field_definition",
                                             "public_field_definition"):
                continue
            if field == "key" and t == "pair":
                continue
            if t == "export_statement" and field is None \
                    and child.type == "export_clause":
                continue
            self.walk(child)

        if entering:
            self._exit(*entering)

    # -- scoped declarations ------------------------------------------------------

    def _enter_named(self, node):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        name = _text(name_node, self.mi.source)
        self.qual_stack.append(name)
        sym_id = f"{self.mi.name}:{'.'.join(self.qual_stack)}"
        known = sym_id in self.idx.by_id
        self.src_stack.append(sym_id if known else self.src_stack[-1])
        self.scopes.append(self._local_defs(sym_id) if known else {})
        is_class = node.type in ("class_declaration",
                                 "abstract_class_declaration")
        if is_class and known:
            self.class_stack.append(sym_id)
        return (is_class and known,)

    def _enter_declarator(self, node):
        name_node = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if name_node is None or name_node.type != "identifier" \
                or value is None or value.type not in (
                    "arrow_function", "function_expression", "function",
                    "generator_function"):
            return None
        name = _text(name_node, self.mi.source)
        self.qual_stack.append(name)
        sym_id = f"{self.mi.name}:{'.'.join(self.qual_stack)}"
        known = sym_id in self.idx.by_id
        self.src_stack.append(sym_id if known else self.src_stack[-1])
        self.scopes.append(self._local_defs(sym_id) if known else {})
        return (False,)

    def _exit(self, was_class: bool) -> None:
        if was_class:
            self.class_stack.pop()
        self.scopes.pop()
        self.src_stack.pop()
        self.qual_stack.pop()

    # -- reference emission ---------------------------------------------------------

    def _ref(self, node) -> None:
        name = _text(node, self.mi.source)
        kind, target = self._lookup(name)
        if kind == "symbol" and target != self.src_stack[-1]:
            self._emit(target, EdgeKind.REFERENCE, EdgeStrength.STRONG, node,
                       name)
        elif kind == "module" and target in self.idx.by_id:
            self._emit(target, EdgeKind.REFERENCE, EdgeStrength.STRONG, node,
                       name)

    def _member(self, node) -> None:
        obj = node.child_by_field_name("object")
        prop = node.child_by_field_name("property")
        prop_name = _text(prop, self.mi.source) if prop is not None else ""
        if obj is not None and obj.type == "identifier":
            oname = _text(obj, self.mi.source)
            kind, target = self._lookup(oname)
            if kind == "module":
                sid = self.idx.toplevel.get(target, {}).get(prop_name)
                if sid:
                    self._emit(sid, EdgeKind.REFERENCE, EdgeStrength.STRONG,
                               node, f"{oname}.{prop_name}")
                else:
                    self._emit(target, EdgeKind.REFERENCE, EdgeStrength.STRONG,
                               node, oname)
                return
            if kind == "symbol":
                sym = self.idx.by_id.get(target)
                if sym is not None and sym.type is SymbolType.CLASS:
                    member = self.idx.children.get(target, {}).get(prop_name)
                    self._emit(target, EdgeKind.REFERENCE, EdgeStrength.STRONG,
                               node, oname)
                    if member:
                        self._emit(member, EdgeKind.ATTRIBUTE,
                                   EdgeStrength.STRONG, node,
                                   f"{oname}.{prop_name}")
                    return
                self._emit(target, EdgeKind.REFERENCE, EdgeStrength.STRONG,
                           node, oname)
                if prop_name:
                    self._weak_name_edges(prop_name, node)
                return
        if obj is not None and obj.type == "this" and self.class_stack:
            member = self.idx.children.get(self.class_stack[-1], {}) \
                .get(prop_name)
            if member:
                self._emit(member, EdgeKind.ATTRIBUTE, EdgeStrength.STRONG,
                           node, f"this.{prop_name}")
                return
        if obj is not None:
            self.walk(obj)
        if prop_name:
            self._weak_name_edges(prop_name, node)

    def _subscript(self, node) -> None:
        obj = node.child_by_field_name("object")
        index = node.child_by_field_name("index")
        if obj is not None:
            self.walk(obj)
        if index is not None:
            if index.type == "string":
                literal = _text(index, self.mi.source).strip("'\"`")
                for sid in self.idx.by_name.get(literal, []):
                    self._emit(sid, EdgeKind.DYNAMIC, EdgeStrength.WEAK, node,
                               f"computed access ['{literal}']")
            else:
                self.walk(index)


def build_edges(modules: list[ModuleInfo], idx: SymbolIndex, graph: CodeGraph,
                markers: list[Marker]) -> None:
    for sym in idx.all_symbols():
        graph.add_symbol(sym)
    for mi in modules:
        if mi.tree is None:
            continue
        scope = build_scope(mi, idx, graph)
        EdgeWalker(mi, scope, idx, graph).walk(mi.tree.root_node)

    # Methods on classes extending something unresolvable may implement a
    # framework interface (React lifecycle, web components, ORM hooks).
    for mi in modules:
        for sym in mi.symbols:
            if sym.type is not SymbolType.CLASS or not sym.bases:
                continue
            base = sym.bases[0]
            root = base.split(".")[0]
            local = idx.toplevel.get(mi.name, {})
            imported = any(imp.alias == root or imp.name == root
                           for imp in mi.imports
                           if idx.resolve_source(mi.name, imp.source))
            if base in BENIGN_BASES or root in local or imported:
                continue
            for member_id in idx.children.get(sym.id, {}).values():
                member = idx.by_id[member_id]
                if member.type is SymbolType.METHOD:
                    markers.append(Marker(
                        member_id, MarkerKind.CAUTION,
                        f"class {sym.qualname} extends external base "
                        f"{base!r}; this method may implement its interface",
                        rule="external-base", file=sym.file, line=sym.line))
