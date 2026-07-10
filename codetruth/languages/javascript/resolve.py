"""Module resolution for real JS/TS projects: path aliases and monorepos.

Relative imports (`./util`) are handled inline by SymbolIndex. This module
adds the two things every non-toy TS/JS project needs:

- **tsconfig/jsconfig `paths` + `baseUrl`** — `import x from '@/utils'` or
  `'~/lib/api'` mapped to real source dirs. Without this the alias import
  resolves to nothing and its target looks unused.
- **monorepo workspace packages** — `import { x } from '@acme/core'`
  resolved to the sibling package's source that declares that `name` in its
  package.json.

Config is parsed leniently (tsconfig allows comments and trailing commas).
All resolution is best-effort: an unresolved specifier is simply treated as
an external dependency, never guessed.
"""
from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path
from typing import Optional

_MODULE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue")
_INDEX_NAMES = tuple(f"index{e}" for e in _MODULE_EXTS)


def _strip_jsonc(text: str) -> str:
    """Best-effort JSONC -> JSON: drop // and /* */ comments and trailing
    commas so tsconfig files parse."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"(^|[^:])//[^\n]*", lambda m: m.group(1), text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text


def _load_jsonc(path: Path) -> Optional[dict]:
    try:
        return json.loads(_strip_jsonc(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


class Resolver:
    """Resolves bare/aliased specifiers to repo-relative module names
    (matching the extractor's naming: posix path minus extension)."""

    def __init__(self, repo_root: Path, module_names: set[str]):
        self.root = repo_root
        self.modules = module_names
        self.base_url = ""                      # posix, repo-relative
        self.paths: dict[str, list[str]] = {}   # alias glob -> targets
        self.packages: dict[str, str] = {}      # pkg name -> dir (repo-rel)
        self._load_tsconfig()
        self._load_workspaces()

    # -- config loading -------------------------------------------------------

    def _rel(self, p: Path) -> str:
        try:
            return p.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return ""

    def _load_tsconfig(self) -> None:
        for name in ("tsconfig.json", "jsconfig.json"):
            cfg = _load_jsonc(self.root / name)
            if not cfg:
                continue
            opts = cfg.get("compilerOptions", {}) or {}
            base = opts.get("baseUrl")
            if isinstance(base, str):
                self.base_url = posixpath.normpath(base).lstrip("./")
                if self.base_url == ".":
                    self.base_url = ""
            paths = opts.get("paths")
            if isinstance(paths, dict):
                for alias, targets in paths.items():
                    if isinstance(targets, list):
                        self.paths[alias] = [str(t) for t in targets]
            if self.base_url or self.paths:
                break

    def _load_workspaces(self) -> None:
        root_pkg = _load_jsonc(self.root / "package.json")
        globs = []
        if root_pkg:
            ws = root_pkg.get("workspaces")
            if isinstance(ws, list):
                globs = ws
            elif isinstance(ws, dict) and isinstance(ws.get("packages"), list):
                globs = ws["packages"]
        # Even without a declared workspaces field, index any nested
        # package.json (common in monorepos / vendored packages). Prune the
        # walk so we don't descend into node_modules/.git/dist.
        import os
        _skip = {"node_modules", ".git", "dist", "build", ".next", "out",
                 ".codetruth"}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in _skip]
            if dirpath == str(self.root) or "package.json" not in filenames:
                continue
            pkg_json = Path(dirpath) / "package.json"
            doc = _load_jsonc(pkg_json)
            if not doc or not isinstance(doc.get("name"), str):
                continue
            self.packages[doc["name"]] = self._rel(pkg_json.parent)

    # -- resolution -----------------------------------------------------------

    def _as_module(self, target: str) -> Optional[str]:
        """A repo-relative path (no ext) -> a known module name, trying the
        path itself and index files."""
        target = posixpath.normpath(target).lstrip("./")
        for ext in _MODULE_EXTS:
            if target.endswith(ext):
                target = target[: -len(ext)]
                break
        if target in self.modules:
            return target
        for idx in _INDEX_NAMES:
            cand = posixpath.normpath(f"{target}/{idx}")
            cand = cand.rsplit(".", 1)[0]
            if cand in self.modules:
                return cand
        if f"{target}/index" in self.modules:
            return f"{target}/index"
        return None

    def resolve(self, specifier: str) -> Optional[str]:
        if specifier.startswith("."):
            return None  # relative: handled by SymbolIndex

        # 1. tsconfig path aliases (e.g. "@/*": ["src/*"])
        for alias, targets in self.paths.items():
            mapped = self._match_alias(alias, targets, specifier)
            if mapped is not None:
                mod = self._as_module(mapped)
                if mod:
                    return mod

        # 2. baseUrl (non-relative imports resolve against it)
        if self.base_url is not None:
            mod = self._as_module(posixpath.join(self.base_url, specifier))
            if mod:
                return mod
        mod = self._as_module(specifier)
        if mod:
            return mod

        # 3. monorepo workspace package
        for pkg_name, pkg_dir in self.packages.items():
            if specifier == pkg_name:
                for entry in ("index", "src/index", "src/main", "main"):
                    m = self._as_module(posixpath.join(pkg_dir, entry))
                    if m:
                        return m
            elif specifier.startswith(pkg_name + "/"):
                sub = specifier[len(pkg_name) + 1:]
                m = self._as_module(posixpath.join(pkg_dir, sub))
                if m:
                    return m
        return None

    @staticmethod
    def _match_alias(alias: str, targets: list[str], spec: str) -> Optional[str]:
        if alias.endswith("/*") and spec.startswith(alias[:-1]):
            tail = spec[len(alias) - 1:]
            for t in targets:
                if t.endswith("/*"):
                    return t[:-1] + tail
                return posixpath.join(t, tail)
        elif alias == spec:
            return targets[0] if targets else None
        return None
