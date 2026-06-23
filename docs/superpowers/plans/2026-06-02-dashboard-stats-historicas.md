# Dashboard de Estadísticas Históricas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar `liga_nombre` a predicciones, un endpoint `GET /api/stats` y un panel "History" en el dashboard que muestra estadísticas históricas del usuario.

**Architecture:** Tres partes ortogonales: (1) campo `liga_nombre` en Supabase + `memory.py` + callers; (2) endpoint `/api/stats` que consulta Supabase en tiempo real y calcula hit rate, ROI y racha; (3) panel `#stats-panel` en el HTML con cuatro secciones wired a la función `loadStats()`. El engine no necesita cambios de cálculo, solo que `_guardar_picks_combinada` pase `liga_nombre`.

**Tech Stack:** Python 3.11, FastAPI, Supabase (postgrest-py), Tailwind CSS + Material Symbols (ya cargados), unittest sin credenciales.

---

## Archivos modificados

| Archivo | Cambio |
|---|---|
| `memory.py` | `liga_nombre` en firma, INSERT y UPDATE de `guardar_prediccion` + helper `_calcular_racha` |
| `backend/main.py` | `liga_nombre=liga_nombre` en llamada a `guardar_prediccion`; endpoint `GET /api/stats` |
| `backend/engine.py` | `liga_nombre=pick["liga"]` en `_guardar_picks_combinada` |
| `backend/test_chat.html` | `#stats-panel` DOM, `showView()`, `loadStats()`, wiring sidebar |
| `test_stats_fields.py` | Tests unitarios para `_calcular_racha` |

---

### Task 1: Supabase — agregar columna `liga_nombre`

**Files:**
- Supabase SQL Editor (manual)

- [ ] **Step 1: Ejecutar en el SQL Editor de Supabase**

```sql
ALTER TABLE predicciones
  ADD COLUMN IF NOT EXISTS liga_nombre TEXT;
```

- [ ] **Step 2: Verificar que la columna aparece en la tabla `predicciones`**

En el panel de Supabase → Table Editor → predicciones → ver que `liga_nombre` existe.

---

### Task 2: Tests para `_calcular_racha` (TDD)

**Files:**
- Create: `test_stats_fields.py`

- [ ] **Step 3: Crear el archivo de tests**

```python
"""
test_stats_fields.py — Tests para _calcular_racha.
Ejecutar desde la raíz: python test_stats_fields.py
No requiere credenciales de Supabase ni Groq.
"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
sys.path.insert(0, os.path.dirname(__file__))


class TestCalcularRacha(unittest.TestCase):

    def _racha(self, acertos):
        from memory import _calcular_racha
        preds = [{"acerto": a} for a in acertos]
        return _calcular_racha(preds)

    def test_racha_aciertos(self):
        self.assertEqual(self._racha([True, True, True, False]), "3 aciertos seguidos 🔥")

    def test_racha_fallos(self):
        self.assertEqual(self._racha([False, False, True]), "2 fallos consecutivos")

    def test_racha_uno_solo_es_none(self):
        self.assertIsNone(self._racha([True, False, True]))

    def test_racha_lista_vacia(self):
        self.assertIsNone(self._racha([]))

    def test_racha_todos_aciertos(self):
        self.assertEqual(self._racha([True, True, True, True, True]), "5 aciertos seguidos 🔥")

    def test_racha_un_elemento(self):
        # Una sola predicción no es racha
        self.assertIsNone(self._racha([True]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 4: Correr los tests — deben FALLAR (función no existe)**

```
python test_stats_fields.py
```

Salida esperada: `ImportError: cannot import name '_calcular_racha' from 'memory'`

---

### Task 3: Agregar `liga_nombre` y `_calcular_racha` en `memory.py`

**Files:**
- Modify: `memory.py`

- [ ] **Step 5: Agregar `_calcular_racha` junto a los otros helpers privados**

Insertar justo después de `_calcular_ganancia` (antes de la línea `# Guardar predicción`):

```python
def _calcular_racha(verificadas_desc: list) -> str | None:
    """Calcula la racha actual desde predicciones verificadas ordenadas desc."""
    if not verificadas_desc:
        return None
    primer = verificadas_desc[0].get("acerto")
    count = 0
    for p in verificadas_desc:
        if p.get("acerto") == primer:
            count += 1
        else:
            break
    if count < 2:
        return None
    return f"{count} aciertos seguidos 🔥" if primer else f"{count} fallos consecutivos"
```

- [ ] **Step 6: Correr los tests — deben PASAR**

```
python test_stats_fields.py
```

Salida esperada:
```
test_racha_aciertos ... ok
test_racha_fallos ... ok
test_racha_lista_vacia ... ok
test_racha_todos_aciertos ... ok
test_racha_uno_solo_es_none ... ok
test_racha_un_elemento ... ok
Ran 6 tests in 0.XXXs
OK
```

- [ ] **Step 7: Agregar `liga_nombre` a la firma de `guardar_prediccion`**

Localizar la firma actual (tiene `ganancia: float | None = None` como último param) y agregar `liga_nombre` al final:

```python
def guardar_prediccion(equipo1: str, equipo2: str, foco: str, prediccion: str,
                       evento_id=None, liga_id=None, temporada_id=None,
                       user_id: str = "default",
                       probabilidad_modelo: float | None = None,
                       linea_recomendada: str | None = None,
                       confianza: str | None = None,
                       cuota: float | None = None,
                       edge: float | None = None,
                       ganancia: float | None = None,
                       liga_nombre: str | None = None):
```

- [ ] **Step 8: Agregar `liga_nombre` al UPDATE (cuando hay evento_id duplicado)**

Localizar el bloque update existente (tiene `"ganancia": ganancia`) y agregar la línea:

```python
                db.table("predicciones").update({
                    "prediccion":          prediccion,
                    "fecha":               fecha,
                    "probabilidad_modelo": probabilidad_modelo,
                    "linea_recomendada":   linea_recomendada,
                    "confianza":           confianza,
                    "edge":                edge,
                    "ganancia":            ganancia,
                    "liga_nombre":         liga_nombre,
                }).eq("id", row_id).execute()
```

- [ ] **Step 9: Agregar `liga_nombre` al INSERT (nueva predicción)**

Localizar el dict del INSERT (tiene `"ganancia": ganancia` como último campo) y agregar:

```python
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
            "liga_nombre":         liga_nombre,
        }).execute()
```

- [ ] **Step 10: Correr ambos suites de tests para verificar que nada se rompió**

```
python test_pred_fields.py && python test_stats_fields.py
```

Salida esperada: `Ran 12 tests ... OK` + `Ran 6 tests ... OK`

---

### Task 4: Pasar `liga_nombre` en `main.py`

**Files:**
- Modify: `backend/main.py` (dentro del bloque `ACTION:ANALIZAR`, ~línea 860)

En este punto `liga_nombre` ya está en scope (se establece en `fix_data` o en el request directo).

- [ ] **Step 11: Agregar `liga_nombre` a la llamada a `guardar_prediccion`**

Localizar la llamada existente (tiene `edge=_edge` como último kwarg) y agregar:

```python
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
                    liga_nombre=liga_nombre,
                )
            except Exception as _save_err:
                _safe_print(f"[warn] guardar_prediccion falló: {_save_err}")
```

---

### Task 5: Pasar `liga_nombre` en `engine.py` → `_guardar_picks_combinada`

**Files:**
- Modify: `backend/engine.py:1641-1647`

- [ ] **Step 12: Agregar `liga_nombre` a la llamada en `_guardar_picks_combinada`**

Localizar la llamada a `guardar_prediccion` dentro de `_guardar_picks_combinada` (línea ~1641) y agregar `liga_nombre`:

```python
        guardar_prediccion(
            equipo1=pick["equipo1"], equipo2=pick["equipo2"], foco=pick["stat"],
            prediccion=pred_texto, evento_id=None,
            liga_id=liga_info["id"] if liga_info else None,
            temporada_id=liga_info["temporada"] if liga_info else None,
            user_id=user_id,
            liga_nombre=pick["liga"],
        )
```

- [ ] **Step 13: Commit del backend (memory + main + engine)**

```bash
git add memory.py backend/main.py backend/engine.py test_stats_fields.py
git commit -m "feat: agregar liga_nombre a predicciones y helpers de racha"
```

---

### Task 6: Endpoint `GET /api/stats` en `main.py`

**Files:**
- Modify: `backend/main.py` (agregar antes de `@app.post("/api/chat")`)

- [ ] **Step 14: Agregar el endpoint completo**

Insertar el siguiente bloque justo antes de `@app.post("/api/chat")` (actualmente está alrededor de la línea 947):

```python
@app.get("/api/stats")
async def get_stats(http_request: Request):
    auth_header = http_request.headers.get("Authorization")
    try:
        user_id = verificar_token(auth_header)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    from supabase_client import db
    from collections import defaultdict
    from memory import _calcular_racha

    res = db.table("predicciones").select("*").eq("user_id", user_id).order("created_at").execute()
    preds = res.data or []

    total      = len(preds)
    verificadas = [p for p in preds if p.get("acerto") is not None]
    acertadas   = [p for p in verificadas if p.get("acerto") is True]
    n_v = len(verificadas)
    n_a = len(acertadas)

    hit_rate = round(n_a / n_v * 100, 1) if n_v else None

    ganancias = [p["ganancia"] for p in verificadas if p.get("ganancia") is not None]
    roi    = round(sum(ganancias) / len(ganancias) * 100, 1) if ganancias else None
    yield_ = round(sum(ganancias) / total * 100, 1) if (ganancias and total) else None

    verificadas_desc = sorted(verificadas, key=lambda p: p.get("created_at", ""), reverse=True)
    racha_actual = _calcular_racha(verificadas_desc)

    # Por mercado
    mercado_map: dict = defaultdict(lambda: {"total": 0, "verificadas": 0, "acertadas": 0})
    for p in preds:
        fk = p.get("foco") or "desconocido"
        mercado_map[fk]["total"] += 1
        if p.get("acerto") is not None:
            mercado_map[fk]["verificadas"] += 1
            if p.get("acerto") is True:
                mercado_map[fk]["acertadas"] += 1

    por_mercado = []
    for fk, s in sorted(mercado_map.items(), key=lambda x: -x[1]["total"]):
        if s["verificadas"] == 0:
            continue
        hr = round(s["acertadas"] / s["verificadas"] * 100, 1) if s["verificadas"] else None
        por_mercado.append({"foco": fk, "total": s["total"], "acertadas": s["acertadas"],
                            "hit_rate": hr, "roi": None})

    # Por liga
    liga_map: dict = defaultdict(lambda: {"total": 0, "verificadas": 0, "acertadas": 0})
    for p in preds:
        lk = p.get("liga_nombre") or "Sin liga"
        liga_map[lk]["total"] += 1
        if p.get("acerto") is not None:
            liga_map[lk]["verificadas"] += 1
            if p.get("acerto") is True:
                liga_map[lk]["acertadas"] += 1

    por_liga = []
    for lk, s in sorted(liga_map.items(), key=lambda x: -x[1]["total"]):
        if s["verificadas"] == 0:
            continue
        hr = round(s["acertadas"] / s["verificadas"] * 100, 1) if s["verificadas"] else None
        por_liga.append({"liga": lk, "total": s["total"], "acertadas": s["acertadas"],
                         "hit_rate": hr, "roi": None})

    recientes_raw = sorted(preds, key=lambda p: p.get("created_at", ""), reverse=True)[:10]
    recientes = [
        {
            "fecha":             p.get("fecha", ""),
            "equipo1":           p.get("equipo1", ""),
            "equipo2":           p.get("equipo2", ""),
            "foco":              p.get("foco", ""),
            "linea_recomendada": p.get("linea_recomendada"),
            "confianza":         p.get("confianza"),
            "acerto":            p.get("acerto"),
            "liga_nombre":       p.get("liga_nombre"),
        }
        for p in recientes_raw
    ]

    return {
        "general": {
            "total":        total,
            "verificadas":  n_v,
            "acertadas":    n_a,
            "hit_rate":     hit_rate,
            "roi":          roi,
            "yield":        yield_,
            "racha_actual": racha_actual,
        },
        "por_mercado": por_mercado,
        "por_liga":    por_liga,
        "recientes":   recientes,
    }
```

- [ ] **Step 15: Verificar el endpoint manualmente**

Arrancar el backend:
```
cd backend && uvicorn main:app --reload
```

Llamar con curl (reemplazar `<token>` con el access_token de Supabase):
```
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/stats
```

Salida esperada: JSON con las claves `general`, `por_mercado`, `por_liga`, `recientes`. Si no hay predicciones: `general.total = 0`, arrays vacíos.

- [ ] **Step 16: Commit del endpoint**

```bash
git add backend/main.py
git commit -m "feat: endpoint GET /api/stats con hit rate, ROI y racha"
```

---

### Task 7: Panel `#stats-panel` en `test_chat.html`

**Files:**
- Modify: `backend/test_chat.html`

- [ ] **Step 17: Agregar `#stats-panel` después de `#dashboard-body`**

Localizar la línea que cierra `#dashboard-body` (tiene `</div>` y el comentario de cierre) y agregar el panel a continuación, dentro de `<main id="app-main">`:

```html
    <!-- Stats Panel (History view) -->
    <div id="stats-panel" class="hidden" style="flex:1;overflow-y:auto;padding:24px;">

      <!-- Sección 1: Cards de resumen -->
      <div class="mb-8">
        <h2 class="font-headline-lg text-[20px] text-on-surface mb-4">Resumen de predicciones</h2>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4" id="stats-cards">
          <div class="col-span-4 text-center py-6 text-on-surface-variant">Cargando...</div>
        </div>
      </div>

      <!-- Sección 2: Por mercado -->
      <div class="mb-8">
        <h3 class="font-label-caps text-label-caps text-on-surface-variant uppercase mb-3">Por mercado</h3>
        <div class="glass-panel rounded-xl overflow-hidden">
          <table class="w-full text-sm">
            <thead class="border-b border-glass-border">
              <tr class="text-on-surface-variant font-label-caps text-[11px] uppercase">
                <th class="text-left px-4 py-3">Mercado</th>
                <th class="text-center px-4 py-3">Apuestas</th>
                <th class="text-center px-4 py-3">Hit Rate</th>
              </tr>
            </thead>
            <tbody id="stats-mercado-body">
              <tr><td colspan="3" class="text-center px-4 py-6 text-on-surface-variant">Cargando...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Sección 3: Por liga -->
      <div class="mb-8">
        <h3 class="font-label-caps text-label-caps text-on-surface-variant uppercase mb-3">Por liga</h3>
        <div class="glass-panel rounded-xl overflow-hidden">
          <table class="w-full text-sm">
            <thead class="border-b border-glass-border">
              <tr class="text-on-surface-variant font-label-caps text-[11px] uppercase">
                <th class="text-left px-4 py-3">Liga</th>
                <th class="text-center px-4 py-3">Apuestas</th>
                <th class="text-center px-4 py-3">Hit Rate</th>
              </tr>
            </thead>
            <tbody id="stats-liga-body">
              <tr><td colspan="3" class="text-center px-4 py-6 text-on-surface-variant">Cargando...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Sección 4: Últimas predicciones -->
      <div>
        <h3 class="font-label-caps text-label-caps text-on-surface-variant uppercase mb-3">Últimas predicciones</h3>
        <div id="stats-recientes" class="space-y-2">
          <div class="text-center py-6 text-on-surface-variant">Cargando...</div>
        </div>
      </div>

    </div><!-- /#stats-panel -->
```

---

### Task 8: Wiring del sidebar en `test_chat.html`

**Files:**
- Modify: `backend/test_chat.html` (sidebar, ~línea 479)

- [ ] **Step 18: Agregar `id` y `onclick` al nav item Dashboard**

Localizar el `<a>` del Dashboard (tiene `bg-primary/10 text-primary border-r-4 border-primary`) y agregar `id` y `onclick`:

```html
      <a id="nav-dashboard"
         class="flex items-center gap-3 px-4 py-3 bg-primary/10 text-primary border-r-4 border-primary transition-all duration-300 cursor-pointer"
         onclick="showView('dashboard')" href="#">
        <span class="material-symbols-outlined text-[20px]">dashboard</span>
        <span class="font-label-caps text-label-caps">Dashboard</span>
      </a>
```

- [ ] **Step 19: Agregar `id` y `onclick` al nav item History**

Localizar el `<a>` de History (tiene el ícono `history`) y agregar `id` y `onclick`:

```html
      <a id="nav-history"
         class="flex items-center gap-3 px-4 py-3 text-on-surface-variant hover:bg-surface-variant/30 transition-all duration-300 cursor-pointer"
         onclick="showView('history')" href="#">
        <span class="material-symbols-outlined text-[20px]">history</span>
        <span class="font-label-caps text-label-caps">History</span>
      </a>
```

---

### Task 9: Funciones JS en `test_chat.html`

**Files:**
- Modify: `backend/test_chat.html` (sección `<script>`)

- [ ] **Step 20: Agregar `showView`, `loadStats` y helpers de renderizado**

Localizar el bloque `<script>` principal (donde está `async function sendMessage`) y agregar las siguientes funciones antes del cierre `</script>`:

```javascript
    // ── Vista History / Dashboard ─────────────────────────────────────

    function showView(name) {
      const dashBody   = document.getElementById('dashboard-body');
      const statsPanel = document.getElementById('stats-panel');
      const navDash    = document.getElementById('nav-dashboard');
      const navHist    = document.getElementById('nav-history');

      const ACTIVE   = 'flex items-center gap-3 px-4 py-3 bg-primary/10 text-primary border-r-4 border-primary transition-all duration-300 cursor-pointer';
      const INACTIVE = 'flex items-center gap-3 px-4 py-3 text-on-surface-variant hover:bg-surface-variant/30 transition-all duration-300 cursor-pointer';

      if (name === 'history') {
        dashBody.classList.add('hidden');
        statsPanel.classList.remove('hidden');
        navHist.className = ACTIVE;
        navDash.className = INACTIVE;
        loadStats();
      } else {
        statsPanel.classList.add('hidden');
        dashBody.classList.remove('hidden');
        navDash.className = ACTIVE;
        navHist.className = INACTIVE;
      }
    }

    async function loadStats() {
      const { data: { session } } = await _sb.auth.getSession();
      const token = session?.access_token || "";
      try {
        const res = await fetch(`${BASE_URL}/api/stats`, {
          headers: { "Authorization": `Bearer ${token}` }
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        renderStatsCards(data.general);
        renderStatsMercado(data.por_mercado);
        renderStatsLiga(data.por_liga);
        renderStatsRecientes(data.recientes);
      } catch (e) {
        document.getElementById('stats-cards').innerHTML =
          '<div class="col-span-4 text-center py-6 text-on-surface-variant">Sin predicciones verificadas aún.</div>';
        document.getElementById('stats-mercado-body').innerHTML =
          '<tr><td colspan="3" class="text-center px-4 py-6 text-on-surface-variant">—</td></tr>';
        document.getElementById('stats-liga-body').innerHTML =
          '<tr><td colspan="3" class="text-center px-4 py-6 text-on-surface-variant">—</td></tr>';
        document.getElementById('stats-recientes').innerHTML =
          '<div class="text-center py-6 text-on-surface-variant">Sin predicciones aún.</div>';
      }
    }

    function _hitBadge(rate) {
      if (rate === null || rate === undefined) return '<span class="text-on-surface-variant">—</span>';
      const cls = rate >= 60 ? 'text-neon-accent' : rate >= 45 ? 'text-yellow-400' : 'text-error';
      return `<span class="${cls} font-data-point">${rate.toFixed(1)}%</span>`;
    }

    function renderStatsCards(g) {
      if (!g) return;
      const roi = g.roi !== null && g.roi !== undefined
        ? `<span class="${g.roi >= 0 ? 'text-neon-accent' : 'text-error'}">${g.roi > 0 ? '+' : ''}${g.roi.toFixed(1)}%</span>`
        : '<span class="text-on-surface-variant">—</span>';
      const hrCls = g.hit_rate === null ? 'text-on-surface-variant'
        : g.hit_rate >= 60 ? 'text-neon-accent' : g.hit_rate >= 45 ? 'text-yellow-400' : 'text-error';
      document.getElementById('stats-cards').innerHTML = `
        <div class="glass-panel rounded-xl p-5 inner-glow">
          <p class="font-label-caps text-[10px] text-on-surface-variant uppercase mb-2">Total</p>
          <p class="font-data-point text-[32px] text-on-surface">${g.total}</p>
          <p class="text-[11px] text-on-surface-variant mt-1">${g.verificadas} verificadas</p>
        </div>
        <div class="glass-panel rounded-xl p-5 inner-glow">
          <p class="font-label-caps text-[10px] text-on-surface-variant uppercase mb-2">Hit Rate</p>
          <p class="font-data-point text-[32px] ${hrCls}">${g.hit_rate !== null && g.hit_rate !== undefined ? g.hit_rate.toFixed(1) + '%' : '—'}</p>
          <p class="text-[11px] text-on-surface-variant mt-1">${g.acertadas} aciertos</p>
        </div>
        <div class="glass-panel rounded-xl p-5 inner-glow">
          <p class="font-label-caps text-[10px] text-on-surface-variant uppercase mb-2">ROI</p>
          <p class="font-data-point text-[32px]">${roi}</p>
          <p class="text-[11px] text-on-surface-variant mt-1">yield: ${g.yield !== null && g.yield !== undefined ? g.yield.toFixed(1) + '%' : '—'}</p>
        </div>
        <div class="glass-panel rounded-xl p-5 inner-glow">
          <p class="font-label-caps text-[10px] text-on-surface-variant uppercase mb-2">Racha</p>
          <p class="font-data-point text-[14px] text-on-surface leading-snug">${g.racha_actual || '—'}</p>
        </div>
      `;
    }

    function renderStatsMercado(items) {
      const body = document.getElementById('stats-mercado-body');
      if (!items || !items.length) {
        body.innerHTML = '<tr><td colspan="3" class="text-center px-4 py-6 text-on-surface-variant">Sin datos suficientes</td></tr>';
        return;
      }
      body.innerHTML = items.map(r => `
        <tr class="border-t border-glass-border hover:bg-surface-variant/10 transition-colors">
          <td class="px-4 py-3 font-medium">${r.foco}</td>
          <td class="px-4 py-3 text-center text-on-surface-variant">${r.total}</td>
          <td class="px-4 py-3 text-center">${_hitBadge(r.hit_rate)}</td>
        </tr>`).join('');
    }

    function renderStatsLiga(items) {
      const body = document.getElementById('stats-liga-body');
      if (!items || !items.length) {
        body.innerHTML = '<tr><td colspan="3" class="text-center px-4 py-6 text-on-surface-variant">Sin datos suficientes</td></tr>';
        return;
      }
      body.innerHTML = items.map(r => `
        <tr class="border-t border-glass-border hover:bg-surface-variant/10 transition-colors">
          <td class="px-4 py-3 font-medium">${r.liga || '—'}</td>
          <td class="px-4 py-3 text-center text-on-surface-variant">${r.total}</td>
          <td class="px-4 py-3 text-center">${_hitBadge(r.hit_rate)}</td>
        </tr>`).join('');
    }

    function renderStatsRecientes(items) {
      const el = document.getElementById('stats-recientes');
      if (!items || !items.length) {
        el.innerHTML = '<div class="text-center py-6 text-on-surface-variant">Sin predicciones aún.</div>';
        return;
      }
      el.innerHTML = items.map(p => {
        const icono = p.acerto === true ? '✅' : p.acerto === false ? '❌' : '⏳';
        return `
          <div class="glass-panel rounded-xl p-4 flex items-start justify-between gap-4">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 mb-1 flex-wrap">
                <span class="font-medium text-on-surface text-sm">${p.equipo1} vs ${p.equipo2}</span>
                ${p.liga_nombre ? `<span class="text-[10px] font-label-caps text-on-surface-variant uppercase">${p.liga_nombre}</span>` : ''}
              </div>
              <div class="flex items-center gap-3 text-[12px] text-on-surface-variant flex-wrap">
                <span>${p.foco}</span>
                ${p.linea_recomendada ? `<span class="text-primary">${p.linea_recomendada}</span>` : ''}
                ${p.confianza ? `<span class="opacity-70">${p.confianza}</span>` : ''}
              </div>
            </div>
            <div class="text-2xl flex-shrink-0">${icono}</div>
          </div>`;
      }).join('');
    }
```

---

### Task 10: Verificación final y commit

- [ ] **Step 21: Correr todos los tests unitarios**

```
python test_pred_fields.py && python test_stats_fields.py
```

Salida esperada: `Ran 12 tests ... OK` + `Ran 6 tests ... OK`

- [ ] **Step 22: Verificar el panel en el navegador**

1. Arrancar el servidor: `cd backend && uvicorn main:app --reload`
2. Abrir `http://localhost:8000`
3. Hacer login
4. Hacer click en "History" en el sidebar
5. Verificar que el dashboard se oculta y aparece el panel de stats
6. Verificar que las 4 secciones se muestran (incluso si están vacías con "Sin datos")
7. Hacer click en "Dashboard" → vuelve al chat
8. Verificar que las clases activas del sidebar cambian correctamente

- [ ] **Step 23: Commit final**

```bash
git add backend/test_chat.html backend/main.py
git commit -m "feat: panel History con stats históricas — endpoint /api/stats, showView, loadStats"
```

---

## Self-Review

**Spec coverage:**
- ✅ `liga_nombre` — Supabase (Task 1), memory.py firma + INSERT + UPDATE (Task 3), main.py (Task 4), engine.py (Task 5)
- ✅ `GET /api/stats` con auth — Task 6
- ✅ `general` con total, verificadas, acertadas, hit_rate, roi, yield, racha_actual — Task 6
- ✅ `por_mercado` agrupado por foco, ordenado desc, solo verificadas — Task 6
- ✅ `por_liga` agrupado por liga_nombre — Task 6
- ✅ `recientes` últimas 10 con los 8 campos del spec — Task 6
- ✅ `#stats-panel` sibling de `#dashboard-body`, oculto por default — Task 7
- ✅ 4 secciones (cards, mercado, liga, recientes) — Task 7
- ✅ Colores semáforo hit rate (verde ≥60%, amarillo ≥45%, rojo <45%) — Task 9 `_hitBadge`
- ✅ ROI con color (verde si ≥0, rojo si <0) — Task 9 `renderStatsCards`
- ✅ `showView('dashboard')` y `showView('history')` — Task 8 + Task 9
- ✅ `loadStats()` llamado al cambiar a History — Task 9 `showView`
- ✅ Error/vacío: "Sin predicciones verificadas aún." — Task 9 `loadStats` catch

**Placeholder scan:** Ninguno — todo el código está completo.

**Type consistency:**
- `_calcular_racha(verificadas_desc: list) → str | None` — definida en Task 3, importada en Task 6, testeada en Task 2
- `guardar_prediccion(..., liga_nombre: str | None = None)` — modificada en Task 3, usada con kwarg en Tasks 4 y 5
- `data.general.yield` (JS) ↔ `"yield": yield_` (Python dict, Task 6) — consistente
- `data.por_mercado[].hit_rate` ↔ `_hitBadge(r.hit_rate)` — consistente
- `data.recientes[].acerto` ↔ `p.acerto === true/false` — consistente
