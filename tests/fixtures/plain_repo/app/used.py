def used_func():
    return _helper() + 1


def _helper():
    return 41


def _dead_private():
    return "nothing references me and I'm private"


def dead_public():
    return "nothing references me, but I'm public"


def only_tested():
    return "only the test suite references me"


def string_referenced():
    return "a config file names me in a string"
