"""Build the German, source-traceable Bergwitzsee report. No database access.

Run with the bundled primary-runtime Python (ReportLab, pdfplumber, pypdf).
REPORT_STATUS.json is read on every build; never infer live Desktop completion.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'outputs/bergwitzsee_ergaenzung_20261102'
FINAL = ROOT / 'output/pdf/Bergwitzsee_Knowledgegraph_Recherche_und_Ergebnisstand_2026-11-02.pdf'
QA = ROOT / 'tmp/pdfs/bergwitzsee_report'
NAVY = colors.HexColor('#193F52')
TEAL = colors.HexColor('#147B80')
INK = colors.HexColor('#243642')
MUTED = colors.HexColor('#526873')
PALE = colors.HexColor('#F0F6F6')
LINE = colors.HexColor('#D2DFE3')
AMBER = colors.HexColor('#9A5B13')
W = A4[0] - 92


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def safe(text):
    return escape(str(text)).replace('\u2013', '-').replace('\u2014', '-').replace('\u2011', '-')


def register_fonts():
    for name, filename in [('Calibri', 'calibri.ttf'), ('Calibri-Bold', 'calibrib.ttf'),
                           ('Calibri-Italic', 'calibrii.ttf')]:
        pdfmetrics.registerFont(TTFont(name, str(Path('C:/Windows/Fonts') / filename)))
    pdfmetrics.registerFontFamily('Calibri', normal='Calibri', bold='Calibri-Bold', italic='Calibri-Italic', boldItalic='Calibri-Bold')


register_fonts()
styles = {
    'title': ParagraphStyle('Title', fontName='Calibri-Bold', fontSize=29, leading=32, textColor=NAVY, spaceAfter=14),
    'h1': ParagraphStyle('H1', fontName='Calibri-Bold', fontSize=21, leading=24, textColor=NAVY, spaceAfter=12),
    'h2': ParagraphStyle('H2', fontName='Calibri-Bold', fontSize=12.4, leading=15.3, textColor=TEAL, spaceBefore=12, spaceAfter=6),
    'body': ParagraphStyle('Body', fontName='Calibri', fontSize=10.4, leading=14.7, textColor=INK, spaceAfter=8),
    'small': ParagraphStyle('Small', fontName='Calibri', fontSize=9, leading=12.4, textColor=MUTED, spaceAfter=6),
    'tiny': ParagraphStyle('Tiny', fontName='Calibri', fontSize=8, leading=10.3, textColor=MUTED, spaceAfter=3),
    'cell': ParagraphStyle('Cell', fontName='Calibri', fontSize=9.25, leading=12, textColor=INK),
    'cell_small': ParagraphStyle('CellSmall', fontName='Calibri', fontSize=8.5, leading=10.8, textColor=INK),
    'th': ParagraphStyle('TH', fontName='Calibri-Bold', fontSize=9.2, leading=12, textColor=colors.white),
    'path': ParagraphStyle('Path', fontName='Calibri', fontSize=8.4, leading=11, textColor=MUTED, wordWrap='CJK', spaceAfter=5),
    'eyebrow': ParagraphStyle('Eyebrow', fontName='Calibri-Bold', fontSize=9.5, leading=12, textColor=TEAL, spaceAfter=10),
}


def p(text, style='body'):
    return Paragraph(text, styles[style])


def ref(label, url):
    return f'<link href="{escape(url, {chr(34): "&quot;"})}" color="#147B80"><u>{safe(label)}</u></link>'


def table(headers, rows, widths, compact=False):
    st = 'cell_small' if compact else 'cell'
    data = [[p(safe(x), 'th') for x in headers]]
    data += [[p(safe(x), st) for x in row] for row in rows]
    t = Table(data, colWidths=widths, hAlign='LEFT', repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, PALE]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6 if compact else 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6 if compact else 7),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, NAVY),
    ]))
    return t


def box(title, text, warning=False):
    col = AMBER if warning else TEAL
    content = [p(f'<b><font color="{col.hexval()}">{safe(title)}</font></b>'), p(text, 'small')]
    t = Table([[content]], colWidths=[W])
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FAF5EB') if warning else PALE),
                          ('BOX', (0, 0), (-1, -1), 0.6, LINE), ('LEFTPADDING', (0, 0), (-1, -1), 12),
                          ('RIGHTPADDING', (0, 0), (-1, -1), 12), ('TOPPADDING', (0, 0), (-1, -1), 9),
                          ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
    return t


class Metrics(Flowable):
    def __init__(self):
        super().__init__(); self.width = W; self.height = 83

    def draw(self):
        c = self.canv
        entries = [('25.537', 'Knoten'), ('98.577', 'Beziehungen'), ('73', 'recherchierte Ereignisse')]
        gap = 10; width = (W - 2 * gap) / 3
        for i, (value, label) in enumerate(entries):
            x = i * (width + gap)
            c.setFillColor(PALE); c.roundRect(x, 7, width, 70, 6, fill=1, stroke=0)
            c.setFillColor(NAVY); c.setFont('Calibri-Bold', 25); c.drawString(x + 12, 41, value)
            c.setFont('Calibri', 10); c.setFillColor(MUTED); c.drawString(x + 12, 23, label)


class GraphSketch(Flowable):
    def __init__(self):
        super().__init__(); self.width = W; self.height = 346

    def draw(self):
        c = self.canv
        def node(x, y, w, h, title, subtitle='', accent=False):
            c.setFillColor(TEAL if accent else PALE); c.setStrokeColor(TEAL if accent else LINE)
            c.roundRect(x, y, w, h, 5, fill=1, stroke=1)
            c.setFillColor(colors.white if accent else NAVY); c.setFont('Calibri-Bold', 9.7)
            c.drawCentredString(x+w/2, y+h/2+(3 if subtitle else -3), title)
            if subtitle:
                c.setFont('Calibri', 8.2); c.drawCentredString(x+w/2, y+h/2-10, subtitle)
        def arrow(x1,y1,x2,y2,label='',lx=None,ly=None):
            import math
            c.setStrokeColor(MUTED); c.setFillColor(MUTED); c.setLineWidth(1)
            c.line(x1,y1,x2,y2)
            angle=math.atan2(y2-y1,x2-x1); size=5
            path=c.beginPath();path.moveTo(x2,y2)
            path.lineTo(x2-size*math.cos(angle-0.5),y2-size*math.sin(angle-0.5))
            path.lineTo(x2-size*math.cos(angle+0.5),y2-size*math.sin(angle+0.5));path.close()
            c.drawPath(path,fill=1,stroke=0)
            if label:
                c.setFont('Calibri', 7.6);c.drawCentredString(lx if lx is not None else (x1+x2)/2,ly if ly is not None else (y1+y2)/2+5,label)
        c.setFont('Calibri', 9);c.setFillColor(MUTED)
        c.drawString(0, 332, 'Parallele Berichtsstände des gleichen Sees')
        for x,year in [(15,'2010'),(188,'2016'),(361,'2022')]:
            node(x,280,128,35,'WaterBodyVersion',year)
        node(185,184,133,52,'WaterBodyV2','Bergwitzsee',True)
        for x in [79,252,425]:
            c.setStrokeColor(MUTED);c.line(252,236,252,258);c.line(252,258,x,258)
            arrow(x,258,x,280)
        c.setFillColor(MUTED);c.setFont('Calibri',7.8);c.drawString(259,247,'HAS_VERSION')
        node(8,184,128,52,'MonitoringStationV2','Messstelle / Monitoringort')
        arrow(136,211,185,211,'MONITORS',160,220)
        node(361,190,128,41,'Event','73 recherchierte Einträge')
        arrow(318,210,361,210,'HAS_EVENT',339,220)
        node(361,116,128,41,'EventEvidence','Beleg und Fundstelle')
        arrow(425,190,425,157,'HAS_EVIDENCE',461,174)
        node(361,42,128,41,'SourceDocument','Webseite / PDF')
        arrow(425,116,425,83,'FROM_DOCUMENT',465,100)
        node(8,102,128,48,'MeasurementV2','Wert + Zeit + Originaltext')
        arrow(72,184,72,150,'RECORDED',107,168)
        node(8,20,128,40,'ParameterV2','Was wurde gemessen?')
        node(192,20,111,40,'UnitV2','Einheit')
        arrow(72,102,72,60,'MEASURES_PARAMETER',89,79)
        arrow(136,110,247,60,'HAS_UNIT',211,91)
        c.setFont('Calibri-Italic',8.2);c.setFillColor(MUTED)
        c.drawString(0, 1, 'Vereinfachter Ausschnitt. Kanten beschreiben Zuordnungen, keine kausalen Wirkungen.')


def footer(c, doc):
    c.saveState()
    c.setFillColor(TEAL); c.rect(0, A4[1]-6, A4[0], 6, fill=1, stroke=0)
    c.setFont('Calibri-Bold', 8.2);c.setFillColor(MUTED)
    c.drawString(46, A4[1]-32, 'SOLVE  /  BERGWITZSEE  /  RECHERCHE UND KNOWLEDGEGRAPH')
    c.setStrokeColor(LINE);c.setLineWidth(0.6);c.line(46,37,A4[0]-46,37)
    c.setFont('Calibri',8);c.setFillColor(MUTED)
    c.drawString(46,24,'Ergebnisstand 02.11.2026  |  Quellen und Prüfnachweise im Projektdossier')
    c.drawRightString(A4[0]-46,24,f'{doc.page} / 8')
    c.restoreState()


def main():
    FINAL.parent.mkdir(parents=True,exist_ok=True);QA.mkdir(parents=True,exist_ok=True)
    status=read(OUT/'REPORT_STATUS.json')
    graph=read(OUT/'NEO4J_VALIDATION.json')
    preservation=read(OUT/'ADDITIVE_VALIDATION.json')
    mon=read(OUT/'data/monitoring/monitoring_supplement.json')
    assert (graph['nodes'],graph['relationships'])==(25537,98577)
    assert preservation['status']=='passed' and preservation['total_errors']==0
    encoding_files=list((OUT/'data').rglob('*.json'))
    def replacement_count(value):
        if isinstance(value,str):return value.count('\ufffd')
        if isinstance(value,list):return sum(replacement_count(x) for x in value)
        if isinstance(value,dict):return sum(replacement_count(k)+replacement_count(v) for k,v in value.items())
        return 0
    replacement_chars=sum(replacement_count(read(path)) for path in encoding_files)
    assert replacement_chars==0
    story=[]
    def add(*items):story.extend(items)
    def page(title,number):
        if story:add(PageBreak())
        add(p(f'{number:02d}  /  RECHERCHEPROTOKOLL','eyebrow'),p(title,'h1'))

    # 1. Ergebnis und Auftrag.
    add(p('SOLVE  /  WISSENSCHAFTLICHE DATENINTEGRATION','eyebrow'),
        p('Bergwitzsee<br/>Recherche und<br/>Knowledgegraph','title'),
        p('Was recherchiert wurde, wie die Quellen zusammengeführt wurden und welcher Datenstand jetzt vorliegt.','body'),
        Metrics(),Spacer(1,12))
    add(p('Auftrag und Ausgangspunkt','h2'),p('Ziel war ein Knowledgegraph für den <b>Bergwitzsee</b>, der das ursprüngliche V2-Modell weiterverwendet und die recherchierten Ereignisse ergänzt. Die früheren Brandenburger Dummy-Dateien dienten als <b>Schema-Vorlage</b>. Ihre Werte wurden nicht als Bergwitzsee-Messungen übernommen.'))
    add(p('Der gemeinsame Anker ist der Wasserkörper <b>DELW_DEST_EL03OW07-00</b>. Amtliche Bewertungen, Messstellen, Einzelmessungen und recherchierte Ereignisse können darüber gemeinsam untersucht werden. Historische Berichtsstände liegen als eigene Versionen nebeneinander.'))
    add(box('Erhalten und erweitert','Alle bisherigen <b>73 Ereignisse</b> und <b>23.789 Messwerte</b> blieben unverändert. Der neue Schritt ergänzt <b>47 Knoten und 104 Beziehungen</b>, darunter 14 veröffentlichte Badegewässerwerte und zusätzliche Raum- und Bewertungsdaten.'))
    add(p('Prüf- und Bereitstellungsstand','h2'),p('Die ergänzte Arbeitskopie ist geprüft. Der Status von Desktop-Übernahme und Dump wird gesondert auf Seite 8 ausgewiesen. Eine erfolgreiche Verbindung zu einer Instanz bedeutet noch nicht, dass dort die Ergänzung importiert ist.','small'))
    add(p('Weg durch den Bericht','h2'),p('2 Rechercheweg  ·  3 Datenbestand und Messbeispiel  ·  4 Verknüpfungsmodell<br/>5 Die 21 ursprünglichen Klassen  ·  6 Quellen  ·  7 Grenzen  ·  8 Prüfungen und Dateien','small'))

    # 2. Rechercheweg.
    page('Rechercheweg und Entscheidungen',2)
    phases=[
      ('1', 'Ursprüngliches Modell prüfen', 'Importskripte und V2-Schema wurden als Grundlage verwendet. Die neue Bergwitzsee-Datenbank wurde isoliert aufgebaut; das fachliche Grundmodell wurde beibehalten.'),
      ('2', 'Amtliche WRRL-Stände beschaffen', 'WasserBLIcK/BfG-Abrufwege wurden geprüft. Direkte BfG-Download- beziehungsweise Steckbriefaufrufe waren hier teilweise mit HTTP 403 nicht zugänglich. Nach ausdrücklicher Freigabe wurde eine dokumentierte öffentliche Kopie verwendet.'),
      ('3', 'Historische Zustände trennen', 'Die EEA-/WISE-WFD-Dienste liefern Berichtsstände 2010, 2016 und 2022. Identität und Berichtsstand wurden getrennt modelliert; ein Berichtsjahr ist keine punktgenaue Messung und garantiert keine zeitgenaue Grenzgeometrie.'),
      ('4', 'LHW-Rohdaten integrieren', 'Chemische Wasser- und Sedimentmessungen, Phytoplankton, Makrophyten/Diatomeen, Makrozoobenthos sowie Stammdaten wurden aus den amtlichen GLD-Exporten übernommen. Originaldateien, Quelldaten und unsichere Einheiten-/CRS-Angaben blieben erhalten.'),
      ('5', 'Ereignisse anbinden', '73 recherchierte Ereignisse wurden mit 79 Belegobjekten und 15 Quelldokumenten direkt an den See angebunden. Die Verknüpfung bedeutet thematische Zuordnung, nicht automatisch kausale Erklärung einer Messwertänderung.'),
      ('6', 'Gezielt verbleibende Lücken prüfen', 'Zusätzlich wurden die Landes-Badegewässerseite, ein UBA-Bericht, Schutzgebietsgeometrien und historische Grundwasserdaten ausgewertet. Nur belegte Inhalte wurden ergänzt; leere Modellklassen wurden nicht mit Vermutungen gefüllt.'),
    ]
    for no,title,body in phases:
        add(p(f'{no}. {title}','h2'),p(body))
    add(box('Nachvollziehbarkeit statt stiller Ersetzung','Jede Ergänzung hat einen Quellenbezug. Identische ursprüngliche Datensätze wurden nicht erneut als neue Messungen importiert. Der Ausgangsgraph und die Ausgangstabellen wurden vor und nach der Ergänzung verglichen.'))

    # 3. Bestand und Messbeispiel.
    page('Datenbestand und konkretes Messbeispiel',3)
    add(table(['Bestand','Vorher','Ergänzung','Jetzt'],[
      ['Messwerte','23.789','14','23.803'],['Monitoringstellen','25','1','26'],
      ['Proben / SamplingSectionV2','324','5','329'],['Gewässerbewertungen','48','8','56'],
      ['Ereignisse / Belegobjekte','73 / 79','0 / 0','73 / 79'],
      ['Schutzgebiete / Grundwasserkörper','0 / 0','2 / 1','2 / 1'],
    ],[W*0.48,W*0.17,W*0.17,W*0.18]))
    add(p('Badegewässerüberwachung 2026: Originalangaben','h2'),
        p('Die zusätzliche Quelle veröffentlicht fünf Probentermine für Bergwitzsee. Die Werte unten sind aus der Landes-Webseite übernommen, nicht aus Rohdaten neu berechnet. Quelle [Q3]; jeweils das aufklappbare Feld mit dem Probendatum.','small'))
    bysample={s['sample_id']:{'date':s['date']} for s in mon['samples']}
    for r in mon['measurements']+mon['missing_values']:
        bysample[r['sample_id']][r['parameter']]=r['value_text'] or 'nicht angegeben'
    sample_rows=[]
    for r in sorted(bysample.values(),key=lambda x:x['date']):
        y,m,d=r['date'].split('-')
        sample_rows.append([f'{d}.{m}.{y}',r['Entnahmetemperatur'],r['Intestinale Enterokokken'],r['Escherichia coli']])
    add(table(['Probenahme','Temperatur [°C]','Enterokokken [KBE/100 ml]','E. coli [KBE/100 ml]'],sample_rows,[W*.19,W*.24,W*.29,W*.28]))
    add(Spacer(1,10),box('Sieben Werte sind zensiert','<b>&lt;10</b> bedeutet „unter dem berichteten Grenzwert“, nicht 0 und nicht exakt 10. Der numerische Messwert bleibt leer; Originaltext, Operator und Grenze werden separat gespeichert. Für die fehlende Temperatur am 31.08.2026 wurde kein Messwert erfunden.'))
    add(p('Welche acht Bewertungen kamen hinzu?','h2'),p('Drei Badegewässerklassen für 2023-2025: jeweils ausgezeichnet. Diese Einstufungen werten vier Badesaisons aus. Dazu kommen fünf UBA-Angaben: Phytoplankton 2013, Phyto-See-Index 1,05, Makrophyten/Phytobenthos 2013, ökologisches Potenzial 2015 und die ausdrücklich um Quecksilber in Biota eingeschränkte Chemiebewertung 2015. [Q3-Q6]','small'))

    # 4. Graph.
    page('Wie die Quellen im Graphen verbunden sind',4)
    add(p('Die stabile Gewässeridentität bildet den gemeinsamen Bezugspunkt. Messstellen, Bewertungszeitpunkte und Belege bleiben unterscheidbar. Die Skizze zeigt einen Ausschnitt des tatsächlich verwendeten Modells.'))
    add(GraphSketch(),Spacer(1,8))
    add(p('Ereignisse direkt am See','h2'),p('Die gewünschte Verbindung lautet <b>WaterBodyV2 - HAS_EVENT - Event</b>. Ein Event kann mehrere Belege besitzen; diese führen über EventEvidence zu SourceDocument. Die 73 Einträge wurden maschinell recherchiert und zusammengestellt; sie sind <b>kein vollständig manuell freigegebener Datensatz</b>.','small'))
    add(p('Historische Versionen parallel','h2'),p('Die vorhandenen drei WaterBodyVersion-Knoten bleiben bestehen. Neu folgen dem gleichen Prinzip drei GroundwaterBodyVersion-Knoten für 2010, 2016 und 2022. Ein neuer Berichtsstand überschreibt die frühere Version nicht.','small'))
    add(p('Räumliche Verbindung mit begrenzter Aussage','h2'),p('Der See überschneidet sich mit zwei Schutzgebieten sowie dem Grundwasserkörper DEGB_DEST_EL-3-1. Die Kanten OVERLAPS und OVERLAPS_PROTECTED_AREA belegen eine Polygonüberschneidung. Sie belegen weder hydraulischen Austausch noch eine Ursache-Wirkungs-Beziehung.','small'))

    # 5. Original schema.
    page('Die 21 ursprünglichen V2-Klassen',5)
    add(p('Unverändertes Grundmodell, aktuelle Belegung für Bergwitzsee. Klassen ohne belegte Daten bleiben leer. Das Sammellabel WaterKgV2 ist keine zusätzliche fachliche Klasse.','small'))
    definitions=[
      ('PlanningUnitV2','Wasserwirtschaftliche Planungseinheit'),('WaterBodyV2','Stabile Oberflächengewässer-Identität'),
      ('WaterBodyIdentifierV2','Weitere amtliche Gewässerkennungen'),('GroundwaterBodyV2','Stabile Grundwasserkörper-Identität'),
      ('CatchmentV2','Einzugsgebiet mit belegter Abgrenzung'),('MonitoringStationV2','Mess- oder Monitoringstelle'),
      ('StationAliasV2','Quellenabhängige Stationskennung'),('CoreLocationV2','Entnahmestelle eines Sedimentkerns'),
      ('ParameterV2','Mess-/Bewertungsgröße im Fachkontext'),('UnitV2','Einheit einer Messangabe'),
      ('PollutantV2','Identifizierter bewerteter Schadstoff'),('PaleoElementV2','Element einer Sedimentkernreihe'),
      ('ProtectedAreaV2','Amtliches Schutzgebiet'),('SamplingSectionV2','Beprobungsabschnitt oder Probe'),
      ('MeasurementV2','Messwert einschließlich Quellenangabe'),('WaterBodyAssessmentV2','Bewertung oder Index auf Gewässerebene'),
      ('StationAssessmentV2','Ausdrücklich stationsbezogene Bewertung'),('ChemicalStatusV2','Stoffbezogener chemischer Zustand'),
      ('PaleoMeasurementV2','Messung an einer Sedimentkernreihe'),('ProtectedAreaAssessmentV2','Bewertung eines Schutzgebiets'),
      ('ImportIssueV2','Dokumentierter Import-/Qualitätshinweis'),
    ]
    rows=[[label,meaning,f"{graph['counts_by_label'].get(label,0):,}".replace(',','.')] for label,meaning in definitions]
    add(table(['Klasse','Fachliche Bedeutung','Knoten'],rows,[W*.40,W*.48,W*.12],compact=True))
    add(Spacer(1,8),p('Beleg des Originalmodells: 21 NODE_SPECS im gesicherten Originalskript neo4j_v2_import.py (source_scripts des Dossiers vom 01.11.2026). Der Ergänzungsimport verwendet diese Schlüssel und Beziehungsmappings weiter. Ereignis-, Quellen- und Versionsklassen ergänzen dieses Modell.','tiny'))

    # 6. Sources.
    page('Quellenregister und überprüfbare Fundstellen',6)
    source_items=[
      ('Q1','LHW / Gewässerkundlicher Landesdienst', 'https://gld.lhw-sachsen-anhalt.de/',
       'Bereits importierte Originalexporte: Wasser, Sediment, Phytoplankton, Makrophyten/Diatomeen, Makrozoobenthos und Stammdaten. Rohdaten: bergwitzsee_lhw_20261101/original_export.'),
      ('Q2','EEA / WISE WFD: historische Berichtsstände', 'https://water.discomap.eea.europa.eu/arcgis/rest/services/WISE_WFD',
       'Öffentliche dokumentierte Kopie für Berichtsstände 2010, 2016 und 2022. Neue Grundwasserdaten: jeweils groundwater_JAHR_polygon.geojson und groundwater_JAHR_status.geojson; genaue Query-URLs und Hashes in prepared_geodata.json.'),
      ('Q3','Land Sachsen-Anhalt: Bergwitzsee', mon['sources'][0]['url'],
       'Aktuelle Messwerte: Datumsfelder der fünf Termine 2026. Einstufungen: Absätze „Einstufung 2023“, „2024“, „2025“. Rohdatei ms_bergwitzsee.html.'),
      ('Q4','Badegewässerprofil Bergwitzsee', mon['sources'][2]['url'],
       'Profil erstellt 2011, aktualisiert 2015. PDF-Seite 2: Kennung und Monitoringort; Seite 12: Überwachung. Kennung DEST_PR_0064 ist keine WRRL-Messstellenkennung.'),
      ('Q5','Land Sachsen-Anhalt: Bewertungsmethodik', mon['sources'][1]['url'],
       '„Rechtsgrundlagen und Überwachung“: mikrobiologische Parameter, vier Badesaisons und Bewertungsklassen. Rohdatei ms_badegewaesser_methodik.html.'),
      ('Q6','UBA Texte 68/2016: Tagebauseen', mon['sources'][3]['url'],
       'Bergwitzsee-Steckbrief: PDF-Seite 71 mit Bewertungen 2013/2015; Stand 18.04.2016 auf Seite 72. Scopekontrolle: Tabelle 15, PDF-Seiten 52-53. Roh-PDF gespeichert.'),
      ('Q7','GDI-LSA: Schutzgebiete, amtlicher WFS', 'https://www.geodatenportal.sachsen-anhalt.de/gfds/ws/wfs/942f5d74-6c2b-263a/GDI-LSA_Schutzgebiete/ows.wfs?service=WFS&request=GetCapabilities',
       'Layer lau_lsg_lsa und lau_nup_lsa, Abruf 02.11.2026. Rohdateien lau_lsg_lsa.gml und lau_nup_lsa.gml; räumliche Schnittprüfung dokumentiert.'),
      ('Q8','FGG Elbe: Grundwasserkörperverzeichnis', 'https://lvwa.sachsen-anhalt.de/fileadmin/Bibliothek/Politik_und_Verwaltung/LVWA/LVwA/Dokumente/4_landwirtschaftumwelt/404/Seite_Wasser_bewegt/FGG_Elbe/Anhaenge/Bewirtschaftungsplan_FGG_Elbe_2021_Anhang_A1-1.pdf',
       'Anhang A1-1, Seite 6, Stand 14.10.2021: Kennung DEGB_DEST_EL-3-1 und Name „Elbtal und Moränenlandschaft Dübener Heide“.'),
    ]
    for key,title,url,detail in source_items:
        add(p(f'<b>[{key}]</b> {ref(title,url)}','body'),p(detail,'small'),Spacer(1,3))
    add(p('Die Quellenbezeichnungen sind anklickbar. Vollständige Abruf-URLs, SHA256-Prüfsummen und Rohdatenlokatoren liegen im Dossier. Für die amtliche Ergänzung wurden 13 neue Rohdateien im Quellenregister registriert. Die 15 bestehenden Event-Quelldokumente bilden nicht die Gesamtzahl aller amtlichen Datenquellen ab.','tiny'))

    # 7. Limits.
    page('Qualität, Grenzen und offene Datenlücken',7)
    add(p('Messwerte richtig lesen','h2'),p('Das historische Feld detection_limit bleibt kompatibel erhalten. Bei den sieben neuen &lt;10-Werten geben reported_censoring_bound und limit_kind ausdrücklich an, dass die Quelle einen Grenzwert berichtet, aber keine analytische Nachweis- oder Bestimmungsgrenze spezifiziert.'))
    add(p('Unklare Einheiten und Koordinaten erhalten','h2'),p('Die bisherigen LHW-Daten enthalten Qualitätsfragen, unter anderem fehlende Einheiten in biologischen Exporten und widersprüchliche CRS-Hinweise zwischen SHP-Projektion und Koordinatenattributen. Diese Angaben wurden nicht still korrigiert. Nicht gesichert verortbare Stationen erhielten keine erfundene Punktgeometrie.'))
    add(p('Badegewässer ist keine LHW-Messstellenfusion','h2'),p('Das Profil nennt zwei Probenahmeorte, die aktuelle Ergebnisliste keinen Unterort. Deshalb bleiben die zusätzlichen Daten unter einer eigenen Monitoring-Identität. Profilkoordinaten und Unklarheit sind dokumentiert; keine der fünf Proben wurde einer konkreten LHW-Station zugeschrieben.'))
    add(p('Bewertungen nicht überdehnen','h2'),p('Die UBA-Chemiebewertung 2015 lautet nur unter Ausschluss der bundesweit angenommenen Quecksilberüberschreitung in Biota „gut“. Sie ist kein Beleg für einen uneingeschränkt guten chemischen Gesamtzustand. Die Diatomeenbewertung 2013 ist laut Quelle ungesichert; die kombinierte Klasse beruht auf Makrophyten.'))
    add(table(['Noch nicht belastbar beschafft','Folge im Modell'],[
      ['Stationsbezogene biologische Bewertung','StationAssessmentV2 bleibt leer.'],
      ['WRRL-Einzelstoffliste mit eindeutiger Bewertung','ChemicalStatusV2 / PollutantV2 bleiben leer.'],
      ['Einzugsgebiets- und Planungseinheitsgeometrien','CatchmentV2 / PlanningUnitV2 bleiben leer.'],
      ['Sedimentkern- und quantitative Fischmessreihen','Keine Paleo-/Core-Knoten oder erfundenen Fischwerte.'],
    ],[W*.58,W*.42],compact=True))
    add(Spacer(1,9),p('BfG-Steckbrief: Die konkrete Verknüpfung wurde aus '+ref('FGG-Elbe-Anhang A5-2','https://mluk.brandenburg.de/w/WRRL2022-27/Bewirtschaftungsplan/Anhaenge/Anhang-A5-2-FGG-Elbe-Bewirtschaftungsplan.pdf')+', Seite 12, extrahiert; der direkte Abruf lieferte HTTP 403. Die dort genannte Anzahl relevanter Schadstoffe wurde nicht in vermutete Stoffnamen übersetzt. Fehlender Abruf bedeutet nicht, dass die Daten grundsätzlich nicht existieren.','small'))

    # 8. Validation / delivery.
    page('Prüfungen, Dateien und aktueller Abschlussstand',8)
    add(table(['Prüfung','Ergebnis'],[
      ['PostgreSQL-Ausgangskopie und Bestandserhalt','Alle ursprünglichen Zeilen und Felder erhalten; Schema nur ergänzt.'],
      ['Neo4j-Bestandserhalt','25.490 alte Knoten und 98.473 alte Beziehungen geprüft; 0 Abweichungen.'],
      ['Ergänzungsumfang','47 neue Knoten / 104 Beziehungen; gesamt 25.537 / 98.577.'],
      ['Daten- und Strukturprüfung','Quelle, Zahlen, Zensierung, Bezüge und Originalmappings geprüft.'],
      ['Textkodierung','Quellen-JSON beim Berichtsbau geprüft: keine U+FFFD-Ersatzzeichen.'],
    ],[W*.44,W*.56],compact=True))
    add(Spacer(1,10),box('Neo4j Desktop / ver4',safe(status.get('desktop_details','Direkte Übernahme nicht bestätigt.')),
                       warning=not status.get('desktop_applied',False)))
    add(Spacer(1,8),box('Dump und Wiederherstellung',safe(status.get('dump_details','Exportprüfung noch nicht abgeschlossen.')),
                       warning=not status.get('dump_verified',False)))
    add(p('Lieferdateien und Nachweise','h2'))
    add(p('Projekt: outputs/bergwitzsee_ergaenzung_20261102/','path'))
    for name,description in [
      ('export/bergwitzsee-v2-ergaenzt-20261102.dump','Neo4j-Datenbank; Freigabe durch EXPORT_VALIDATION.json.'),
      ('postgres/bergwitzsee-v2-ergaenzt-20261102.backup','PostgreSQL-Custom-Backup; Prüfumfang in BACKUP_VALIDATION.json.'),
      ('README.md und browse.cypher','Ausführliche Dokumentation und 14 lesende Beispielabfragen.'),
      ('POSTGRES_DELTA.json und GRAPH_DELTA.json','Reproduzierbarer Inhalt der eingefügten Ergänzungen.'),
    ]:
        add(p(f'<b>{safe(name)}</b>','path'),p(description,'tiny'))
    add(p('Prüfnachweise: POSTGRES_VALIDATION.json, NEO4J_VALIDATION.json, ADDITIVE_VALIDATION.json und die Export-/Desktop-Statusdateien. Geprüft wurden Mapping, Quellentreue und Bestandserhalt; das ersetzt keine manuelle Fachprüfung aller Ereignisse. Die Archivverzeichnisprüfung eines PostgreSQL-Backups allein ist kein vollständiger Restore-Test.','small'))
    add(p('Weiterarbeiten','h2'),p('Den fertigen Dump in eine kompatible Neo4j-Desktop-Instanz importieren und die lesenden Abfragen nutzen. Für eine belastbare Ereignisanalyse sollten die 73 recherchierten Einträge fachlich gesichtet und verbleibende Mess-/Quellenlücken gezielt geschlossen werden. Ein erneuter Append auf die fertige Arbeitsdatenbank ist nicht erforderlich.','small'))
    add(p(f'Statusquelle: REPORT_STATUS.json, aktualisiert {safe(status.get("updated_at","unbekannt"))}.','tiny'))

    doc=SimpleDocTemplate(str(FINAL),pagesize=A4,rightMargin=46,leftMargin=46,topMargin=52,bottomMargin=49,
                          title='Bergwitzsee - Recherche und Ergebnisstand des Knowledgegraphs',
                          author='SOLVE / dokumentierte Recherche und Datenintegration',
                          subject='Quellen, Schema, Bestandsdaten, Ergänzungen und Qualitätsgrenzen, Stand 02.11.2026')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    import pdfplumber
    with pdfplumber.open(FINAL) as pdf:
        pages=[page.extract_text() or '' for page in pdf.pages]
        assert len(pages)==8, f'Unexpected pagination: {len(pages)} pages'
        assert all(len(text)>450 for text in pages)
        assert all('\ufffd' not in text for text in pages)
        assert '98.577' in pages[0] and '23.803' in pages[2]
        links=sum(len(page.hyperlinks) for page in pdf.pages)
    report={'file':str(FINAL),'pages':len(pages),'sha256':hashlib.sha256(FINAL.read_bytes()).hexdigest(),
            'bytes':FINAL.stat().st_size,'text_characters_per_page':list(map(len,pages)),
            'hyperlinks':links,'source_status':status,'text_checks_passed':True,
            'encoding_audit':{'json_files':len(encoding_files),'replacement_chars':replacement_chars},
            'rendered_visual_review_required':True}
    (QA/'BUILD_VALIDATION.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    (QA/'report_text.txt').write_text('\n\n'.join(f'--- Seite {i+1} ---\n{t}' for i,t in enumerate(pages)),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='source_status'},ensure_ascii=False))


if __name__=='__main__':
    main()
