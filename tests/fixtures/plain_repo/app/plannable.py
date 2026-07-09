import functools
import json

__all__ = ["public_api"]


def public_api():
    return json.dumps({"ok": True})


@functools.lru_cache(maxsize=None)
def _doomed():
    return 41
