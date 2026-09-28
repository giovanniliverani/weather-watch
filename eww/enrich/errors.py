"""Errors an enrichment collector raises to stop itself without failing the rest of `eww sync`."""


class SkipSource(Exception):
    """This provider cannot be used. The message is the one line written to the log.

    Raised for a missing credential, a refused session, or a response that says a billing account
    would be required. The run stops calling that provider and continues with the next one.
    """
