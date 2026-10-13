"""Exceptions that drive abort-and-restore."""


class BenchError(Exception):
    """Base class."""


class AbortRun(BenchError):
    """Stop measuring now; the lane must restore the production service."""

    def __init__(self, reason: str, code: str = "abort") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


class ForbiddenTarget(AbortRun):
    """A bench request tried to leave its own port (Guardian 8080/8081, R9700 prod)."""

    def __init__(self, url: str) -> None:
        super().__init__(f"request to forbidden target {url}", "forbidden_target")


class WindowError(BenchError):
    """No valid window file; nothing real may start."""


class FrozenSuiteMismatch(BenchError):
    """A suite's hash differs from FROZEN.json."""
