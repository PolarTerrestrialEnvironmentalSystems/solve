"""Shared policy: causal effects must connect distinct event records."""
CAUSAL_RELATIONSHIP_TYPES = frozenset({
    "causes", "contributes_to", "affects", "increases", "decreases", "prevents",
})


def is_causal_self_relationship(row: dict) -> bool:
    return (row["relationship_type"] in CAUSAL_RELATIONSHIP_TYPES
            and row["source_event_id"] == row["target_event_id"])
