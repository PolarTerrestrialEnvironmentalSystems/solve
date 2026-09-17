# Eigenständiger Elbe-CSV-Downloader

Dieser Downloader schreibt keine Daten nach SOLVE, PostgreSQL oder Neo4j. Er
speichert Originalexporte des öffentlichen Elbe-Datenportals und einen getrennten
Messstellenkatalog. Python ab 3.10 genügt; zusätzliche Pakete sind nicht nötig.

Im GitHub-Repository liegt dieser eigenständige Downloader unter
`scripts/fgg_downloader`. Befehle und Tests aus diesem Unterordner ausführen.
Messdaten, Sitzungen und Fortschrittsdatenbanken gehören nicht in Git; die
beiliegende `.gitignore` lässt hier ausschließlich Quellcode und Dokumentation zu.

## Linux und paralleler Download

Die zusätzliche Datei `parallel_download.py` unterstützt bis zu 30 parallele
I/O-Worker mit getrennten Sitzungen, gemeinsamer Anfragedrossel und abgesicherter
Auftragsverteilung. Sie ist ein eigener Einstieg; die unten beschriebenen
Startbefehle für `downloader.py` bleiben sequenziell. Umzug, Linux-Start und
Wiederaufnahme sind in [SERVER_LINUX.md](SERVER_LINUX.md) beschrieben.
Es wurde kein Serverzugriff eingerichtet und kein paralleler Live-Abruf gestartet.

Für alte parallele Exporte, deren Wiederaufnahme eine bekannte Portal-Fehlerseite
oder eine nachweislich zurückgesetzte Auswahl liefert, gibt es außerdem
`resume --defer-unavailable-exports`. Der Modus sichert
Zustand und Sitzungen, dokumentiert betroffene Altaufträge als weiterhin offene
Lücken und lässt übrige Downloads weiterlaufen. Er fordert diese Altaufträge
nicht erneut an und meldet sie nicht als erledigt. Voraussetzungen und Grenzen:
[Wiederherstellung auf Linux](SERVER_LINUX.md#alte-parallele-exporte-mit-fehlerseite-zurückstellen).

## Ziel und Datenstand

Zielordner: C:\Users\jowals001\awi\solve\dummy_data\fgg_data.
Die zwölf Themenauswahlen zeigten am 08.09.2026 zusammen 8.316.642 Messwerte.
Das ist die Summe der Themenzählungen, kein Nachweis über die Anzahl global
einzigartiger Messungen. Der Stationskatalog zeigt 522 Einträge an, seine Auswahl
bietet jedoch nur 521 benannte Stationen. Diese Differenz wird gemeldet und
verhindert eine unberechtigte Vollständigkeitsmeldung, nicht den übrigen Abruf.

Der aktuelle, tatsächlich heruntergeladene Umfang steht in **fortschritt.txt**,
**fortschritt.json** und **dateiuebersicht.csv** im Zielordner. Die Existenz eines
Themenordners bedeutet noch nicht, dass dieses Thema vollständig vorliegt.
Nur die JSON-Angabe **all_complete: true** kennzeichnet einen vollständig
abgeglichenen Lauf.

## Starten und wiederaufnehmen

In diesem Skriptverzeichnis:

    python downloader.py resume --accept-terms

Der Befehl startet einen neuen Lauf oder setzt den bestehenden fort. Er entfernt
ausschließlich die eigene STOP-Markierung. Eine exklusive Sperre verhindert
einen zweiten Worker für denselben Zielordner.

Im Hintergrund ohne zusätzliches Fenster:

    .\start_download.ps1 -AcceptTerms

Der PowerShell-Starter verwendet standardmäßig die bei der Erstellung verfügbare
Python-Laufzeit. Mit -PythonExecutable kann eine andere Python-Datei angegeben
werden. Er gibt Prozess-ID sowie die Pfade seiner Ausgabelogs zurück.

Die Zustimmung gilt für die [Nutzungsbedingungen des Portals](https://www.elbe-datenportal.de/FisFggElbe/content/statisch/nutzungsbedingungen.jsp).
Sie wurde vom Nutzer für diesen Gesamtabruf ausdrücklich erteilt. Der Code
behandelt dies nicht als Erlaubnis zur Veröffentlichung oder Weitergabe.
Ändert sich der Inhalt der Bedingungen während eines fortgesetzten Laufs,
wird eine erneute Prüfung verlangt.

## Fortschritt ansehen

    python downloader.py progress

Dies liest den gespeicherten Zustand und zeigt Prozentwert, geprüfte Messwerte,
Dateizahl, Messstellen und offene Probleme. Alternativ **fortschritt.txt** öffnen.
Bei laufenden Exporten wird die Datei regelmäßig aktualisiert; bei geöffneten
Editoren kann ein erneutes Laden nötig sein.
Zusätzlich stehen dort die letzte Aktivität, die Wartezeit auf einen Export und
nach einem regulär protokollierten Stopp dessen Grund. Im Downloadlog erscheint
während der serverseitigen Berechnung ungefähr jede Minute eine Wartemeldung.
Eine alte Prozess-ID ist kein Beleg für einen noch laufenden Prozess.

Seit der Korrektur vom 09.09.2026 stoppen vorübergehend gesperrte Statusdateien
den Download nicht mehr. Für `fortschritt.txt`, `fortschritt.json` und
`dateiuebersicht.csv` werden Zugriffs-/Dateisperrfehler höchstens fünfmal mit
insgesamt 1,5 Sekunden Pause versucht. Bleibt die Datei gesperrt, bleibt die
bisherige Anzeige erhalten; der Download arbeitet weiter. Die Warnung steht in
SQLite unter `report_write_errors`, im Statusbefehl und, soweit schreibbar, in
der JSON-Anzeige. Das Fehlerlog meldet die erste Sperre und später ihre Aufhebung.
Beim nächsten Bericht wird erneut versucht, die Anzeige zu aktualisieren.
Messdaten, Abfragebelege und Wiederaufnahmestand werden dabei **nicht** als
optionale Dateien behandelt; andere Schreibfehler wie ein voller Datenträger
werden weiterhin nicht unterdrückt.

Der Prozentwert basiert auf heruntergeladenen und geprüften Messwerten geteilt
durch die Themenzählungen vom Beginn des Laufs. Er ist keine Zeitprognose:
Einzelne Exporte brauchen unterschiedlich lange, und die endgültige Anzahl
kleiner Teilaufträge steht erst während der Aufteilung fest.

Maschinenlesbarer Status:

    python downloader.py status

## Unterbrechen und fortsetzen

    python downloader.py stop

Oder im Vordergrund Strg+C. Der Client speichert zuerst die Warteadresse und
beendet sich beim nächsten sicheren Haltepunkt. Die Berechnung auf dem Portal
kann weiterlaufen; der lokale Stopp storniert sie nicht. Fertige Dateien bleiben erhalten. Danach:

    python downloader.py resume --accept-terms

Auch nach einem beendeten Python-Prozess werden geplante und fertige Aufträge
aus **_state/jobs.sqlite** wieder eingelesen. Ein Download mit bekanntem
Ergebnislink kann direkt fortgesetzt werden. Eine nur teilweise geschriebene
.part-Datei wird nicht als Erfolg gezählt.

Für einen Abbruch während der serverseitigen Berechnung speichert der Client
seine eigene öffentliche Gastsitzung und die vom Portal gelieferte Warteadresse.
Beim Wiederanlauf fragt er zuerst dort nach, ohne den Export erneut anzufordern.
Er verwendet keine Browserprofile oder Browser-Cookies.

Die Wartezeit pro Export und Prozess beträgt standardmäßig eine Stunde statt
15 Minuten. Sie ist konfigurierbar, zum Beispiel:

    python downloader.py resume --accept-terms --export-timeout 7200

Seit dem 11.09.2026 gilt dieser Wert sowohl als HTTP-Zeitgrenze für die **erste
Exportantwort** als auch, getrennt davon, als Zeitgrenze für anschließende
Ergebnisabfragen. Die beiden Phasen können zusammen länger dauern. Normale
Auswahl-/Downloadanfragen behalten 120 Sekunden pro HTTP-Aufruf. Manche Exporte
liefern keine Zwischenantwort und benötigen bereits für die erste Antwort mehr
als zwei Minuten. Währenddessen steht der Auftrag auf `submitting`, mit Startzeit
und Zeitgrenze in der Fortschrittsanzeige; es gibt dann noch keine Pollingmeldungen.
Ein angeforderter Stopp kann in dieser Phase erst nach Ende des HTTP-Aufrufs
bearbeitet werden. Ein Timeout wird niemals durch automatische Wiederholung des
Exportauftrags behandelt. Ein Ergebnislink wird sofort gespeichert: Die
Warteadresse ist kein dauerhaft wiederholbar abrufbarer Downloadlink.

**Grenze:** Ist die Gastsitzung abgelaufen oder brach die Verbindung ab, bevor
überhaupt eine Warteadresse zurückkam, ist die Serverausführung nicht sicher
rekonstruierbar. Dieser Auftrag wird als **uncertain** gemeldet. Er wird nicht
blind doppelt angefordert. Alle schon abgeschlossenen Downloads bleiben gültig.

Für einen ausdrücklich zurückgestellten Altauftrag gibt es `defer-uncertain`
mit exakter `--job-id`, `--evidence` (gesicherte Prüfung der nicht mehr verfügbaren
Auswahlseite) und `--reason`. Dies ist kein automatischer Resume-Schritt: Er ist
nur ohne Ergebnis-/Warteadresse und bei mindestens einer Stunde altem Auftrag
möglich. Der Auftrag erhält `deferred_uncertain`, behält Erwartungszahl und
Versuchszähler und bleibt ein offenes Vollständigkeitsproblem. Ein separater
Beleg dokumentiert den alten Zustand. Weitere Aufträge verwenden eine neue eigene
Gastsitzungsdatei; die alte wird nicht gelöscht. `retry-errors` greift solche
zurückgestellten Aufträge nicht auf.

Der Rechner muss für den Download eingeschaltet und mit dem Internet verbunden
sein. Ein Neustart des Rechners startet den Python-Prozess nicht automatisch;
danach erneut den Start-/Resume-Befehl ausführen. Für die zusätzliche Überwachung
in dieser Aufgabe muss auch die Desktop-App laufen.

## Ablage

    fgg_data/
      schadstoffe_wasserphase/
        2020/
          <job-id>/
            dbe_gast_<zeitstempel>.csv
            abfrage.json
            metadata.json
        2000-2007/
          ...
      hydrologie/
      fischfauna/
      ... weitere Themen ...
      messstellen.csv
      dateiuebersicht.csv
      fortschritt.txt
      fortschritt.json
      _state/
        jobs.sqlite
        download.log
        worker.json
        guest_session.cookies
        catalog/
        stations/
      _validation/
        pilot/

Mehrere kleine benachbarte Jahre werden zu einem Paket zusammengefasst, wenn
ihre Summe unter der Zielgröße liegt. Die zugehörigen Ordner tragen dann einen
Jahresbereich. **_validation/pilot** enthält einen gesonderten Entwicklungstest;
diese Datei gehört nicht zum Vollständigkeitszähler und kann sich mit späteren
Regelabrufen überschneiden. Beim Einlesen die Datenordner oder Dateiübersicht
verwenden, nicht wahllos alle CSV-Dateien einschließlich Index und Pilot.

**_state** muss für die Wiederaufnahme erhalten bleiben. Die gespeicherte
Gastsitzung ist ausschließlich lokaler Betriebszustand und sollte nicht geteilt
oder in Git eingecheckt werden.

## Aufteilung und Schutz vor Datenverlust

1. Alle verfügbaren Themen entdecken und deren Ausgangszählungen speichern.
2. Medium/Erfassungsart, Messwertart und Messvorgang nach tatsächlichen Optionen
   aufteilen. Nicht verfügbare Felder werden nicht erfunden.
3. Eine Station oder einen Parameter festlegen. Die Wahl der Aufteilungsrichtung
   richtet sich nach vollständigen Trefferzahlen und der geschätzten Dateizahl.
4. Verfügbare Jahre in disjunkte Bereiche aufteilen. Zielgröße: 8.000 Werte;
   harte Portalgrenze: 10.000.
5. Weiter eingrenzen, falls nötig. Die Trefferzahlen aller Teilmengen müssen
   zusammen die ursprüngliche Menge ergeben.
6. Einen Export zur Zeit anfordern, Ergebnislink abwarten, Originaldatei speichern,
   Datenzeilen zählen und Prüfsumme hinterlegen.

Parametergruppen werden nicht als unabhängige Mengen heruntergeladen, weil sie
sich überlappen können. Nicht verfügbare Monate, Tagesfilter oder Seiten-Offsets
werden nicht angenommen. Eine nicht vollständig aufteilbare Menge bleibt
sichtbar als offenes Problem stehen.

Fehler und zurückgestellte Aufträge werden nicht als Erfolg gezählt. Einzelne
Netzwerkaufrufe werden begrenzt mit Pausen wiederholt; drei aufeinanderfolgende
Netzwerkfehler stoppen den Lauf. Technische Netzwerkfehler können später mit
dem folgenden Befehl erneut eingeplant werden:

    python downloader.py retry-errors

Fachliche Abweichungen und unklare Serverexporte werden dabei nicht automatisch
freigegeben.

Einzelne abgeschlossene Exporte mit abweichenden Zeilenzahlen bleiben unter
`needs_review` erhalten. `resume` bearbeitet die anderen verfügbaren Aufträge
weiter; der Gesamtlauf wird dadurch nicht als vollständig ausgegeben.
Mit `refine --job-id <ID> --accept-terms` lässt sich eine solche Teilmenge nach
live geprüften Stations-/Parameterzählungen weiter aufteilen. Die Originaldatei
bleibt mit einer Prüfnotiz beim übergeordneten Auftrag erhalten und wird nicht
zusätzlich als erfolgreicher Teilauftrag gezählt. Dies löst keine beliebigen
Quellfehler und darf nicht zum Wegrechnen einer Differenz verwendet werden.

## Fortsetzung vom 11.09.2026

Der Export für **FB Klietznik / Tangermünde**, adulte Individuen, 2000–2024,
erwartete 615 Werte und blieb am 09.09.2026 ohne erste Antwort. Am 11.09. lieferte
die gespeicherte Auswahlseite nur eine Portal-Fehlerseite mit
`java.lang.NullPointerException`, ohne Ergebnislink oder Warteadresse. Die Antwort
wurde im Auftragsordner gesichert. Auf Wunsch des Nutzers wird der übrige Abruf
fortgesetzt, während dieser Auftrag ausdrücklich ungeklärt bleibt und nicht
erneut angefordert wird. Neue Exportanforderungen erhalten die längere Zeitgrenze
für ihre erste Antwort. 69 automatisierte Tests bestehen nach dieser Änderung.

## Korrektur vom 09.09.2026

- Der Lauf stoppte um 00:46 Uhr deutscher Zeit beim Ersetzen von
  `fortschritt.txt` mit Windows-Fehler 5. Der letzte Export war zuvor vollständig
  gespeichert; kein unklarer Serverexport war offen. Die konkrete Ursache der
  Zugriffssperre ist nicht nachgewiesen.
- Statusdateien werden jetzt unabhängig voneinander mit begrenzten Wiederholungen
  aktualisiert. Bleibt eine gesperrt, bleibt ihr alter Inhalt erhalten und eine
  Warnung wird gespeichert. Ein erfolgreicher späterer Schreibversuch entfernt
  die Warnung. An Originalexporten und ihrer Prüfung wurde nichts gelockert.
- 60 automatisierte Tests bestehen, darunter eine echte Windows-Dateisperre,
  anhaltende und kurzzeitige Sperren aller drei Statusdateien sowie die Prüfung,
  dass abgeschlossene Aufträge bei einem Anzeigefehler abgeschlossen bleiben.
- Vor dem Neustart wurden alle 322 akzeptierten Originaldateien erneut anhand
  ihrer Prüfsummen und Zeilenzahlen geprüft: 256.988 Messwerte. Die bestehenden
  neun Prüfprobleme wurden nicht automatisch freigegeben oder erneut angefordert.

## Befunde vom 08.09.2026

- Der spätere Stopp bei Phytoplankton, Magdeburg rechtes Ufer, ist behoben:
  Das Portal änderte beim Export den angefragten Zeitraum 2003–2006 auf 2003–2003,
  obwohl die Trefferzahl unverändert 155 blieb. Alle 155 Original-Datumswerte
  wurden anschließend geprüft und liegen in 2003. Die vorher ungespeicherte
  Ergebnisantwort war über die alte Sitzung nicht mehr verfügbar; für diesen
  bereits serverseitig abgeschlossenen, aber nicht übernommenen Auftrag wurde
  ein kontrollierter neuer Export erstellt und genau einmal gezählt.
- Der Downloader akzeptiert eine solche Eingrenzung nur bei gleicher Trefferzahl,
  unveränderten übrigen Filtern und gültigen Originaldaten innerhalb des engeren
  Zeitraums. Andere Stations-/Parameteränderungen, größere Zeiträume oder
  abweichende Zählungen werden weiterhin abgelehnt. Beide Zeiträume und die
  Datumsprüfung werden in `metadata.json` dokumentiert und bei `verify` erneut
  kontrolliert. Der Filter-Zwischenspeicher wird danach verworfen, damit die
  nächste Abfrage nicht versehentlich den engeren Zeitraum übernimmt.
- Ergebnislinks, zurückgegebene Filter und genaue Abweichungen werden nun vor
  einer Ablehnung als `export_response_<zeitpunkt>.json` gespeichert. Ursprüngliche
  Abfragen bleiben erhalten, neue Versuche erhalten separate Abfrageprotokolle.
  Nach dieser Korrektur bestehen 50 automatisierte Tests.
- Der wartende Hydrologieexport mit 7.670 Werten wurde ohne erneuten Exportauftrag
  abgeholt; alle 7.670 Datenzeilen sind geprüft und gespeichert.
- Beim Hydrologieexport 1978–2025 (Durchfluss, Intervallmessungen) zählt das Portal
  4.251 Werte, liefert jedoch 4.263 unterschiedliche Datensätze. Die Differenz
  sind zwölf Rosenburg-Werte aus 2025, die in der Jahresauswahl fehlen. Der
  Originalexport bleibt unverändert unter `needs_review`; Details stehen in
  seiner `count_audit.json`. Es werden keine Zeilen gelöscht und keine
  Erwartungszahlen nachträglich passend gemacht.
- Dasselbe Muster trat bei Lufttemperatur 2009–2025 auf: 2.579 statt 2.567
  Datenzeilen, ebenfalls zwölf zusätzliche Rosenburg-Werte aus 2025. Auch dieser
  Originalexport bleibt mit eigener `count_audit.json` unter `needs_review`.
- Im Stationskatalog stehen 521 auswählbare Stationen einer angezeigten Zielzahl
  von 522 gegenüber. Alle verfügbaren Einträge werden abgerufen, die Differenz
  bleibt als eigenes offenes Problem sichtbar. Eine Gegenprüfung aller 14
  Länderoptionen ergab ebenfalls nur 521 unterschiedliche Namen; die Abweichung
  liegt in Sachsen-Anhalt (90 angezeigt, 89 auswählbar). Das Ergebnis steht in
  `_state/catalog/country_count_audit.json`.
- 41 automatisierte Tests bestanden. Zusätzlich wurde ein Meteoexport mit 77
  Werten nach einem absichtlichen Prozessende von einem neuen Prozess exakt
  einmal übernommen. `live_resume_test.status` ist `passed`; die Zahl der
  Exportanforderungen blieb unverändert. Anschließend wurden alle elf bis dahin
  akzeptierten Originaldateien erneut geprüft und der unbegrenzte Lauf gestartet.

## CSV-Besonderheiten

- Die Originaldateien bleiben bytegenau erhalten. Kodierung und Prüfsumme stehen
  in ihrer metadata.json; nicht pauschal UTF-8 oder Latin-1 annehmen.
- Das Portal verwendet Semikolon, einfache Anführungszeichen und Dezimalkomma.
- Die optionale letzte Spalte „zusätzliche Informationen“ fehlt in manchen
  Datenzeilen. Das wird protokolliert, nicht in den Originaldateien aufgefüllt.
- Biota-Dateien enthalten zusätzliche Fisch-/Muscheldaten und teilweise
  nicht maskierte Apostrophe in Stoffnamen. Die Funktion parse_portal_rows in
  portal.py erhält diese inneren Apostrophe und meldet die Abweichung. Ein
  gewöhnlicher CSV-Parser kann hier scheitern oder Zeichen verlieren.
- Ein Wert wie < 0,005 bleibt ein Messwert mit Vergleichszeichen, nicht eine
  exakt gemessene Zahl und insbesondere nicht null.
- Biologische Erhebungen, Einzelproben und Tagesstatistiken werden nicht vermischt.
- Der Stationskatalog übernimmt Koordinaten unverändert. Das vollständige CRS
  muss vor einem räumlichen Abgleich bestätigt werden; es gibt noch kein
  automatisches Stationsmatching und keinen Graphimport.

## Prüfen

Lokale automatisierte Tests:

    python -m unittest -v

Alle erfolgreich gespeicherten Originalexporte erneut auf Prüfsumme und
Datensatzanzahl prüfen, wenn kein Worker läuft:

    python downloader.py verify

Kleine Live-Prüfung:

    python downloader.py samples --accept-terms

Sie ergänzt jeweils einen echten vollständigen Teilauftrag pro Themenbereich;
bereits vorhandene Themenbeispiele werden nicht erneut angefordert.

Ein Vollabruf kann viele Stunden bis Tage dauern. Der Downloader hält die
Last niedrig und speichert Zwischenstände. Serverausfälle, Quelldatenänderungen
und abgelaufene Gastsitzungen werden als offene Zustände behandelt und nicht
durch eine vermeintliche Erfolgsmeldung verdeckt.
