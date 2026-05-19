import json

with open("datos/partidos_islandia_2025.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# Ver info de la liga
if data["response"]:
    liga = data["response"][0]["league"]
    print(f"Liga: {liga['name']}")
    print(f"ID: {liga['id']}")
    print(f"Temporada: {liga['season']}")