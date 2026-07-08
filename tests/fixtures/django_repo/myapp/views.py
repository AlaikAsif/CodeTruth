def home(request):
    return "home page"


def legacy_view(request):
    return "referenced only by a dotted string in urls.py"


def totally_dead_view(request):
    return "nothing references me at all"
