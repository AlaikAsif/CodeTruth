"""Scan scoping: which folders are walked. Guards the perf fix (SKIP_DIRS are
pruned from the traversal, not just filtered from results) and vendored-dir
and ignore_paths exclusion."""
import textwrap
import time

from codetruth import scan
from codetruth.languages.python.extractor import iter_py_files


def _make(root, rel, body="x = 1\n"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_skip_dirs_are_pruned_not_just_filtered(tmp_path):
    """A big node_modules must not even be walked — the traversal is pruned,
    so this stays fast regardless of how many files it contains."""
    _make(tmp_path, "src/app.py", "def real():\n    return 1\n")
    for i in range(4000):
        _make(tmp_path, f"node_modules/pkg{i}/index.py")
        _make(tmp_path, f".git/o{i}.py")
    t = time.time()
    files = list(iter_py_files(tmp_path))
    elapsed = time.time() - t
    assert [f.name for f in files] == ["app.py"]
    assert elapsed < 1.0, f"walk not pruned: {elapsed:.2f}s for 8000 skipped files"


def test_vendored_dirs_excluded(tmp_path):
    _make(tmp_path, "app.py", "def keep():\n    return 1\n")
    _make(tmp_path, "vendor/dep.py", "def vendored():\n    return 2\n")
    _make(tmp_path, "third_party/lib.py", "def tp():\n    return 3\n")
    result = scan(tmp_path, use_cache=False)
    assert result.find("app:keep")
    assert not result.find("vendor.dep:vendored")
    assert not result.find("third_party.lib:tp")


def test_ignore_paths_prunes_folder(tmp_path):
    _make(tmp_path, ".codetruth.toml", "")  # placeholder, overwritten below
    (tmp_path / ".codetruth.toml").write_text(textwrap.dedent("""\
        [codetruth]
        ignore_paths = ["generated/"]
    """), encoding="utf-8")
    _make(tmp_path, "app.py", "def keep():\n    return 1\n")
    _make(tmp_path, "generated/pb.py", "def generated_symbol():\n    return 2\n")
    result = scan(tmp_path, use_cache=False)
    assert result.find("app:keep")
    assert not result.find("generated.pb:generated_symbol")


def test_venv_inside_repo_not_scanned(tmp_path):
    """A virtualenv committed/left in the repo must not pollute the scan."""
    _make(tmp_path, "app.py", "def keep():\n    return 1\n")
    _make(tmp_path, ".venv/Lib/site-packages/requests/api.py",
          "def get():\n    return 1\n")
    _make(tmp_path, "env/bin/activate_this.py", "x = 1\n")
    result = scan(tmp_path, use_cache=False)
    syms = [r.symbol for r in result.records]
    assert not any("requests" in s or "site-packages" in s for s in syms)
    assert not any(s.startswith("env.") or ".venv" in s for s in syms)
