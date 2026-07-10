import httpx

from shared_models import serialize_user


def sync_users():
    resp = httpx.get("https://api.internal/users/42")
    return serialize_user(resp.json())


def place_order(payload):
    return httpx.post("https://api.internal/orders", json=payload)
