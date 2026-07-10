"""Layer 3 — semantic safety rules for Python.

Each rule scans ASTs / the symbol table for one dynamic-usage pattern and
emits weak edges or markers. Framework knowledge (FastAPI, Django, Celery,
pytest, ...) lives in YAML files under codetruth/rules/python/ so coverage
can grow without code changes.
"""
from __future__ import annotations

import ast
import fnmatch
import re
from pathlib import Path

import yaml

from ...core.models import (Edge, EdgeKind, EdgeStrength, Marker, MarkerKind,
                            SymbolType)
from ...core.plugin import Rule, RuleContext
from .extractor import dotted_name

RULES_DIR = Path(__file__).resolve().parents[2] / "rules" / "python"

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DOTTED_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")

# Bare identifiers too common to count as a string reference to a symbol.
COMMON_WORDS = {
    "main", "test", "data", "name", "type", "value", "true", "false", "none",
    "null", "user", "file", "path", "list", "dict", "info", "debug", "error",
    "warning", "default", "config", "utf-8", "ascii", "json", "yaml", "text",
    "html", "http", "https", "get", "post", "put", "delete", "read", "write",
    "id", "key", "app", "api", "run", "start", "stop", "date", "time",
}


class DeclaredEntrypointRule(Rule):
    """Symbols the user declared externally reached in .codetruth.toml
    (cron jobs, service runners, cross-repo handlers)."""
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


class KeepCommentRule(Rule):
    """`# codetruth: keep` on (or directly above) a definition marks it as
    an entry point — the user asserts it is reached in a way the scanner
    cannot see."""
    id = "keep-comment"
    TAG = "codetruth: keep"

    def apply(self, ctx: RuleContext) -> None:
        for mi in ctx.modules:
            if mi.tree is None or not mi.symbols:
                continue
            try:
                lines = Path(mi.abs_path).read_text(
                    encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            if not any(self.TAG in ln for ln in lines):
                continue
            for sym in mi.symbols:
                for lineno in (sym.line, sym.line - 1):
                    if 1 <= lineno <= len(lines) and self.TAG in lines[lineno - 1]:
                        ctx.add_marker(Marker(
                            sym.id, MarkerKind.ENTRYPOINT,
                            "marked '# codetruth: keep' in source",
                            rule=self.id, file=sym.file, line=sym.line))
                        break


# Base-class names (last dotted segment) whose subclasses are declarative
# schemas: fields are populated/validated/serialized by the framework, never
# referenced like ordinary attributes. Pydantic, Django ORM/forms, DRF,
# SQLModel, marshmallow, msgspec, attrs-style Table declarations.
SCHEMA_BASES = {
    "BaseModel", "BaseSettings", "GenericModel", "SQLModel",
    "TypedDict", "NamedTuple",
    "Model", "Form", "ModelForm",
    "Serializer", "ModelSerializer", "HyperlinkedModelSerializer",
    "Schema", "Struct", "Document", "EmbeddedDocument", "DeclarativeBase",
}


class SchemaFieldRule(Rule):
    """Fields of declarative schema models are used by the framework.

    `name: str` on a pydantic/Django/DRF model is a wire-format contract —
    populated from requests, validated, serialized to responses — and often
    has zero direct attribute references in the repo. Treat every field of a
    schema class (transitively: subclasses of schema classes too) as an entry
    point, and honour the `Config`/`Meta` nested-class convention. Marking
    used can only keep code, so this is safe by construction; a dead *model*
    is still flagged at the class level.
    """
    id = "python-schema-fields"

    def apply(self, ctx: RuleContext) -> None:
        idx = ctx.index
        classes = [s for s in idx.all_symbols()
                   if s.type is SymbolType.CLASS]
        by_name: dict[str, list] = {}
        for c in classes:
            by_name.setdefault(c.name, []).append(c)
        memo: dict[str, bool] = {}

        def is_schema(cls, seen: frozenset = frozenset()) -> bool:
            if cls.id in memo:
                return memo[cls.id]
            if cls.id in seen:
                return False
            result = False
            for base in cls.bases:
                leaf = base.split(".")[-1]
                if leaf in SCHEMA_BASES:
                    result = True
                    break
                candidates = by_name.get(leaf, [])
                if len(candidates) == 1 and is_schema(
                        candidates[0], seen | {cls.id}):
                    result = True
                    break
            memo[cls.id] = result
            return result

        def mark(sym, reason: str) -> None:
            ctx.add_marker(Marker(sym.id, MarkerKind.ENTRYPOINT, reason,
                                  rule=self.id, file=sym.file, line=sym.line))

        for cls in classes:
            parent = idx.by_id.get(cls.parent) if cls.parent else None
            if cls.name in ("Config", "Meta") and parent is not None \
                    and parent.type is SymbolType.CLASS:
                mark(cls, f"`{cls.name}` nested class — framework "
                          "configuration convention")
                for member_id in idx.class_members.get(cls.id, {}).values():
                    member = idx.by_id[member_id]
                    if member.type is SymbolType.VARIABLE:
                        mark(member, f"{parent.name}.{cls.name} option — "
                                     "read by the framework")
                continue
            if not is_schema(cls):
                continue
            for member_id in idx.class_members.get(cls.id, {}).values():
                member = idx.by_id[member_id]
                if member.type is SymbolType.VARIABLE:
                    mark(member, "schema/model field — populated, validated "
                                 "and serialized by the framework")


class DunderRule(Rule):
    """Dunder methods are invoked implicitly by the interpreter."""
    id = "python-dunder-methods"

    def apply(self, ctx: RuleContext) -> None:
        for sym in ctx.index.all_symbols():
            if sym.type is SymbolType.METHOD and sym.name.startswith("__") \
                    and sym.name.endswith("__"):
                ctx.add_marker(Marker(sym.id, MarkerKind.ENTRYPOINT,
                                      "dunder method — invoked implicitly by Python",
                                      rule=self.id, file=sym.file, line=sym.line))


class TestEntryRule(Rule):
    """Test functions/classes are collected by the test runner, not called."""
    id = "python-test-entrypoints"

    def apply(self, ctx: RuleContext) -> None:
        for sym in ctx.index.all_symbols():
            if not sym.is_test:
                continue
            if (sym.type in (SymbolType.FUNCTION, SymbolType.METHOD)
                    and sym.name.startswith("test")) or \
               (sym.type is SymbolType.CLASS and sym.name.startswith("Test")):
                ctx.add_marker(Marker(sym.id, MarkerKind.ENTRYPOINT,
                                      "test entry point — collected by the test runner",
                                      rule=self.id, file=sym.file, line=sym.line))


class MainGuardRule(Rule):
    """Modules with a __main__ guard are executable scripts."""
    id = "python-main-guard"

    def apply(self, ctx: RuleContext) -> None:
        for mi in ctx.modules:
            if mi.has_main_guard:
                ctx.add_marker(Marker(mi.name, MarkerKind.ENTRYPOINT,
                                      "has `if __name__ == '__main__'` guard — "
                                      "executable script", rule=self.id,
                                      file=mi.rel_path, line=1))


class ReflectionRule(Rule):
    """getattr/setattr/hasattr, importlib, __import__, eval/exec, globals()[...].

    Literal targets become weak DYNAMIC edges. Non-literal reflection poisons
    the whole module: nothing defined there can be *proved* dead, so every
    symbol in it is capped below `safe_to_delete`.
    """
    id = "python-reflection"

    REFLECT_FUNCS = {"getattr", "setattr", "hasattr", "delattr"}
    DYNAMIC_FUNCS = {"eval", "exec", "__import__", "globals", "locals", "vars"}

    def apply(self, ctx: RuleContext) -> None:
        for mi in ctx.modules:
            if mi.tree is None:
                continue
            for node in ast.walk(mi.tree):
                if not isinstance(node, ast.Call):
                    continue
                fname = dotted_name(node.func) or ""
                short = fname.split(".")[-1]
                if short in self.REFLECT_FUNCS and len(node.args) >= 2:
                    self._reflect(ctx, mi, node, fname)
                elif fname in ("importlib.import_module", "import_module") \
                        and node.args:
                    self._import_module(ctx, mi, node)
                elif short in self.DYNAMIC_FUNCS and fname == short:
                    ctx.add_marker(Marker(
                        mi.name, MarkerKind.DYNAMIC_MODULE,
                        f"non-literal dynamic access `{short}(...)` at "
                        f"{mi.rel_path}:{node.lineno}", rule=self.id,
                        file=mi.rel_path, line=node.lineno))

    def _reflect(self, ctx, mi, node, fname):
        arg = node.args[1]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            for sid in ctx.index.by_name.get(arg.value, []):
                ctx.add_edge(Edge(mi.name, sid, EdgeKind.DYNAMIC,
                                  EdgeStrength.WEAK, mi.rel_path, node.lineno,
                                  f"{fname}(..., '{arg.value}')"))
        else:
            ctx.add_marker(Marker(
                mi.name, MarkerKind.DYNAMIC_MODULE,
                f"non-literal `{fname}()` at {mi.rel_path}:{node.lineno} — "
                "symbols in this module cannot be proved unreachable",
                rule=self.id, file=mi.rel_path, line=node.lineno))

    def _import_module(self, ctx, mi, node):
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            target = arg.value
            if target in ctx.index.modules:
                ctx.add_edge(Edge(mi.name, target, EdgeKind.DYNAMIC,
                                  EdgeStrength.WEAK, mi.rel_path, node.lineno,
                                  f"importlib.import_module('{target}')"))
        else:
            ctx.add_marker(Marker(
                mi.name, MarkerKind.DYNAMIC_MODULE,
                f"non-literal import_module() at {mi.rel_path}:{node.lineno}",
                rule=self.id, file=mi.rel_path, line=node.lineno))


class StringReferenceRule(Rule):
    """String literals naming a symbol (dotted path or exact identifier) in
    Python source or config files create weak string_ref edges — the
    config-driven / dispatch-table usage pattern."""
    id = "python-string-references"

    def apply(self, ctx: RuleContext) -> None:
        # Python source strings
        for mi in ctx.modules:
            if mi.tree is None:
                continue
            for node in ast.walk(mi.tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self._match(ctx, node.value, mi.name, mi.rel_path,
                                node.lineno)
        # Config files
        for path in ctx.config_files:
            rel = path.relative_to(ctx.repo_path).as_posix()
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                self._match(ctx, line, f"file:{rel}", rel, lineno,
                            whole_line=True)

    def _match(self, ctx, text: str, src: str, file: str, lineno: int,
               whole_line: bool = False) -> None:
        stripped = text.strip().strip("\"'")
        # Exact identifier match (dispatch keys, registry names).
        if not whole_line and IDENT_RE.match(stripped):
            if len(stripped) >= 4 and stripped.lower() not in COMMON_WORDS:
                for sid in ctx.index.by_name.get(stripped, []):
                    ctx.add_edge(Edge(src, sid, EdgeKind.STRING_REF,
                                      EdgeStrength.WEAK, file, lineno,
                                      f"string literal '{stripped}'"))
            return
        # Dotted-path matches anywhere in the text ("app.views.login").
        for m in DOTTED_RE.finditer(text):
            dotted = m.group(0)
            self._match_dotted(ctx, dotted, src, file, lineno)

    def _match_dotted(self, ctx, dotted: str, src, file, lineno) -> None:
        if dotted in ctx.index.modules:
            ctx.add_edge(Edge(src, dotted, EdgeKind.STRING_REF,
                              EdgeStrength.WEAK, file, lineno,
                              f"dotted string reference '{dotted}'"))
            return
        parts = dotted.split(".")
        for split in range(len(parts) - 1, 0, -1):
            mod = ".".join(parts[:split])
            if mod in ctx.index.modules:
                qual = ".".join(parts[split:])
                sid = f"{mod}:{qual}"
                if sid in ctx.index.by_id:
                    ctx.add_edge(Edge(src, sid, EdgeKind.STRING_REF,
                                      EdgeStrength.WEAK, file, lineno,
                                      f"dotted string reference '{dotted}'"))
                return


class DecoratorPatternRule(Rule):
    """YAML-defined: decorators that register a symbol with a framework."""

    def __init__(self, rule_id: str, patterns: list[str], reason: str,
                 effect: str = "entrypoint"):
        self.id = rule_id
        self.patterns = patterns
        self.reason = reason
        self.effect = effect

    def _matches(self, dec: str) -> bool:
        for pat in self.patterns:
            if fnmatch.fnmatch(dec, pat):
                return True
            if "." not in pat and dec.split(".")[-1] == pat:
                return True
        return False

    def apply(self, ctx: RuleContext) -> None:
        for sym in ctx.index.all_symbols():
            for dec in sym.decorators:
                if self._matches(dec):
                    kind = MarkerKind.ENTRYPOINT if self.effect == "entrypoint" \
                        else MarkerKind.CAUTION
                    ctx.add_marker(Marker(sym.id, kind,
                                          f"@{dec}: {self.reason}", rule=self.id,
                                          file=sym.file, line=sym.line))
                    break


class NamePatternRule(Rule):
    """YAML-defined: symbols whose name+path+type mark them framework-owned
    (Django settings constants, management commands, migrations, wsgi app)."""

    def __init__(self, rule_id: str, reason: str, name_regex: str = "",
                 path_glob: str = "", symbol_type: str = ""):
        self.id = rule_id
        self.reason = reason
        self.name_re = re.compile(name_regex) if name_regex else None
        self.path_glob = path_glob
        self.symbol_type = symbol_type

    def apply(self, ctx: RuleContext) -> None:
        for sym in ctx.index.all_symbols():
            if self.symbol_type and sym.type.value != self.symbol_type:
                continue
            if self.name_re and not self.name_re.match(sym.name):
                continue
            if self.path_glob and not fnmatch.fnmatch(sym.file, self.path_glob):
                continue
            ctx.add_marker(Marker(sym.id, MarkerKind.ENTRYPOINT, self.reason,
                                  rule=self.id, file=sym.file, line=sym.line))


def load_yaml_rules(rules_dir: Path = RULES_DIR) -> list[Rule]:
    rules: list[Rule] = []
    if not rules_dir.is_dir():
        return rules
    for path in sorted(rules_dir.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for spec in doc.get("rules", []):
            rtype = spec.get("type")
            if rtype == "decorator":
                rules.append(DecoratorPatternRule(
                    spec["id"], spec["patterns"], spec.get("reason", ""),
                    spec.get("effect", "entrypoint")))
            elif rtype == "name":
                rules.append(NamePatternRule(
                    spec["id"], spec.get("reason", ""),
                    spec.get("name_regex", ""), spec.get("path_glob", ""),
                    spec.get("symbol_type", "")))
    return rules


def default_rules() -> list[Rule]:
    return [
        DeclaredEntrypointRule(),
        KeepCommentRule(),
        SchemaFieldRule(),
        DunderRule(),
        TestEntryRule(),
        MainGuardRule(),
        ReflectionRule(),
        StringReferenceRule(),
        *load_yaml_rules(),
    ]
