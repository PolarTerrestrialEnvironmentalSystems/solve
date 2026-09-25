import os
import json
def load_entity_config(path) -> dict:

    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    return data

def load_entity_record(session) -> list[dict]:
    path = "C:\\Users\\jowals001\\awi\\solve\\scripts\\quint\\entity_config.json"
    config = load_entity_config(path)
    labels = list(config)
    values = list(config.values())
    values = set(
    a
    for value in values
    for a in value["alias_properties"]
    )
    cypher = """
    MATCH(n:WaterKgV2)
    WITH n, [label IN labels(n) WHERE label <>'WaterKgV2'][0] AS entity_label
    WHERE entity_label in $labels
    RETURN
        entity_label,
        properties(n) as properties

    """
    records = []

    with driver.session(database="neo4j") as session:

    print(values)
