import pandas as pd
import os

# Cargamos todas las temporadas
dfs = []
for archivo in os.listdir("datos"):
    if archivo.endswith(".csv"):
        df = pd.read_csv(f"datos/{archivo}", encoding="latin1")
        df["temporada"] = archivo.replace("islandia_", "").replace(".csv", "")
        dfs.append(df)

data = pd.concat(dfs, ignore_index=True)

# Ver las columnas disponibles
print("=== COLUMNAS DISPONIBLES ===")
print(data.columns.tolist())

# Ver todos los equipos
print("\n=== EQUIPOS EN LOS DATOS ===")
equipos = sorted(set(data["HomeTeam"].dropna().tolist()))
for e in equipos:
    print(f"  - {e}")

# Filtrar los equipos que seguís
equipos_interes = ["Breidablik", "Valur", "Fram"]
print("\n=== PARTIDOS DE TUS EQUIPOS ===")
for equipo in equipos_interes:
    partidos = data[(data["HomeTeam"] == equipo) | (data["AwayTeam"] == equipo)]
    print(f"{equipo}: {len(partidos)} partidos encontrados")