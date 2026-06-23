"""
buscar_ligas_ids.py — Resuelve y valida los uniqueTournament IDs de SofaScore.

Por qué existe: los IDs de LIGAS_CONFIG (fixture_loader.py) son uniqueTournament
de SofaScore. Si un ID está mal, esa liga carga datos equivocados o no carga.
Este script (corré con red) hace dos cosas:

  1) VALIDA los IDs ya configurados: pide el nombre real de cada uniqueTournament
     y lo compara con la clave de LIGAS_CONFIG. Marca los que no coinciden.
  2) RESUELVE nombres nuevos → id, buscándolos en SofaScore, e imprime un bloque
     listo para pegar en LIGAS_CONFIG.

Uso:
    python tools/buscar_ligas_ids.py            # valida + resuelve los pendientes
    python tools/buscar_ligas_ids.py "Liga MX" "Brasileirao"   # resuelve sólo esos

Respeta PROXY_URL del entorno (igual que el resto del proyecto).
"""

import os
import sys
import time

from curl_cffi import requests as cf_requests

# Nombres a resolver si no se pasan argumentos. Editá esta lista a gusto.
# La clave es el nombre tal como querés que figure en LIGAS_CONFIG; el valor
# es el texto de búsqueda en SofaScore (suele alcanzar con el nombre).
PENDIENTES = {
    "Liga MX":                      "Liga MX",
    "Primera División Chile":       "Primera Division Chile",
    "Primera A Colombia":           "Primera A Colombia",
    "Primera División Uruguay":     "Primera Division Uruguay",
    "LigaPro Ecuador":              "LigaPro Serie A Ecuador",
    "División Profesional Paraguay":"Primera Division Paraguay",
    "División Profesional Bolivia": "Primera Division Bolivia",
    "Primera División Venezuela":   "Liga FUTVE Venezuela",
}


def _sesion():
    s = cf_requests.Session(impersonate="chrome124")
    proxy = os.getenv("PROXY_URL", "")
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def nombre_de_id(sesion, tid: int) -> str | None:
    """Nombre real del uniqueTournament dado su id (para validar)."""
    try:
        r = sesion.get(f"https://www.sofascore.com/api/v1/unique-tournament/{tid}", timeout=15)
        if r.status_code != 200:
            return None
        return (r.json().get("uniqueTournament") or {}).get("name")
    except Exception as e:
        return f"<error: {e}>"


def buscar_id(sesion, query: str) -> list[tuple[int, str, str]]:
    """Devuelve [(id, nombre, pais)] de uniqueTournaments que matchean la búsqueda."""
    try:
        r = sesion.get(f"https://www.sofascore.com/api/v1/search/all?q={query}", timeout=15)
        if r.status_code != 200:
            return []
        out = []
        for res in r.json().get("results", []):
            if res.get("type") != "uniqueTournament":
                continue
            e = res.get("entity", {})
            out.append((e.get("id"), e.get("name", "?"),
                        (e.get("category", {}) or {}).get("name", "?")))
        return out
    except Exception:
        return []


def _cargar_config_actual() -> dict:
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(here))
    try:
        from fixture_loader import LIGAS_CONFIG
        return dict(LIGAS_CONFIG)
    except Exception as e:
        print(f"(no se pudo importar LIGAS_CONFIG: {e})")
        return {}


def main():
    sesion = _sesion()
    args = sys.argv[1:]

    if not args:
        # ── 1) Validar IDs ya configurados ──────────────────────────────
        print("=== Validando IDs configurados ===")
        for nombre, tid in _cargar_config_actual().items():
            real = nombre_de_id(sesion, tid)
            ok = real and nombre.lower().split()[0] in (real or "").lower()
            flag = "OK " if ok else "⚠️ "
            print(f"  {flag}{nombre} (id={tid}) → SofaScore dice: {real}")
            time.sleep(0.2)
        print()

    # ── 2) Resolver pendientes ──────────────────────────────────────────
    objetivos = {a: a for a in args} if args else PENDIENTES
    print("=== Resolviendo nombres → id (pegá esto en LIGAS_CONFIG) ===")
    for nombre, query in objetivos.items():
        candidatos = buscar_id(sesion, query)
        if not candidatos:
            print(f'    # "{nombre}": ???,   # sin resultados para "{query}"')
        else:
            tid, real, pais = candidatos[0]
            print(f'    "{nombre}": {tid},   # {real} ({pais})')
            for tid2, real2, pais2 in candidatos[1:3]:
                print(f'    #   alt: {tid2} → {real2} ({pais2})')
        time.sleep(0.3)


if __name__ == "__main__":
    main()
