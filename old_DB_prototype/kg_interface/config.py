"""Runtime configuration read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "postgres"
    neo4j_database: str = "neo4j"
    host: str = "127.0.0.1"
    port: int = 8765

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            neo4j_uri=os.getenv("NEO4J_URI", cls.neo4j_uri),
            neo4j_user=os.getenv("NEO4J_USER", cls.neo4j_user),
            neo4j_password=os.getenv("NEO4J_PASSWORD", cls.neo4j_password),
            neo4j_database=os.getenv("NEO4J_DATABASE", cls.neo4j_database),
            host=os.getenv("KG_INTERFACE_HOST", cls.host),
            port=int(os.getenv("KG_INTERFACE_PORT", str(cls.port))),
        )
