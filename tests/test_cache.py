"""Persistent scan-cache tests.

Cache hits are proven by tampering: we write a sentinel status into the
on-disk cache and confirm a subsequent scan returns it (only possible if the
result was loaded from disk, not recomputed), then confirm that changing a
source file invalidates the tampered cache.
"""
import json
import shutil

from codetruth import scan
from codetruth.core.cache import _cache_path

from conftest import FIXTURES


def _copy_repo(tmp_path):
    dst = tmp_path / "repo"
    shutil.copytree(FIXTURES / "plain_repo", dst,
                    ignore=shutil.ignore_patterns(".codetruth"))
    return dst


def test_cache_file_is_written(tmp_path):
    repo = _copy_repo(tmp_path)
    scan(repo)
    assert _cache_path(repo).is_file()


def test_unchanged_repo_hits_cache(tmp_path):
    repo = _copy_repo(tmp_path)
    scan(repo)  # populate cache

    # Tamper: flip one record to a sentinel the scanner would never emit.
    cache_file = _cache_path(repo)
    doc = json.loads(cache_file.read_text(encoding="utf-8"))
    target = doc["records"][0]["symbol"]
    doc["records"][0]["status"] = "definitely_used"
    doc["records"][0]["rank_score"] = 0.123
    cache_file.write_text(json.dumps(doc), encoding="utf-8")

    hit = scan(repo)  # nothing changed on disk -> must load the tampered cache
    rec = hit.find(target)[0]
    assert rec.rank_score == 0.123


def test_source_change_invalidates_cache(tmp_path):
    repo = _copy_repo(tmp_path)
    scan(repo)

    cache_file = _cache_path(repo)
    doc = json.loads(cache_file.read_text(encoding="utf-8"))
    doc["records"][0]["rank_score"] = 0.123  # tamper
    cache_file.write_text(json.dumps(doc), encoding="utf-8")

    # Change a source file: a new dead function should appear, and the
    # tampered cache must be ignored.
    (repo / "app" / "new_mod.py").write_text(
        "def freshly_added_dead():\n    return 1\n", encoding="utf-8")

    result = scan(repo)
    assert result.find("app.new_mod:freshly_added_dead")
    tampered = result.find(doc["records"][0]["symbol"])[0]
    assert tampered.rank_score != 0.123  # recomputed, not read from cache


def test_use_cache_false_bypasses(tmp_path):
    repo = _copy_repo(tmp_path)
    scan(repo)

    cache_file = _cache_path(repo)
    doc = json.loads(cache_file.read_text(encoding="utf-8"))
    doc["records"][0]["rank_score"] = 0.123
    cache_file.write_text(json.dumps(doc), encoding="utf-8")

    result = scan(repo, use_cache=False)
    rec = result.find(doc["records"][0]["symbol"])[0]
    assert rec.rank_score != 0.123


def test_treat_public_as_api_keys_cache_separately(tmp_path):
    """Different scan options must not collide in the cache."""
    repo = _copy_repo(tmp_path)
    lib = scan(repo, treat_public_as_api=True)
    app = scan(repo, treat_public_as_api=False)
    # dead_public is likely_dead as a library, safe_to_delete as an app.
    assert lib.find("app.used:dead_public")[0].status.value == "likely_dead"
    assert app.find("app.used:dead_public")[0].status.value == "safe_to_delete"
