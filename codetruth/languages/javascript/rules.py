"""Layer 3 — semantic safety rules for JavaScript/TypeScript.

The JS ecosystem wires code through package metadata and dynamic constructs;
these rules surface those indirect usage paths as markers/weak edges.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ...core.models import (Edge, EdgeKind, EdgeStrength, Marker, MarkerKind,
                            SymbolType)
from ...core.plugin import Rule, RuleContext
from .extractor import _text

IDENT_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")

COMMON_WORDS = {
    "main", "test", "data", "name", "type", "value", "true", "false", "null",
    "undefined", "default", "error", "index", "utils", "string", "number",
    "object", "click", "change", "submit", "content", "message", "props",
    "state", "children", "class", "style", "text", "html", "json", "body",
}


_FILE_TOKEN_RE = re.compile(r"[\w./-]+\.(?:jsx?|tsx?|mjs|cjs)")


class PackageJsonEntrypointRule(Rule):
    """Modules the package exposes or runs are entry points: main/module/bin/
    exports (loaded by external consumers), and `scripts` targets (run by
    `npm run`). External consumers and the shell are invisible to the graph."""
    id = "js-package-json-entrypoints"

    def apply(self, ctx: RuleContext) -> None:
        pkg = ctx.repo_path / "package.json"
        if not pkg.is_file():
            return
        try:
            doc = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        specs: list[tuple[str, str]] = []  # (spec, why)
        for key in ("main", "module", "browser", "types"):
            if isinstance(doc.get(key), str):
                specs.append((doc[key], key))
        bin_field = doc.get("bin")
        if isinstance(bin_field, str):
            specs.append((bin_field, "bin"))
        elif isinstance(bin_field, dict):
            specs.extend((v, "bin") for v in bin_field.values()
                         if isinstance(v, str))
        for spec in self._flatten_exports(doc.get("exports")):
            specs.append((spec, "exports"))
        # scripts: "start": "node src/server.js" -> src/server.js runs
        scripts = doc.get("scripts")
        if isinstance(scripts, dict):
            for cmd in scripts.values():
                if isinstance(cmd, str):
                    for tok in _FILE_TOKEN_RE.findall(cmd):
                        specs.append((tok, "scripts"))

        for spec, why in specs:
            mod = ctx.index.resolve_source("package", "./" + spec.lstrip("./"))
            if mod is not None:
                ctx.add_marker(Marker(
                    mod, MarkerKind.ENTRYPOINT,
                    f"package.json {why} ('{spec}') — loaded/run externally",
                    rule=self.id, file="package.json", line=1))

    def _flatten_exports(self, exports) -> list[str]:
        out: list[str] = []
        if isinstance(exports, str):
            out.append(exports)
        elif isinstance(exports, dict):
            for v in exports.values():
                out.extend(self._flatten_exports(v))
        return out


# Method names that register a callback invoked later by a framework/runtime.
_REGISTRATION_VERBS = {
    # Express / Fastify / Koa / router
    "get", "post", "put", "delete", "patch", "options", "head", "all",
    "use", "route", "register", "addHook",
    # event emitters / DOM / signals / observables
    "on", "once", "off", "addListener", "prependListener",
    "addEventListener", "subscribe", "connect", "listen", "watch",
}


class CallbackEntrypointRule(Rule):
    """Functions passed to a registration call — `app.get('/x', handler)`,
    `emitter.on('evt', handler)`, `server.listen(port, handler)` — are
    invoked by the framework/runtime, not by any call site. Mark the
    resolved handler symbol as an entry point (so it survives strict mode and
    carries clear evidence). Over-marking here is safe: it can only keep code,
    never mislabel used code as deletable."""
    id = "js-callback-entrypoints"

    def apply(self, ctx: RuleContext) -> None:
        for mi in ctx.modules:
            if mi.tree is None:
                continue
            bindings = _module_bindings(mi, ctx.index)
            self._scan(ctx, mi, mi.tree.root_node, bindings)

    def _scan(self, ctx, mi, node, bindings) -> None:
        if node.type == "call_expression":
            self._check_call(ctx, mi, node, bindings)
        for child in node.named_children:
            self._scan(ctx, mi, child, bindings)

    def _check_call(self, ctx, mi, node, bindings) -> None:
        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if fn is None or args is None or fn.type != "member_expression":
            return
        prop = fn.child_by_field_name("property")
        verb = _text(prop, mi.source) if prop is not None else ""
        if verb not in _REGISTRATION_VERBS:
            return
        for arg in args.named_children:
            if arg.type != "identifier":
                continue
            sid = bindings.get(_text(arg, mi.source))
            if sid and ctx.index.by_id[sid].type in (
                    SymbolType.FUNCTION, SymbolType.METHOD):
                ctx.add_marker(Marker(
                    sid, MarkerKind.ENTRYPOINT,
                    f"registered as a callback via `.{verb}(...)` — invoked "
                    "by the framework/runtime", rule=self.id,
                    file=mi.rel_path, line=node.start_point[0] + 1))


def _module_bindings(mi, idx) -> dict:
    """name -> symbol id for names usable in this module (its own top-level
    symbols plus named imports resolving to repo symbols)."""
    b: dict[str, str] = dict(idx.toplevel.get(mi.name, {}))
    for imp in mi.imports:
        if imp.kind != "named" or not imp.alias:
            continue
        tgt = idx.resolve_source(mi.name, imp.source)
        if tgt is None:
            continue
        sid = idx.resolve_export(tgt, imp.name)   # follow barrel chains
        if sid:
            b[imp.alias] = sid
    return b


class ConstructorRule(Rule):
    """`constructor` is invoked implicitly via `new Class()`."""
    id = "js-constructor"

    def apply(self, ctx: RuleContext) -> None:
        for sym in ctx.index.all_symbols():
            if sym.type is SymbolType.METHOD and sym.name == "constructor":
                ctx.add_marker(Marker(
                    sym.id, MarkerKind.ENTRYPOINT,
                    "constructor — invoked implicitly via `new`",
                    rule=self.id, file=sym.file, line=sym.line))


class DynamicCodeRule(Rule):
    """eval / new Function / non-literal dynamic import poison the module:
    nothing in it can be proved unreachable."""
    id = "js-dynamic-code"

    def apply(self, ctx: RuleContext) -> None:
        for mi in ctx.modules:
            if mi.tree is None:
                continue
            self._scan(ctx, mi, mi.tree.root_node)

    def _scan(self, ctx, mi, node) -> None:
        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None:
                fname = _text(fn, mi.source)
                if fname in ("eval",):
                    ctx.add_marker(Marker(
                        mi.name, MarkerKind.DYNAMIC_MODULE,
                        f"eval() at {mi.rel_path}:{node.start_point[0] + 1} — "
                        "symbols here cannot be proved unreachable",
                        rule=self.id, file=mi.rel_path,
                        line=node.start_point[0] + 1))
        elif node.type == "new_expression":
            ctor = node.child_by_field_name("constructor")
            if ctor is not None and _text(ctor, mi.source) == "Function":
                ctx.add_marker(Marker(
                    mi.name, MarkerKind.DYNAMIC_MODULE,
                    f"new Function() at {mi.rel_path}:{node.start_point[0] + 1}",
                    rule=self.id, file=mi.rel_path,
                    line=node.start_point[0] + 1))
        for child in node.named_children:
            self._scan(ctx, mi, child)


class StringReferenceRule(Rule):
    """String literals naming a symbol or module path create weak edges —
    config-driven wiring, dependency-injection tokens, dynamic routes."""
    id = "js-string-references"

    def apply(self, ctx: RuleContext) -> None:
        for mi in ctx.modules:
            if mi.tree is None:
                continue
            self._scan_tree(ctx, mi, mi.tree.root_node)
        for path in ctx.config_files:
            rel = path.relative_to(ctx.repo_path).as_posix()
            if rel == "package.json":
                continue  # handled structurally by the entrypoint rule
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                self._match_line(ctx, line, f"file:{rel}", rel, lineno)

    def _scan_tree(self, ctx, mi, node) -> None:
        if node.type in ("string", "template_string"):
            value = _text(node, mi.source).strip("'\"`")
            self._match_value(ctx, value, mi.name, mi.rel_path,
                              node.start_point[0] + 1)
            return
        for child in node.named_children:
            self._scan_tree(ctx, mi, child)

    def _match_value(self, ctx, value: str, src, file, lineno) -> None:
        if IDENT_RE.match(value) and len(value) >= 4 \
                and value.lower() not in COMMON_WORDS:
            for sid in ctx.index.by_name.get(value, []):
                ctx.add_edge(Edge(src, sid, EdgeKind.STRING_REF,
                                  EdgeStrength.WEAK, file, lineno,
                                  f"string literal '{value}'"))
        elif "/" in value:
            mod = value.lstrip("./")
            if mod in ctx.index.modules:
                ctx.add_edge(Edge(src, mod, EdgeKind.STRING_REF,
                                  EdgeStrength.WEAK, file, lineno,
                                  f"path string '{value}'"))

    def _match_line(self, ctx, line: str, src, file, lineno) -> None:
        for token in re.findall(r"[A-Za-z_$][A-Za-z0-9_$./-]*", line):
            if "/" in token and token.lstrip("./") in ctx.index.modules:
                ctx.add_edge(Edge(src, token.lstrip("./"), EdgeKind.STRING_REF,
                                  EdgeStrength.WEAK, file, lineno,
                                  f"path reference '{token}'"))
            elif IDENT_RE.match(token) and len(token) >= 4 \
                    and token.lower() not in COMMON_WORDS \
                    and token in ctx.index.by_name:
                for sid in ctx.index.by_name[token]:
                    ctx.add_edge(Edge(src, sid, EdgeKind.STRING_REF,
                                      EdgeStrength.WEAK, file, lineno,
                                      f"config reference '{token}'"))


class DeclaredEntrypointRule(Rule):
    """.codetruth.toml entrypoints — same contract as the Python plugin."""
    id = "declared-entrypoints"

    def apply(self, ctx: RuleContext) -> None:
        from ...core.config import load_config
        cfg = load_config(ctx.repo_path)
        if not cfg.entrypoints:
            return
        for sym in ctx.index.all_symbols():
            if cfg.is_declared_entrypoint(sym.id):
                ctx.add_marker(Marker(
                    sym.id, MarkerKind.ENTRYPOINT,
                    "declared as an entry point in .codetruth.toml",
                    rule=self.id, file=sym.file, line=sym.line))


def default_rules() -> list[Rule]:
    return [
        DeclaredEntrypointRule(),
        PackageJsonEntrypointRule(),
        CallbackEntrypointRule(),
        ConstructorRule(),
        DynamicCodeRule(),
        StringReferenceRule(),
    ]
