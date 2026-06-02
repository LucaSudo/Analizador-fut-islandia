"""
memory.py — Predicciones y notas de equipos, almacenadas en Supabase.

API pública (idéntica a la versión con JSON local):
  cargar_memoria()               → dict con estructura legacy
  guardar_prediccion(...)        → inserta/actualiza en Supabase
  agregar_nota_equipo(...)       → inserta en Supabase
  generar_contexto_memoria()     → texto para el system prompt
  verificar_predicciones(sesion) → verifica resultados en SofaScore
"""

import re
from datetime import datetime, timedelta

from supabase_client import db


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────

def _predicciones(user_id: str = None) -> list:
    """Trae predicciones ordenadas por fecha. Si user_id se provee, filtra por usuario."""
    try:
        q = db.table("predicciones").select("*").order("created_at")
        if user_id is not None:
            q = q.eq("user_id", user_id)
        res = q.execute()
        return res.data or []
    except Exception as e:
        print(f"⚠️  Supabase error (predicciones): {e}")
        return []


def _notas_equipos(user_id: str = None) -> dict:
    """Trae notas de equipos agrupadas por equipo. Si user_id se provee, filtra por usuario."""
    try:
        q = db.table("notas_equipos").select("*").order("created_at")
        if user_id is not None:
            q = q.eq("user_id", user_id)
        res = q.execute()
        agrupadas: dict = {}
        for row in (res.data or []):
            eq = row["equipo"]
            agrupadas.setdefault(eq, []).append({"fecha": row["fecha"], "nota": row["nota"]})
        return agrupadas
    except Exception as e:
        print(f"⚠️  Supabase error (notas_equipos): {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Compatibilidad legacy: cargar_memoria()
# ─────────────────────────────────────────────────────────────────────────────

def cargar_memoria(user_id: str = None) -> dict:
    """
    Devuelve la estructura legacy {predicciones, notas_equipos, conversaciones_destacadas}
    para código que la usa directamente.
    """
    preds = _predicciones(user_id)
    notas = _notas_equipos(user_id)
    return {
        "predicciones":              preds,
        "notas_equipos":             notas,
        "conversaciones_destacadas": [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Guardar predicción
# ─────────────────────────────────────────────────────────────────────────────

def guardar_prediccion(equipo1: str, equipo2: str, foco: str, prediccion: str,
                       evento_id=None, liga_id=None, temporada_id=None,
                       user_id: str = "default"):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Si hay evento_id y ya existe esa combinación evento+foco+user → actualizar
    if evento_id:
        try:
            res = (db.table("predicciones")
                     .select("id")
                     .eq("evento_id", evento_id)
                     .eq("foco", foco)
                     .eq("user_id", user_id)
                     .execute())
            if res.data:
                row_id = res.data[0]["id"]
                db.table("predicciones").update({
                    "prediccion": prediccion,
                    "fecha":      fecha,
                }).eq("id", row_id).execute()
                print(f"↩️  Predicción actualizada (evento {evento_id}, foco '{foco}')")
                return
        except Exception as e:
            print(f"⚠️  Supabase error al actualizar predicción: {e}")

    # Nueva predicción
    try:
        db.table("predicciones").insert({
            "fecha":          fecha,
            "equipo1":        equipo1,
            "equipo2":        equipo2,
            "foco":           foco,
            "prediccion":     prediccion,
            "evento_id":      evento_id,
            "liga_id":        liga_id,
            "temporada_id":   temporada_id,
            "resultado_real": None,
            "acerto":         None,
            "user_id":        user_id,
        }).execute()
    except Exception as e:
        print(f"⚠️  Supabase error al guardar predicción: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Notas de equipos
# ─────────────────────────────────────────────────────────────────────────────

def agregar_nota_equipo(equipo: str, nota: str, user_id: str = "default"):
    try:
        db.table("notas_equipos").insert({
            "equipo":  equipo,
            "fecha":   datetime.now().strftime("%Y-%m-%d"),
            "nota":    nota,
            "user_id": user_id,
        }).execute()
    except Exception as e:
        print(f"⚠️  Supabase error al guardar nota: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Extractor de predicción numérica (sin cambios)
# ─────────────────────────────────────────────────────────────────────────────

def _extraer_prediccion_numerica(texto):
    texto_lower = texto.lower()
    recomendacion = ""
    for linea in texto_lower.split("\n"):
        if "recomendación:" in linea or "recomendacion:" in linea:
            recomendacion = linea
            break

    fuentes = [recomendacion, texto_lower] if recomendacion else [texto_lower]
    for fuente in fuentes:
        m = re.search(r'over\s+(\d+(?:[.,]\d+)?)', fuente)
        if m:
            return ('over', float(m.group(1).replace(',', '.')))
        m = re.search(r'under\s+(\d+(?:[.,]\d+)?)', fuente)
        if m:
            return ('under', float(m.group(1).replace(',', '.')))
        m = re.search(r'más de\s+(\d+(?:[.,]\d+)?)', fuente)
        if m:
            return ('over', float(m.group(1).replace(',', '.')))
        m = re.search(r'menos de\s+(\d+(?:[.,]\d+)?)', fuente)
        if m:
            return ('under', float(m.group(1).replace(',', '.')))
        m = re.search(r'(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)', fuente)
        if m:
            a = float(m.group(1).replace(',', '.'))
            b = float(m.group(2).replace(',', '.'))
            return ('range', (min(a, b), max(a, b)))
        m = re.search(r'alrededor de\s+(\d+(?:[.,]\d+)?)', fuente)
        if m:
            v = float(m.group(1).replace(',', '.'))
            return ('range', (v - 1, v + 1))
        m = re.search(r'(\d+(?:[.,]\d+)?)', fuente)
        if m:
            return ('exact', float(m.group(1).replace(',', '.')))

    return (None, None)


def _acerto_numerico(tipo, valor_pred, valor_real):
    if tipo == 'over':   return valor_real > valor_pred
    if tipo == 'under':  return valor_real < valor_pred
    if tipo == 'range':  return valor_pred[0] <= valor_real <= valor_pred[1]
    if tipo == 'exact':  return abs(valor_real - valor_pred) <= 1
    return None


def _total_stat(stats_periodo, nombre):
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
    "goles":                    ("ALL",  None),
    "corners":                  ("ALL",  "Corner kicks"),
    "corners_1h":               ("1ST",  "Corner kicks"),
    "corners_2h":               ("2ND",  "Corner kicks"),
    "tarjetas":                 ("ALL",  "Yellow cards"),
    "tarjetas_amarillas":       ("ALL",  "Yellow cards"),
    "tarjetas_amarillas_1h":    ("1ST",  "Yellow cards"),
    "tarjetas_amarillas_2h":    ("2ND",  "Yellow cards"),
    "tarjetas_rojas":           ("ALL",  "Red cards"),
    "tarjetas_rojas_1h":        ("1ST",  "Red cards"),
    "tarjetas_rojas_2h":        ("2ND",  "Red cards"),
    "remates":                  ("ALL",  "Shots on target"),
    "remates_1h":               ("1ST",  "Shots on target"),
    "remates_2h":               ("2ND",  "Shots on target"),
    "faltas":                   ("ALL",  "Fouls"),
    "faltas_1h":                ("1ST",  "Fouls"),
    "faltas_2h":                ("2ND",  "Fouls"),
    "completo":                 (None,   None),
}


# ─────────────────────────────────────────────────────────────────────────────
# Lógica de acierto por foco
# ─────────────────────────────────────────────────────────────────────────────

def _determinar_acerto(pred_texto, foco, resultado):
    pred_lower = pred_texto.lower()
    stats      = resultado.get("stats", {})
    foco_key   = foco.lower().replace(" ", "_")

    if foco_key == "completo":
        ganador     = resultado.get("ganador")
        nombre_home = resultado.get("equipo_home", "").lower()
        nombre_away = resultado.get("equipo_away", "").lower()
        primer_home = nombre_home.split()[0] if nombre_home else ""
        primer_away = nombre_away.split()[0] if nombre_away else ""

        def menciona(nombre, primer):
            return nombre in pred_lower or (primer and primer in pred_lower)

        if ganador == "home":   return menciona(nombre_home, primer_home)
        elif ganador == "away": return menciona(nombre_away, primer_away)
        else: return "empate" in pred_lower or "draw" in pred_lower or "igualdad" in pred_lower

    if foco_key not in MAPA_FOCO:
        return None

    periodo, stat_nombre = MAPA_FOCO[foco_key]
    valor_real = (
        resultado.get("goles_home", 0) + resultado.get("goles_away", 0)
        if foco_key == "goles"
        else _total_stat(stats.get(periodo, {}), stat_nombre)
    )
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
    "Corner kicks", "Yellow cards", "Red cards",
    "Shots on target", "Total shots", "Fouls", "Ball possession",
]


def _buscar_evento_por_equipos(sesion, equipo1: str, equipo2: str, fecha_str: str) -> int | None:
    """
    #0n: Busca en SofaScore el evento_id de un partido terminado dado
    dos equipos y una fecha (DD/MM/YYYY o YYYY-MM-DD). Consulta los
    eventos del día y días adyacentes para tolerar diferencias de TZ.
    Retorna el evento_id si encuentra match único; None si ambiguo o no encontrado.
    """
    # Normalizar fecha a objeto date
    try:
        if "/" in fecha_str:
            fecha = datetime.strptime(fecha_str[:10], "%d/%m/%Y").date()
        else:
            fecha = datetime.strptime(fecha_str[:10], "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None

    eq1_l = equipo1.lower()
    eq2_l = equipo2.lower()

    # Buscar en el día exacto y el anterior/siguiente (tolerancia de TZ)
    candidatos = []
    for delta in (-1, 0, 1):
        dia = fecha + timedelta(days=delta)
        fecha_api = dia.strftime("%Y-%m-%d")
        try:
            data = sesion.get(
                f"https://www.sofascore.com/api/v1/sport/football/scheduled-events/{fecha_api}",
                timeout=15,
            ).json()
        except Exception:
            continue

        for ev in data.get("events", []):
            if ev.get("status", {}).get("type", "") != "finished":
                continue
            home = ev.get("homeTeam", {}).get("name", "").lower()
            away = ev.get("awayTeam", {}).get("name", "").lower()
            # Match flexible: el nombre guardado puede ser alias parcial
            eq1_match = eq1_l in home or eq1_l in away or home in eq1_l or away in eq1_l
            eq2_match = eq2_l in home or eq2_l in away or home in eq2_l or away in eq2_l
            if eq1_match and eq2_match:
                candidatos.append(ev["id"])

    if len(candidatos) == 1:
        return candidatos[0]
    if len(candidatos) > 1:
        # Ambiguo: dos partidos distintos con nombres similares en días adyacentes
        # Preferir el del día exacto
        try:
            fecha_api_exacta = fecha.strftime("%Y-%m-%d")
            data_exacta = sesion.get(
                f"https://www.sofascore.com/api/v1/sport/football/scheduled-events/{fecha_api_exacta}",
                timeout=15,
            ).json()
            ids_exactos = {ev["id"] for ev in data_exacta.get("events", [])
                           if ev.get("status", {}).get("type", "") == "finished"}
            overlap = [c for c in candidatos if c in ids_exactos]
            if len(overlap) == 1:
                return overlap[0]
        except Exception:
            pass
        print(f"  ⚠️  Búsqueda ambigua ({len(candidatos)} resultados) para {equipo1} vs {equipo2} ({fecha_str})")
    return None


def _obtener_stats_evento(sesion, evento_id: int) -> tuple[dict, dict]:
    """
    Devuelve (info_evento, stats_por_periodo) para un evento_id dado.
    info_evento: {equipo_home, equipo_away, goles_home, goles_away, ganador}
    """
    data_evento = sesion.get(
        f"https://www.sofascore.com/api/v1/event/{evento_id}",
        timeout=15,
    ).json()
    evento = data_evento.get("event", {})

    equipo_home = evento["homeTeam"]["name"]
    equipo_away = evento["awayTeam"]["name"]
    goles_home  = evento.get("homeScore", {}).get("current", 0)
    goles_away  = evento.get("awayScore", {}).get("current", 0)
    ganador     = "home" if goles_home > goles_away else ("away" if goles_away > goles_home else "draw")

    info = {
        "marcador":    f"{equipo_home} {goles_home} - {goles_away} {equipo_away}",
        "goles_home":  goles_home,
        "goles_away":  goles_away,
        "ganador":     ganador,
        "equipo_home": equipo_home,
        "equipo_away": equipo_away,
    }

    data_stats = sesion.get(
        f"https://www.sofascore.com/api/v1/event/{evento_id}/statistics",
        timeout=15,
    ).json()
    stats_por_periodo: dict = {}
    for grupo in data_stats.get("statistics", []):
        periodo = grupo["period"]
        stats_por_periodo.setdefault(periodo, {})
        for g in grupo["groups"]:
            for item in g["statisticsItems"]:
                if item["name"] in STATS_A_CAPTURAR:
                    stats_por_periodo[periodo][item["name"]] = {
                        "home": item.get("home", 0),
                        "away": item.get("away", 0),
                    }

    info["stats"] = stats_por_periodo
    return info


def verificar_predicciones(sesion):
    """
    #0n: Recorre TODAS las predicciones pendientes y verifica resultados:
    - Con evento_id → consulta directamente (flujo original).
    - Sin evento_id → busca el partido por (equipo1, equipo2, fecha) en
      SofaScore usando el endpoint de eventos por fecha.
    """
    preds = _predicciones()
    pendientes = [p for p in preds if p.get("resultado_real") is None]

    if not pendientes:
        print("  Sin predicciones nuevas para verificar.")
        return

    actualizadas = 0
    for pred in pendientes:
        evento_id = pred.get("evento_id")

        try:
            # ── Resolver evento_id si no lo tiene ───────────────────
            if not evento_id:
                fecha_pred = pred.get("fecha", "")  # "YYYY-MM-DD HH:MM"
                eid = _buscar_evento_por_equipos(
                    sesion,
                    pred.get("equipo1", ""),
                    pred.get("equipo2", ""),
                    fecha_pred,
                )
                if not eid:
                    # No se pudo resolver → dejar para más adelante
                    continue
                # Guardar evento_id para futuras verificaciones
                try:
                    db.table("predicciones").update(
                        {"evento_id": eid}
                    ).eq("id", pred["id"]).execute()
                except Exception:
                    pass
                evento_id = eid

            # ── Verificar si el evento terminó ──────────────────────
            status_data = sesion.get(
                f"https://www.sofascore.com/api/v1/event/{evento_id}",
                timeout=15,
            ).json()
            if status_data.get("event", {}).get("status", {}).get("type", "") != "finished":
                continue

            # ── Obtener datos y calcular acierto ────────────────────
            resultado = _obtener_stats_evento(sesion, evento_id)
            acerto    = _determinar_acerto(pred["prediccion"], pred["foco"], resultado)

            db.table("predicciones").update({
                "resultado_real": resultado,
                "acerto":         acerto,
                "evento_id":      evento_id,   # persist si vino del lookup
            }).eq("id", pred["id"]).execute()

            actualizadas += 1
            acerto_str = "✅ Acertó" if acerto else ("❌ Falló" if acerto is False else "⏳ Indeterminado")
            print(
                f"  {resultado['equipo_home']} {resultado['goles_home']}"
                f"-{resultado['goles_away']} {resultado['equipo_away']}"
                f" | foco='{pred['foco']}' | {acerto_str}"
            )

        except Exception as e:
            print(f"  ⚠️  Error pred id={pred.get('id')} evento={evento_id}: {e}")

    if actualizadas:
        print(f"\n✅ {actualizadas} predicción(es) verificada(s) en Supabase.")
    else:
        print("  Sin predicciones nuevas para verificar.")


# ─────────────────────────────────────────────────────────────────────────────
# Contexto de memoria para el system prompt
# ─────────────────────────────────────────────────────────────────────────────

def generar_contexto_memoria(user_id: str = None) -> str:
    preds = _predicciones(user_id)
    notas = _notas_equipos(user_id)
    contexto = ""

    if not preds:
        return contexto

    total       = len(preds)
    verificadas = [p for p in preds if p.get("acerto") is not None]
    acertadas   = [p for p in verificadas if p.get("acerto") is True]

    contexto += "=== HISTORIAL DE PREDICCIONES ===\n"
    if verificadas:
        tasa = round(len(acertadas) / len(verificadas) * 100)
        contexto += f"Total: {total} | Verificadas: {len(verificadas)} | Acertadas: {len(acertadas)} ({tasa}%)\n\n"
    else:
        contexto += f"Total: {total} | Sin verificar aún\n\n"

    focos_stats: dict = {}
    for p in verificadas:
        fk = p.get("foco", "desconocido").lower()
        focos_stats.setdefault(fk, {"aciertos": 0, "fallos": 0, "historial": []})
        if p["acerto"] is True:
            focos_stats[fk]["aciertos"] += 1
            focos_stats[fk]["historial"].append(True)
        elif p["acerto"] is False:
            focos_stats[fk]["fallos"] += 1
            focos_stats[fk]["historial"].append(False)

    if focos_stats:
        contexto += "=== PRECISIÓN POR TIPO DE APUESTA ===\n"
        for fk, s in sorted(focos_stats.items()):
            total_f = s["aciertos"] + s["fallos"]
            if total_f == 0:
                continue
            tasa_f   = round(s["aciertos"] / total_f * 100)
            semaforo = "🟢" if tasa_f >= 70 else ("🟡" if tasa_f >= 50 else "🔴")
            racha_str = ""
            ultimos = s["historial"][-3:]
            if len(ultimos) >= 2:
                if all(not x for x in ultimos):
                    racha_str = f" ⚠️ RACHA NEGATIVA ({len(ultimos)} fallos seguidos)"
                elif all(x for x in ultimos):
                    racha_str = f" 🔥 Racha positiva ({len(ultimos)} aciertos seguidos)"
            contexto += f"  {semaforo} {fk}: {s['aciertos']}/{total_f} ({tasa_f}%){racha_str}\n"
        contexto += "\n"

    contexto += "Últimas predicciones:\n"
    for p in preds[-10:]:
        contexto += f"- {p['fecha']}: {p['equipo1']} vs {p['equipo2']} (foco: {p['foco']})\n"
        contexto += f"  Predicción: {p['prediccion'][:150]}...\n"

        res = p.get("resultado_real")
        if res:
            acerto_str = ("✅ Acertó" if p["acerto"] is True
                          else ("❌ Falló" if p["acerto"] is False else "⏳ Indeterminado"))
            if isinstance(res, dict):
                marcador = res.get("marcador", "?")
                contexto += f"  Resultado: {marcador} | {acerto_str}\n"
                stats = res.get("stats", {})
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
                contexto += f"  Resultado: {res} | {acerto_str}\n"

    if notas:
        contexto += "\n=== NOTAS DE EQUIPOS ===\n"
        for equipo, ns in notas.items():
            contexto += f"{equipo}:\n"
            for n in ns[-3:]:
                contexto += f"  - {n['nota']}\n"

    if verificadas:
        focos_bajos = {
            fk: s for fk, s in focos_stats.items()
            if (s["aciertos"] + s["fallos"]) >= 2
            and s["aciertos"] / (s["aciertos"] + s["fallos"]) < 0.5
        }
        focos_buenos = {
            fk: s for fk, s in focos_stats.items()
            if (s["aciertos"] + s["fallos"]) >= 2
            and s["aciertos"] / (s["aciertos"] + s["fallos"]) >= 0.75
        }
        focos_racha_neg = {
            fk: s for fk, s in focos_stats.items()
            if len(s["historial"]) >= 3 and all(not x for x in s["historial"][-3:])
        }

        contexto += "\n=== INSTRUCCIONES DE CALIBRACIÓN (OBLIGATORIO) ===\n"
        contexto += "Antes de cada análisis, aplicá estas reglas basadas en tu historial real:\n\n"
        if focos_bajos:
            lista = ", ".join(sorted(focos_bajos.keys()))
            contexto += f"🔴 FOCOS CON BAJA PRECISIÓN (<50%): {lista}\n"
            contexto += "   → Bajá la confianza un escalón (Alta→Media, Media→Baja).\n"
            contexto += "   → Usá la línea segura más conservadora disponible.\n"
            contexto += "   → Decile al usuario que históricamente sos poco preciso en este tipo.\n\n"
        if focos_racha_neg:
            lista = ", ".join(sorted(focos_racha_neg.keys()))
            contexto += f"⚠️ RACHAS NEGATIVAS ACTIVAS (3+ fallos consecutivos): {lista}\n"
            contexto += "   → Advertí explícitamente: 'Mis últimas N predicciones de este tipo fallaron.'\n\n"
        if focos_buenos:
            lista = ", ".join(sorted(focos_buenos.keys()))
            contexto += f"🟢 FOCOS CON ALTA PRECISIÓN (≥75%): {lista}\n"
            contexto += "   → Podés mantener la confianza normal para estos focos.\n\n"
        contexto += "REGLAS GENERALES:\n"
        contexto += "1. Siempre mencioná tu precisión histórica para el foco actual.\n"
        contexto += "2. Si el foco tiene baja precisión, justificá por qué aun así hacés la recomendación.\n"
        contexto += "3. No inflés la confianza. Si los datos históricos indican poca precisión, sé honesto.\n"
        contexto += "4. Si no hay historial para el foco actual, indicá que es tu primera predicción de ese tipo.\n"

    return contexto
