from fastapi import FastAPI

app = FastAPI()


@app.get("/users/{user_id}")
def get_user(user_id: int):
    return {"id": user_id}


@app.post("/orders")
def create_order(payload: dict):
    return payload


@app.get("/internal/metrics")
def metrics():
    return {"ok": True}
