"""Cross-service surface extraction for workspace scans.

Single-repo static analysis cannot see that an endpoint is called by another
service (PLAN §10). This module extracts, per repo:

- **provided routes** — HTTP handlers a service exposes (FastAPI/Flask/
  Starlette decorators, Express-style registrations), each with its path
  template and handler symbol id;
- **consumed routes** — outbound HTTP client calls (requests/httpx/aiohttp/
  fetch/axios) with their URL path literal;
- **exported names** — the public symbol/module names another repo could
  import or reference by string.

The workspace layer matches consumed↔provided across repos and overlays the
result as cross-repo evidence. Everything here is best-effort and additive:
a missed match just means less cross-repo evidence, never a wrong deletion.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from .scanner import ScanResult

_HTTP_VERBS = {"get", "post", "put", "delete", "patch", "options", "head"}
_ROUTE_DECOS = _HTTP_VERBS | {"route", "api_route", "websocket"}
_CLIENT_ROOTS = {"requests", "httpx", "aiohttp", "session", "client",
                 "http", "self"}


@dataclass
class Route:
    method: str
    path: str            # normalized template, params -> '*'
    raw: str
    handler: str         # symbol id of the handler (provider) or '' (consumer)
    repo: str
    file: str
    line: int


def _norm_path(raw: str) -> str:
    """Normalize a URL or route template to a comparable path.
    Strips scheme/host and querystring; collapses path params to '*'."""
    p = raw.strip()
    p = re.sub(r"^[a-zA-Z]+://[^/]+", "", p)   # scheme://host
    p = p.split("?")[0].split("#")[0]
    if not p.startswith("/"):
        p = "/" + p
    # {id}, :id, <int:id>, ${id}, %s, and bare numeric/uuid segments -> '*'
    p = re.sub(r"\{[^}]*\}", "*", p)
    p = re.sub(r":[A-Za-z_][A-Za-z0-9_]*", "*", p)
    p = re.sub(r"<[^>]*>", "*", p)
    p = re.sub(r"\$\{[^}]*\}", "*", p)
    p = re.sub(r"%[sd]", "*", p)
    segs = []
    for seg in p.split("/"):
        if re.fullmatch(r"\d+", seg) or re.fullmatch(
                r"[0-9a-fA-F-]{8,}", seg):
            segs.append("*")
        else:
            segs.append(seg)
    p = "/".join(segs)
    return p.rstrip("/") or "/"


def paths_match(provided: str, consumed: str) -> bool:
    """A consumed URL matches a provided template when their segment lists
    align with '*' as a single-segment wildcard (suffix-tolerant: the client
    may include a base-path prefix the server route omits)."""
    pv = [s for s in provided.split("/") if s != ""]
    cs = [s for s in consumed.split("/") if s != ""]
    if not pv:
        return False
    if len(cs) < len(pv):
        return False
    cs_tail = cs[len(cs) - len(pv):]   # allow a leading base-path on the client
    for a, b in zip(pv, cs_tail):
        if a == "*" or b == "*":
            continue
        if a != b:
            return False
    return True


def _dotted(node) -> str:
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _first_str_arg(call: ast.Call) -> str | None:
    for a in call.args:
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            return a.value
        # requests.get(f"{BASE}/users/1") -> take the literal tail
        if isinstance(a, ast.JoinedStr):
            lit = "".join(v.value for v in a.values
                          if isinstance(v, ast.Constant)
                          and isinstance(v.value, str))
            if "/" in lit:
                return lit
        # BASE + "/users" -> take the string operand
        if isinstance(a, ast.BinOp) and isinstance(a.op, ast.Add):
            for side in (a.right, a.left):
                if isinstance(side, ast.Constant) and isinstance(side.value, str):
                    return side.value
    for kw in call.keywords:
        if kw.arg in ("url", "path") and isinstance(kw.value, ast.Constant) \
                and isinstance(kw.value.value, str):
            return kw.value.value
    return None


@dataclass
class RepoSurface:
    repo: str
    provided: list[Route]
    consumed: list[Route]
    modules: set          # dotted module names defined here
    exported_names: dict  # name -> symbol id (exported/public symbols)
    imported: set         # dotted module names this repo imports
    imported_symbols: set # (module, name) pairs from `from mod import name`


def extract_surface_python(repo_label: str, repo_path: Path,
                           result: ScanResult) -> RepoSurface:
    from ..languages.python.extractor import extract_repo

    modules, _ = extract_repo(repo_path)
    provided: list[Route] = []
    consumed: list[Route] = []
    mod_names = {m.name for m in modules}
    local_prefixes = {name.split(".")[0] for name in mod_names}
    exported: dict[str, str] = {}
    imported: set[str] = set()
    imported_symbols: set = set()

    id_by_pos: dict[tuple[str, int], str] = {}
    for m in modules:
        for s in m.symbols:
            id_by_pos[(m.rel_path, s.line)] = s.id
            if s.exported and s.parent == m.name:
                exported.setdefault(s.name, s.id)

    for m in modules:
        if m.tree is None:
            continue
        handler_lines = {s.line: s.id for s in m.symbols}
        for imp in m.imports:
            # absolute imports whose root isn't defined locally are candidates
            # for a cross-repo (shared package) dependency
            if imp.kind == "import" and imp.level == 0 and imp.module:
                imported.add(imp.module)
            elif imp.kind == "from" and imp.level == 0 and imp.module \
                    and imp.module.split(".")[0] not in local_prefixes:
                imported.add(imp.module)
                if imp.name and imp.name != "*":
                    imported_symbols.add((imp.module, imp.name))
        for node in ast.walk(m.tree):
            # provider: decorated function
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in node.decorator_list:
                    if not isinstance(deco, ast.Call):
                        continue
                    dotted = _dotted(deco.func)
                    verb = dotted.rsplit(".", 1)[-1]
                    if verb in _ROUTE_DECOS:
                        path = _first_str_arg(deco)
                        if path is not None:
                            hid = handler_lines.get(node.lineno, "")
                            method = verb if verb in _HTTP_VERBS else "any"
                            provided.append(Route(
                                method, _norm_path(path), path, hid,
                                repo_label, m.rel_path, node.lineno))
            # consumer: http client call
            if isinstance(node, ast.Call):
                dotted = _dotted(node.func)
                root = dotted.split(".")[0]
                verb = dotted.rsplit(".", 1)[-1]
                if verb in _HTTP_VERBS and (root in _CLIENT_ROOTS
                                            or "request" in dotted.lower()):
                    url = _first_str_arg(node)
                    if url and "/" in url:
                        consumed.append(Route(
                            verb, _norm_path(url), url, "", repo_label,
                            m.rel_path, node.lineno))

    return RepoSurface(repo_label, provided, consumed, mod_names, exported,
                       imported, imported_symbols)
