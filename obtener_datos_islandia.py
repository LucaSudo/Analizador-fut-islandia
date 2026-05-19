import requests
import json

API_KEY = "96b51f4c8ee37084a8e919636008e825"

headers = {
    "x-apisports-key": API_KEY
}

# Buscar la liga de Islandia
url = "https://v3.football.api-sports.io/leagues"
params = {"country": "Iceland"}

respuesta = requests.get(url, headers=headers, params=params)
data = respuesta.json()

print(json.dumps(data, indent=2))