# Abschnittsgrößen: kontrollierter Pilot vom 29.09.2026

Getestet wurde der unveränderte Parser 1.1.0 mit Qwen3.8-Flash-Next-NVFP4 über Blablador. **Größere Abschnitte waren in diesem Pilot schneller; Geschwindigkeit allein ist aber kein Qualitätsgewinn.** Die Tabelle trennt Laufzeit, automatisch akzeptierte Ausgaben und manuell geprüfte Vollständigkeit.

## Versuchsaufbau

- Identischer Quelltext: 7746 Zeichen aus PDF-Seiten 10, 11 und 17 des bereitgestellten Elbe-Berichts. PDF-SHA256: `1bfed581c2f2d2d5047c584035c526a2eb9c75315447f09b9d482f053671f44d`.
- Grenzen: 2.000 / 4.000 / 10.000 Zeichen, entsprechend fünf / zwei / einem Anfangsabschnitt. Der große Abschnitt enthält tatsächlich 7.746 Zeichen.
- Je zwei frische Durchgänge: zuerst klein → mittel → groß, danach groß → mittel → klein. Kein Wiederverwenden gespeicherter Modellantworten in den gemessenen Läufen.
- Identische feste Quellblöcke, Prompt, Schema, Ontologie und Einstellungen: Temperatur 0, Thinking aus, 16.000 Ausgabetokens, 65.536 Kontexttokens, ein Worker, 30 RPM, maximal ein HTTP-Retry. Selbstbeziehungsschutz aktiv.
- Die native Übergabe angrenzender Quellblöcke bleibt aktiv. Zusätzlich übermittelte Kontextzeichen pro vollständigem Lauf: 5.370 / 426 / 0. Das gehört zum Verhalten der jeweiligen Abschnittsgröße.
- Gemessen: Extraktion einschließlich Reparaturen, Wiederholungen, möglicher Teilungen, bis zu zwei ergänzender Kontextanfragen, Validierung und Export. PDF-Aufbereitung und Modellkatalogabfrage sind ausgeschlossen.
- Die Vorverarbeitung wurde einmalig übernommen, damit Unterschiede bei der PDF-Aufbereitung nicht den Größenvergleich verfälschen. Dies ist deshalb kein kompletter Vergleich aller Auswirkungen des CLI-Parameters auf neue PDF-Aufbereitungen.
- Ein erster unterbrochener Versuch mit mehrfachen HTTP-502-Fehlern liegt separat unter `interrupted_r1_chars2000_502`; er wurde nicht gewertet. Eine kurze Erreichbarkeitsprobe ist ebenfalls ausgeschlossen.

## Einzelmessungen

| Grenze | Durchgang | Zeit (min:s) | API-Aufrufe | Fakten exportiert | Beziehungen exportiert | Prüfpunkte vollständig / 19 | Teilweise | Fehlerhafte Ergänzung |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2000 | 1 | 8:10 | 7 | 42 | 4 | 9 | 4 | 1 |
| 2000 | 2 | 6:21 | 7 | 44 | 5 | 9 | 4 | 1 |
| 4000 | 1 | 3:29 | 3 | 15 | 1 | 8 | 1 | 0 |
| 4000 | 2 | 3:36 | 3 | 17 | 0 | 6 | 3 | 0 |
| 10000 | 1 | 1:16 | 1 | 6 | 1 | 5 | 0 | 0 |
| 10000 | 2 | 2:09 | 2 | 0 | 0 | 0 | 0 | 0 |

## Mittelwerte

| Grenze | Mittlere Zeit | Vollständige Prüfpunkte, Mittel / 19 |
|---:|---:|---:|
| 2000 | 7:16 | 9.0 |
| 4000 | 3:32 | 7.0 |
| 10000 | 1:43 | 2.5 |

## Einordnung und Empfehlung

**Nicht pauschal vergrößern.** Der große Abschnitt ist schnell, verliert hier aber erhebliche Inhalte. In einem seiner zwei Läufe wurden alle Einträge wegen ungültiger Themen-IDs ausgesondert. Auch seine Rohantworten enthalten nur acht bzw. zehn Events; Filterfehler erklären die fehlende Abdeckung daher nicht allein.

Die kleinen Abschnitte decken jeweils neun der 19 Prüfpunkte vollständig und vier teilweise ab, kosten aber deutlich mehr Zeit. Ihre 42 bzw. 44 exportierten Fakten enthalten mehrere inhaltliche Dubletten. Unbelegte Zeitzuordnungen (z. B. Beginn einer Betriebsregel 1964) bestehen fort. Klein ist deshalb ebenfalls keine Garantie für korrekte Ergebnisse.

4.000 Zeichen sind hier ein sinnvoller Kandidat für weitere Optimierung: rund 51 % weniger Laufzeit als 2.000 Zeichen, allerdings nur sechs bzw. acht vollständig erhaltene Prüfpunkte. Das ist ein beobachteter Kompromiss, kein nachgewiesenes allgemeines Optimum. Vor einer dauerhaften Umstellung sollten zulässige Themen-IDs, Beleggrenzen und Vollständigkeit verbessert und derselbe Vergleich mit weiteren Dokumenten wiederholt werden.

In den sechs gewerteten Läufen waren alle 23 API-Aufrufe HTTP 200 (16 Extraktionen, sieben Selbstbeziehungsreparaturen). Alle Antworten endeten regulär mit `stop`; es gab keine automatischen Teilungen und keine zusätzlichen Kontextanfragen. Kein finaler Export enthält eine Beziehung mit identischer Quell- und Ziel-ID. Das schließt andere semantische Beziehungsfehler nicht aus.

## Qualitätsmaß und Grenzen

Die 19 Prüfpunkte wurden vor den Modellantworten festgelegt und stehen in `plan.json`. Geprüft werden belegte Werte, zugehörige Zeiträume, Sachverhalte und Prognosestatus in den finalen Fachfeldern. Ein Fakt, der nur im langen Belegzitat vorkommt, zählt nicht als extrahiert. F = vollständig, P = teilweise, M = fehlt, E = vorhanden, aber mit einer wesentlichen unbelegten Ergänzung. Wiederholungen desselben Fakts erhöhen die Abdeckung nicht.

Dies ist eine begrenzte, nicht verblindete Prüfung durch den Assistenten, kein unabhängiges Expertenrating und keine allgemeine Precision-/Recall-Messung. Die Abdeckung misst Sachverhalte; sie ersetzt keine gesonderte Prüfung korrekter Ontologieklassen oder eines vollständigen Beziehungsgraphen. Auch automatisch exportierte Zeilen können semantische Fehler enthalten.

Ein Dokument, drei ausgewählte Seiten und zwei Wiederholungen erlauben keine statistisch belastbare allgemeine optimale Abschnittsgröße. Tabellen, Bilder und größere Abschnitte oberhalb der gesamten 7.746 Zeichen wurden damit nicht bewertet. Serverlast kann die Zeiten beeinflussen.

## Dokumentierte Einzelbefunde

### Alle 19 Prüfpunkte

| Prüfpunkt | 2k/1 | 2k/2 | 4k/1 | 4k/2 | 10k/1 | 10k/2 |
|---|---|---|---|---|---|---|
| 1. Moldau QM364: 20,0 → 38,8 m³/s samt beiden Zeitreihen und Talsperrenwirkung | P | P | P | P | F | M |
| 2. Moldau QM364: 36,9 m³/s für 1961–2005 | F | F | F | F | F | M |
| 3. Berounka-Anteil 3,8 m³/s innerhalb der 38,8 m³/s | M | M | M | M | M | M |
| 4. Moldaukaskade: neun Talsperren, 1.352,58 Mio. m³ Stauraum | M | M | M | M | M | M |
| 5. Vrané: Mindestabgabe 40,0 m³/s | F | F | F | F | F | M |
| 6. Prag vor Talsperrenbau: 12,0–15,0 m³/s | F | F | M | F | F | M |
| 7. Nechranice: Stauraum 272,43 Mio. m³; Wirkung ab 1966, offizielle Inbetriebnahme 1968 | M | M | M | M | M | M |
| 8. Eger/Louny QM364: 2,7 → 8,0 m³/s mit beiden Zeitreihen und Talsperrenwirkung | P | P | M | M | M | M |
| 9. Jesenice: 52,75 Mio. m³, Inbetriebnahme 1961 | M | M | M | M | M | M |
| 10. Skalka: 15,92 Mio. m³, Inbetriebnahme 1964 | M | M | M | M | M | M |
| 11. Nechranice: Mindestabgabe 8,0 m³/s | E | E | F | M | M | M |
| 12. Niedrigwasseraufhöhung der Eger wirkt auch auf die Elbe unterhalb der Mündung | F | F | F | M | M | M |
| 13. Prognose tschechisches Elbegebiet: Jahresabfluss −10 %, beide Vergleichszeiträume | F | F | F | F | M | M |
| 14. Prognose Moldau: mittlerer Abfluss −10 bis −15 % | F | F | F | F | F | M |
| 15. Prognose: mehr winterlicher Regen/Niederschlag → mehr Abfluss/Hochwasser | P | P | F | P | M | M |
| 16. Prognose: weniger Sommerregen → weniger ober-/unterirdischer Abfluss → schärferes Niedrigwasser | F | F | M | M | M | M |
| 17. Grenz-Einzugsgebiet 51.394 km²; Talsperrenanteil 21.372 km² / 41,6 % | P | P | M | P | M | M |
| 18. Künftiger Konflikt: Hochwasserrückhalteraum versus Speicherung zur Niedrigwasserabgabe | F | F | M | M | M | M |
| 19. Prognose häufigerer Unterschreitungen vereinbarter Monatsminima, besonders im Sommer | F | F | F | F | M | M |

F = vollständig, P = teilweise, M = fehlt, E = wesentliche unbelegte Ergänzung. Prüfpunkte beziehen sich auf finale Fachfelder einschließlich Aussagen; nur im Zitat enthaltene Informationen zählen nicht.

### r1_chars10000

- omissions: Only six exported events. Winter/summer chains, Elbe -10% projection, catchment figures, management conflict and future undershooting are absent. Raw response also contains only ten events, so omissions are not explained by filtering alone.
- 1: Both periods retained explicitly in statement; single normalized interval does not represent both periods but source comparison is recoverable.
- relationship_quality: The single exported edge turns the 40.0 m3/s minimum release into the cause of the historical QM364 increase. Source explicitly attributes the increase to the cascade and says 40.0 approximately corresponds to 38.8; this more specific edge is not directly supported.

### r1_chars2000

- 1: Event 1 retains before/after values but only the after-period 1955-1993 in the extracted fields; baseline 1924-1953 only in quotation. Ontology fine:ConcentrationChange is also semantically inappropriate for discharge.
- 8: Event 5 retains only 2.7 and explicitly says the next value is cut off; no exported complete comparison.
- 11: Event 9 invents a start in 1964 by borrowing Skalka commissioning; source does not date the operating rule this way. Event 10 also invents a before-1961 cutoff for historical low flows.
- 17: 51394 and 41.6% retained, but total 21372 is missing from extracted fields.
- duplicates: Examples: Elbe -10% events 12/20, Moldau -10 to -15% 13/22, future undershooting 37/41, research project 38/42. Event numbers refer to final events.jsonl line numbers.
- relationship_quality: The last exported edge narrows the climatic cause of border-flow undershooting to low-rainfall catchments, while the undershooting passage speaks about expected climate change generally. Export acceptance does not guarantee causal precision.
- 15: Winter rain increase and higher flows/floods are co-listed, but the causal connection is not explicitly retained in final statements or edges; strict checklist therefore only partial.

### r1_chars4000

- 1: Before and after values retained, but baseline years missing from extracted fields; only after-period normalized.
- 8: Complete comparison appears in raw response but is rejected for quote_not_found:p0011_b0003.
- duplicates: Events 7/8 repeat the border agreement validity statement.
- omissions: Summer causal chain, catchment totals and reservoir management conflict absent from final exports.

### r2_chars10000

- export_failure: All eight raw events and three raw relationships contain unknown_topic; additional quote errors occur. No final records, therefore zero checklist coverage. This is export coverage, not a claim that the raw answer contains no correct statements.
- repair: Self-relationship repair rejected because it changed unaffected metadata.

### r2_chars2000

- 1: Baseline period missing from extracted fields, as in first small trial.
- 8: Both numerical values appear in separate events 5/6, but baseline period 1922-1965 is absent and comparison is not fully reconstructed.
- 11: Event 10 again invents operating-rule start in 1964; event 11 invents historical cutoff 1961.
- 15: Winter rain and higher flows co-listed without explicit causal connection in exported statement or relationship.
- 17: 51394 and 41.6% present, total 21372 missing from extracted fields.
- duplicates: Examples: Elbe forecast events 15/21, Moldau forecast 16/23, border undershooting 39/43, research project 40/44.
- other: Existing mean precipitation below 550 mm/a is labeled predicted in event 32, although the source uses it as an existing regional property. Last edge again narrows the cause to low-rainfall catchments.

### r2_chars4000

- 1: Baseline period omitted from extracted fields.
- 13: Both comparison periods retained in time_original.
- 15: Winter flooding and redistribution retained, increased rain share and full causal chain not extracted.
- 17: Only 41.6% retained; catchment totals missing.
- other: Border agreement wrongly modeled as interval 1988-05 to 2000 rather than agreement date plus target horizon. No final relationships survived validation.

## Nachvollziehbarkeit

- `run_study.py`: reproduzierbarer Versuchsablauf, Abbruch bei unvollständiger Extraktion; vorhandene Messungen werden niemals mit Cache-Zeiten überschrieben.
- `results/*/manifest.json`: Eingaben, Einstellungen, Modellkennung und Prüfsummen.
- `results/*/fein/`: Rohantworten, Checkpoints, JSONL-/Excel-Exporte und Review-Gründe.
- `manual_review.json`: alle 19 Bewertungen je Lauf mit Erläuterungen.
- `verified_metrics.json`: gemessene Zeiten, Tokenzahlen, Fehlergründe, HTTP-Status und Checklistenwerte.
- Quelltextgleichheit aller sechs Varianten, gleiche Einstellungen und unveränderter Parsercode wurden beim Erstellen dieses Berichts geprüft.
- Produktionscode und Standard-Abschnittsgröße wurden nicht geändert.
