"""Small deterministic helpers; no database or network side effects on import."""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata
from pathlib import Path


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def stable_id(namespace, *parts):
    return namespace + ':' + hashlib.sha256(canonical_json(parts).encode('utf8')).hexdigest()[:24]


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    temp = p.with_suffix(p.suffix + '.tmp')
    temp.write_text(content, encoding='utf8')
    temp.replace(p)


def normalized_name(value):
    # Keep Roman numerals, numbers, and all meaningful tokens. No "I/II" folding.
    return ' '.join(re.sub(r'[^\w]+', ' ', unicodedata.normalize('NFKC', value).casefold()).split())


def canonical_eu_code(value):
    value = str(value).strip().upper()
    # Historical DE_LW_ and current DELW_ forms have explicit category semantics.
    if value.startswith('DE_LW_'):
        value = 'DELW_' + value[6:]
    return value


def code_matches(requested, official):
    requested = canonical_eu_code(requested)
    official = canonical_eu_code(official)
    if requested == official:
        return True
    # Project's short codes are Sachsen-Anhalt state IDs. Never strip arbitrary
    # prefixes or match a suffix across states/countries.
    return bool(re.fullmatch(r'[A-Z]+\d*OW\d+-\d+', requested)) and official == 'DELW_DEST_' + requested


def targets_from_events(events):
    groups = {}
    ids_seen = set()
    for event in events:
        if event['event_id'] in ids_seen:
            raise ValueError('Duplicate event_id: ' + event['event_id'])
        ids_seen.add(event['event_id'])
        name = event['see']
        row = groups.setdefault(name, {'lake_id': stable_id('lake', name), 'name': name,
                                       'aliases': [], 'requested_identifiers': []})
        for identifier in event.get('waterbody_identifiers', []):
            if identifier not in row['requested_identifiers']:
                row['requested_identifiers'].append(identifier)
    return list(groups.values())


def select_matches(lakes, water_bodies, decisions=None):
    """Exact known IDs accepted only with corroborated names or reviewed mapping.

    Explicit decisions must cite evidence; fuzzy/name-only hits remain candidates.
    Multiple physical lakes sharing a code retain independent lake IDs and use
    part_of relationships; no station/event observations are conflated.
    """
    decisions = decisions or {}
    links, candidates = [], []
    unknown_decisions = set(decisions) - {lake['name'] for lake in lakes}
    if unknown_decisions:
        raise ValueError('Decisions for unknown project lakes: ' + str(sorted(unknown_decisions)))
    use_counts = {}
    for lake in lakes:
        decision = decisions.get(lake['name'])
        possible = [w for w in water_bodies if (w['eu_code'] == decision['eu_code'] if decision else
                    any(code_matches(i['id'], w['eu_code']) for i in lake['requested_identifiers']))]
        if len(possible) == 1:
            use_counts.setdefault(possible[0]['water_body_id'], set()).add(lake['lake_id'])
    for lake in lakes:
        name_keys = {normalized_name(n) for n in [lake['name'], *lake['aliases']]}
        requested = [i['id'] for i in lake['requested_identifiers']]
        matching = [w for w in water_bodies if any(code_matches(i, w['eu_code']) for i in requested)]
        decision = decisions.get(lake['name'])
        if decision:
            evidence = decision.get('evidence')
            valid_evidence = isinstance(evidence, list) and evidence and all(
                isinstance(e, dict) and str(e.get('url', '')).startswith(('https://', 'http://')) and e.get('reason')
                for e in evidence)
            if not valid_evidence or not decision.get('reviewed_by') or decision.get('relation_type') not in ('same_water_body', 'part_of'):
                raise ValueError('Reviewed mapping requires evidence and relation_type: ' + lake['name'])
            chosen = [w for w in water_bodies if w['eu_code'] == decision['eu_code']]
            if len(chosen) != 1:
                raise ValueError('Reviewed EU code absent or ambiguous: ' + lake['name'])
            matching = chosen
        if not matching:
            # Candidates are exact names only here; names alone are NOT a FK.
            matching = [w for w in water_bodies if normalized_name(w['name']) in name_keys]
        if not matching:
            candidates.append(dict(candidate_id=stable_id('match', lake['lake_id'], 'unmatched'),
                lake_id=lake['lake_id'], water_body_id=None, status='unmatched', method='no_supported_match',
                reason='No uniquely supported official ID/name found in selected source snapshot.', evidence={'requested_identifiers': lake['requested_identifiers']}))
            continue
        for water in matching:
            code_ok = any(code_matches(i, water['eu_code']) for i in requested)
            name_ok = normalized_name(water['name']) in name_keys
            accepted = len(matching) == 1 and (bool(decision) or (code_ok and name_ok))
            method = 'reviewed_official_mapping' if decision else 'exact_code_and_name' if accepted else 'identifier_or_name_candidate'
            reason = ('Official code and corroborated name or documented source review.' if accepted else
                      'Name-only, conflicting name, or multiple candidates; not an accepted foreign-key association.')
            ev = {'requested_identifiers': lake['requested_identifiers'], 'official_eu_code': water['eu_code'],
                  'official_name': water['name'], 'raw_id': water['raw_id'], 'review': decision}
            candidates.append(dict(candidate_id=stable_id('match', lake['lake_id'], water['water_body_id']),
                lake_id=lake['lake_id'], water_body_id=water['water_body_id'], status='accepted' if accepted else 'review',
                method=method, reason=reason, evidence=ev))
            if accepted:
                shared = len(use_counts.get(water['water_body_id'], set())) > 1
                relation = decision['relation_type'] if decision else ('part_of' if shared else 'same_water_body')
                if shared and relation != 'part_of':
                    raise ValueError('Shared WRRL code cannot collapse several project lakes: ' + lake['name'])
                links.append(dict(link_id=stable_id('lake_wb', lake['lake_id'], water['water_body_id']),
                    lake_id=lake['lake_id'], water_body_id=water['water_body_id'], relation_type=relation,
                    method=method, evidence=ev))
    return links, candidates
