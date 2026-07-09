"""Repo-level configuration: let users teach the scanner about usage it
cannot see, and scope what it looks at.

`.codetruth.toml` at the repository root:

    [codetruth]
    app_mode = true                # public symbols are internal (application)
    entrypoints = [                # externally-reached symbols (cron, RPC, ...)
        "jobs.nightly:run",
        "services.handlers.*",
    ]
    ignore_paths = [               # not scanned at all
        "migrations/",
        "vendor/**",
    ]

Inline suppression: a `# codetruth: keep` comment on (or directly above) a
definition marks it as an entry point.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - Python 3.10
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

CONFIG_FILENAME = ".codetruth.toml"


@dataclass
class RepoConfig:
    app_mode: Optional[bool] = None
    entrypoints: list[str] = field(default_factory=list)
    ignore_paths: list[str] = field(default_factory=list)

    def is_ignored(self, rel_path: str) -> bool:
        rel = rel_path.replace("\\", "/")
        for pat in self.ignore_paths:
            pat = pat.replace("\\", "/")
            base = pat.rstrip("/*")
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, base + "/*") \
                    or rel == base or rel.startswith(base + "/"):
                return True
        return False

    def is_declared_entrypoint(self, symbol_id: str) -> bool:
        dotted = symbol_id.replace(":", ".")
        return any(fnmatch.fnmatch(symbol_id, pat)
                   or fnmatch.fnmatch(dotted, pat.replace(":", "."))
                   for pat in self.entrypoints)


def load_config(repo: Path) -> RepoConfig:
    path = repo / CONFIG_FILENAME
    if tomllib is None or not path.is_file():
        return RepoConfig()
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return RepoConfig()
    section = doc.get("codetruth", doc)  # tolerate a bare top-level table
    return RepoConfig(
        app_mode=section.get("app_mode"),
        entrypoints=[str(x) for x in section.get("entrypoints", [])],
        ignore_paths=[str(x) for x in section.get("ignore_paths", [])],
    )
