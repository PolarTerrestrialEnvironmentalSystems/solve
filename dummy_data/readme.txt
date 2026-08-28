Data description and key columns included

lakewaterbody.shp
- geographical extent of lake bodies
- current status of different monitoring groups
- key column: EU_CD_LW (EU_CD_LS)
- useful foreign key: RBD_CD

catchments.shp
- geographical extent of catchments
- key colum: DRAIN_CD
- useful foreign keys: EU_CD_WB, RBD_CD

ffh.shp
- geographical extent of FFH protected areas
- key column: EU_CD_PH
- useful foreign keys: RBD_CD

groundwaterbody.shp
- geographical extent of groundwater body
- current status of different monitoring groups
- key column: EU_CD_GB
- useful foreign keys: RBD_CD 

messstellen.shp
- location of monitoring sites
- key column: EU_CD_SM
- useful foreign keys: EU_CD_WB

planunits.shp
- geographical extent of administrative water planning units
- key column: PLANU_CD
- useful foreign keys: RBD_CD

recreation_areas.shp
- geographical extent of protected recreation areas
- key column: EU_CD_PR
- useful foreign keys: RBD_CD

waterprotection.shp
- geographical extent of water protection areas
- key column: EU_CD_PD
- useful foreign columns: RBD_CD

einzeldaten.csv
- bio monitoring data
- key column: NONE DEFINED
- useful foreign columns: EU_CD_LW

einzeldaten_abschnitte.csv
- describes exact sampling locations (maybe this table could be ignored for now)
- key column: NONE DEFINED
- useful foreign columns: EU_CD_LW

einzeldaten_chemie.csv
- chemical measurements
- key column: NONE DEFINED
- useful foreign columns: EU_CD_LW, ID_ABSCHNITT (?)

GW_measures.csv
- chemical measurements in groundwater
- key column: NONE DEFINED
- useful foreign columns: RBD_CD, EU_CD_GB

messstellen_bewertung.csv
- grading of measurements
- key column: NONE DEFINED
- useful foreign keys: EU_CD_LW

OW_measures.csv
- above ground water measurements
- key column: NONE DEFINED
- useful foreign keys: EU_CD_LS (is sometimes equivalent to EU_CD_LW)

protection_area_bewertung.csv
- protection area state
- key column: NONE DEFINED
- useful foreign keys: EU_CD_P, EU_CD_WB

stammdaten.csv
- base data for each lake
- key column: EU_CD_LW

wasserkoerper_bewertung.csv
- grading of water body state for different variables
- key column: NONE DEFINED
- useful foreign key: EU_CD_LW


