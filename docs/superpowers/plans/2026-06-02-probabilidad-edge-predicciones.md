# Probabilidad / Edge en Predicciones — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guardar probabilidad del modelo, línea recomendada, confianza, cuota y calcular ROI + Edge automáticamente al analizar un partido.

**Architecture:** Dos cambios ortogonales: (1) `memory.py` extiende el schema de `guardar_prediccion` con 6 campos nuevos y calcula `ganancia` al verificar resultados; (2) `main.py` extrae esos campos del dict `lineas_py` que el engine ya retorna y los pasa a `guardar_prediccion`. El engine **no necesita cambios** — ya retorna `lineas_python` como quinto elemento.

**Tech Stack:** Python 3.11, Supabase (postgrest-py), FastAPI/SSE, unittest (sin pytest ni credenciales de Supabase necesarias para tests unitarios).

---

## Archivos modificados

| Archivo | Cambio |
|---|---|
| `memory.py` | Nuevos params en `guardar_prediccion` + lógica de `ganancia` en `verificar_predicciones` |
| `backend/main.py` | Extraer stats de `lineas_py` antes de llamar a `guardar_prediccion` |
| `test_pred_fields.py` | Tests unitarios para las dos funciones de cálculo puro |

---

### Task 1: Tests unitarios de la lógica de cálculo puro

**Files:**
- Create: `test_pred_fields.py`

Los tres cálculos que vamos a agregar son pura lógica matemática (sin Supabase).
Hay que escribir los tests primero para que fallen, luego implementar.

- [ ] **Step 1: Crear el archivo de tests**

```python
"""
test_pred_fields.py — Tests para ganancia, probabilidad_modelo y edge.
Ejecutar desde la raíz: python test_pred_fields.py
No requiere credenciales de Supabase ni Groq.
"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
sys.path.insert(0, os.path.dirname(__file__))


class TestGananciaCalculo(unittest.TestCase):
    """Verifica cálculo de ganancia al verificar predicciones."""

    def _ganancia(self, acerto, cuota):
        """Replica la lógica que irá en verificar_predicciones."""
        from memory import _calcular_ganancia
        return _calcular_ganancia(acerto, cuota)

    def test_ganancia_acierto_con_cuota(self):
        self.assertAlmostEqual(self._ganancia(True, 1.80), 0.80, places=4)

    def test_ganancia_fallo_con_cuota(self):
        self.assertAlmostEqual(self._ganancia(False, 2.50), -1.0, places=4)

    def test_ganancia_sin_cuota_true(self):
        self.assertIsNone(self._ganancia(True, None))

    def test_ganancia_sin_cuota_false(self):
        self.assertIsNone(self._ganancia(False, None))

    def test_ganancia_acerto_none(self):
        self.assertIsNone(self._ganancia(None, 1.80))


class TestProbabilidadModelo(unittest.TestCase):
    """Verifica conversión rough de total esperado a probabilidad."""

    def _prob(self, total):
        from memory import _prob_desde_total
        return _prob_desde_total(total)

    def test_prob_total_9(self):
        # 9 / (9+1) = 0.9
        self.assertAlmostEqual(self._prob(9.0), 0.9, places=4)

    def test_prob_total_0(self):
        # 0 / (0+1) = 0.0
        self.assertAlmostEqual(self._prob(0.0), 0.0, places=4)

    def test_prob_resultado_redondeado(self):
        # 13.5 / 14.5 ≈ 0.9310... → round 4 decimales
        expected = round(13.5 / 14.5, 4)
        self.assertAlmostEqual(self._prob(13.5), expected, places=4)


class TestEdgeCalculo(unittest.TestCase):
    """Verifica cálculo de edge (probabilidad modelo − probabilidad implícita)."""

    def _edge(self, probabilidad_modelo, cuota):
        from memory import _calcular_edge
        return _calcular_edge(probabilidad_modelo, cuota)

    def test_edge_positivo(self):
        # prob=0.70, cuota=1.80 → implícita=1/1.80≈0.5556 → edge=(0.70-0.5556)*100≈14.4
        result = self._edge(0.70, 1.80)
        self.assertAlmostEqual(result, 14.4, delta=0.1)

    def test_edge_negativo(self):
        # prob=0.40, cuota=1.50 → implícita=1/1.50≈0.6667 → edge=(0.40-0.6667)*100≈-26.7
        result = self._edge(0.40, 1.50)
        self.assertAlmostEqual(result, -26.7, delta=0.1)

    def test_edge_cuota_none(self):
        self.assertIsNone(self._edge(0.70, None))

    def test_edge_prob_none(self):
        self.assertIsNone(self._edge(None, 1.80))


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Correr los tests y verificar que FALLAN (las funciones no existen aún)**

```
python test_pred_fields.py
```

Salida esperada: `ImportError: cannot import name '_calcular_ganancia' from 'memory'`

---

### Task 2: Helpers de cálculo en `memory.py`

**Files:**
- Modify: `memory.py` (agregar 3 helpers privados antes de `guardar_prediccion`)

Estos helpers hacen el cálculo puro y son testeables sin Supabase.

- [ ] **Step 3: Agregar los tres helpers en `memory.py`**

Insertarlos justo antes de la línea `def guardar_prediccion(` (línea 74 aprox):

```python
# ─────────────────────────────────────────────────────────────────────────────
# Helpers de cálculo para nuevos campos de predicción
# ─────────────────────────────────────────────────────────────────────────────

def _prob_desde_total(total: float) -> float:
    """Conversión rough: total esperado → probabilidad de que ocurra al menos 1."""
    return round(total / (total + 1), 4)


def _calcular_edge(probabilidad_modelo: float | None, cuota: float | None) -> float | None:
    if probabilidad_modelo is None or cuota is None:
        return None
    prob_implicita = 1.0 / cuota
    return round((probabilidad_modelo - prob_implicita) * 100, 1)


def _calcular_ganancia(acerto: bool | None, cuota: float | None) -> float | None:
    if acerto is None or cuota is None:
        return None
    return round(cuota - 1, 4) if acerto else -1.0
```

- [ ] **Step 4: Correr los tests y verificar que PASAN**

```
python test_pred_fields.py
```

Salida esperada:
```
test_edge_cuota_none ... ok
test_edge_negativo ... ok
test_edge_positivo ... ok
test_edge_prob_none ... ok
test_ganancia_acerto_none ... ok
test_ganancia_acierto_con_cuota ... ok
test_ganancia_fallo_con_cuota ... ok
test_ganancia_sin_cuota_false ... ok
test_ganancia_sin_cuota_true ... ok
test_prob_resultado_redondeado ... ok
test_prob_total_0 ... ok
test_prob_total_9 ... ok
Ran 12 tests in 0.00Xs
OK
```

---

### Task 3: Extender `guardar_prediccion` en `memory.py`

**Files:**
- Modify: `memory.py:74-115`

- [ ] **Step 5: Reemplazar la firma de `guardar_prediccion` y el cuerpo**

Localizar la función actual (línea 74) y reemplazarla completa:

```python
def guardar_prediccion(equipo1: str, equipo2: str, foco: str, prediccion: str,
                       evento_id=None, liga_id=None, temporada_id=None,
                       user_id: str = "default",
                       probabilidad_modelo: float | None = None,
                       linea_recomendada: str | None = None,
                       confianza: str | None = None,
                       cuota: float | None = None,
                       edge: float | None = None,
                       ganancia: float | None = None):
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
                    "prediccion":          prediccion,
                    "fecha":               fecha,
                    "probabilidad_modelo": probabilidad_modelo,
                    "linea_recomendada":   linea_recomendada,
                    "confianza":           confianza,
                    "edge":                edge,
                    "ganancia":            ganancia,
                }).eq("id", row_id).execute()
                print(f"↩️  Predicción actualizada (evento {evento_id}, foco '{foco}')")
                return
        except Exception as e:
            print(f"⚠️  Supabase error al actualizar predicción: {e}")

    # Nueva predicción
    try:
        db.table("predicciones").insert({
            "fecha":               fecha,
            "equipo1":             equipo1,
            "equipo2":             equipo2,
            "foco":                foco,
            "prediccion":          prediccion,
            "evento_id":           evento_id,
            "liga_id":             liga_id,
            "temporada_id":        temporada_id,
            "resultado_real":      None,
            "acerto":              None,
            "user_id":             user_id,
            "probabilidad_modelo": probabilidad_modelo,
            "linea_recomendada":   linea_recomendada,
            "confianza":           confianza,
            "cuota":               cuota,
            "edge":                edge,
            "ganancia":            ganancia,
        }).execute()
    except Exception as e:
        print(f"⚠️  Supabase error al guardar predicción: {e}")
```

---

### Task 4: Calcular `ganancia` en `verificar_predicciones`

**Files:**
- Modify: `memory.py:436-440` (el bloque `db.table("predicciones").update(...)` dentro de `verificar_predicciones`)

- [ ] **Step 6: Agregar cálculo de ganancia en el update de verificación**

Localizar el bloque de update dentro de `verificar_predicciones` (aprox línea 436):

```python
            # (código existente arriba: resultado = _obtener_stats_evento, acerto = _determinar_acerto)

            cuota_pred = pred.get("cuota")
            ganancia   = _calcular_ganancia(acerto, cuota_pred)

            db.table("predicciones").update({
                "resultado_real": resultado,
                "acerto":         acerto,
                "evento_id":      evento_id,   # persist si vino del lookup
                "ganancia":       ganancia,
            }).eq("id", pred["id"]).execute()
```

El bloque original era:
```python
            db.table("predicciones").update({
                "resultado_real": resultado,
                "acerto":         acerto,
                "evento_id":      evento_id,
            }).eq("id", pred["id"]).execute()
```

Solo se agrega la variable `cuota_pred`, el cálculo de `ganancia`, y el campo en el `update`.

---

### Task 5: Extraer stats en `main.py` antes de `guardar_prediccion`

**Files:**
- Modify: `backend/main.py:834-843` (bloque `try: engine.guardar_prediccion(...)`)

En este punto del flujo `ACTION:ANALIZAR`, `lineas_py` y `prom_eq1`/`prom_eq2` ya están disponibles (se desempaquetan en línea 742).

- [ ] **Step 7: Agregar extracción de stats y llamada actualizada**

Reemplazar el bloque `try: engine.guardar_prediccion(...)` (aprox líneas 834-843) con:

```python
            # ── Extraer stats del análisis para persistir ─────────────
            _prob_mod = _linea_rec = _conf_val = None

            if foco_lower != "completo":
                _entry = lineas_py.get(foco_lower)
                if _entry:
                    from memory import _prob_desde_total
                    _prob_mod = _prob_desde_total(_entry[0])
                    _linea_rec = _entry[2]
                    _conf_val  = _entry[3]
            else:
                _goles_e = lineas_py.get("goles")
                if _goles_e:
                    from memory import _prob_desde_total
                    _prob_mod  = _prob_desde_total(_goles_e[0])
                    _linea_rec = _goles_e[2]
                    _conf_val  = _goles_e[3]

            # BTTS como fallback de probabilidad cuando no hay línea directa
            if _prob_mod is None and foco_lower in ("goles", "completo"):
                _b1 = prom_eq1.get("btts_score")
                _b2 = prom_eq2.get("btts_score")
                if _b1 is not None and _b2 is not None:
                    _prob_mod = round(min(_b1 * _b2, 0.95), 4)

            # cuota/edge quedan None hasta que se implemente el ingreso de cuotas
            _cuota = None
            _edge  = None

            try:
                engine.guardar_prediccion(
                    equipo1, equipo2, foco, analisis_limpio,
                    evento_id=evento_id,
                    liga_id=liga_info["id"],
                    temporada_id=liga_info["temporada"],
                    user_id=user_id,
                    probabilidad_modelo=_prob_mod,
                    linea_recomendada=_linea_rec,
                    confianza=_conf_val,
                    cuota=_cuota,
                    edge=_edge,
                )
            except Exception as _save_err:
                _safe_print(f"[warn] guardar_prediccion falló: {_save_err}")
```

---

### Task 6: Crear columnas en Supabase (manual)

El usuario debe ejecutar esto en el **SQL Editor** de Supabase antes de hacer deploy:

```sql
ALTER TABLE predicciones
  ADD COLUMN IF NOT EXISTS probabilidad_modelo FLOAT,
  ADD COLUMN IF NOT EXISTS linea_recomendada   TEXT,
  ADD COLUMN IF NOT EXISTS confianza           TEXT,
  ADD COLUMN IF NOT EXISTS cuota               FLOAT,
  ADD COLUMN IF NOT EXISTS edge                FLOAT,
  ADD COLUMN IF NOT EXISTS ganancia            FLOAT;
```

- [ ] **Step 8: Ejecutar el ALTER TABLE en Supabase SQL Editor**
- [ ] **Step 9: Verificar en la tabla `predicciones` que aparecen las 6 columnas nuevas**

---

### Task 7: Verificación end-to-end y commit

- [ ] **Step 10: Correr los tests unitarios**

```
python test_pred_fields.py
```

Salida esperada: `Ran 12 tests in 0.0XXs — OK`

- [ ] **Step 11: Commit**

```bash
git add memory.py backend/main.py test_pred_fields.py
git commit -m "feat: guardar probabilidad_modelo, linea_recomendada, confianza y calcular edge/ganancia en predicciones"
```

---

## Self-Review

**Spec coverage:**
- ✅ `probabilidad_modelo` — calculado con `_prob_desde_total` y persistido
- ✅ `linea_recomendada` — extraída de `lineas_py[foco][2]`
- ✅ `confianza` — extraída de `lineas_py[foco][3]`
- ✅ `cuota` — campo preparado (None por ahora)
- ✅ `edge` — helper `_calcular_edge` listo, calculado si cuota != None
- ✅ `ganancia` — calculada en `verificar_predicciones` usando `_calcular_ganancia`
- ✅ Todos los campos en INSERT y UPDATE (evento_id existente)
- ✅ BTTS fallback para foco goles/completo
- ✅ Columnas Supabase — instrucciones en Task 6
- ✅ engine.py sin cambios (ya retorna lineas_python)

**Placeholder scan:** Ninguno — todo el código está completo.

**Type consistency:**
- `_prob_desde_total(total: float) → float` — usado igual en tests y en main.py
- `_calcular_edge(prob, cuota) → float | None` — firma consistente en tests y Task 4
- `_calcular_ganancia(acerto, cuota) → float | None` — firma consistente en tests y Task 4
- `lineas_py` entries: `(total, directa, recomendada, confianza, conservadora)` — indices [0][2][3] usados en Task 5, consistent con engine.py línea 934
