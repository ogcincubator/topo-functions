"""Problems found while reading or encoding a Topo Feature document."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class Problem:
    """One reason a document cannot be encoded.

    ``code`` is stable and meant for tests and tools; ``message`` is for people;
    ``id`` is the feature the problem was found on, when there is one.
    """

    code: str
    message: str
    id: Optional[str] = None

    def __str__(self) -> str:
        where = f" [{self.id}]" if self.id else ""
        return f"{self.code}{where}: {self.message}"


class EncodingError(ValueError):
    """The document cannot be encoded; ``problems`` lists every reason found."""

    def __init__(self, problems: Iterable[Problem]):
        self.problems = list(problems)
        lines = "\n".join(f"  {p}" for p in self.problems)
        super().__init__(f"{len(self.problems)} problem(s):\n{lines}")

    @property
    def codes(self) -> set[str]:
        return {p.code for p in self.problems}
