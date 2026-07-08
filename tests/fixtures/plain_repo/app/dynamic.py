class Plugin:
    def maybe_dead(self):
        return "reflection in this module means nobody can prove I'm dead"


def load(attr_name):
    return getattr(Plugin, attr_name)
