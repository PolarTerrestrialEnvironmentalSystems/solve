"""Resolve exact official waterbody catalogue links and validate them, without search APIs.

Requires pypdf (the normal crawler dependency). Catalogues and per-waterbody
checkpoints live under --output, never in the source tree. HEAD-verified PDF
links are explicitly marked as metadata checks, not downloaded/read documents.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import html
import io
import json
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse as up
import urllib.request as ur

BB_CATALOGUE = 'https://mleuv-daten.brandenburg.de/w/Steckbriefe/Abrufliste_RWBODY_DEBB.pdf'
BB_LAKE_CATALOGUE = 'https://mleuv-daten.brandenburg.de/w/biodaten/info/LinklisteSeen.pdf'
BB_LIMNO_CATALOGUE = 'https://lfu.brandenburg.de/lfu/de/aufgaben/wasser/fliessgewaesser-und-seen/gewaesserzustandsbewertung/seensteckbriefe/'
SN_SERVICE = 'https://luis.sachsen.de/arcgis/rest/services/wasser/wrrlowksteckbriefe_22_27/FeatureServer'
SN_HISTORICAL_SERVICE = 'https://luis.sachsen.de/arcgis/rest/services/wasser/wrrlowksteckbriefe_16_21/FeatureServer'
TH_ASSESSMENTS = 'https://aktion-fluss.de/wp-content/uploads/Anlage_1.pdf'
FGG_DRINKING_WATER = ('https://lvwa.sachsen-anhalt.de/fileadmin/Bibliothek/Politik_und_Verwaltung/LVWA/LVwA/Dokumente/'
                      '4_landwirtschaftumwelt/404/Seite_Wasser_bewegt/FGG_Elbe/Anhaenge/Bewirtschaftungsplan_FGG_Elbe_2021_Anhang_A1-1.pdf')
FGG_INVENTORY = ('https://www.fgg-elbe.de/berichte/aktualisierung-nach-art-13-2021.html?'
                 '_hash=WqdtWteCp5iRIrRTs45ObIMXICF3-zp_PhPeGMxEC-E&ctx=a%3A1%3A%7Bs%3A2%3A%22id%22%3Bi%3A14889%3B%7D'
                 '&d=attachment&f=Bewirtschaftungsplan_FGG_Elbe_2021_Anhang_A5-2.pdf'
                 '&p=Downloads%2FEG_WRRL%2Fber%2Fbp2021%2Fanhaenge%2FBewirtschaftungsplan_FGG_Elbe_2021_Anhang_A5-2.pdf')
MV_RIVER_CATALOGUE = 'https://fis-wasser-mv.de/charts/steckbriefe/neu/fg/fg.php'
MV_LAKE_CATALOGUE = 'https://fis-wasser-mv.de/charts/steckbriefe/neu/sg/sg.php'
FGG_2015_PAGE = 'https://www.fgg-elbe.de/berichte/aktualisierung-nach-art-13.html'
FGG_MEASURES_2021 = 'https://www.fgg-elbe.de/files/Downloads/EG_WRRL/ber/mnp2021/Massnahmenprogramm_FGG_Elbe_2021_Anhang_M5.pdf'
FGG_MEASURES_2015 = ('https://wasserblick.bafg.de/servlet/is/156065/M4_Massnahmen_Wasserkoerper_aller_Elbe-Bundeslaender.pdf'
                     '?command=downloadContent&filename=M4_Massnahmen_Wasserkoerper_aller_Elbe-Bundeslaender.pdf')
USER_AGENT = 'SOLVE-official-catalogue-check/1.0'
CAPTCHA = re.compile(rb'captcha|verify you are human|access denied|friendly-challenge|cf-chl-', re.I)


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def http_url(value):
    """Encode literal spaces/unicode from official URL fields without changing paths."""
    parts = up.urlsplit(value)
    return up.urlunsplit((parts.scheme, parts.netloc, up.quote(parts.path, safe="/%:@-._~!$&'()*+,;="),
                         up.quote(parts.query, safe="%=&;:+,/?@-._~!$'()*[]"), ''))


class HostRateLimiter:
    def __init__(self, interval=1.0):
        if interval < 1:
            raise ValueError('Minimum one second between requests to the same host')
        self.interval, self.lock, self.next_start = interval, threading.Lock(), {}

    def wait(self, url):
        host = up.urlsplit(url).hostname
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_start.get(host, 0))
            self.next_start[host] = start + self.interval
        if start > now:
            time.sleep(start - now)


class RateLimitedRedirect(ur.HTTPRedirectHandler):
    def __init__(self, limiter):
        self.limiter = limiter

    def redirect_request(self, request, fp, code, message, headers, newurl):
        redirected = super().redirect_request(request, fp, code, message, headers, newurl)
        if redirected is not None:
            # urllib otherwise converts a redirected HEAD to GET.
            if request.get_method() == 'HEAD':
                redirected.method = 'HEAD'
            self.limiter.wait(newurl)
        return redirected


class Client:
    def __init__(self, output, interval=1.0, timeout=35):
        self.output, self.timeout = Path(output), timeout
        self.limiter = HostRateLimiter(interval)
        self.validation_lock = threading.Lock()
        self.url_locks = {}

    def request(self, url, method='GET', cap=65536, headers=None):
        self.limiter.wait(url)
        request = ur.Request(url, method=method, headers={'User-Agent': USER_AGENT, **(headers or {})})
        opener = ur.build_opener(RateLimitedRedirect(self.limiter))
        record = dict(url=url, method=method, checked_at=utc())
        try:
            with opener.open(request, timeout=self.timeout) as response:
                body = response.read(cap) if method != 'HEAD' else b''
                record.update(http_status=response.status, final_url=response.url,
                              content_type=response.headers.get('Content-Type', '').split(';')[0].strip().lower(),
                              content_length=response.headers.get('Content-Length'),
                              bytes_sampled=len(body))
                return body, record
        except urllib.error.HTTPError as error:
            record.update(http_status=error.code, final_url=error.url,
                          content_type=error.headers.get('Content-Type', '').split(';')[0].strip().lower(),
                          error='http_' + str(error.code))
        except Exception as error:
            record.update(http_status=None, error=type(error).__name__)
        return b'', record

    def catalogue(self, url, filename, expected, refresh=False):
        destination = self.output / 'raw' / filename
        metadata = destination.with_suffix(destination.suffix + '.source.json')
        if destination.exists() and metadata.exists() and not refresh:
            body = destination.read_bytes()
            record = json.loads(metadata.read_text(encoding='utf-8'))
            if record.get('url') == url and digest(body) == record.get('sha256'):
                return body
        body, record = self.request(url, cap=40_000_001)
        if record.get('http_status') != 200 or len(body) > 40_000_000:
            raise RuntimeError('Catalogue download failed: ' + filename + ' ' + str(record))
        if expected == 'pdf' and not body.lstrip().startswith(b'%PDF-'):
            raise ValueError('Not a PDF catalogue: ' + filename)
        if expected == 'json':
            parsed = json.loads(body)
            if isinstance(parsed, dict) and parsed.get('error'):
                raise ValueError('ArcGIS application error: ' + filename)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
        write_json(metadata, dict(record, sha256=digest(body)))
        return body

    def validate_pdf(self, url, refresh=False):
        with self.validation_lock:
            lock = self.url_locks.setdefault(url, threading.Lock())
        with lock:
            return self._validate_pdf(url, refresh)

    def _validate_pdf(self, url, refresh=False):
        path = self.output / 'url_checks' / (digest(url.encode()) + '.json')
        if path.exists() and not refresh:
            saved = json.loads(path.read_text(encoding='utf-8'))
            if saved.get('url') == url:
                return saved
        _, head = self.request(url, method='HEAD')
        attempts = [head]
        result = dict(head, validation_ok=False)
        if head.get('http_status') == 200 and head.get('content_type') == 'application/pdf':
            result.update(validation_ok=True, validation_basis='catalogue_identity_and_http_head_pdf_mime',
                          original_bytes_downloaded=False)
        elif head.get('http_status') in (200, 405, 501) or head.get('http_status') is None:
            # A small prefix can validate unsupported HEAD or application/octet-stream.
            body, sample = self.request(url, cap=8192, headers={'Range': 'bytes=0-8191'})
            attempts.append(sample)
            result.update(sample)
            if (sample.get('http_status') in (200, 206)
                    and sample.get('content_type') in ('application/pdf', 'application/octet-stream')
                    and body.lstrip().startswith(b'%PDF-')):
                result.update(validation_ok=True, validation_basis='catalogue_identity_and_pdf_signature_sample',
                              original_bytes_downloaded=False, sampled_prefix_sha256=digest(body))
            else:
                result['error'] = 'captcha_or_access_challenge' if CAPTCHA.search(body) else 'not_a_valid_pdf_response'
        result['attempts'] = attempts
        write_json(path, result)
        return result

    def validate_html(self, url, local_id, refresh=False):
        """Verify a catalogue-linked HTML factsheet by actual content and exact ID."""
        key = digest(('html:' + local_id + ':' + url).encode())
        path = self.output / 'url_checks' / (key + '.json')
        if path.exists() and not refresh:
            return json.loads(path.read_text(encoding='utf-8'))
        body, record = self.request(url, cap=512001)
        text = html.unescape(re.sub(r'<[^>]+>', ' ', body.decode('utf-8', 'replace')))
        exact = re.search(r'(?<![A-Za-z0-9_-])' + re.escape(local_id) + r'(?![A-Za-z0-9_-])', text)
        result = dict(record, validation_ok=False)
        if CAPTCHA.search(body):
            result['error'] = 'captcha_or_access_challenge'
        elif (record.get('http_status') == 200 and record.get('content_type') in ('text/html', 'application/xhtml+xml')
                and len(body) <= 512000 and exact and 'steckbrief' in text.casefold()
                and 'belastungssituation' in text.casefold()):
            sample = self.output / 'html_samples' / (key + '.html')
            sample.parent.mkdir(parents=True, exist_ok=True)
            sample.write_bytes(body)
            result.update(validation_ok=True, validation_basis='exact_catalogue_identifier_and_html_factsheet_content',
                          verified_local_id=local_id, body_sha256=digest(body), original_bytes_downloaded=True,
                          sample_path=str(sample))
        else:
            result['error'] = 'not_a_verified_waterbody_html_factsheet'
        write_json(path, result)
        return result


def bb_candidates(pdf_bytes, inventory, category='RW', source_url=BB_CATALOGUE):
    from pypdf import PdfReader
    wanted = {w['waterbody_id']: w for w in inventory}
    result = {}
    for page_number, page in enumerate(PdfReader(io.BytesIO(pdf_bytes)).pages, 1):
        text = page.extract_text() or ''
        codes = set(re.findall(r'\bDE' + category + r'_DEBB[0-9A-Za-z_-]+', text))
        urls = []
        for ref in page.get('/Annots', []):
            annotation = ref.get_object()
            action = annotation.get('/A')
            if action:
                uri = action.get_object().get('/URI')
                if uri:
                    urls.append(str(uri))
        # Some document revisions include visible links but no annotation objects.
        urls.extend(re.findall(r'https?://[^\s<>]+\.pdf', text))
        for url in set(urls):
            parts = up.urlsplit(url)
            filename = up.unquote(parts.path.rsplit('/', 1)[-1])
            match = re.fullmatch(r'(DE' + category + r'_DEBB[0-9A-Za-z_-]+)\.pdf', filename, re.I)
            if not match:
                continue
            code = match[1]
            if code not in codes or code not in wanted:
                continue
            # Host migration is only a candidate; acceptance requires its own HTTP check.
            current = (up.urlunsplit(('https', 'mleuv-daten.brandenburg.de', parts.path, parts.query, ''))
                       if parts.hostname in ('mluk.brandenburg.de', 'mleuv.brandenburg.de', 'mleuv-daten.brandenburg.de') else url)
            if parts.hostname not in ('mluk.brandenburg.de', 'mleuv.brandenburg.de', 'mleuv-daten.brandenburg.de'):
                continue
            entry = dict(url=current, catalogue_url=url, fallback_url=url if current != url else None,
                         title='LfU WRRL-Steckbrief: ' + wanted[code]['name_original'] + ' (' + code + ')',
                         provider='LfU Brandenburg', source_url=source_url,
                         catalogue_page=page_number, catalogue_verified_waterbody_id=code,
                         evidence_basis='Exact EU code appears on catalogue page and in the linked PDF filename',
                         identity_scope='exact_wfd_waterbody', identity_method='exact_catalogue_identifier')
            result.setdefault(code, {})[current] = entry
    return {code: list(entries.values()) for code, entries in result.items()}


def bb_limnological_candidates(html_bytes, inventory):
    wanted = {w['waterbody_id']: w for w in inventory if w['wfd_category'] == 'LW'}
    result = {}
    for href in re.findall(r'href\s*=\s*[\"\']([^\"\']+)[\"\']', html_bytes.decode('utf-8', 'replace'), re.I):
        url = up.urljoin(BB_LIMNO_CATALOGUE, html.unescape(href))
        parts = up.urlsplit(url)
        match = re.fullmatch(r'/daten/w/seen/([0-9]+)\.pdf', parts.path)
        if parts.hostname != 'lfu.brandenburg.de' or not match:
            continue
        code = 'DELW_DEBB' + match[1]
        if code not in wanted:
            continue
        entry = dict(url=url, title='LfU limnologischer Seensteckbrief: ' + wanted[code]['name_original'] + ' (' + code + ')',
                     provider='LfU Brandenburg', source_url=BB_LIMNO_CATALOGUE, source_local_id=match[1],
                     catalogue_verified_waterbody_id=code, report_type='limnological_lake_factsheet',
                     identity_scope='exact_wfd_waterbody', identity_method='exact_catalogue_identifier',
                     evidence_basis='Official limnological catalogue links this PDF; numeric waterbody identifier in its filename equals the complete EU identifier after DELW_DEBB')
        result[code] = [entry]
    return result


def sn_candidates(features, inventory, layer, source_url, historical=False):
    category = 'LW' if layer == 0 else 'RW'
    prefix = 'DE' + category + '_'
    # Namespace decomposition, not name matching or guessed download filenames.
    wanted = {w['waterbody_id'][len(prefix):]: w for w in inventory
              if w['waterbody_id'].startswith(prefix) and w['wfd_category'] == category}
    result = {}
    for feature in features:
        attr = feature.get('attributes', {})
        code = attr.get('OWK_ID')
        wb = wanted.get(code)
        field = 'LINK' if historical else 'STECKBRIEF'
        original_url = attr.get(field)
        if not wb or not isinstance(original_url, str) or not original_url.startswith('https://'):
            continue
        url = http_url(original_url)
        kinds = {'lw', 'standgewässer', 'standgewaesser'} if layer == 0 else {'rw', 'fließgewässer', 'fliessgewässer', 'fliessgewaesser'}
        if attr.get('GEW_ART') is not None and str(attr['GEW_ART']).casefold() not in kinds:
            continue
        entry = dict(url=url, title='LfULG WRRL-Steckbrief: ' + str(attr.get('OWK_NAME', wb['name_original'])) + ' (' + wb['waterbody_id'] + ')',
                     provider='LfULG Sachsen', source_url=source_url, source_feature_id=attr.get('OBJECTID'),
                     catalogue_url=original_url,
                     source_owk_id=code, source_name=attr.get('OWK_NAME'), catalogue_verified_waterbody_id=wb['waterbody_id'],
                     evidence_basis='Exact state OWK_ID equals the EEA EU identifier after removing only DERW_/DELW_; PDF URL copied from ' + field,
                     identity_scope='exact_wfd_waterbody', identity_method='exact_catalogue_identifier')
        if historical:
            entry.update(reporting_period='2016-2021', report_type='historical_waterbody_factsheet',
                         temporal_note='The service label describes the management/reporting cycle; assessment and measurement dates must be read separately from the actual PDF.',
                         title=entry['title'] + ' [Bewirtschaftungszeitraum 2016–2021]')
        result.setdefault(wb['waterbody_id'], {})[url] = entry
    return {code: list(entries.values()) for code, entries in result.items()}


def mv_river_candidates(rows, inventory):
    wanted = {w['waterbody_id']: w for w in inventory if w['wfd_category'] == 'RW'}
    result = {}
    for row in rows:
        # Only Mecklenburg-Vorpommern IDs: other state namespaces have different rules.
        if len(row) < 11 or row[0] != 'DEMV':
            continue
        local_id = str(row[1])
        code = 'DERW_DEMV_' + local_id
        if code not in wanted:
            continue
        for column, kind, content_kind in [(8, 'waterbody_factsheet', 'html'),
                                           (9, 'measures_factsheet', 'pdf'), (10, 'waterbody_designation_review', 'pdf')]:
            cell = row[column]
            if not isinstance(cell, str):
                continue
            match = re.search(r'href\s*=\s*[\"\']([^\"\']+)[\"\']', cell, re.I)
            if not match:
                continue
            url = html.unescape(match[1])
            if up.urlsplit(url).hostname not in ('fis-wasser-mv.de', 'www.fis-wasser-mv.de'):
                continue
            entry = dict(url=url, title='LUNG MV: ' + kind + ' ' + wanted[code]['name_original'] + ' (' + code + ')',
                         provider='LUNG Mecklenburg-Vorpommern', source_url=MV_RIVER_CATALOGUE,
                         source_local_id=local_id, catalogue_verified_waterbody_id=code, report_type=kind,
                         content_kind=content_kind, identity_scope='exact_wfd_waterbody', identity_method='exact_catalogue_identifier',
                         evidence_basis='Exact catalogue Federführung DEMV and WK_K ' + local_id + '; URL copied from catalogue cell')
            result.setdefault(code, []).append(entry)
    return result


def mv_lake_candidates(rows, inventory):
    wanted = {w['waterbody_id']: w for w in inventory if w['wfd_category'] == 'LW'}
    result = {}
    for row in rows:
        if len(row) < 5:
            continue
        local_id = str(row[0])
        code = 'DELW_DEMV_' + local_id
        if code not in wanted or not isinstance(row[4], str):
            continue
        match = re.search(r'href\s*=\s*[\"\']([^\"\']+)[\"\']', row[4], re.I)
        if not match:
            continue
        url = html.unescape(match[1])
        if up.urlsplit(url).hostname != 'fis-wasser-mv.de':
            continue
        result[code] = [dict(url=url, title='LUNG MV Seensteckbrief: ' + wanted[code]['name_original'] + ' (' + code + ')',
                            provider='LUNG Mecklenburg-Vorpommern', source_url=MV_LAKE_CATALOGUE,
                            source_local_id=local_id, catalogue_verified_waterbody_id=code,
                            report_type='waterbody_factsheet', content_kind='html', identity_scope='exact_wfd_waterbody',
                            identity_method='exact_catalogue_identifier',
                            evidence_basis='Exact numeric WK_K in the official MV lake catalogue matches the EU identifier after DELW_DEMV_; URL copied from catalogue')]
    return result


def table_identifiers(text, wanted, historical=False):
    if not historical:
        return set(re.findall(r'\bDE(?:RW|LW)_[A-Za-z0-9_-]+', text)) & wanted
    found = set()
    lines = text.splitlines()
    for number, line in enumerate(lines):
        for match in re.finditer(r'\bDE_(?:RW|LW)_[A-Za-z0-9_-]+', line):
            code = match[0].replace('DE_LW_', 'DELW_', 1).replace('DE_RW_', 'DERW_', 1)
            # PDF column wraps only: continuation must consist entirely of an ID token.
            if not line[match.end():].strip():
                for continuation in lines[number + 1:number + 3]:
                    if not re.fullmatch(r'\s*[0-9][A-Za-z0-9_-]*\s*', continuation):
                        break
                    code += continuation.strip()
            if code in wanted:
                found.add(code)
    return found


def national_identifier_index(inventory):
    mapping = {}
    for wb in inventory:
        code = wb['waterbody_id']
        if code.startswith(('DELW_', 'DERW_')):
            mapping.setdefault(code.split('_', 1)[1], []).append(code)
    unique = {national: codes[0] for national, codes in mapping.items() if len(codes) == 1}
    prefixes = {national[:n] for national in unique for n in range(1, len(national) + 1)}
    return unique, prefixes


def national_table_identifiers(text, inventory, index=None):
    """M4/2015 uses untyped national IDs: join only unique complete identifiers."""
    unique, prefixes = index or national_identifier_index(inventory)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    found = set()
    for number, line in enumerate(lines):
        for match in re.finditer(r'(?<![A-Za-z0-9_-])DE[A-Z]{2}[A-Za-z0-9_-]+(?![A-Za-z0-9_-])', line):
            code = match[0]
            if code not in prefixes:
                continue
            exact = {code} if code in unique else set()
            if not line[match.end():].strip():
                for continuation in lines[number + 1:number + 3]:
                    if not re.fullmatch(r'[A-Za-z0-9_-]+', continuation):
                        break
                    candidate = code + continuation
                    if candidate not in prefixes:
                        if re.search(r'\d', continuation):
                            exact.clear()  # Possible longer out-of-scope ID, not its prefix.
                        break
                    code = candidate
                    if code in unique:
                        exact.add(code)
            # An identifier cannot be inferred from an ambiguous category or prefix.
            if len(exact) == 1:
                found.add(unique[next(iter(exact))])
    return found


def table_pdf_candidates(pdf_bytes, inventory, url, provider, report_type, title, cache_dir=None, historical=False,
                         national_identifiers=False):
    from pypdf import PdfReader
    wanted = {w['waterbody_id']: w for w in inventory}
    pdf_hash = digest(pdf_bytes)
    inventory_hash = digest(json.dumps(sorted(wanted)).encode())
    suffix = '_national' if national_identifiers else '_legacy2015' if historical else ''
    cache_path = Path(cache_dir) / (pdf_hash + '_' + inventory_hash + suffix + '.json') if cache_dir else None
    saved = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path and cache_path.exists() else {}
    if saved.get('pdf_sha256') == pdf_hash and saved.get('inventory_sha256') == inventory_hash:
        located = saved['located']
    else:
        located = {}
        national_index = national_identifier_index(inventory) if national_identifiers else None
        for page_number, page in enumerate(PdfReader(io.BytesIO(pdf_bytes)).pages, 1):
            text = page.extract_text() or ''
            matches = (national_table_identifiers(text, inventory, national_index) if national_identifiers else
                       table_identifiers(text, set(wanted), historical=historical))
            for code in matches:
                if code in wanted:
                    located.setdefault(code, []).append(page_number)
        if cache_path:
            write_json(cache_path, dict(pdf_sha256=pdf_hash, inventory_sha256=inventory_hash, located=located))
    return {code: [dict(url=url, title=title + ': ' + wanted[code]['name_original'] + ' (' + code + '), PDF-Seite(n) ' + ', '.join(map(str, pages)), provider=provider, source_url=url,
                       catalogue_verified_waterbody_id=code, catalogue_pages=pages,
                       locator='PDF-Seite(n) ' + ', '.join(map(str, pages)), report_type=report_type,
                       identity_document_downloaded=True,
                       evidence_basis=('The exact national identifier, with bounded column-wrap joins, uniquely matches the national suffix of EU code '
                                       if national_identifiers else 'Exact source identifier, normalizing only the DE_LW_/DE_RW_ namespace and PDF line wraps, matches EU code '
                                       if historical else 'Exact complete EU code ') + code + ' in this official PDF on the recorded page(s)',
                       identity_scope='exact_wfd_waterbody_in_regional_table',
                       identity_method='exact_unique_national_identifier' if national_identifiers else
                                       'exact_historical_identifier_namespace' if historical else 'exact_document_identifier')]
            for code, pages in located.items()}


def acquire_candidates(client, inventory, sources):
    candidates = {}
    def merge(incoming):
        for key, entries in incoming.items():
            candidates.setdefault(key, []).extend(entries)
    if 'bb' in sources:
        merge(bb_candidates(client.catalogue(BB_CATALOGUE, 'bb_river_catalogue.pdf', 'pdf'), inventory))
    if 'bb_lakes' in sources:
        merge(bb_candidates(client.catalogue(BB_LAKE_CATALOGUE, 'bb_lake_biodata_catalogue.pdf', 'pdf'), inventory,
                            category='LW', source_url=BB_LAKE_CATALOGUE))
        merge(bb_limnological_candidates(client.catalogue(BB_LIMNO_CATALOGUE, 'bb_limnological_catalogue.html', 'html'), inventory))
    for source, service, layers, historical in [('sn', SN_SERVICE, (0, 1), False),
                                               ('sn_historical', SN_HISTORICAL_SERVICE, (0,), True)]:
        if source not in sources:
            continue
        for layer in layers:
            # Count/IDs prevent unnoticed server truncation; geometry is unnecessary here.
            base = service + '/' + str(layer) + '/query?'
            ids_url = base + up.urlencode(dict(where='1=1', returnIdsOnly='true', f='json'))
            ids = json.loads(client.catalogue(ids_url, f'{source}_layer{layer}_ids.json', 'json')).get('objectIds', [])
            features = []
            for start in range(0, len(ids), 500):
                query = dict(objectIds=','.join(map(str, ids[start:start + 500])), outFields='*', returnGeometry='false', f='json')
                url = base + up.urlencode(query)
                batch = json.loads(client.catalogue(url, f'{source}_layer{layer}_features_{start}.json', 'json'))
                if batch.get('exceededTransferLimit'):
                    raise ValueError('Sachsen catalogue response is truncated')
                features.extend(batch.get('features', []))
            if len(features) != len(ids) or {f['attributes']['OBJECTID'] for f in features} != set(ids):
                raise ValueError('Sachsen feature IDs do not match the complete catalogue')
            merge(sn_candidates(features, inventory, layer, service + '/' + str(layer), historical=historical))
    if 'mv' in sources:
        catalogue = json.loads(client.catalogue(MV_RIVER_CATALOGUE, 'mv_river_catalogue.json', 'json'))
        rows = catalogue.get('data', [])
        if len(rows) != int(catalogue.get('recordsTotal', -1)):
            raise ValueError('Mecklenburg-Vorpommern river catalogue is truncated')
        merge(mv_river_candidates(rows, inventory))
        catalogue = json.loads(client.catalogue(MV_LAKE_CATALOGUE, 'mv_lake_catalogue.json', 'json'))
        rows = catalogue.get('data', [])
        if len(rows) != int(catalogue.get('recordsTotal', -1)):
            raise ValueError('Mecklenburg-Vorpommern lake catalogue is truncated')
        merge(mv_lake_candidates(rows, inventory))
    for key, url, filename, provider, kind, title in [
            ('th', TH_ASSESSMENTS, 'th_owk_assessments.pdf', 'TLUBN / Aktion Fluss', 'regional_assessment_table',
             'Thüringen: Liste der Oberflächenwasserkörper mit Zustandsbewertung und Zielerreichung'),
            ('fgg_drinking', FGG_DRINKING_WATER, 'fgg_drinking_water_A1-1.pdf', 'FGG Elbe / LVwA Sachsen-Anhalt',
             'drinking_water_protection_seed', 'FGG Elbe 2021 Anhang A1-1: Wasserkörper für Trinkwasserentnahme nach Artikel 7'),
            ('fgg_inventory', FGG_INVENTORY, 'fgg_owk_inventory_A5-2.pdf', 'FGG Elbe', 'regional_waterbody_inventory',
             'FGG Elbe 2021 Anhang A5-2: Ziele und Fristen für Oberflächenwasserkörper')]:
        if key in sources:
            merge(table_pdf_candidates(client.catalogue(url, filename, 'pdf'), inventory, url, provider, kind, title,
                                       cache_dir=client.output / 'table_indexes'))
    if 'fgg_historical' in sources:
        # The CMS signs attachment URLs. Re-read its actual link; never manufacture a hash.
        page = client.catalogue(FGG_2015_PAGE, 'fgg_2015_index.html', 'html', refresh=True).decode('utf-8', 'replace')
        links = {up.urljoin(FGG_2015_PAGE, html.unescape(href))
                 for href in re.findall(r'href=[\"\']([^\"\']+)[\"\']', page, re.I) if 'A_5-2_OWK.pdf' in href}
        if len(links) != 1:
            raise ValueError('Expected one explicit 2015 A5-2 attachment link on the official FGG page')
        url = next(iter(links))
        lake_inventory = [w for w in inventory if w['wfd_category'] == 'LW']
        historical = table_pdf_candidates(client.catalogue(url, 'fgg_owk_inventory_2015.pdf', 'pdf'), lake_inventory,
                                         url, 'FGG Elbe', 'historical_regional_assessment_table',
                                         'FGG Elbe 2015 Anhang A5-2: Belastungen, ökologischer Zustand und Bewirtschaftungsziele',
                                         cache_dir=client.output / 'table_indexes', historical=True)
        for entries in historical.values():
            for entry in entries:
                entry.update(reporting_period='2016-2021', source_landing_url=FGG_2015_PAGE)
        merge(historical)
    if 'fgg_measures' in sources:
        for url, filename, period, national_ids in [
                (FGG_MEASURES_2021, 'fgg_measures_2021_M5.pdf', '2022-2027', False),
                (FGG_MEASURES_2015, 'fgg_measures_2015_M4.pdf', '2016-2021', True)]:
            mapped = table_pdf_candidates(client.catalogue(url, filename, 'pdf'), inventory, url, 'FGG Elbe',
                                          'waterbody_measures_programme', 'FGG Elbe: Maßnahmenprogramm ' + period,
                                          cache_dir=client.output / 'table_indexes', national_identifiers=national_ids)
            for entries in mapped.values():
                for entry in entries:
                    entry.update(reporting_period=period, evidence_scope='A measure programme is not evidence that a measure was implemented; verify status and dates from the source.')
                    if national_ids:
                        entry['context_pages'] = [3]  # Implementation-status legend; not an ID match.
            merge(mapped)
    write_json(client.output / 'candidate_links.json', candidates)
    return candidates


def accepted_record(candidate, check):
    return dict(candidate, url=check.get('final_url', candidate['url']), final_url=check.get('final_url', candidate['url']),
                reason='Exact official catalogue mapping and successful ' + candidate.get('content_kind', 'pdf').upper() + ' response check',
                evidence=candidate['evidence_basis'], validation_basis=check['validation_basis'],
                checked_at=check['checked_at'], http_status=check['http_status'], mime=check['content_type'],
                http=check, original_bytes_downloaded=(check.get('original_bytes_downloaded', False)
                                                       or candidate.get('identity_document_downloaded', False)))


def validate_waterbody(client, wb, candidates, refresh=False):
    destination = client.output / 'by_waterbody' / (wb['waterbody_id'] + '.json')
    candidate_hash = digest(json.dumps(candidates, sort_keys=True).encode())
    if destination.exists() and not refresh:
        saved = json.loads(destination.read_text(encoding='utf-8'))
        if saved.get('candidate_hash') == candidate_hash:
            return saved
    accepted, rejected = [], []
    for candidate in candidates:
        check = (client.validate_html(candidate['url'], candidate['source_local_id'], refresh=refresh)
                 if candidate.get('content_kind') == 'html' else client.validate_pdf(candidate['url'], refresh=refresh))
        if not check.get('validation_ok') and candidate.get('fallback_url'):
            rejected.append(dict(candidate, reason='migrated_host_validation_failed', http=check))
            check = client.validate_pdf(candidate['fallback_url'], refresh=refresh)
        if check.get('validation_ok'):
            accepted.append(accepted_record(candidate, check))
        else:
            rejected.append(dict(candidate, reason=check.get('error', 'invalid_pdf_response'), http=check))
    accepted = list({x['url']: x for x in accepted}.values())
    result = dict(waterbody_id=wb['waterbody_id'], name=wb['name_original'],
                  waterbody_type=wb['waterbody_type'], reporting_state=wb['reporting_state'],
                  status='validated' if accepted else 'no_verified_official_seed', candidate_hash=candidate_hash,
                  accepted=accepted, rejected=rejected, human_review=[], seed_urls=[a['url'] for a in accepted], checked_at=utc())
    write_json(destination, result)
    return result


def interleave_hosts(selected, candidates):
    """Keep two workers useful without lifting any per-host rate limit."""
    buckets = {}
    for wb in selected:
        host = up.urlsplit(candidates[wb['waterbody_id']][0]['url']).hostname
        buckets.setdefault(host, deque()).append(wb)
    result = []
    while buckets:
        for host in list(buckets):
            result.append(buckets[host].popleft())
            if not buckets[host]:
                del buckets[host]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    valid_sources = {'bb', 'bb_lakes', 'sn', 'sn_historical', 'mv', 'th', 'fgg_drinking', 'fgg_inventory', 'fgg_historical', 'fgg_measures'}
    parser.add_argument('--sources', default='bb,bb_lakes,sn,sn_historical,mv,th,fgg_drinking,fgg_inventory,fgg_historical,fgg_measures')
    parser.add_argument('--workers', type=int, choices=(1, 2), default=2)
    parser.add_argument('--interval', type=float, default=1)
    parser.add_argument('--limit', type=int, default=0, help='Optional bounded validation sample; zero validates all mapped bodies')
    parser.add_argument('--catalogues-only', action='store_true')
    parser.add_argument('--refresh-validation', action='store_true')
    args = parser.parse_args(argv)
    sources = set(args.sources.split(','))
    if sources - valid_sources:
        parser.error('Unknown sources: ' + ', '.join(sorted(sources - valid_sources)))
    inventory = json.loads(args.inventory.read_text(encoding='utf-8-sig'))
    client = Client(args.output, interval=args.interval)
    candidates = acquire_candidates(client, inventory, sources)
    print(json.dumps(dict(catalogue_mapped_waterbodies=len(candidates), candidates=sum(map(len, candidates.values())))), flush=True)
    if args.catalogues_only:
        return 0
    selected = [w for w in inventory if w['waterbody_id'] in candidates]
    if args.limit:
        selected = selected[:args.limit]
    selected = interleave_hosts(selected, candidates)
    results = []
    interrupted = False
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(validate_waterbody, client, wb, candidates[wb['waterbody_id']], args.refresh_validation) for wb in selected]
        try:
            for future in as_completed(futures):
                results.append(future.result())
                if len(results) % 25 == 0 or len(results) == len(selected):
                    print(f'Validated {len(results)}/{len(selected)} waterbodies; accepted links {sum(len(r["accepted"]) for r in results)}', flush=True)
                    write_json(args.output / 'official_seed_manifest.json', sorted(results, key=lambda r: r['waterbody_id']))
        except KeyboardInterrupt:
            interrupted = True
            for future in futures:
                future.cancel()
            print('Interrupted: pending checks cancelled; saving completed checks after active HTTP requests finish.', flush=True)
    if interrupted:
        results = [future.result() for future in futures if not future.cancelled() and future.exception() is None]
    write_json(args.output / 'official_seed_manifest.json', sorted(results, key=lambda r: r['waterbody_id']))
    summary = dict(checked_at=utc(), inventory_waterbodies=len(inventory), catalogue_mapped=len(candidates),
                   interrupted=interrupted,
                   checked_waterbodies=len(results), accepted_waterbodies=sum(bool(r['accepted']) for r in results),
                   accepted_links=sum(len(r['accepted']) for r in results),
                   unique_accepted_urls=len({a['url'] for r in results for a in r['accepted']}),
                   rejected_links=sum(len(r['rejected']) for r in results),
                   accepted_by_provider=dict(Counter(a['provider'] for r in results for a in r['accepted'])),
                   accepted_by_state_and_type=dict(Counter(r['reporting_state'] + '/' + r['waterbody_type'] for r in results if r['accepted'])),
                   validation='PDF: HEAD with PDF MIME, bounded GET signature fallback; HTML: exact ID and factsheet content',
                   sources=sorted(set(args.sources.split(','))))
    write_json(args.output / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 130 if interrupted else 0


if __name__ == '__main__':
    raise SystemExit(main())
