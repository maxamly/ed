class EdError(Exception):
    """Any failure that must abort the run: missing anchor, ambiguity, failed check."""
