"""In-process Langflow capability; never accepted through harness request JSON."""
from dataclasses import dataclass
from typing import Callable

@dataclass(frozen=True)
class DraftLeanBackend:
    enabled: bool
    load_verifier: Callable
    verify_exact: Callable
