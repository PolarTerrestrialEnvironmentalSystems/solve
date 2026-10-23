"""One conservative repair request; original facts are never silently replaced."""
from pathlib import Path
import uuid

from pydantic import ValidationError
from blablador_client import APIError
from common import canonical, write_json
from schemas import Event, Extraction, extraction_schema
from relationship_guard import CAUSAL_RELATIONSHIP_TYPES
from prepare_document import normalize_quote
from validate_export import checks, resolve_ref, semantic_key


def event_index(parsed, chunk_id, existing_events):
    events = {e["qualified_id"]: Event.model_validate(
        {k: v for k, v in e.items() if k != "qualified_id"}).model_dump(mode="json")
        for e in existing_events}
    for e in parsed.events:
        events[chunk_id + ":" + e.event_id] = e.model_dump(mode="json")
    return events


def flagged_relationships(parsed, chunk_id, existing_events):
    events = event_index(parsed, chunk_id, existing_events)
    keys = {ref: semantic_key(event) for ref, event in events.items()}
    bad = []
    for edge in parsed.relationships:
        if edge.relationship_type not in CAUSAL_RELATIONSHIP_TYPES:
            continue
        source = resolve_ref(edge.source_event_id, chunk_id, keys)
        target = resolve_ref(edge.target_event_id, chunk_id, keys)
        if edge.source_event_id == edge.target_event_id or (source is not None and source == target):
            bad.append(edge.relationship_id)
    return bad


def validate_repair(original, candidate, flagged, chunk_id, existing_events, ontology, blocks):
    before = original.model_dump(mode="json")
    after = candidate.model_dump(mode="json")
    for field in ["events", "relationships"]:
        name = "event_id" if field == "events" else "relationship_id"
        ids = [r[name] for r in after[field]]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_repair_ids")
    old_events = {e["event_id"]: e for e in before["events"]}
    new_events = {e["event_id"]: e for e in after["events"]}
    if any(new_events.get(k) != v for k, v in old_events.items()):
        raise ValueError("repair_changed_original_events")
    old_edges = {r["relationship_id"]: r for r in before["relationships"]}
    new_edges = {r["relationship_id"]: r for r in after["relationships"]}
    if set(new_edges) - set(old_edges):
        raise ValueError("repair_added_unrelated_relationships")
    if any(new_edges.get(k) != v for k, v in old_edges.items() if k not in flagged):
        raise ValueError("repair_changed_unaffected_relationships")
    for key in ["document", "context_requests", "notes"]:
        if before[key] != after[key]:
            raise ValueError("repair_changed_unaffected_metadata")
    index = event_index(candidate, chunk_id, existing_events)
    refs = {key: key for key in index}
    statements = {normalize_quote(e["statement"]).casefold() for e in old_events.values()}
    used = set()
    for key in flagged:
        if key not in new_edges:  # Withdrawal is allowed, and audited below.
            continue
        row = new_edges[key]
        if row["relationship_type"] != old_edges[key]["relationship_type"]:
            raise ValueError("repair_changed_relationship_type")
        # Uncertainty and negation must not be upgraded during structural repair.
        for field in ["assertion_mode", "polarity", "relationship_scope", "derivation_status"]:
            if row[field] != old_edges[key][field]:
                raise ValueError("repair_changed_relationship_status")
        fatal, _ = checks(row, ontology, blocks, False)
        if fatal:
            raise ValueError("repair_invalid_relationship:" + ",".join(fatal))
        endpoints = [resolve_ref(row[k], chunk_id, refs) for k in ["source_event_id", "target_event_id"]]
        if any(p is None for p in endpoints):
            raise ValueError("repair_unresolved_reference")
        if normalize_quote(index[endpoints[0]]["statement"]).casefold() == normalize_quote(index[endpoints[1]]["statement"]).casefold():
            raise ValueError("repair_duplicate_event_statements")
        for endpoint in endpoints:
            fatal, _ = checks(index[endpoint], ontology, blocks, True)
            if fatal:
                raise ValueError("repair_invalid_endpoint:" + ",".join(fatal))
        used.update(endpoints)
    for key in set(new_events) - set(old_events):
        row = new_events[key]
        if chunk_id + ":" + key not in used:
            raise ValueError("repair_added_unused_event")
        statement = normalize_quote(row["statement"]).casefold()
        if statement in statements:
            raise ValueError("repair_copied_original_event")
        statements.add(statement)
    if flagged_relationships(candidate, chunk_id, existing_events):
        raise ValueError("repair_still_contains_causal_self_relationship")


def repair_causal_relationships(client, parsed, messages, chunk, cover, ontology,
                                output: Path, variant, original_path, existing_events):
    flagged = flagged_relationships(parsed, chunk["chunk_id"], existing_events)
    if not flagged:
        return parsed, [], None
    originals = [r.model_dump(mode="json") for r in parsed.relationships if r.relationship_id in flagged]
    review = [{"kind": "causal_self_relationship_repair", "chunk_id": chunk["chunk_id"],
               "relationship_ids": flagged, "original_candidates": originals,
               "original_raw_response_file": str(original_path), "status": "pending"}]
    instruction = (
        "Repariere ausschließlich die folgenden kausalen Selbstbeziehungen: " + canonical(flagged) + ". "
        "Nutze ausschließlich die bereits gelieferten Original-Quellblöcke als Belege. "
        "Lass alle bisherigen Events vollständig unverändert; bei Bedarf ergänze zwei wirklich unterschiedliche "
        "belegte Ursache-/Wirkungs-Events mit neuen eindeutigen lokalen IDs. Keine bloßen Kopien mit neuen IDs. "
        "Ändere nur die betroffenen Beziehungen unter ihrer bisherigen relationship_id; Beziehungstyp, "
        "assertion_mode, polarity, relationship_scope und derivation_status bleiben unverändert. "
        "Wenn keine belegte Aufteilung möglich ist, entferne nur die betroffene Beziehung. "
        "Alle anderen Beziehungen, document, context_requests und notes unverändert zurückgeben. "
        "Neue Events müssen von einer reparierten Beziehung verwendet werden. Keine zusätzlichen Fakten. "
        "Zitate wörtlich aus dem jeweiligen Block übernehmen; nur erlaubte Ontologie- und Themen-IDs. "
        "Gib das vollständige korrigierte Extraction-JSON zurück."
    )
    request = messages + [{"role": "assistant", "content": parsed.model_dump_json(exclude_none=True)},
                          {"role": "user", "content": instruction}]
    from extract_facts import estimated_tokens, parse_answer
    if estimated_tokens(request) + client.settings.max_output_tokens > .9 * client.settings.context_tokens:
        review[0].update(status="skipped", reason="repair_context_budget_exceeded")
        return parsed, review, None
    try:
        response = client.complete(request, extraction_schema(ontology), ontology=variant,
                                   chunk_id=chunk["chunk_id"], purpose="causal_self_relationship_repair")
        path = output / "raw_responses" / (chunk["chunk_id"] + "_causal_repair_" + uuid.uuid4().hex[:12] + ".json")
        write_json(path, response)
        review[0]["repair_raw_response_file"] = str(path)
        candidate = parse_answer(response)
        blocks = {b["block_id"]: b for b in cover + chunk.get("context_blocks", []) + chunk["blocks"]}
        validate_repair(parsed, candidate, flagged, chunk["chunk_id"], existing_events, ontology, blocks)
    except (APIError, ValidationError, ValueError, OverflowError) as exc:
        # Do not split/retry a semantic repair or discard already valid facts.
        reason = type(exc).__name__ if not isinstance(exc, ValueError) or isinstance(exc, ValidationError) else str(exc)
        review[0].update(status="rejected", reason=reason)
        return parsed, review, None
    retained = {r.relationship_id for r in candidate.relationships}
    review[0].update(status="accepted", withdrawn_relationship_ids=[k for k in flagged if k not in retained])
    return candidate, review, path
