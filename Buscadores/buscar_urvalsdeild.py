import requests
import json

API_KEY = "96b51f4c8ee37084a8e919636008e825"

headers = {
    "x-apisports-key": API_KEY
}

respuesta = requests.get(
    "https://v3.football.api-sports.io/leagues",
    headers=headers,
    params={"country": "Iceland", "type": "League"}
)

data = respuesta.json()

for liga in data["response"]:
    print(f"ID: {liga['league']['id']} | Nombre: {liga['league']['name']}")