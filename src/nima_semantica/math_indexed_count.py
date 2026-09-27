"""Small harness-bound index-set contract for private calculation graphs."""
from __future__ import annotations

from pydantic import Field, model_validator

from .models import StrictModel


class IndexedSet(StrictModel):
    set_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    fact_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    members: tuple[str, ...] = Field(min_length=2, max_length=16)
    required_paths: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def distinct_members_and_paths(self):
        if (any(not member or len(member) > 64 or not member.replace("_", "").isalnum()
                for member in self.members) or len(set(self.members)) != len(self.members)):
            raise ValueError("indexed-set members must be distinct simple labels")
        if len(set(self.required_paths)) != len(self.required_paths):
            raise ValueError("indexed-set output paths must be distinct")
        return self


def validate_indexed_sets(indexed_sets: tuple[IndexedSet, ...], request) -> None:
    if len({item.set_id for item in indexed_sets}) != len(indexed_sets):
        raise ValueError("indexed-set IDs must be distinct")
    facts = {fact.fact_id: fact for fact in request.task_facts}
    for item in indexed_sets:
        if item.fact_id not in facts:
            raise ValueError(f"indexed set {item.set_id} needs an exact harness task fact")
        if not set(item.required_paths).issubset(request.required_output_paths):
            raise ValueError(f"indexed set {item.set_id} references unknown output paths")
