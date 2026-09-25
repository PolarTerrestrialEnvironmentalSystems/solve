"""Human-review queue from existing evidence; no fetches or status changes."""
import re

FIELDS = ['waterbody_name','url','title','human_review_reasons','human_review_note',
          'access_error','task_status','decision','identity_status','relevance_reason',
          'preflight_reason','start_link_reason','parent_urls','link_labels','locations',
          'original_file','waterbody_id']


def entry(record, preflight=None, start=None):
    preflight, start = preflight or {}, start or {}
    if record.get('decision')=='irrelevant':
        return None
    reasons, notes = [], []
    error = record.get('access_error') or record.get('reason') or ''
    if re.fullmatch(r'http_(401|403|406|407|429|451)',error) or error in {'robots_restricted','robots_disallowed'}:
        reasons.append('automated_access_blocked')
        notes.append('Zugriff im Browser oder über einen regulären Zugangsweg prüfen; maschinelle Sperre nicht umgehen.')
    elif error in {'http_404','http_410'}:
        reasons.append('source_not_found')
        notes.append('Quelle nicht gefunden; auf der Fundseite nach einer aktuellen Adresse suchen.')
    elif record.get('task_status') in {'blocked','retry'} and error not in {'host_cooldown','run_fetch_budget','run_host_budget'}:
        reasons.append('technical_access_problem')
        notes.append('Technischer Abruf fehlgeschlagen; Erreichbarkeit manuell prüfen.')
    parsed=record.get('parse_status')
    if parsed and parsed!='ok':
        reasons.append('content_not_reliably_readable')
        notes.append('Original im Browser/PDF-Viewer auf lesbaren Inhalt prüfen.')
    complete=record.get('review_status')=='complete'
    if record.get('decision')=='uncertain' or (complete and record.get('identity_status') in {'unresolved','conflict'}):
        reasons.append('identity_or_relevance_uncertain')
        notes.append('Gewässerzuordnung und fachliche Relevanz anhand der Quelle prüfen.')
    elif record.get('original_bytes') and not complete:
        reasons.append('content_review_unresolved')
        notes.append('Original vorhanden, aber automatische Inhaltsbewertung noch nicht abgeschlossen.')
    if not complete:
        for assessment,label in [(preflight,'preflight_uncertain'),(start,'start_link_uncertain')]:
            if assessment.get('category') in {'tourism','navigation','unrelated'}:
                continue
            if assessment.get('identity')=='uncertain' or assessment.get('action')=='uncertain' or assessment.get('category')=='unknown':
                reasons.append(label)
                notes.append('Metadaten reichen für eine sichere Auswahl nicht aus; Quelle manuell sichten.')
    if not reasons:
        return None
    routes=record.get('discovered_via',[])
    unique=lambda key:list(dict.fromkeys(r.get(key) for r in routes if r.get(key)))
    result={key:record.get(key) for key in FIELDS}
    result.update(human_review_reasons=list(dict.fromkeys(reasons)),human_review_note=' '.join(dict.fromkeys(notes)),
        access_error=record.get('access_error') or (error if record.get('task_status') in {'blocked','retry'} else ''),
        preflight_reason=preflight.get('reason',''),start_link_reason=start.get('reason',''),
        parent_urls=unique('parent_url'),link_labels=unique('label'),locations=unique('locator'))
    return result
