"""Reproducible, reviewed seed configuration for revision 1.4; no credentials."""
import copy
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
HYDREG='https://lhw.sachsen-anhalt.de/fileadmin/Bibliothek/Politik_und_Verwaltung/Landesbetriebe/LHW/neu_PDF/5.0_GLD/Dokumente_GLD/Wasserhaushalt_Bio_Gew-Struktur/Endbericht_HYDREG_2010.pdf'
OHRE='https://lhw.sachsen-anhalt.de/fileadmin/Bibliothek/Politik_und_Verwaltung/Landesbetriebe/LHW/DownloadBereich/Gew-bericht_OW_2005_2008/MEL03_text_web.pdf'
UFZ='https://www.ufz.de/index.php?de=40943'
TOR='https://www.ufz.de/index.php?de=39919'
TOR_GOALS='https://www.ufz.de/index.php?de=39920'
LAU='https://lau.sachsen-anhalt.de/fachthemen/naturschutz/schutzgebiete-nach-landesrecht/landschaftsschutzgebiet-lsg/lsg35'
HYDREG_NAMES={'Bergwitzsee','Kiessee Prettin','Niegripper See','Schollener See','Barleber See I','Barleber See II',
 'Arendsee','Alte Elbe Sandkrug','Kiessee Barby','Geiseltalsee','Wallendorfer See','Raßnitzer See','Süßer See',
 'Tagebausee Luckenau','Hufeisensee','Talsperre Wendefurth','Vorsperre Rappbode','Vorsperre Hassel',
 'Königsauer See','Concordiasee','Muldestausee','Gröberner See'}


def seed(wb,url,label):
    if url not in wb['seed_urls']:
        wb['seed_urls'].append(url)
    wb.setdefault('seed_titles',{})[url]=label
    wb.setdefault('seed_evidence',{})[url]=dict(checked='2026-09-23',kind='publisher_metadata_or_document_name_listing',
                                             purpose='Rechercheeinstieg; fachliche Bewertung nach Abruf erforderlich')


def main():
    path=ROOT/'inputs/sites_blablador.json'
    cfg=json.loads(path.read_text(encoding='utf-8'))
    for wb in cfg['waterbodies']:
        wb['retrieval_input_revision']='1.4'
        wb['preferred_domains']=list(dict.fromkeys(['lhw.sachsen-anhalt.de','lau.sachsen-anhalt.de']+wb.get('preferred_domains',[])))
        wb.setdefault('english_terms',['limnology','sediment'])
        name=wb['name']
        if wb.get('query_name',name) in HYDREG_NAMES:
            seed(wb,HYDREG,'HYDREG 2010: hydrologisches Regime der Oberflächenwasserkörper in Sachsen-Anhalt; Gewässerliste mit '+wb.get('query_name',name))
        if name in {'Barleber See I','Barleber See II'}:
            seed(wb,OHRE,'Gewässerbericht 2005–2008, Betrachtungsraum Ohre: Barleber See I und Barleber See II')
            wb['identifiers']=list(dict.fromkeys(wb['identifiers']+['MEL03OW21-00' if name.endswith(' I') else 'MEL03OW22-00']))
            wb['identifier_provenance']=dict(url=OHRE,period='2005–2008',note='Historische Kennung aus Bericht; keine Behauptung unveränderter aktueller Kennung')
            note=' See I und See II getrennt bewerten; Sammelberichte können beide enthalten.'
            if note not in wb['context']: wb['context']+=note
        if name=='Arendsee':
            wb['aliases']=list(dict.fromkeys(wb['aliases']+['Lake Arendsee']))
            wb['ambiguous_place_name']=True
            wb['preferred_domains']=['ufz.de','lhw.sachsen-anhalt.de','lau.sachsen-anhalt.de']
            seed(wb,UFZ,'UFZ-Publikationsliste: Phosphorus input by nordic geese to the eutrophic Lake Arendsee, Germany')
            note=' Gemeint ist der See. Stadtgeschichte, Kommunalwahlen und Hauptsatzung sind allein kein Gewässerbeleg.'
            if note not in wb['context']: wb['context']+=note
        if name=='Bergwitzsee':
            seed(wb,LAU,'LAU: Landschaftsschutzgebiet Dübener Heide; Bergwitzsee, Lebensräume und Nutzung')
        if name=='Rappbodetalsperre/Vorsperre Hassel':
            wb['aliases']=list(dict.fromkeys(wb['aliases']+['Hassel-Vorsperre','Vorsperren Hassel']))
            wb['preferred_domains']=['ufz.de','talsperrenbetrieb-lsa.de','lhw.sachsen-anhalt.de']
            seed(wb,TOR,'UFZ Talsperrenobservatorium: Monitoring an den Vorsperren Hassel und Rappbode')
            seed(wb,TOR_GOALS,'UFZ Wissenschaftliche Ziele: Messprogramme an der Hassel-Vorsperre')
        if name=='Eckertalsperre':
            seed(wb,'https://www.harzwasserwerke.de/ueber-uns/anlagen/talsperren/eckertalsperre/','Harzwasserwerke: Eckertalsperre')
            wb['preferred_domains']=['harzwasserwerke.de','lhw.sachsen-anhalt.de','ufz.de']
    cfg['search'].update(strategy='focused',adaptive_max_queries=12,plateau_queries=4,count=8)
    cfg['retrieval']=dict(enabled=True,engines=['google','default','brave'],max_health_calls_per_run=6,
                          health_ttl_seconds=3600,cooldown_seconds=1800,crossref_enabled=True,max_pages=2,bibliography=True)
    cfg['preflight'].update(rank_before_download=True,exploration_per_waterbody=2,max_calls_per_run=66)
    cfg['review'].update(mode='api',base_url='https://api.blablador.fz-juelich.de/v1',model='alias-large',
                        api_key_env='BLABLADOR_KEY',max_calls_per_run=33,max_input_chars=16000,
                        require_topic_evidence=True,required=True)
    cfg['limits'].update(max_searches_per_run=33,max_fetches_per_run=66,timeout_seconds=25,
                         parse_timeout_seconds=75,max_seconds_per_run=900,max_pdf_pages=180,max_ocr_pages=8)
    # OCR runs only on sparse pages; absence of Tesseract remains visible in parse warnings.
    cfg['ocr']=dict(enabled=True,language='deu+eng')
    path.write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    pilot=copy.deepcopy(cfg)
    selected={'Arendsee','Bergwitzsee','Barleber See I','Barleber See II','Rappbodetalsperre/Vorsperre Hassel'}
    pilot['waterbodies']=[w for w in pilot['waterbodies'] if w['name'] in selected]
    pilot['limits'].update(max_searches_per_run=10,max_fetches_per_run=15,max_tasks_per_run=100,max_seconds_per_run=420)
    pilot['preflight']['max_calls_per_run']=25
    pilot['review']['max_calls_per_run']=15
    (ROOT/'inputs/pilot_5_retrieval_v1_4.json').write_text(json.dumps(pilot,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('33 Gewässer konfiguriert; Pilot mit 5 Gewässern geschrieben.')


if __name__=='__main__':
    main()
