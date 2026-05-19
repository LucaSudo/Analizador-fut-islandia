import requests
import json

API_KEY = "96b51f4c8ee37084a8e919636008e825"

headers = {
    "x-apisports-key": API_KEY
}

# Úrvalsdeild = liga 166, temporada 2025
# Usamos 2025 porque tiene mejor cobertura que 2026
url = "https://v3.football.api-sports.io/fixtures"
params = {
    "league": 164,
    "season": 2024
}

respuesta = requests.get(url, headers=headers, params=params)
data = respuesta.json()

# Guardar los datos
with open("datos/partidos_islandia_2024.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

# Ver cuántos partidos bajamos
total = len(data.get("response", []))
print(f"✅ {total} partidos descargados")

# Mostrar los primeros 3 como preview
for partido in data["response"][:3]:
    fixture = partido["fixture"]
    teams = partido["teams"]
    goals = partido["goals"]
    print(f"{teams['home']['name']} {goals['home']} - {goals['away']} {teams['away']['name']} ({fixture['date'][:10]})")