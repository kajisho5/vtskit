"""The one exception vtskit raises."""


class VtsError(Exception):
    """A disc cannot be read or written as asked."""

    def __init__(self, message):
        super().__init__(message)
