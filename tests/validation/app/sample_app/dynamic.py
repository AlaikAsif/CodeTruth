"""Config-driven dispatch — the handler is named by a string in config.yaml."""


def string_wired_task(payload):
    return {"handled": payload}


def really_dead_task(payload):
    return payload
