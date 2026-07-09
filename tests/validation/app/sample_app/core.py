"""Application core. Ground truth is recorded in ../../labels.json."""


def run_pipeline(data):
    cleaned = _normalize(data)
    return _summarize(cleaned)


def _normalize(data):
    return [x.strip() for x in data]


def _summarize(rows):
    return {"count": len(rows)}


def deprecated_export(data):
    return _legacy_transform(data)


def _legacy_transform(data):
    return list(reversed(data))


def orphan_helper():
    return 42


def compute_ratio(a, b):
    return a / b if b else 0.0
