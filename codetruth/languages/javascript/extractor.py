"""Layer 1 — symbol extraction for JavaScript/TypeScript via tree-sitter.

Modules are files; a module's name is its repo-relative posix path without
extension (e.g. 'src/utils'). Symbol ids are '<module>:<qualname>' exactly
like the Python plugin, so every language-agnostic layer (graph, evidence,
ranking, backstop, cache, config) applies unchanged.

Requires the optional dependency group:  pip install codetruth[javascript]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ...core.models import Symbol, SymbolType

try:
    from tree_sitter_language_pack import get_parser
except ImportError:  # pragma: no cover
    get_parser = None

JS_EXTS = {".js", ".jsx", ".mjs", ".cjs"}
TS_EXTS = {".ts", ".mts", ".cts"}
TSX_EXTS = {".tsx"}
VUE_EXTS = {".vue"}
ALL_EXTS = JS_EXTS | TS_EXTS | TSX_EXTS | VUE_EXTS

SKIP_DIRS = {
    "node_modules", ".git", ".hg", "dist", "build", "out", ".next", ".nuxt",
    "coverage", ".turbo", ".cache", "vendor", ".codetruth", "__pycache__",
}

CONFIG_EXTS = {".json", ".yaml", ".yml", ".toml", ".html"}

_FUNC_VALUE_TYPES = {"arrow_function", "function_expression", "function",
                     "generator_function"}


_VUE_SCRIPT_RE = re.compile(
    rb"<script\b[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)


def _parser_for(path: Path):
    if path.suffix in TS_EXTS or path.suffix in VUE_EXTS:
        return get_parser("typescript")   # .vue script blocks are TS/JS
    if path.suffix in TSX_EXTS:
        return get_parser("tsx")
    return get_parser("javascript")


def _vue_script_source(raw: bytes) -> bytes:
    """Return a same-length buffer with only the <script> block(s) retained
    (everything else blanked, newlines preserved) so parsed line/byte
    positions still map to the original .vue file."""
    out = bytearray(len(raw))
    for i, b in enumerate(raw):
        out[i] = b if b == 0x0A else 0x20   # keep newlines, blank the rest
    found = False
    for m in _VUE_SCRIPT_RE.finditer(raw):
        found = True
        start, end = m.start(1), m.end(1)
        out[start:end] = raw[start:end]
    return bytes(out) if found else b""


@dataclass
class ImportRec:
    kind: str          # named | default | namespace | side | reexport | reexport_all
    source: str        # the module specifier string ('./utils', 'react')
    name: str          # imported name ('' for default/namespace/side)
    alias: str         # local binding ('' when none, e.g. side-effect import)
    line: int


@dataclass
class ModuleInfo:
    name: str
    rel_path: str
    abs_path: str
    is_test: bool
    source: bytes = b""
    tree: object = None                      # tree_sitter.Tree
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportRec] = field(default_factory=list)
    exports: set = field(default_factory=set)   # exported local names


def is_test_path(rel_path: str) -> bool:
    p = rel_path.replace("\\", "/").lower()
    base = p.rsplit("/", 1)[-1]
    return ("/__tests__/" in f"/{p}" or ".test." in base or ".spec." in base
            or any(part in ("tests", "test") for part in p.split("/")[:-1]))


def iter_source_files(root: Path, ignores: tuple[str, ...] = ()):
    from ...core.config import RepoConfig
    cfg = RepoConfig(ignore_paths=list(ignores)) if ignores else None
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in ALL_EXTS:
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name.endswith(".d.ts"):
            continue  # type declarations describe externals; no runtime code
        if cfg and cfg.is_ignored(rel.as_posix()):
            continue
        yield path


def iter_config_files(root: Path, ignores: tuple[str, ...] = ()):
    from ...core.config import RepoConfig
    cfg = RepoConfig(ignore_paths=list(ignores)) if ignores else None
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in CONFIG_EXTS:
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if cfg and cfg.is_ignored(rel.as_posix()):
            continue
        yield path


def module_name_for(path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    return rel.rsplit(".", 1)[0]


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


class _Extractor:
    def __init__(self, mi: ModuleInfo):
        self.mi = mi
        self.src = mi.source

    # -- helpers --------------------------------------------------------------

    def _add(self, name: str, qual: str, stype: SymbolType, node,
             parent: str, exported: bool) -> Symbol:
        sym = Symbol(
            id=f"{self.mi.name}:{qual}", name=name, qualname=qual, type=stype,
            file=self.mi.rel_path, line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1, module=self.mi.name,
            parent=parent, exported=exported, is_public=exported,
            is_test=self.mi.is_test,
        )
        self.mi.symbols.append(sym)
        return sym

    # -- main walk ------------------------------------------------------------

    def run(self) -> None:
        mi = self.mi
        root = mi.tree.root_node
        mi.symbols.append(Symbol(
            id=mi.name, name=mi.name.rsplit("/", 1)[-1], qualname=mi.name,
            type=SymbolType.MODULE, file=mi.rel_path, line=1,
            end_line=root.end_point[0] + 1, module=mi.name, parent=None,
            exported=True, is_public=True, is_test=mi.is_test,
        ))
        self._walk_statements(root, prefix="", parent=mi.name, exported=False)
        # Second pass: exported flags for names exported via clauses/CommonJS.
        for s in mi.symbols:
            if s.parent == mi.name and s.name in mi.exports:
                s.exported = True
                s.is_public = True

    def _walk_statements(self, container, prefix: str, parent: str,
                         exported: bool) -> None:
        for node in container.named_children:
            t = node.type
            if t == "export_statement":
                self._handle_export(node, prefix, parent)
            elif t in ("function_declaration", "generator_function_declaration"):
                self._add_function(node, prefix, parent, exported)
            elif t in ("class_declaration", "abstract_class_declaration"):
                self._add_class(node, prefix, parent, exported)
            elif t in ("lexical_declaration", "variable_declaration"):
                self._add_variables(node, prefix, parent, exported)
            elif t in ("interface_declaration", "enum_declaration"):
                self._add_named(node, prefix, parent, exported, SymbolType.CLASS)
            elif t == "type_alias_declaration":
                self._add_named(node, prefix, parent, exported,
                                SymbolType.VARIABLE)
            elif t == "import_statement":
                self._handle_import(node)
            elif t == "expression_statement":
                self._handle_expression(node)
            elif t in ("statement_block", "if_statement", "try_statement",
                       "for_statement", "while_statement", "labeled_statement"):
                self._walk_statements(node, prefix, parent, exported)

    # -- declarations -----------------------------------------------------------

    def _add_function(self, node, prefix, parent, exported):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = _text(name_node, self.src)
        qual = f"{prefix}{name}"
        sym = self._add(name, qual, SymbolType.FUNCTION, node, parent, exported)
        body = node.child_by_field_name("body")
        if body is not None:
            self._walk_statements(body, prefix=f"{qual}.", parent=sym.id,
                                  exported=False)

    def _add_class(self, node, prefix, parent, exported):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = _text(name_node, self.src)
        qual = f"{prefix}{name}"
        sym = self._add(name, qual, SymbolType.CLASS, node, parent, exported)
        # Superclass name recorded for the external-base caution logic.
        heritage = next((c for c in node.named_children
                         if c.type in ("class_heritage", "extends_clause")), None)
        if heritage is not None:
            base = _text(heritage, self.src)
            base = base.replace("extends", "").strip().split("{")[0].strip()
            if base:
                sym.bases.append(base.split("(")[0].strip())
        body = node.child_by_field_name("body")
        if body is None:
            return
        for member in body.named_children:
            if member.type == "method_definition":
                mname_node = member.child_by_field_name("name")
                if mname_node is None:
                    continue
                mname = _text(mname_node, self.src)
                msym = self._add(mname, f"{qual}.{mname}", SymbolType.METHOD,
                                 member, sym.id, exported=False)
                # Methods are reachable on any instance — public unless the
                # name is conventionally/lexically private.
                msym.is_public = not mname.startswith(("_", "#"))
            elif member.type in ("field_definition", "public_field_definition"):
                fname_node = member.child_by_field_name("property") \
                    or member.child_by_field_name("name")
                value = member.child_by_field_name("value")
                if fname_node is None:
                    continue
                fname = _text(fname_node, self.src)
                stype = SymbolType.METHOD if value is not None \
                    and value.type in _FUNC_VALUE_TYPES else SymbolType.VARIABLE
                fsym = self._add(fname, f"{qual}.{fname}", stype, member,
                                 sym.id, exported=False)
                fsym.is_public = not fname.startswith(("_", "#"))

    def _add_named(self, node, prefix, parent, exported, stype):
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            name = _text(name_node, self.src)
            self._add(name, f"{prefix}{name}", stype, node, parent, exported)

    def _add_variables(self, node, prefix, parent, exported):
        for decl in node.named_children:
            if decl.type != "variable_declarator":
                continue
            name_node = decl.child_by_field_name("name")
            if name_node is None or name_node.type != "identifier":
                continue  # destructuring handled by import logic when relevant
            name = _text(name_node, self.src)
            value = decl.child_by_field_name("value")
            # `const m = require('./x')` is an import, not a symbol.
            if value is not None and self._maybe_require(name, value):
                continue
            stype = SymbolType.FUNCTION if value is not None \
                and value.type in _FUNC_VALUE_TYPES else SymbolType.VARIABLE
            qual = f"{prefix}{name}"
            sym = self._add(name, qual, stype, decl, parent, exported)
            if value is not None and value.type in _FUNC_VALUE_TYPES:
                body = value.child_by_field_name("body")
                if body is not None and body.type == "statement_block":
                    self._walk_statements(body, prefix=f"{qual}.",
                                          parent=sym.id, exported=False)

    # -- imports / exports ------------------------------------------------------

    def _maybe_require(self, local_name: str, value) -> bool:
        """`const x = require('lit')` → namespace import; returns True if so."""
        if value.type == "call_expression":
            fn = value.child_by_field_name("function")
            args = value.child_by_field_name("arguments")
            if fn is not None and _text(fn, self.src) == "require" \
                    and args is not None and args.named_child_count == 1 \
                    and args.named_children[0].type == "string":
                source = _text(args.named_children[0], self.src).strip("'\"`")
                self.mi.imports.append(ImportRec(
                    "namespace", source, "", local_name,
                    value.start_point[0] + 1))
                return True
        return False

    def _handle_import(self, node) -> None:
        src_node = node.child_by_field_name("source")
        if src_node is None:
            return
        source = _text(src_node, self.src).strip("'\"`")
        line = node.start_point[0] + 1
        clause = next((c for c in node.named_children
                       if c.type == "import_clause"), None)
        if clause is None:
            self.mi.imports.append(ImportRec("side", source, "", "", line))
            return
        for child in clause.named_children:
            if child.type == "identifier":  # default import
                self.mi.imports.append(ImportRec(
                    "default", source, "default", _text(child, self.src), line))
            elif child.type == "namespace_import":
                ident = next((c for c in child.named_children
                              if c.type == "identifier"), None)
                if ident is not None:
                    self.mi.imports.append(ImportRec(
                        "namespace", source, "", _text(ident, self.src), line))
            elif child.type == "named_imports":
                for spec in child.named_children:
                    if spec.type != "import_specifier":
                        continue
                    name_node = spec.child_by_field_name("name")
                    alias_node = spec.child_by_field_name("alias")
                    name = _text(name_node, self.src) if name_node else ""
                    alias = _text(alias_node, self.src) if alias_node else name
                    self.mi.imports.append(ImportRec(
                        "named", source, name, alias, line))

    def _handle_export(self, node, prefix, parent) -> None:
        line = node.start_point[0] + 1
        src_node = node.child_by_field_name("source")
        source = _text(src_node, self.src).strip("'\"`") if src_node else None

        decl = node.child_by_field_name("declaration")
        if decl is not None:
            t = decl.type
            if t in ("function_declaration", "generator_function_declaration"):
                self._add_function(decl, prefix, parent, exported=True)
            elif t in ("class_declaration", "abstract_class_declaration"):
                self._add_class(decl, prefix, parent, exported=True)
            elif t in ("lexical_declaration", "variable_declaration"):
                self._add_variables(decl, prefix, parent, exported=True)
            elif t in ("interface_declaration", "enum_declaration"):
                self._add_named(decl, prefix, parent, True, SymbolType.CLASS)
            elif t == "type_alias_declaration":
                self._add_named(decl, prefix, parent, True, SymbolType.VARIABLE)
            elif t == "identifier":  # export default someName
                self.mi.exports.add(_text(decl, self.src))
            return

        # `export * from './m'` / `export { a, b as c } [from './m']`
        for child in node.named_children:
            if child.type == "export_clause":
                for spec in child.named_children:
                    if spec.type != "export_specifier":
                        continue
                    name_node = spec.child_by_field_name("name")
                    if name_node is None:
                        continue
                    name = _text(name_node, self.src)
                    if source:
                        self.mi.imports.append(ImportRec(
                            "reexport", source, name, "", line))
                    else:
                        self.mi.exports.add(name)
            elif child.type in ("identifier",):  # export default ident (fallback)
                self.mi.exports.add(_text(child, self.src))
        if source and not any(c.type == "export_clause"
                              for c in node.named_children):
            self.mi.imports.append(ImportRec("reexport_all", source, "*", "",
                                             line))

    def _handle_expression(self, node) -> None:
        """CommonJS export forms: module.exports = X / exports.name = X."""
        expr = node.named_children[0] if node.named_child_count else None
        if expr is None or expr.type != "assignment_expression":
            return
        left = expr.child_by_field_name("left")
        right = expr.child_by_field_name("right")
        if left is None or right is None:
            return
        lhs = _text(left, self.src)
        if lhs == "module.exports":
            if right.type == "identifier":
                self.mi.exports.add(_text(right, self.src))
            elif right.type == "object":
                for pair in right.named_children:
                    if pair.type == "shorthand_property_identifier":
                        self.mi.exports.add(_text(pair, self.src))
                    elif pair.type == "pair":
                        v = pair.child_by_field_name("value")
                        if v is not None and v.type == "identifier":
                            self.mi.exports.add(_text(v, self.src))
        elif lhs.startswith("exports.") or lhs.startswith("module.exports."):
            if right.type == "identifier":
                self.mi.exports.add(_text(right, self.src))
            self.mi.exports.add(lhs.rsplit(".", 1)[-1])


def extract_repo(repo_path: Path,
                 ignores: tuple[str, ...] = ()) -> tuple[list[ModuleInfo], list[str]]:
    if get_parser is None:
        raise ImportError(
            "The JavaScript plugin needs tree-sitter: "
            "pip install codetruth[javascript]")
    modules: list[ModuleInfo] = []
    warnings: list[str] = []
    for path in iter_source_files(repo_path, ignores):
        rel = path.relative_to(repo_path).as_posix()
        mi = ModuleInfo(name=module_name_for(path, repo_path), rel_path=rel,
                        abs_path=str(path), is_test=is_test_path(rel))
        try:
            mi.source = path.read_bytes()
            if path.suffix in VUE_EXTS:
                # Parse only the <script> block; keep raw bytes for _text so
                # symbol names read correctly, positions stay file-accurate.
                script = _vue_script_source(mi.source)
                if not script:
                    modules.append(mi)   # no <script>: template-only component
                    continue
                mi.tree = _parser_for(path).parse(script)
            else:
                mi.tree = _parser_for(path).parse(mi.source)
        except (OSError, ValueError) as exc:
            warnings.append(f"parse error in {rel}: {exc}")
            modules.append(mi)
            continue
        _Extractor(mi).run()
        modules.append(mi)
    return modules, warnings
