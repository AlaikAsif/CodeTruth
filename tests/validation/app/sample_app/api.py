"""HTTP surface — handlers are used by the framework, not by call sites."""
from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health_check():
    return {"ok": True}


@app.post("/items")
def create_item(payload):
    return _persist(payload)


def _persist(payload):
    return {"id": 1, **payload}


def unrouted_handler(payload):
    return payload
