# Stats Colectivas — Diseño

**Fecha:** 2026-06-02  
**Estado:** Aprobado, pendiente implementación

---

## Objetivo

Mejorar la calidad del análisis del bot mediante dos mecanismos:

1. **Calibración de confianza** basada en track record histórico colectivo (anónimo, todos los usuarios)
2. **Diversificación de focos** en combinadas y análisis completo, incluyendo btts y 1x2 además de los focos numéricos actuales

---

## Contexto

### Problema actual
- `generar_contexto_memoria(user_id)` solo muestra el historial del usuario actual → el LLM pierde el aprendizaje colectivo
- `_STATS_COMBINADA` solo evalúa: corners, goles, tarjetas_amarillas, faltas, remates → sesgo sistemático hacia corners
- `calcular_1x2()` y `btts_score` ya se calculan en `hacer_analisis_completo` pero **no están disponibles en combinadas**

### Lo que ya existe
- `calcular_1x2(xg1, xg2)` → probabilidades local/empate/visitante (línea ~690)
- `btts_score` en `precomputar_stats_equipo()` → P(equipo anota) por partido (línea ~802)
- Confianza calculada por sigma en `calcular_lineas_y_confianza()` → "Alta/Media/Baja"
- Cache en Supabase (tabla `predicciones`) con columna `user_id`, `foco`, `prediccion`, `acerto`

---

## Arquitectura

```
Supabase (tabla predicciones, filas verificadas)
    ↓ al arrancar + cada 2h (background thread)
backend/stats_colectivas.py
    ↓ get_track_record(foco, liga, linea)
engine.py
    ├── _ajustar_confianza_por_track_record()   ← Python toca números
    ├── _calcular_picks_partido()               ← btts + 1x2 nuevos
    └── _generar_parrafos_python()              ← pasa track record al LLM
        ↓
LLM → escribe narrativa con track record, nunca inventa números
```

---

## Componente A — `backend/stats_colectivas.py` (nuevo)

### Caché
- Calculado al arrancar el servidor (llamado desde `initialize_engine()`)
- Refrescado cada 2 horas via background thread
- Almacenado en memoria (`_cache_stats: dict`, protegido con `threading.Lock`)

### Fuente de datos
Solo filas de `predicciones` donde `acerto IS NOT NULL` (verificadas).

### Granularidad con fallback en cascada

```
Nivel C: foco + liga + rango_linea   requiere ≥ 10 muestras
Nivel B: foco + liga                 requiere ≥ 10 muestras
Nivel A: foco                        requiere ≥  5 muestras
Sin datos: retorna None
```

### Buckets de línea por foco (numéricos)

| Foco | Buckets |
|------|---------|
| corners | 6.5, 7.5, 8.5, 9.5, 10.5, 11.5 |
| goles | 1.5, 2.5, 3.5, 4.5 |
| tarjetas_amarillas | 2.5, 3.5, 4.5, 5.5 |
| tarjetas_rojas | 0.5, 1.5 |
| remates | 8.5, 10.5, 12.5 |
| faltas | 18.5, 22.5, 26.5 |

Para focos con período (corners_1h, tarjetas_amarillas_2h, etc.) usar la mitad de los buckets del foco base.

### Buckets para focos categóricos

| Foco | Bucket = valor categórico |
|------|--------------------------|
| btts | "Sí" / "No" |
| 1x2 | "Local" / "Empate" / "Visitante" |
| doble_oportunidad | "1X" / "X2" / "12" |

Para categóricos el "nivel C" no usa rango de línea sino el valor predicho.

### API pública

```python
def get_track_record(foco: str, liga: str, linea: float | str | None) -> dict | None:
    """
    Retorna el track record más específico disponible, o None si no hay
    suficientes muestras en ningún nivel.
    
    Retorno:
    {
        "nivel": "C",                  # "A", "B" o "C"
        "foco": "corners",
        "liga": "Besta deild karla",   # None si nivel A
        "rango": "Over 9.5",           # None si nivel A o B
        "muestras": 14,
        "aciertos": 9,
        "tasa": 0.64,
    }
    """

def refresh_stats() -> None:
    """Recalcula y reemplaza el caché. Llamado al arrancar y cada 2h."""

def get_resumen_global() -> str:
    """
    Texto compacto con estadísticas globales para el system prompt.
    Ej: "Tasa global: 62% (148 pred). Mejor foco: goles 71%. Peor: faltas 48%."
    Solo incluye focos con ≥5 muestras.
    """
```

---

## Componente B — Ajuste de confianza en `engine.py`

### Nueva función `_ajustar_confianza_por_track_record(foco, liga, linea, confianza_actual)`

```
track_record = get_track_record(foco, liga, linea)
Si track_record es None → no tocar confianza

Reglas de ajuste:
  tasa < 0.45  → bajar un nivel (Alta → Media, Media → Baja, Baja → Baja)
  tasa > 0.70  → subir un nivel (Baja → Media, Media → Alta, Alta → Alta)
  0.45–0.70    → sin cambio
```

La función retorna `(confianza_ajustada, track_record | None)`.

### Integración en `_calcular_picks_partido()` y `hacer_analisis_completo()`

Después de calcular `conf` con `calcular_lineas_y_confianza()`, llamar a `_ajustar_confianza_por_track_record()`. El `track_record` resultante se adjunta al pick como campo `"track_record"`.

### Texto para el LLM (en `_generar_parrafos_python()`)

Si hay track record disponible, agregar al contexto:

```
TRACK RECORD COLECTIVO (foco: corners | liga: Besta deild karla | Over 9.5):
  14 predicciones verificadas → 9 aciertos (64%)
  Confianza ajustada: Media 🟡 (era Alta 🟢 por stats actuales)
```

Si no hay track record, no agregar nada (no contaminar el prompt con "sin datos").

---

## Componente C — btts y 1x2 en combinadas

### Nuevos candidatos en `_calcular_picks_partido()`

Los inputs para btts y 1x2 ya están disponibles en `_calcular_picks_partido()`:
- `btts_prob` = `prom1["btts_score"] * prom2["btts_score"]` (ya calculado en `precomputar_stats_equipo`)
- `xg1`, `xg2` para `calcular_1x2()` = `prom1.get("goles")`, `prom2.get("goles")` (promedio de goles anotados)

**btts:**
- Umbral para incluir como pick: `btts_prob ≥ 0.65`
- `linea_segura` = "Ambos anotan Sí" o "Ambos anotan No"
- `confianza` derivada de: Alta si prob ≥ 0.75, Media si ≥ 0.65
- Sujeto al mismo ajuste de track record que los demás

**1x2:**
- Incluir si el ganador más probable tiene `prob ≥ 0.55`
- `linea_segura` = "Local gana", "Visitante gana" o "Empate"
- `confianza` derivada de: Alta si prob ≥ 0.65, Media si ≥ 0.55

**doble_oportunidad:**
- Incluir si 1X, X2 o 12 tiene `prob ≥ 0.75`
- Calculado sumando dos probabilidades de `calcular_1x2()`
- `confianza` = Alta si ≥ 0.80, Media si ≥ 0.75

### Actualizar `_STAT_NOMBRE_ES`

Agregar entradas para btts, 1x2, doble_oportunidad para que `_formatear_combinada()` muestre nombres legibles.

---

## Resumen global en system prompt

`get_resumen_global()` se agrega al system prompt al arrancar (en `initialize_engine()`), por encima del historial personal del usuario. Ejemplo:

```
=== TRACK RECORD DEL SISTEMA ===
Tasa global: 62% (148 predicciones verificadas)
Por foco: goles 71% (32) | corners 65% (61) | tarjetas 54% (28) | faltas 48% (14)
Por liga: Besta deild karla 68% (47) | Premier League 60% (20)
```

Esto queda fijo en el system prompt y se actualiza al refrescar el caché.

---

## Archivos a modificar

| Archivo | Cambio |
|---------|--------|
| `backend/stats_colectivas.py` | **Nuevo** — módulo completo |
| `backend/engine.py` | `_calcular_picks_partido()`, `hacer_analisis_completo()`, `_generar_parrafos_python()`, `initialize_engine()`, `_STATS_COMBINADA`, `_STAT_NOMBRE_ES` |
| `backend/memory.py` | `generar_contexto_memoria()` — agregar resumen global antes del historial personal |

---

## Lo que NO cambia

- Python sigue siendo el único que toca números
- El LLM solo escribe narrativa
- La verificación de predicciones (`verificar_predicciones()`) sigue procesando todas sin filtro de usuario
- La lógica de scraping de SofaScore no se toca
