import textwrap


class Wrapper(textwrap.TextWrapper):
    def _handle_long_word(self, *args):
        return "overrides the stdlib parent — invoked by code outside this repo"
