import wikipedia
import os
import json

from langchain_openai import ChatOpenAI
from langchain_community.document_loaders import TextLoader
from langchain_openai import OpenAIEmbeddings
from langchain_classic.indexes import VectorstoreIndexCreator

from urllib.request import urlretrieve
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from bs4 import BeautifulSoup
from urllib.parse import quote


token = os.getenv("BLABLADOR_KEY")
print(token)
os.environ["OPENAI_API_KEY"] = token
os.environ["OPENAI_API_BASE"] = "https://api.blablador.fz-juelich.de/v1"
embedding = OpenAIEmbeddings(model = "text-embedding-ada-002")

wikipedia.set_lang("de")
list_see = [
    "Adolf-Mittag-See",
    "Angersdorfer Teiche",
    "Arendsee (See)",
    "Ascherslebener See",
    "Barleber See I",
    "Barleber See II",
    "Bauerngraben",
    "Bergwitzsee",
    "Bindersee",
    "Blauer See (Hüttenrode)",
    "Blaues Auge (Bad Schmiedeberg)",
    "Bruchsee (Halle)",
    "Concordiasee (Seeland)",
    "Cösitzer Teich",
    "Der Schwarze Krüger",
    "Edersee (Plötzky)",
    "Friedrichsbad",
    "Fuchsbusch",
    "Geiseltalsee",
    "Giselasee",
    "Gremminer See",
    "Großer Goitzschesee",
    "Gröberner See",
    "Hohenweidener See",
    "Hufeisensee",
    "Hungersee (Breitungen)",
    "Kernersee",
    "Kiesgrube Adria",
    "Kirchteich (Halle)",
    "Kolumbussee",
    "Königsee (Plötzky)",
    "Kuhlenhagen",
    "Kulk (Gommern)",
    "Lappwaldsee",
    "Löderburger See",
    "Mondsee (Sachsen-Anhalt)",
    "Neustädter See I (Sachsen-Anhalt)",
    "Osendorfer See",
    "Paupitzscher See",
    "Randauer Baggerloch",
    "Rappbodetalsperre",
    "Raßnitzer See",
    "Rattmannsdorfer See",
    "Rohrteich",
    "Runstedter See",
    "Rüsternpfuhl",
    "Salbker See I",
    "Salbker See II",
    "Salziger See",
    "Schönitzer See",
    "Schwarzkopfkolk",
    "Seelhausener See",
    "Silbersee (Calvörde)",
    "Sonnensee (Magdeburg)",
    "Steinbruchsee Halle-Neustadt",
    "Sternsee",
    "Süßer See",
    "Wallendorfer See",
    "Wörlitzer See",
]
gewaesser = [
    # Flüsse, Bäche und zugehörige Gräben
    "Elbe",
    "Aland",
    "Biese",
    "Milde",
    "Uchte",
    "Augraben",
    "Havel",
    "Königsgraben",
    "Tanger",
    "Ihle",
    "Ehle",
    "Roter Graben",
    "Münchenbach",
    "Bache",
    "Ohre",
    "Schrote",
    "Siegrenne",
    "Faule Renne",
    "Große Sülze",
    "Klinke",
    "Eulegraben",
    "Nuthe (Elbe)",
    "Saale",
    "Bode",
    "Selke",
    "Holtemme",
    "Kalte Bode",
    "Warme Bode",
    "Hassel",
    "Brummeckebach",
    "Sellegraben",
    "Murmelbach",
    "Hagenbach",
    "Sautal",
    "Fuhne",
    "Wipper (Harz)",
    "Mühlgraben",
    "Eine",
    "Wiebeck",
    "Leine",
    "Schwennecke",
    "Mukarehne",
    "Langetalbach",
    "Rote Welle",
    "Walbke",
    "Hadeborn",
    "Stockbach",
    "Schlenze",
    "Fleischbach",
    "Lobach",
    "Rüsterbach",
    "Grift",
    "Salza",
    "Laweke",
    "Würde",
    "Weida/Querne",
    "Böse Sieben",
    "Götsche",
    "Weiße Elster",
    "Gerwische",
    "Reide",
    "Geisel",
    "Unstrut",
    "Helme",
    "Rohne",
    "Westerbach",
    "Gonna",
    "Ungeheurer Graben",
    "Botzemannsgraben",
    "Heimbach",
    "Riestedter Bach",
    "Thyra",
    "Mulde",
    "Pelze",
    "Schwarze Elster",
    "Jeetze",
    "Dumme",
    "Parnitz",
    "Steinbach",
    "Karower Landgraben",
    "Fiener Hauptvorfluter",
    "Ilse",
    "Oker",
    "Aller",
    "Weser",
    "Zillierbach",
    "Rödelbachgraben",
    "Büschengraben",
    "Teufelsgrundbach",
    "Birnbaumbach",
    "Schmale Wipper",
    "Querne",

    # Talsperren und Vorsperren
    "Talsperre Kelbra",
    "Rappbodetalsperre",
    "Talsperre Wendefurth",
    "Talsperre Wippra",
    "Talsperre Königshütte",
    "Hasselvorsperre",
    "Rappbodevorsperre",
    "Talsperre Zillierbach",
    "Frankenteich",
    "Talsperre Kiliansteich",
    "Oberer Kiliansteich",
    "Teufelsteich",
    "Birnbaumteich",
    "Gondelteich",
    "Großer Siebersteinteich",
    "Bremer Teich",
    "Kunstteich Neudorf",
    "Kunstteich Ballenstedt",
    "Fürstenteich",
    "Neuer Teich",
    "Kleiner Siebersteinteich",
    "Bergrat-Müller-Teich",
    "Erichsburger Teich",

    # Tagebaurestseen
    "Geiseltalsee",
    "Großer Goitzschesee",
    "Muldestausee",
    "Concordiasee",
    "Raßnitzer See",
    "Wallendorfer See",
    "Gremminer See",
    "Gröberner See",
    "Barleber See",
    "Paupitzscher See",
    "Hufeisensee",
    "Neustädter See",
    "Posthornteiche",
    "Heidesee",
    "Rattmannsdorfer See",
    "Hohenweidener See",
    "Osendorfer See",
    "Angersdorfer Teiche",
    "Runstedter See",
    "Bergwitzsee",
    "Kiesgrube Adria",
    "Kiessee Gerwisch",

    # Speicher und Rückhaltebecken
    "Speicher Wettelrode",
    "Speicher Schmon",
    "Hochwasserschutzbecken Kalte Bode",
    "Rückhaltebecken Stöbnitz",
    "Rückhaltebecken Schrote",
    "Rückhaltebecken Gleinaer Grund",
    "Hochwasserrückhaltebecken Querfurt",

    # Natürliche Seen und Moore
    "Arendsee",
    "Süßer See",
    "Schönitzer See",
    "Bindersee",
    "Kernersee",
    "Schönfeld-Kamerner See",
    "Niegripper See",
    "Salziger See",
    "Crassensee",
    "Rehsener See",
    "Friedrichsbad",

    # Teiche und sonstige Gewässer
    "Gotthardteich",
    "Grenzteich",
    "Lausiger Teiche",
    "Maliniusteich",
    "Mensingteich",
    "Möllerteich",
    "Mühlenteich",
    "Neudorfer Gemeindeteich",
    "Neudorfer kleiner Teich 1",
    "Neudorfer kleiner Teich 2",

    # Kanäle und Kunstgräben
    "Elster-Saale-Kanal",
    "Mittellandkanal",
    "Mittelkanal",
    "Elbe-Havel-Kanal",
    "Gnevsdorfer Vorfluter",
    "Niegripper Verbindungskanal",
    "Pareyer Verbindungskanal",
    "Rothenseer Verbindungskanal",
    "Schindelbrücher Kunstgraben",
    "Siebengründer Graben",
]

ohne_artikel = {
    "Roter Graben",
    "Münchenbach",
    "Siegrenne",
    "Mühlgraben",
    "Schwennecke",
    "Mukarehne",
    "Langetalbach",
    "Lobach",
    "Westerbach",
    "Ungeheurer Graben",
    "Botzemannsgraben",
    "Heimbach",
    "Riestedter Bach",
    "Parnitz",
    "Karower Landgraben",
    "Fiener Hauptvorfluter",
    "Teufelsgrundbach",
    "Gondelteich",
    "Schmale Wipper",
    "Erichsburger Teich",
    "Kiessee Gerwisch",
    "Speicher Schmon",
    "Rückhaltebecken Stöbnitz",
    "Rückhaltebecken Gleinaer Grund",
    "Schönfeld-Kamerner See",
}

gewaesser = [
    name for name in gewaesser
    if name not in ohne_artikel
]
ergebnisse = []
for see in gewaesser:
    url = f"https://de.wikipedia.org/wiki/" + quote(see.replace(" ", "_"),safe="")
    request = Request(
        url,
        headers={"User-Agent": "SOLVE-WikipediaParser/1.0"}
    )
    try:
        with urlopen(request) as response:
            soup = BeautifulSoup(response.read(), "html.parser")
    except HTTPError as error:
        continue
    article = soup.find(id="mw-content-text")

    if article is None:
        raise RuntimeError("Artikelbereich nicht gefunden")
    infobox = article.select_one("table.infobox")
    allgemeine_daten = []

    if infobox is not None:
        for zeile in infobox.find_all("tr"):
            if zeile.find_parent("table") is not infobox:
                continue

            zellen = zeile.find_all(["th", "td"], recursive=False)
            texte = [
                zelle.get_text(" ", strip=True)
                for zelle in zellen
            ]

            # Datenzeilen mit Bezeichnung und Wert
            if len(texte) >= 2 and texte[0]:
                allgemeine_daten.append({
                    "feld": texte[0],
                    "wert": " | ".join(texte[1:])
                })

    content = "\n\n".join(
        paragraph.get_text(" ", strip=True)
        for paragraph in article.find_all("p")
    )

    with open("file.txt", "w", encoding="utf-8") as f:
        f.write(content)

    loader = TextLoader("file.txt", encoding="utf-8")
    index = VectorstoreIndexCreator(
        embedding=embedding
    ).from_loaders([loader])

    llm = ChatOpenAI(model="alias-fast")
    question = """Ordne aus dem Text Gewässerrelevant Ereignisse einer Jahreszahl und falls möglich eine Quelle zu
    in einem json format, wo du year, event, source and lake auflistest
    Erfinde keine Ereignisse oder Quellen(url-link).
    Wenn keine passenden Ereignisse enthalten sind, antworte mit []
    Antworte ausschließlich mit einem gültigen JSON-Array.
    Verwende keine Markdown-Codeblöcke und keinen zusätzlichen Text.
            """

    data = index.query(question, llm=llm)
    ereignisse = json.loads(data)

    if not isinstance(ereignisse, list):
        raise ValueError(f"JSON-Antwort für {see} ist keine Liste")

        ergebnisse.append({
        "lake": see,
        "url": url,
        "allgemeine_daten": allgemeine_daten,
        "ereignisse": ereignisse
    })

    with open("wikipedia_lake.json", "w", encoding="utf-8") as file:
        json.dump(ergebnisse, file, ensure_ascii=False, indent=4)
