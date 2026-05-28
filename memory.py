import json
import os
import re
from datetime import datetime

ARCHIVO_MEMORIA = "memoria.json"

# ─────────────────────────────────────────────────────────────────────────────
# Helpers de I/O
# ─────────────────────────────────────────────────────────────────────────────

def cargar_memoria():
    if os.path.exists(ARCHIVO_MEMORIA):
        with open(ARCHIVO_MEMORIA, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"predicciones": [], "notas_equipos": {}, "conversaciones_destacadas": []}


def guardar_memoria(memoria):
    with open(ARCHIVO_MEMORIA, "w", encoding="utf-8") as f:
        json.dump(memoria, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# Guardar predicción (con deduplicación)
# ─────────────────────────────────────────────────────────────────────────────

def guardar_prediccion(equipo1, equipo2, foco, prediccion,
                       evento_id=None, liga_id=None, temporada_id=None):
    memoria = cargar_memoria()

    # Si hay evento_id y ya existe esa combinación evento+foco → actualizar
    if evento_id:
        for pred in memoria["predicciones"]:
            if pred.get("evento_id") == evento_id and pred.get("foco") == foco:
                pred["prediccion"] = prediccion
                pred["fecha"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                guardar_memoria(memoria)
                print(f"↩️  Predicción actualizada (evento {evento_id}, foco '{foco}')")
                return

    memoria["predicciones"].append({
        "fecha":        datetime.now().strftime("%Y-%m-%d %H:%M"),
        "equipo1":      equipo1,
        "equipo2":      equipo2,
        "foco":         foco,
        "prediccion":   prediccion,
        "evento_id":    evento_id,
        "liga_id":      liga_id,
        "temporada_id": temporada_id,
        "resultado_real": None,
        "acerto":         None,
    })
    guardar_memoria(memoria)


# ─────────────────────────────────────────────────────────────────────────────
# Notas de equipos
# ─────────────────────────────────────────────────────────────────────────────

def agregar_nota_equipo(equipo, nota):
    memoria = cargar_memoria()
    if equipo not in memoria["notas_equipos"]:
        memoria["notas_equipos"][equipo] = []
    memoria["notas_equipos"][equipo].append({
        "fecha": datetime.now().strftime("%Y-%m-%d"),
        "nota":  nota,
    })
    guardar_memoria(memoria)


# ─────────────────────────────────────────────────────────────────────────────
# Extractor de predicción numérica
# ─────────────────────────────────────────────────────────────────────────────

def _extraer_prediccion_numerica(texto):
    """
    Analiza el texto de predicción y devuelve (tipo, valor) donde:
      tipo  = 'over' | 'under' | 'range' | 'exact' | None
      valor = float  para over/under/exact
              (float, float) para range
    Prioriza la línea "Recomendación:" si existe.
    """
    texto_lower = texto.lower()

    # Intentar extraer solo la línea de recomendación
    recomendacion = ""
    for linea in texto_lower.split("\n"):
        if "recomendación:" in linea or "recomendacion:" in linea:
            recomendacion = linea
            break

    # Buscar en recomendación primero; si no hay, en todo el texto
    fuentes = [recomendacion, texto_lower] if recomendacion else [texto_lower]

    for fuente in fuentes:
        # Over X.5 / over X
        m = re.search(r'over\s+(\d+(?:[.,]\d+)?)', fuente)
        if m:
            return ('over', float(m.group(1).replace(',', '.')))

        # Under X.5 / under X
        m = re.search(r'under\s+(\d+(?:[.,]\d+)?)', fuente)
        if m:
            return ('under', float(m.group(1).replace(',', '.')))

        # más de X
        m = re.search(r'más de\s+(\d+(?:[.,]\d+)?)', fuente)
        if m:
            return ('over', float(m.group(1).replace(',', '.')))

        # menos de X
        m = re.search(r'menos de\s+(\d+(?:[.,]\d+)?)', fuente)
        if m:
            return ('under', float(m.group(1).replace(',', '.')))

        # rango X-Y o "X a Y"
        m = re.search(r'(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)', fuente)
        if m:
            a = float(m.group(1).replace(',', '.'))
            b = float(m.group(2).replace(',', '.'))
            return ('range', (min(a, b), max(a, b)))

        # alrededor de X
        m = re.search(r'alrededor de\s+(\d+(?:[.,]\d+)?)', fuente)
        if m:
            v = float(m.group(1).replace(',', '.'))
            return ('range', (v - 1, v + 1))

        # número suelto con contexto
        m = re.search(r'(\d+(?:[.,]\d+)?)', fuente)
        if m:
            v = float(m.group(1).replace(',', '.'))
            return ('exact', v)

    return (None, None)


def _acerto_numerico(tipo, valor_pred, valor_real):
    """True/False según si valor_real cumple la predicción."""
    if tipo == 'over':
        return valor_real > valor_pred
    if tipo == 'under':
        return valor_real < valor_pred
    if tipo == 'range':
        return valor_pred[0] <= valor_real <= valor_pred[1]
    if tipo == 'exact':
        return abs(valor_real - valor_pred) <= 1  # tolerancia ±1
    return None


def _total_stat(stats_periodo, nombre):
    """Suma home + away de una stat en un período. Devuelve None si no existe."""
    if nombre not in stats_periodo:
        return None
    try:
        h = stats_periodo[nombre].get("home", 0) or 0
        a = stats_periodo[nombre].get("away", 0) or 0
        return int(str(h).replace('%', '')) + int(str(a).replace('%', ''))
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Mapa foco → (período, estadística SofaScore)
# ─────────────────────────────────────────────────────────────────────────────

MAPA_FOCO = {
    # Goles
    "goles":                    ("ALL",  None),           # especial: usa goles_home+away
    # Corners
    "corners":                  ("ALL",  "Corner kicks"),
    "corners_1h":               ("1ST",  "Corner kicks"),
    "corners_2h":               ("2ND",  "Corner kicks"),
    # Tarjetas amarillas
    "tarjetas":                 ("ALL",  "Yellow cards"),
    "tarjetas_amarillas":       ("ALL",  "Yellow cards"),
    "tarjetas_amarillas_1h":    ("1ST",  "Yellow cards"),
    "tarjetas_amarillas_2h":    ("2ND",  "Yellow cards"),
    # Tarjetas rojas
    "tarjetas_rojas":           ("ALL",  "Red cards"),
    "tarjetas_rojas_1h":        ("1ST",  "Red cards"),
    "tarjetas_rojas_2h":        ("2ND",  "Red cards"),
    # Remates al arco
    "remates":                  ("ALL",  "Shots on target"),
    "remates_1h":               ("1ST",  "Shots on target"),
    "remates_2h":               ("2ND",  "Shots on target"),
    # Faltas
    "faltas":                   ("ALL",  "Fouls"),
    "faltas_1h":                ("1ST",  "Fouls"),
    "faltas_2h":                ("2ND",  "Fouls"),
    # Análisis completo
    "completo":                 (None,   None),
}


# ─────────────────────────────────────────────────────────────────────────────
# Lógica de acierto por foco
# ─────────────────────────────────────────────────────────────────────────────

def _determinar_acerto(pred_texto, foco, resultado):
    """
    Devuelve True/False/None según si la predicción acertó.
    None = no se pudo determinar (foco desconocido o datos insuficientes).
    """
    pred_lower  = pred_texto.lower()
    stats       = resultado.get("stats", {})
    foco_key    = foco.lower().replace(" ", "_")

    # ── COMPLETO: acierta si menciona al ganador real ──────────────────────
    if foco_key == "completo":
        ganador      = resultado.get("ganador")
        nombre_home  = resultado.get("equipo_home", "").lower()
        nombre_away  = resultado.get("equipo_away", "").lower()
        # También chequear solo la primera palabra del nombre (ej. "Breidablik" de "Breidablik Kópavogur")
        primer_home  = nombre_home.split()[0] if nombre_home else ""
        primer_away  = nombre_away.split()[0] if nombre_away else ""

        def menciona(nombre, primer):
            return nombre in pred_lower or (primer and primer in pred_lower)

        if ganador == "home":
            return menciona(nombre_home, primer_home)
        elif ganador == "away":
            return menciona(nombre_away, primer_away)
        else:
            return "empate" in pred_lower or "draw" in pred_lower or "igualdad" in pred_lower

    # ── FOCOS NUMÉRICOS ─────────────────────────────────────────────────────
    if foco_key not in MAPA_FOCO:
        return None

    periodo, stat_nombre = MAPA_FOCO[foco_key]

    # Obtener valor real
    if foco_key == "goles":
        valor_real = resultado.get("goles_home", 0) + resultado.get("goles_away", 0)
    else:
        valor_real = _total_stat(stats.get(periodo, {}), stat_nombre)

    if valor_real is None:
        return None

    tipo, valor_pred = _extraer_prediccion_numerica(pred_lower)
    if tipo is None:
        return None

    return _acerto_numerico(tipo, valor_pred, valor_real)


# ─────────────────────────────────────────────────────────────────────────────
# Verificar predicciones al iniciar la app
# ─────────────────────────────────────────────────────────────────────────────

STATS_A_CAPTURAR = [
    "Corner kicks",
    "Yellow cards",
    "Red cards",
    "Shots on target",
    "Total shots",
    "Fouls",
    "Ball possession",
]


def verificar_predicciones(sesion):
    """
    Recorre todas las predicciones con evento_id pendientes de verificar,
    consulta SofaScore para obtener el resultado y estadísticas (ALL + 1ST + 2ND),
    y actualiza resultado_real y acerto en memoria.json.
    """
    memoria    = cargar_memoria()
    actualizadas = 0

    for pred in memoria["predicciones"]:
        if not pred.get("evento_id") or pred.get("resultado_real") is not None:
            continue

        evento_id = pred["evento_id"]
        try:
            # ── Datos del evento ──────────────────────────────────────────
            data_evento = sesion.get(
                f"https://www.sofascore.com/api/v1/event/{evento_id}",
                timeout=15
            ).json()
            evento = data_evento.get("event", {})
            if evento.get("status", {}).get("type", "") != "finished":
                continue

            equipo_home  = evento["homeTeam"]["name"]
            equipo_away  = evento["awayTeam"]["name"]
            goles_home   = evento.get("homeScore", {}).get("current", 0)
            goles_away   = evento.get("awayScore", {}).get("current", 0)

            if goles_home > goles_away:
                ganador = "home"
            elif goles_away > goles_home:
                ganador = "away"
            else:
                ganador = "draw"

            # ── Estadísticas por período ──────────────────────────────────
            data_stats = sesion.get(
                f"https://www.sofascore.com/api/v1/event/{evento_id}/statistics",
                timeout=15
            ).json()

            stats_por_periodo = {}   # {"ALL": {...}, "1ST": {...}, "2ND": {...}}
            for grupo in data_stats.get("statistics", []):
                periodo = grupo["period"]          # "ALL" | "1ST" | "2ND"
                if periodo not in stats_por_periodo:
                    stats_por_periodo[periodo] = {}
                for g in grupo["groups"]:
                    for item in g["statisticsItems"]:
                        nombre = item["name"]
                        if nombre in STATS_A_CAPTURAR:
                            stats_por_periodo[periodo][nombre] = {
                                "home": item.get("home", 0),
                                "away": item.get("away", 0),
                            }

            resultado = {
                "marcador":    f"{equipo_home} {goles_home} - {goles_away} {equipo_away}",
                "goles_home":  goles_home,
                "goles_away":  goles_away,
                "ganador":     ganador,
                "equipo_home": equipo_home,
                "equipo_away": equipo_away,
                "stats":       stats_por_periodo,
            }

            pred["resultado_real"] = resultado
            pred["acerto"]         = _determinar_acerto(pred["prediccion"], pred["foco"], resultado)
            actualizadas += 1

            acerto_str = "✅ Acertó" if pred["acerto"] else ("❌ Falló" if pred["acerto"] is False else "⏳ Indeterminado")
            print(f"  {equipo_home} {goles_home}-{goles_away} {equipo_away} | foco='{pred['foco']}' | {acerto_str}")

        except Exception as e:
            print(f"  ⚠️  Error evento {evento_id}: {e}")

    if actualizadas > 0:
        guardar_memoria(memoria)
        print(f"\n✅ {actualizadas} predicción(es) verificada(s) y guardada(s).")
    else:
        print("  Sin predicciones nuevas para verificar.")


# ─────────────────────────────────────────────────────────────────────────────
# Contexto de memoria para el system prompt
# ─────────────────────────────────────────────────────────────────────────────

def generar_contexto_memoria():
    memoria   = cargar_memoria()
    contexto  = ""

    if not memoria["predicciones"]:
        return contexto

    total      = len(memoria["predicciones"])
    verificadas = [p for p in memoria["predicciones"] if p.get("acerto") is not None]
    acertadas   = [p for p in verificadas if p.get("acerto")]

    if verificadas:
        tasa = round(len(acertadas) / len(verificadas) * 100)
        contexto += "=== HISTORIAL DE PREDICCIONES ===\n"
        contexto += f"Total: {total} | Verificadas: {len(verificadas)} | Acertadas: {len(acertadas)} ({tasa}%)\n\n"

    contexto += "Últimas predicciones:\n"
    for p in memoria["predicciones"][-10:]:
        contexto += f"- {p['fecha']}: {p['equipo1']} vs {p['equipo2']} (foco: {p['foco']})\n"
        contexto += f"  Predicción: {p['prediccion'][:150]}...\n"

        res = p.get("resultado_real")
        if res:
            acerto_str = "✅ Acertó" if p["acerto"] else ("❌ Falló" if p["acerto"] is False else "⏳ Indeterminado")

            if isinstance(res, dict):
                marcador = res.get("marcador", "?")
                contexto += f"  Resultado: {marcador} | {acerto_str}\n"
                stats = res.get("stats", {})

                # Mostrar resumen de stats ALL + por tiempo
                for periodo_label, periodo_key in [("Global", "ALL"), ("1T", "1ST"), ("2T", "2ND")]:
                    ps = stats.get(periodo_key, {})
                    if not ps:
                        continue
                    lineas = []
                    for stat_nombre, emoji in [
                        ("Corner kicks",    "🏁"),
                        ("Yellow cards",    "🟡"),
                        ("Red cards",       "🔴"),
                        ("Shots on target", "🎯"),
                        ("Fouls",           "⚠️"),
                    ]:
                        if stat_nombre in ps:
                            h = ps[stat_nombre].get("home", 0) or 0
                            a = ps[stat_nombre].get("away", 0) or 0
                            lineas.append(f"{emoji}{stat_nombre}: {int(h)+int(a)} ({h}-{a})")
                    if lineas:
                        contexto += f"    [{periodo_label}] " + " | ".join(lineas) + "\n"
            else:
                # Formato legado (string)
                contexto += f"  Resultado: {res} | {acerto_str}\n"

    if memoria.get("notas_equipos"):
        contexto += "\n=== NOTAS DE EQUIPOS ===\n"
        for equipo, notas in memoria["notas_equipos"].items():
            contexto += f"{equipo}:\n"
            for n in notas[-3:]:
                contexto += f"  - {n['nota']}\n"

    return contexto
