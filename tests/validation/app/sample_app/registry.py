"""A plugin registry: registered callables are invoked dynamically by name."""

_HANDLERS = {}


def register(name):
    def deco(fn):
        _HANDLERS[name] = fn
        return fn
    return deco


@register("email")
def send_email(msg):
    return f"email:{msg}"


@register("sms")
def send_sms(msg):
    return f"sms:{msg}"


def dispatch(name, msg):
    return _HANDLERS[name](msg)
