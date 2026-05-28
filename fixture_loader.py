from curl_cffi import requests as cf_requests
from datetime import datetime, date, timedelta

LIGAS_CONFIG = {
    "Besta deild karla": 188,
    "1. deild karla": 675,
    "La Liga": 8,
    "Premier League": 17,
    "Serie A": 23,
    "Bundesliga": 35,
    "Ligue 1": 34,
    "Champions League": 7,
    "Liga Argentina": 406,
    "Copa Libertadores": 384,
    "Copa Sudamericana": 480,
    "Saudi Pro League": 955,
}
LIGAS = {}

def _nueva_sesion():
    return cf_requests.Session(impersonate="chrome124")

def fetch_api(sesion, url):
    return sesion.get(url, timeout=15).json()

def obtener_temporadas_actuales(sesion):
    ligas = {}
    for nombre, liga_id in LIGAS_CONFIG.items():
        try:
            data = fetch_api(sesion, f"https://www.sofascore.com/api/v1/unique-tournament/{liga_id}/seasons")
            temporadas = data.get("seasons", [])
            if temporadas:
                temporada_id = temporadas[0]["id"]
                rounds_data = fetch_api(sesion, f"https://www.sofascore.com/api/v1/unique-tournament/{liga_id}/season/{temporada_id}/rounds")
                rondas = rounds_data.get("rounds", [])
                ronda_actual = (
                    rounds_data.get("currentRound", {}).get("round")
                    or (rondas[-1].get("round", 1) if rondas else 1)
                )
                ligas[nombre] = {
                    "id": liga_id,
                    "temporada": temporada_id,
                    "rondas": ronda_actual
                }
        except:
            pass
    global LIGAS
    LIGAS = ligas
    return ligas

def cargar_proximos_partidos():
    contexto = "=== PRÓXIMOS PARTIDOS POR LIGA ===\n"
    ahora = datetime.now().timestamp()
    inicio_hoy = datetime.combine(date.today(), datetime.min.time()).timestamp()

    sesion = _nueva_sesion()

    global LIGAS
    LIGAS = obtener_temporadas_actuales(sesion)

    # ── Paso extra: buscar por fecha para capturar fases de grupos ──────────
    id_a_nombre = {v: k for k, v in LIGAS_CONFIG.items()}
    partidos_por_fecha: dict[str, list] = {}
    for delta in range(5):
        fecha_str_api = (date.today() + timedelta(days=delta)).strftime("%Y-%m-%d")
        try:
            resp_fecha = fetch_api(
                sesion,
                f"https://www.sofascore.com/api/v1/sport/football/scheduled-events/{fecha_str_api}"
            )
            for evento in resp_fecha.get("events", []):
                torneo_id = (evento.get("tournament", {})
                                   .get("uniqueTournament", {})
                                   .get("id"))
                if torneo_id in id_a_nombre:
                    nombre = id_a_nombre[torneo_id]
                    partidos_por_fecha.setdefault(nombre, []).append(evento)
        except:
            pass

    for nombre_liga, datos in LIGAS.items():
        try:
            candidatos = []
            base = (f"https://www.sofascore.com/api/v1/unique-tournament"
                    f"/{datos['id']}/season/{datos['temporada']}/events")
            for endpoint in ["last/0", "next/0"]:
                try:
                    resp = fetch_api(sesion, f"{base}/{endpoint}")
                    candidatos.extend(resp.get("events", []))
                except:
                    pass

            candidatos.extend(partidos_por_fecha.get(nombre_liga, []))

            vistos = set()
            eventos = []
            for e in sorted(candidatos, key=lambda x: x.get("startTimestamp", 0)):
                eid  = e["id"]
                tipo = e.get("status", {}).get("type", "")
                ts   = e.get("startTimestamp", 0)
                es_hoy    = ts >= inicio_hoy and ts < inicio_hoy + 86400
                es_futuro = (
                    tipo == "inprogress"
                    or (tipo == "notstarted" and ts > ahora)
                    or (tipo == "notstarted" and es_hoy)
                )
                if es_futuro and eid not in vistos:
                    vistos.add(eid)
                    eventos.append(e)

            if eventos:
                contexto += f"\n{nombre_liga}:\n"
                hoy_str = date.today().strftime("%d/%m/%Y")
                for e in eventos:
                    home = e["homeTeam"]["name"]
                    away = e["awayTeam"]["name"]
                    ts_e = e.get("startTimestamp", "")
                    tipo_e = e.get("status", {}).get("type", "")
                    if ts_e:
                        fecha_str = datetime.fromtimestamp(ts_e).strftime("%d/%m/%Y %H:%M")
                        if fecha_str.startswith(hoy_str):
                            fecha_str += " [HOY]"
                    else:
                        fecha_str = "por confirmar"
                    estado_str = " [EN CURSO]" if tipo_e == "inprogress" else ""
                    contexto += f"  - {home} vs {away} ({fecha_str}{estado_str})\n"
        except:
            pass

    return contexto
