"""Deterministic, source-backed waterbody assignments; no entity merging.

Polygons describe register areas. River-network lines provide separate spatial
evidence. Distance alone never produces an automatic assignment.
"""
import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from common import ROOT, digest, uid, waterbody_id, write_json

RULE_VERSION = 'waterbody-matching-v1'
POLICY = dict(version=RULE_VERSION, near_distance_m=50, fuzzy_name_threshold=0.9,
              spatial_crs='EPSG:25833', spatial_only='candidate',
              unmatched_waterbody_code='requires_review', historical_source='requires_review')
CODE_FIELDS = {'owk_st': 'owk', 'owk_nr': 'owk', 'owk_id': 'owk',
               'eu_cd_wb': 'eu', 'eu_cd_lw': 'eu',
               'gkz': 'gkz', 'gewkz': 'gkz', 'lawa': 'gkz',
               'Oberflächenwasserkörper Code': 'owk'}
NAME_FIELDS = ('gewaesser', 'hauptgew', 'owk_name', 's_name',
               'Name des Fließgewässers', 'Name des Gewässers', 'Name des Sees')
SCHEMES = {'owk': 'owk', 'owk nummer': 'owk', 'owk code': 'owk',
           'eu': 'eu', 'eu code': 'eu', 'eu cd wb': 'eu',
           'gkz': 'gkz', 'gewaesserkennzahl': 'gkz', 'gewaesserkennziffer': 'gkz',
           'lawa': 'gkz'}


def text(value):
    if value is None:
        return ''
    result = str(value).strip()
    return '' if result.casefold() in {'', 'nan', 'none', 'null', '-', '---'} else result


def normalize_name(value):
    value = unicodedata.normalize('NFKC', text(value)).casefold()
    for a, b in [('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue')]:
        value = value.replace(a, b)
    return ' '.join(re.findall(r'\w+', value))


def normalize_code(value, scheme):
    value = text(value).upper()
    # The network contains dotted GKZ and its undotted GEWKZ counterpart.
    if scheme == 'gkz':
        return re.sub(r'[.\s]', '', value)
    return value


def reference_files(reference, network=None):
    paths = []
    for path in (reference, network):
        if path:
            path = Path(path)
            paths.extend(sorted(p for p in path.parent.glob(path.stem+'.*') if p.is_file()))
    return paths


def default_network(reference):
    candidate = Path(reference).parent.parent/'Gewässernetz/Gewässernetz.shp'
    return candidate if candidate.exists() else None


def identifiers_from_record(record):
    return [dict(scheme=scheme, value=text(record[field]), field=field)
            for field, scheme in CODE_FIELDS.items() if text(record.get(field))]


def names_from_record(record):
    return [text(record[field]) for field in NAME_FIELDS if text(record.get(field))]


class WaterbodyMatcher:
    def __init__(self, reference, network=None):
        import geopandas as gpd
        self.reference = Path(reference)
        self.network = Path(network) if network else None
        self.files = reference_files(self.reference, self.network)
        self.fingerprint = uid(RULE_VERSION, POLICY, [(p.name, digest(p)) for p in self.files])
        self.codes = defaultdict(lambda: defaultdict(list))
        self.names = defaultdict(lambda: defaultdict(list))
        self.fuzzy_cache = {}
        self.areas = {}
        self.rivers = {}
        self.targets = set()
        register = gpd.read_file(self.reference)
        projected = register.to_crs(25833) if register.crs else None
        for ordinal, (_, row) in enumerate(register.iterrows(), 1):
            code = text(row.get('owk_st'))
            if not code:
                continue
            self.targets.add(code)
            for field, scheme in [('owk_st', 'owk'), ('eu_cd_wb', 'eu')]:
                self.add_code(scheme, row.get(field), code, self.reference.name, ordinal, field)
            for field in ('hauptgew', 'owk_name'):
                self.add_name(row.get(field), code, self.reference.name, ordinal, field)
            if projected is not None:
                geom = projected.geometry.iloc[ordinal-1]
                if geom is not None and not geom.is_empty and geom.is_valid:
                    self.areas.setdefault(code, []).append(geom)
        if self.network:
            network_df = gpd.read_file(self.network)
            network_projected = network_df.to_crs(25833) if network_df.crs else None
            for ordinal, row in enumerate(network_df.itertuples(index=False), 1):
                code = text(getattr(row, 'owk_st', None))
                if code not in self.targets:
                    continue
                # Only explicit network -> register codes establish this crosswalk.
                for field in ('lawa', 'gkz', 'gewkz'):
                    self.add_code('gkz', getattr(row, field, None), code, self.network.name, ordinal, field)
                self.add_name(getattr(row, 'gewaesser', None), code, self.network.name, ordinal, 'gewaesser')
                if network_projected is not None:
                    geom = network_projected.geometry.iloc[ordinal-1]
                    if geom is not None and not geom.is_empty and geom.is_valid:
                        self.rivers.setdefault(code, []).append(geom)
        # Compact repeated crosswalk rows without losing their row provenance.
        from shapely import union_all
        self.areas = {k: union_all(v) for k, v in self.areas.items()}
        self.rivers = {k: union_all(v) for k, v in self.rivers.items()}
        from shapely.strtree import STRtree
        self.area_codes = sorted(self.areas)
        self.river_codes = sorted(self.rivers)
        self.area_tree = STRtree([self.areas[c] for c in self.area_codes])
        self.river_tree = STRtree([self.rivers[c] for c in self.river_codes])

    def add_code(self, scheme, value, target, source, row, field):
        value = normalize_code(value, scheme)
        if value:
            self.codes[(scheme, value)][target].append(dict(source=source, row=row, field=field))

    def add_name(self, value, target, source, row, field):
        name = normalize_name(value)
        if name:
            self.names[name][target].append(dict(source=source, row=row, field=field, name=text(value)))

    @staticmethod
    def provenance(rows):
        grouped = {}
        for row in rows:
            key = (row['source'], row['field'], row.get('name'))
            grouped.setdefault(key, []).append(row['row'])
        return [dict(source=k[0], field=k[1], name=k[2], rows=sorted(set(v))) for k, v in sorted(grouped.items())]

    def similar_names(self, normalized):
        if normalized not in self.fuzzy_cache:
            hits = []
            threshold = POLICY['fuzzy_name_threshold']
            for other in self.names:
                compare = SequenceMatcher(None, normalized, other)
                if compare.real_quick_ratio() < threshold or compare.quick_ratio() < threshold:
                    continue
                similarity = compare.ratio()
                if similarity >= threshold:
                    hits.append((other, similarity))
            self.fuzzy_cache[normalized] = hits
        return self.fuzzy_cache[normalized]

    def match(self, names=(), identifiers=(), geometry=None, crs=None, historical=False):
        from shapely.ops import transform
        from pyproj import Transformer
        evidence = defaultdict(list)
        code_sets, name_sets = [], []
        unresolved_codes, warnings = [], []
        for identifier in identifiers or []:
            if identifier.get('valid_time'):
                historical = True
            value = text(identifier.get('value'))
            if not value:
                continue
            raw_scheme = normalize_name(identifier.get('scheme'))
            scheme = SCHEMES.get(raw_scheme)
            # An unlabelled OWK/EU code can still match exactly; never infer GKZ.
            schemes = [scheme] if scheme else (['owk', 'eu'] if not raw_scheme else [])
            hits = set()
            for s in schemes:
                for code, rows in self.codes.get((s, normalize_code(value, s)), {}).items():
                    hits.add(code)
                    evidence[code].append(dict(kind='code', scheme=s, value=value,
                                               references=self.provenance(rows)))
            if hits:
                code_sets.append(hits)
            elif scheme in {'owk', 'eu', 'gkz'} or (not raw_scheme and re.match(r'^(DE|[A-Z]+\d*OW)', value)):
                unresolved_codes.append(identifier)
        for name in names or []:
            normalized = normalize_name(name)
            hits = self.names.get(normalized, {})
            if hits:
                name_sets.append(set(hits))
                for code, rows in hits.items():
                    evidence[code].append(dict(kind='name', value=name, normalized=normalized,
                                               references=self.provenance(rows)))
        area_hits, river_hits = set(), set()
        geo = None
        if geometry is not None:
            if not crs:
                warnings.append('missing_crs')
            elif geometry.is_empty or not geometry.is_valid:
                warnings.append('invalid_or_empty_geometry')
            else:
                try:
                    geo = transform(Transformer.from_crs(crs, 25833, always_xy=True).transform, geometry)
                    if geo.is_empty or not geo.is_valid:
                        warnings.append('invalid_transformed_geometry')
                        geo = None
                except (ValueError, RuntimeError):
                    warnings.append('crs_transformation_failed')
        if geo is not None:
            for i in self.area_tree.query(geo, predicate='intersects'):
                code = self.area_codes[int(i)]
                area_hits.add(code)
                evidence[code].append(dict(kind='register_area_intersection', spatial_crs='EPSG:25833'))
            for i in self.river_tree.query(geo.buffer(POLICY['near_distance_m']), predicate='intersects'):
                code = self.river_codes[int(i)]
                river_hits.add(code)
                evidence[code].append(dict(kind='river_proximity', distance_m=round(geo.distance(self.rivers[code]), 3),
                                           threshold_m=POLICY['near_distance_m'], spatial_crs='EPSG:25833'))
        strong_sets = code_sets + name_sets
        allowed = set.intersection(*strong_sets) if strong_sets else set()
        conflict = bool(strong_sets and not allowed)
        spatial = river_hits or area_hits
        if geo is not None and allowed:
            intersection = allowed & spatial
            if intersection:
                allowed = intersection
            else:
                conflict = True
        # Fuzzy names are suggestions only and cannot resolve an exact conflict.
        if not evidence:
            for name in names or []:
                normalized = normalize_name(name)
                if len(normalized) < 5:
                    continue
                for other, similarity in self.similar_names(normalized):
                    for code, rows in self.names[other].items():
                        evidence[code].append(dict(kind='similar_name', value=name, matched_name=other,
                            similarity=round(similarity, 4), references=self.provenance(rows)))
        automatic = (len(allowed) == 1 and not conflict and not unresolved_codes and not historical and not warnings)
        if conflict:
            warnings.append('contradictory_evidence')
        if unresolved_codes:
            warnings.append('unmatched_waterbody_code')
        if historical:
            warnings.append('historical_source_requires_review')
        selected = set(evidence) if conflict else (allowed or set(evidence))
        links = []
        for code in sorted(selected):
            kinds = sorted({e['kind'] for e in evidence[code]})
            links.append(dict(target_entity_id=waterbody_id(code), target_code=code,
                assignment_status='rule_assigned' if automatic else 'candidate',
                status='source_reference_unverified', method='+'.join(kinds),
                evidence=evidence[code], warnings=warnings,
                unmatched_identifiers=unresolved_codes, rule_version=RULE_VERSION,
                reference_fingerprint=self.fingerprint,
                relation='WATERBODY_ASSOCIATION', identity_merge=False))
        return dict(status='rule_assigned' if automatic else 'needs_review' if links else 'unresolved',
                    links=links, warnings=warnings, unmatched_identifiers=unresolved_codes)

    def match_record(self, record, geometry=None, crs=None, historical=False):
        return self.match(names_from_record(record), identifiers_from_record(record), geometry, crs, historical)


def main():
    """Profile source feature associations without opening a database."""
    import geopandas as gpd
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, default=ROOT/'Sachsen_Anhalt')
    ap.add_argument('--output', type=Path, default=ROOT/'output/waterbody_matching_audit.json')
    args = ap.parse_args()
    reference = args.source/'Oberflächenwasserkörper/Oberflächenwasserkörper.shp'
    matcher = WaterbodyMatcher(reference, default_network(reference))
    report = dict(policy=POLICY, reference_fingerprint=matcher.fingerprint, layers={})
    for name in ['Wasser Einzelproben', 'Fliessgewässer', 'Pegel Wasserstand-Durchfluss (geprüfte Daten)',
                 'Punktuelle Maßnahmen', 'Lineare Maßnahmen']:
        path = args.source/name/(name+'.shp')
        if not path.exists():
            continue
        df = gpd.read_file(path)
        counts = Counter()
        details = []
        for n, (_, row) in enumerate(df.iterrows(), 1):
            raw = json.loads(row.drop(labels='geometry').to_json())
            result = matcher.match_record(raw, row.geometry, df.crs)
            counts[result['status']] += 1
            details.append(dict(row=n, source=raw, result=result))
        report['layers'][name] = dict(counts=dict(counts), rows=details)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, report)
    print(json.dumps({k: v['counts'] for k, v in report['layers'].items()}))


if __name__ == '__main__':
    main()
