"""Bounded source-driven checks; no claim of exhaustive semantic validation."""
import re
import uuid
from pathlib import Path

from pydantic import ValidationError
from blablador_client import APIError
from common import canonical, write_json
from prepare_document import normalize_quote
from schemas import extraction_schema
from validate_export import checks


def comparison_gaps(parsed, primary_blocks):
    # Only explicit numeric von/auf comparisons trigger a supplemental request.
    # Join adjacent supplied blocks to detect comparisons broken by PDF page layout.
    source = normalize_quote('\n'.join(b['text'] for b in primary_blocks))
    # A new numeric "von" starts a different comparison; do not pair an earlier
    # reservoir volume with a later flow comparison. Never cross sentence ends.
    pattern = (r'\bvon\s+(\d+(?:[.,]\d+)?)'
               r'((?:(?!\bvon\s+\d|[.!?]\s+[A-ZÄÖÜ]).){0,700}?)'
               r'\bauf\s+(\d+(?:[.,]\d+)?)')
    fields = []
    for event in parsed.events:
        row = event.model_dump(mode='json')
        fields.append(normalize_quote(canonical({k: v for k, v in row.items()
                       if k not in {'evidence', 'extraction_metadata', 'source_document', 'quality_assessment'}})))
    gaps = []
    for match in re.finditer(pattern, source, re.IGNORECASE):
        first, second = match.group(1), match.group(3)
        # Word boundaries avoid treating 2.7 as a match for 12.7 etc.
        def has_number(text, number):
            return bool(re.search(r'(?<![\d.,])' + re.escape(number).replace(',', '[.,]').replace(r'\.', '[.,]') + r'(?![\d.,])', text))
        baseline_periods = re.findall(r'\b(?:18|19|20)\d{2}\s*[-–]\s*(?:18|19|20)\d{2}\b', match.group(2))
        if not any(has_number(text, first) and has_number(text, second)
                   and all(p.replace(' ', '') in text.replace(' ', '') for p in baseline_periods) for text in fields):
            gaps.append({'kind': 'incomplete_numeric_comparison', 'from': first, 'to': second,
                         'baseline_periods': baseline_periods, 'source_excerpt': match.group(0)})
    return gaps


def quality_issues(parsed, ontology, blocks, primary_blocks):
    issues = comparison_gaps(parsed, primary_blocks)
    for kind, rows, key in [('events', parsed.events, 'event_id'), ('relationships', parsed.relationships, 'relationship_id')]:
        for row in rows:
            data = row.model_dump(mode='json')
            fatal, _ = checks(data, ontology, blocks, kind == 'events')
            if fatal:
                issues.append({'kind': kind, 'id': data[key], 'errors': fatal})
    return issues


def validate_candidate(original, candidate, ontology, blocks):
    """Preserve every existing factual field; only fix quotes/IDs or add events."""
    before, after = original.model_dump(mode='json'), candidate.model_dump(mode='json')
    for field in ['document', 'context_requests', 'notes']:
        if before[field] != after[field]:
            raise ValueError('quality_repair_changed_metadata')
    for kind, key in [('events', 'event_id'), ('relationships', 'relationship_id')]:
        old = {r[key]: r for r in before[kind]}
        new = {r[key]: r for r in after[kind]}
        if len(new) != len(after[kind]) or len(old) != len(before[kind]):
            raise ValueError('quality_repair_duplicate_ids')
        if not old.keys() <= new.keys():
            raise ValueError('quality_repair_removed_records')
        if kind == 'relationships' and old.keys() != new.keys():
            raise ValueError('quality_repair_added_relationships')
        for ident, row in new.items():
            if ident in old:
                allowed = {'evidence', 'categories', 'ontology_concept'}
                if {k:v for k,v in row.items() if k not in allowed} != {k:v for k,v in old[ident].items() if k not in allowed}:
                    raise ValueError('quality_repair_changed_facts')
                # Valid classification must remain stable.
                for field, valid in [('categories', {t['id'] for t in ontology['topics']}),
                                     ('ontology_concept', {c['id'] for c in ontology['classes']})]:
                    previous = old[ident].get(field)
                    was_valid = all(t in valid for t in previous) if isinstance(previous, list) else previous is None or previous in valid
                    if was_valid and previous != row.get(field):
                        raise ValueError('quality_repair_changed_valid_classification')
            fatal, _ = checks(row, ontology, blocks, kind == 'events')
            if fatal:
                raise ValueError('quality_repair_invalid_evidence_or_ids:' + ','.join(fatal))
    # Relationship endpoints are immutable; new events cannot introduce dangling edges.


def repair_quality(client, parsed, messages, chunk, cover, ontology, output, variant, original_path):
    blocks = {b['block_id']: b for b in cover + chunk.get('context_blocks', []) + chunk['blocks']}
    issues = quality_issues(parsed, ontology, blocks, chunk['blocks'])
    if not issues:
        return parsed, [], None
    audit = [{'kind': 'source_quality_repair', 'chunk_id': chunk['chunk_id'], 'issues': issues,
              'original_raw_response_file': str(original_path), 'status': 'pending'}]
    instruction = (
        'Prüfe ausschließlich diese konkreten Probleme anhand der gelieferten Quellblöcke: ' + canonical(issues) + '. '
        'Alle vorhandenen Events, Beziehungen und IDs behalten. Fachliche Inhalte, Werte, Zeitangaben und Status unverändert lassen. '
        'Nur ungültige Kategorien/Klassen-IDs durch erlaubte IDs ersetzen und ungültige Belege korrigieren. '
        'Bei mehreren Blöcken mehrere separate wörtliche Belege nutzen, niemals Zitate zusammenziehen oder mit ... kürzen. '
        'Für einen unvollständigen Zahlenvergleich darfst du ein zusätzliches, vollständig belegtes Event mit neuer ID ergänzen; '
        'darin beide Werte und JEWEILS den zugehörigen Zeitraum in values[].time_original/time_normalized erhalten. '
        'Keine anderen Events ergänzen, keine neuen Beziehungen. Keine fehlenden Werte oder Zeiträume erfinden. '
        'document, context_requests und notes unverändert lassen. Vollständiges Extraction-JSON zurückgeben.'
    )
    request = messages + [{'role': 'assistant', 'content': parsed.model_dump_json(exclude_none=True)},
                          {'role': 'user', 'content': instruction}]
    from extract_facts import estimated_tokens, parse_answer
    if estimated_tokens(request) + client.settings.max_output_tokens > .9 * client.settings.context_tokens:
        audit[0].update(status='skipped', reason='quality_repair_context_budget_exceeded')
        return parsed, audit, None
    try:
        response = client.complete(request, extraction_schema(ontology), ontology=variant,
                                   chunk_id=chunk['chunk_id'], purpose='source_quality_repair')
        path = output / 'raw_responses' / (chunk['chunk_id'] + '_quality_' + uuid.uuid4().hex[:12] + '.json')
        write_json(path, response)
        audit[0]['repair_raw_response_file'] = str(path)
        candidate = parse_answer(response)
        validate_candidate(parsed, candidate, ontology, blocks)
        # Additional records must be justified by the comparisons that triggered repair.
        old_ids = {e.event_id for e in parsed.events}
        added = [e for e in candidate.events if e.event_id not in old_ids]
        if added and not any(i['kind'] == 'incomplete_numeric_comparison' for i in issues):
            raise ValueError('quality_repair_unrequested_events')
        gaps = [i for i in issues if i['kind'] == 'incomplete_numeric_comparison']
        if len(added) > len(gaps):
            raise ValueError('quality_repair_excess_additions')
        for event in added:
            row = event.model_dump(mode='json')
            text = canonical({k:v for k,v in row.items() if k not in {'evidence', 'source_document', 'quality_assessment'}})
            if not any(gap['from'] in text and gap['to'] in text for gap in gaps):
                raise ValueError('quality_repair_unrelated_addition')
            _, warnings = checks(row, ontology, blocks, True)
            if any(w.startswith(('quantity_', 'invalid_quantity_time')) for w in warnings):
                raise ValueError('quality_repair_unverified_quantity')
        remaining = quality_issues(candidate, ontology, blocks, chunk['blocks'])
        if remaining:
            raise ValueError('quality_repair_unresolved_issues')
    except (APIError, ValidationError, ValueError, OverflowError) as exc:
        audit[0].update(status='rejected', reason=type(exc).__name__ if isinstance(exc, (APIError, ValidationError)) else str(exc))
        return parsed, audit, None
    audit[0].update(status='accepted')
    return candidate, audit, path
