from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'compact'))
from schemas import Event

def raw_event(variant):
    case=json.loads((ROOT/'results'/variant/'case_0.json').read_text(encoding='utf-8'))
    raw=json.loads(Path(case['packages'][0]['raw_response_file']).read_text(encoding='utf-8'))
    output=json.loads(raw['choices'][0]['message']['content'])
    return next(e for e in output['events'] if e['statement']=='Wasserentnahme zur Energiegewinnung wurde gegebenenfalls eingeschränkt.')

event=Event.model_validate(raw_event('reference'))
verbose=event.model_dump(mode='json')
compact=event.model_dump(mode='json',exclude_none=True)
assert Event.model_validate(verbose)==Event.model_validate(compact)
for name,value in [('example_verbose.json',verbose),('example_compact.json',compact),
                   ('actual_compact_qwen_event.json',raw_event('compact_qwen'))]:
    (ROOT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
keys=['event_id','statement','participants','measurement_context','time_original','time_status','assertion_mode','polarity','source_document']
before={k:verbose[k] for k in keys}
after={k:compact[k] for k in keys if k in compact}
text='''# JSON vorher und nachher: tatsächliche Aussage aus der Test-PDF

Die Aussage stammt aus PDF-Seite 2. Zur isolierten Darstellung des Formateffekts
wird **derselbe Referenz-Datensatz** einmal mit und einmal ohne optionale Nullfelder
gezeigt. Das Nachher-Beispiel ist lokal abgeleitet, keine zweite unabhängige Modellantwort.
Die Gleichheit nach Schema-Validierung wurde geprüft. Die Ausschnitte lassen andere
Felder einschließlich der Belege aus; vollständige Datensätze stehen in den JSON-Dateien.

## Vorher (Ausschnitt)

```json
'''+json.dumps(before,ensure_ascii=False,indent=2)+'''
```

## Nachher (derselbe Ausschnitt)

```json
'''+json.dumps(after,ensure_ascii=False,indent=2)+'''
```

Die belegte Aussage, ihr Status und die in den vollständigen Dateien enthaltenen
Zitate bleiben gleich. Beim Einlesen ergänzt das Schema definierte Defaults;
die Excel-Spalten ändern sich nicht. Das lokale Beispiel erklärt nur den Vertrag:
Laufzeit spart erst eine bereits vom Modell erzeugte kompakte Antwort.

- `example_verbose.json`: vollständiger normalisierter Referenz-Datensatz.
- `example_compact.json`: derselbe Datensatz ohne optionale Nullfelder.
- `actual_compact_qwen_event.json`: tatsächlich erzeugter kompakter Qwen-Datensatz
  zur gleichen Aussage, unverändert aus der Rohantwort. Andere Detailfelder und die
  lokale ID können bei dieser unabhängigen Extraktion abweichen.
'''
(ROOT/'JSON_vorher_nachher.md').write_text(text,encoding='utf-8')
print('Validated same meaning; example files written.')
