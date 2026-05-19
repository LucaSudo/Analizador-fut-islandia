import json
import os
from datetime import datetime

ARCHIVO_MEMORIA = "memoria.json"

def cargar_memoria():
    if os.path.exists(ARCHIVO_MEMORIA):
        with open(ARCHIVO_MEMORIA, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "predicciones": [],
        "notas_equipos": {},
        "conversaciones_destacadas": []
    }

def guardar_memoria(memoria):
    with open(ARCHIVO_MEMORIA, "w", encoding="utf-8") as f:
        json.dump(memoria, f, indent=2, ensure_ascii=False)

def guardar_prediccion(equipo1, equipo2, foco, prediccion):
    memoria = cargar_memoria()
    memoria["predicciones"].append({
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "equipo1": equipo1,
        "equipo2": equipo2,
        "foco": foco,
        "prediccion": prediccion,
        "resultado_real": None,
        "acerto": None
    })
    guardar_memoria(memoria)

def agregar_nota_equipo(equipo, nota):
    memoria = cargar_memoria()
    if equipo not in memoria["notas_equipos"]:
        memoria["notas_equipos"][equipo] = []
    memoria["notas_equipos"][equipo].append({
        "fecha": datetime.now().strftime("%Y-%m-%d"),
        "nota": nota
    })
    guardar_memoria(memoria)

def generar_contexto_memoria():
    memoria = cargar_memoria()
    contexto = ""

    if memoria["predicciones"]:
        contexto += "=== PREDICCIONES ANTERIORES ===\n"
        for p in memoria["predicciones"][-10:]:
            contexto += f"- {p['fecha']}: {p['equipo1']} vs {p['equipo2']} (foco: {p['foco']})\n"
            contexto += f"  Predicción: {p['prediccion'][:150]}...\n"
            if p["resultado_real"]:
                contexto += f"  Resultado real: {p['resultado_real']} | Acertó: {p['acerto']}\n"

    if memoria["notas_equipos"]:
        contexto += "\n=== NOTAS DE EQUIPOS ===\n"
        for equipo, notas in memoria["notas_equipos"].items():
            contexto += f"{equipo}:\n"
            for n in notas[-3:]:
                contexto += f"  - {n['nota']}\n"

    return contexto