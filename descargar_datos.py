import requests
import os

temporadas = {
    "2022": "https://www.football-data.co.uk/mmz4281/2122/I1.csv",
    "2023": "https://www.football-data.co.uk/mmz4281/2223/I1.csv",
    "2024": "https://www.football-data.co.uk/mmz4281/2324/I1.csv",
}

os.makedirs("datos", exist_ok=True)

for temporada, url in temporadas.items():
    respuesta = requests.get(url)
    if respuesta.status_code == 200:
        with open(f"datos/islandia_{temporada}.csv", "wb") as f:
            f.write(respuesta.content)
        print(f"✅ Temporada {temporada} descargada")
    else:
        print(f"❌ Error en temporada {temporada}: {respuesta.status_code}")