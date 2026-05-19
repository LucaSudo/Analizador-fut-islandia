import json

# Cargar los partidos
with open("datos/partidos_islandia_2024.json", "r", encoding="utf-8") as f:
    data = json.load(f)

partidos = data["response"]

# Ver todos los equipos disponibles
equipos = set()
for p in partidos:
    equipos.add(p["teams"]["home"]["name"])
    equipos.add(p["teams"]["away"]["name"])

print("=== EQUIPOS EN LA LIGA ===")
for e in sorted(equipos):
    print(f"  - {e}")

# Buscar partidos de tus equipos
mis_equipos = ["Breidablik", "Valur Reykjavik", "Fram Reykjavik", "Vestri"]

print("\n=== PARTIDOS DE TUS EQUIPOS ===")
for equipo in mis_equipos:
    encontrados = [
        p for p in partidos
        if p["teams"]["home"]["name"] == equipo or p["teams"]["away"]["name"] == equipo
    ]
    print(f"{equipo}: {len(encontrados)} partidos encontrados")
    print("\n=== TODOS LOS EQUIPOS ===")
for e in sorted(equipos):
    print(f"  - {e}")