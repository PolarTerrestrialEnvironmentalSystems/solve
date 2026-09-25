import pathlib
import geopandas as gpd
import pandas as pd
from neo4j import GraphDatabase

driver = GraphDatabase.driver("neo4j://127.0.0.1:7687", auth=("neo4j", "Concordia06"))
driver.verify_connectivity()
PATH_station = "C:\\Users\\jowals001\\awi\\solve\\dummy_data\\messstellen.shp"
PATH_fgg_station = "C:\\Users\\jowals001\\awi\\solve\\dummy_data2\\Schadstoffe in Wasserphase (Chemische Qualitätskomponenten)_Datenportal der FGG Elbe.csv"
def load_messtellen(pfad : str) -> gpd.GeoDataFrame:
    messtellen = gpd.read_file(pfad)

    messtellenauswahl = messtellen[["EU_CD_SM","NAME_STN","EU_CD_WB","geometry"]]
    return messtellenauswahl

def load_fgg(pfad :str) -> pd.DataFrame:
    fgg = pd.read_csv(pfad, sep=";",quotechar="'",encoding="utf-8-sig")
    fgg_auswahl = fgg[["Messstelle","Gewässer","Wasserkörper"]].drop_duplicates()
    return fgg_auswahl

messtelle = load_fgg(PATH_fgg_station)
m_name = messtelle.iloc[0]["Wasserkörper"]
print(m_name)

query = """
MATCH (s:MonitoringStationV2)-[r:MONITORS]-> (w:WaterBodyV2)
WHERE w.name = $waterbody_name
RETURN s.name AS station_name, w.name AS waterbody_name, s.station_key AS station_key, s.station_id AS station_id, w.water_body_id AS waterbody_id;
"""
parameters = {
    "waterbody_name": m_name
}
records, summary, key = driver.execute_query(query, parameters,database_="neo4j")
print(records[0].data())
print(len(records))
driver.close()