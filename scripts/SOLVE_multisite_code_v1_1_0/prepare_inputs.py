#!/usr/bin/env python3
"""Build reproducible multi-site inputs from the supplied SOLVE register JSON.

Register statements are research context, not newly verified scientific facts.
The collector itself accepts plain JSON and does not depend on this workbook.
"""
import json
import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REGIONS = {'B':'Berlin', 'BB':'Brandenburg', 'ST':'Sachsen-Anhalt',
           'SN':'Sachsen', 'MV':'Mecklenburg-Vorpommern', 'TH':'Thüringen',
           'NI':'Niedersachsen', 'SH':'Schleswig-Holstein'}
DISTRICTS = {'BRB':'Brandenburg an der Havel', 'HVL':'Havelland',
 'OHV':'Oberhavel', 'P':'Potsdam', 'PM':'Potsdam-Mittelmark', 'UM':'Uckermark',
 'SPN':'Spree-Neiße', 'LDS':'Dahme-Spreewald', 'LOS':'Oder-Spree', 'MOL':'Märkisch-Oderland',
 'BAR':'Barnim', 'LG':'Lüneburg', 'BÖ':'Börde', 'DAN':'Lüchow-Dannenberg',
 'HZ':'Harz', 'JL':'Jerichower Land', 'MD':'Magdeburg', 'MSH':'Mansfeld-Südharz',
 'SDL':'Stendal', 'SLK':'Salzlandkreis', 'ABI':'Anhalt-Bitterfeld', 'DE':'Dessau-Roßlau',
 'WB':'Wittenberg', 'BLK':'Burgenlandkreis', 'BZ':'Bautzen', 'PIR':'Sächsische Schweiz-Osterzgebirge',
 'DD':'Dresden', 'MEI':'Meißen', 'FG':'Mittelsachsen', 'EE':'Elbe-Elster',
 'TDO':'Nordsachsen', 'LLand':'Leipziger Land', 'L':'Leipzig', 'ERZ':'Erzgebirgskreis',
 'LWL':'Ludwigslust', 'MSE':'Mecklenburgische Seenplatte', 'SÖM':'Sömmerda',
 'SOK':'Saale-Orla-Kreis', 'WAK':'Wartburgkreis', 'PLÖ':'Plön'}
DOMAINS = {'B':['berlin.de','parlament-berlin.de'], 'BB':['brandenburg.de','b-tu.de'],
 'ST':['sachsen-anhalt.de','lhw.sachsen-anhalt.de'], 'SN':['sachsen.de','lmbv.de'],
 'MV':['lung.mv-regierung.de','stalu-mv.de'], 'TH':['thueringen.de'],
 'NI':['nlwkn.niedersachsen.de'], 'SH':['schleswig-holstein.de']}
TOPIC_WORDS = [
 ['Gewässerkennung','Gewässersteckbrief','historischer Name'],
 ['Morphometrie','Seebecken','Tiefe'], ['Wasserstand','Grundwasser','Zuleitung'],
 ['Phosphor','Trophie','Eutrophierung','water quality'], ['Makrophyten','Phytoplankton','Fische'],
 ['Sediment','Schadstoff','Schwermetall'], ['Einzugsgebiet','Landnutzung','Entwässerung'],
 ['Abwasser','Kläranlage','Industrie'], ['Wasserbau','Kanal','Wehr','Ausbaggerung'],
 ['Fischerei','Badenutzung','Schifffahrt'], ['Geschichte','historisch','Chronik','1990'],
 ['Sanierung','Restaurierung','Biomanipulation'], ['Nährstoffreduktion','Wiedervernässung'],
 ['Erfolgskontrolle','Vorher-Nachher','Maßnahmen'], ['WRRL','Bewirtschaftungsplan','Zustand'],
 ['FFH','Managementplan','Naturschutzgebiet'], ['Monitoring','Messreihe','Langzeitdaten'],
 ['Paläolimnologie','Sedimentkern','palaeolimnology'], ['Dürre','Hochwasser','Fischsterben']]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='sites_baseline.json')
    parser.add_argument('--refined',action='store_true')
    args=parser.parse_args()
    register = json.loads((ROOT/'source_register/register.json').read_text())
    sources = {r[0]:r for r in register['Quellenregister'][1:]}
    md = (ROOT/'baseline/SOLVE_SCHLAGWORTLISTE_SEEN.md').read_text()
    topic_names = re.findall(r'^\| \d+ \| ([^|]+) \|', md, re.M)
    topics = [dict(name=n.strip(), keywords=k) for n,k in zip(topic_names,TOPIC_WORDS)]
    assert len(topics)==19
    bodies = []
    for r in register['Gewässerregister'][1:]:
        if 'Arendsee' in r[1]:
            continue
        region_code = r[3].split('?')[0].strip()
        region = REGIONS.get(region_code, r[3])
        original = r[1]
        parts = re.split(r'[-_]', original, maxsplit=2)
        district = DISTRICTS.get(parts[1], '') if len(parts)>2 else ''
        label = re.sub(r'\s*\((?:Kandidat|Suchhypothese)\)', '', r[2]).strip()
        name = label.split(' / ')[0]
        tentative = r[4]=='K' or 'vorbehalt' in r[5].casefold() or 'Kandidat' in r[2] or 'Suchhypothese' in r[2]
        original_name = re.sub(r'^(?:B-|(?:BB|ST|SN|MV|TH|NI|SH)[-_][^-]+-)', '', original)
        aliases = []
        if original_name != name: aliases.append(original_name)
        aliases.extend(x.strip() for x in label.split(' / ')[1:] if x.strip())
        keywords = [x for x in [district, region] if x]
        ids = [s.strip() for s in (r[6] or '').split(';') if s.strip()]
        seed_urls = [sources[s][8] for s in ids if s in sources]
        bodies.append(dict(id=f'site_{int(r[0]):03d}', register_number=r[0],
          original_label=original, name=name, aliases=list(dict.fromkeys(aliases)),
          region=region, keywords=keywords, identifiers=[], exclude_keywords=[],
          seed_urls=seed_urls, context=f'Original register entry: {original}. '
          f'Identity caveat: {r[8]}. Suggested route: {r[9]}. '
          f'Register historical search lead (unverified here): {r[7]}. {r[11]}',
          identity_requires_confirmation=tentative, geography_terms=keywords,
          preferred_domains=DOMAINS.get(region_code,[]),
          research_questions=['Gewässergüte und historische Entwicklung','Sanierung und Maßnahmen',
                              'Messreihen, Sedimente und Naturschutz'],
          seed_provenance='Supplied register, not new discoveries in this run',
          suggested_query=r[10], register_priority=r[4]))
        if args.refined and name.split()[0] in {'Elbe','Havel','Mulde'} and len(name.split())>1:
            bodies[-1]['identity_groups']=[[name.split()[0], ' '.join(name.split()[1:])]]
    shared = ('Find and archive sources for each named waterbody or river segment. '
      'Prioritize dated historical context, especially before 2000, including recent retrospective reports. '
      'Publication year is not event year. Do not infer a site identity from a similar name. '
      'Keep river reaches, bays, ponds and area sites distinct. Read sources transiently for relevance '
      'and link discovery only; retain originals and provenance, never extracted fulltexts or scientific facts. '
      'No writes to the SOLVE databases. All 19 research themes apply.')
    config = dict(context=shared, waterbodies=bodies, topics=topics,
      search=dict(provider='external',count=8), review=dict(mode='rules'),
      storage=dict(persist_extracted_text=False),
      limits=dict(max_fetches_per_run=0,max_searches_per_run=len(bodies),
       max_tasks_per_run=600,max_queries_per_waterbody=160,max_seconds_per_run=180,
       timeout_seconds=8,delay_seconds=0.5))
    out=ROOT/'inputs'; out.mkdir(exist_ok=True)
    (out/args.output).write_text(json.dumps(config,ensure_ascii=False,indent=2))
    (out/'site_contexts.json').write_text(json.dumps(bodies,ensure_ascii=False,indent=2))
    print(json.dumps(dict(sites=len(bodies),identity_caveats=sum(b['identity_requires_confirmation'] for b in bodies),topics=len(topics))))

if __name__=='__main__': main()
