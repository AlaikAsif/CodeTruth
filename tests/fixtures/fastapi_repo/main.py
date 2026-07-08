from fastapi import FastAPI

app = FastAPI()


@app.get("/items")
def read_items():
    return build_response([])


@app.post("/items")
async def create_item():
    return {}


def build_response(items):
    return {"items": items}


def never_called_helper():
    return "no route, no caller"
