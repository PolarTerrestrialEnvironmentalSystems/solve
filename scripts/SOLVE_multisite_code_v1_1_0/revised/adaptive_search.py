"""Staged search: bounded discovery, source feedback, greedy or epsilon exploration.
No extracted source text is retained. Scores are heuristic, not calibrated probabilities.
"""
import hashlib
import json
import random
import time
from collections import defaultdict


def feedback(r):
    """Credit first reviewed SHA per waterbody to its discovery branch, once only."""
    searches = r.store.rows('SELECT * FROM searches ORDER BY created,id')
    stats = {q['id']: dict(query=q, checked=0, documents=0, gain=0, topics=[], failed_fetches=0) for q in searches}
    seen, covered = set(), defaultdict(set)
    rows = r.store.rows('''SELECT t.branch,t.profile,t.status,t.reason,u.sha,r.rule_response,r.created,r.rowid sequence
        FROM tasks t LEFT JOIN urls u ON t.url=u.url
        LEFT JOIN reviews r ON r.id=t.review_id ORDER BY r.created,r.rowid,t.id''')
    for row in rows:
        stat = stats.get(row['branch'])
        if not stat:
            continue
        if row['status'] == 'blocked':
            stat['failed_fetches'] += 1
        if not row['rule_response'] or row['status'] != 'done':
            continue
        stat['checked'] += 1
        review = json.loads(row['rule_response'])
        if review['decision'] != 'relevant' or review.get('identity_status') != 'candidate':
            continue
        key = (row['profile'], row['sha'])
        if not row['sha'] or key in seen:
            continue
        seen.add(key)
        topics = set(review.get('topic_hits', []))
        new_topics = topics - covered[row['profile']]
        covered[row['profile']].update(topics)
        stat['documents'] += 1
        stat['gain'] += 3 + 2 * len(new_topics)
        stat['topics'] = sorted(set(stat['topics']) | topics)
    partial = set()
    for event in r.store.rows("SELECT detail FROM events WHERE kind='search_engine_failures'"):
        partial.add(json.loads(event['detail'])['search_id'])
    for key, stat in stats.items():
        q = stat['query']
        stat['informative'] = q['status'] in {'done','no_results'} and (
            stat['checked'] > 0 or q['status'] == 'no_results')
        # A partial search can provide positive evidence, but not reliable negative feedback.
        if key in partial and stat['gain'] == 0:
            stat['informative'] = False
    return stats, covered


def seed_query(wb, stage):
    name = wb.get('query_name',wb['name']).replace('"','')
    if stage == 0:
        return f'"{name}" {wb["region"]}', 'overview'
    return f'"{name}" {wb["region"]} filetype:pdf', 'document_body'


def plan(r):
    for wb in r.cfg['waterbodies']:
        profile = r.profiles[wb['id']]
        query, family = seed_query(wb, 0)
        r.add_search(profile,query,family,'Stufe 1: Identität und Einstieg',100)
        for url in wb['seed_urls']:
            r.enqueue(profile,url,label='Benutzer-Startquelle',priority=90,force=True)
    r.store.db.commit()


def choices(r, wb, covered):
    name = wb.get('query_name',wb['name']).replace('"','')
    for domain in wb.get('preferred_domains',[])[:3]:
        yield f'"{name}" site:{domain}', 'authority', 5.0
    for topic in r.cfg['topics']:
        if topic['name'] in covered:
            continue
        for i, term in enumerate((topic.get('keywords') or [topic['name']])[:2]):
            historical = topic['name'] == 'Historische Gesamtentwicklung'
            yield f'"{name}" {wb["region"]} {term}', 'history' if historical else 'topic', (6.0 if historical else 4.0) - i*.5
    yield f'"{name}" Dissertation limnology', 'science', 3.0
    for alias in wb.get('aliases',[]):
        yield f'"{alias}" {wb["region"]} filetype:pdf', 'alias_documents', 3.5


def next_search(r):
    stats, covered = feedback(r)
    now = time.time()
    # Prefer least-searched waterbodies. No source-poor lake can monopolize the run.
    profiles = sorted(r.active, key=lambda p: (sum(s['query']['status'] != 'pending' for s in stats.values() if s['query']['profile']==p), p))
    for profile in profiles:
        history = [s for s in stats.values() if s['query']['profile']==profile]
        pending = [s['query'] for s in history if s['query']['status'] in {'pending','retry'} and s['query']['next_try']<=now]
        if pending:
            return max(pending,key=lambda q:q['priority'])
        if any(s['query']['status'] in {'waiting_external','retry'} for s in history):
            continue
        # Evaluate direct hits before making new decisions (or after 3 completed checks).
        waiting = False
        for s in history:
            q=s['query']
            if q['status']=='done' and s['checked'] < 3:
                n=r.store.one("SELECT COUNT(*) n FROM tasks WHERE branch=? AND depth=0 AND ref_depth=0 AND status IN ('pending','retry','in_progress')",(q['id'],))['n']
                if n:
                    waiting=True
                    break
        if waiting:
            continue
        if len(history)>=r.cfg['search']['adaptive_max_queries']:
            continue
        wb=r.bodies[profile]
        completed=[s for s in history if s['informative']]
        completed.sort(key=lambda s:s['query']['completed'] or '')
        plateau=r.cfg['search']['plateau_queries']
        if len(completed)>=max(4,plateau) and all(s['gain']==0 for s in completed[-plateau:]):
            continue
        if len(history)<2:
            query,family=seed_query(wb,len(history))
            reason='Stufe 2: Dokumente'
        else:
            used={' '.join(s['query']['query'].casefold().split()) for s in history}
            options=[x for x in choices(r,wb,covered[profile]) if ' '.join(x[0].casefold().split()) not in used]
            if not options:
                continue
            family_stats=defaultdict(list)
            for stat in stats.values():
                if stat['informative']:
                    family_stats[stat['query']['family']].append(stat['gain'])
            def score(option):
                values=family_stats[option[1]]
                # A prior prevents one early success/failure from deciding everything.
                return (2*option[2]+sum(values))/(2+len(values))
            seed=int(hashlib.sha256((profile+str(len(history))).encode()).hexdigest()[:16],16)
            rng=random.Random(seed)
            explore=r.cfg['search']['strategy']=='staged_epsilon' and rng.random()<r.cfg['search']['epsilon']
            query,family,_=rng.choice(options) if explore else max(options,key=score)
            reason='Stufe 3: Erkundung' if explore else 'Stufe 3: Quellengewinn und Themenlücken'
        r.add_search(profile,query,family,reason,90)
        r.store.db.commit()
        return r.store.one('SELECT * FROM searches WHERE profile=? AND query=? ORDER BY created DESC LIMIT 1',(profile,query))
    return None
