# ScoutAI — Completar funcionalidades (landing + app)

Fecha: 2026-06-13
Estado: aprobado por el usuario, pendiente de plan de implementación.

## Objetivo

Dejar funcionando todos los botones y apartados de ScoutAI (`backend/test_chat.html`)
y crear los apartados nuevos del sidebar. Hoy hay elementos decorativos o muertos
(`href="#"`, botones sin handler) y tres ítems del sidebar sin vista.

No se toca el motor de análisis (`engine.py`, `memory.py`, etc.). El alcance es
frontend (la SPA de un solo archivo) + tres adiciones chicas de backend.

## Contexto actual

- Frontend único: `backend/test_chat.html` (~1352 líneas), servido por FastAPI
  en `GET /` vía `FileResponse`. SPA: landing (`#landing`), overlay de auth
  (`#auth-overlay`) y app (`#app`). La app alterna vistas con `showView(name)`
  (hoy: `dashboard` ↔ `history`).
- Auth: Supabase JS (`_sb`), `onAuthStateChange` → `enterApp`/`exitApp`.
- Backend relevante: `GET /api/fixtures` (texto de próximos partidos con tags
  `[HOY]`/`[EN CURSO]`), `GET /api/stats` (auth), `POST /api/chat` (SSE),
  `DELETE /api/session/{id}` (auth, solo dueño). Cuota diaria en `quota.py`
  (2 análisis + 1 combinada/día) — sin endpoint de lectura.

## Inventario de elementos a resolver

Muertos / decorativos detectados:
- Landing nav: `Markets`, `Predictions`, `Insights`, `Pricing` (`href="#"`).
- Footers (landing + auth): `Términos`, `Privacidad`, `Datos`, `API Access`, `Soporte`.
- Auth: botones sociales `GOOGLE` y `API KEY`.
- Sidebar app: `Live Match`, `AI Combos`, `Settings`, `Upgrade to Elite`, `Support`.
- Chat: quick-actions `Hot Matches`, `High Confidence`.
- Barra de uso: hardcodeada `/3`, solo client-side.

## Diseño

### 1. Apartados nuevos del sidebar (vistas SPA vía `showView`)

Se agregan tres paneles hermanos de `dashboard-body`/`stats-panel`, alternados
por `showView`. Se extiende `showView` para aceptar `live`, `combos`, `settings`
y marcar el ítem activo del sidebar de forma genérica (mapa id→panel) en vez de
condicionales sueltos.

- **Live Match** (`#live-panel`)
  - Fuente: `GET /api/fixtures` (texto). Parseo client-side: cabeceras de liga
    (`"Liga:"`) y líneas `"- A vs B (fecha[ HOY][ EN CURSO])"`.
  - Render: agrupado por liga; los `[EN CURSO]` arriba y resaltados (badge "EN VIVO");
    luego los `[HOY]`. Si no hay nada hoy, muestra los próximos.
  - Auto-refresh cada 60s mientras la vista está activa (interval que se limpia
    al salir de la vista).
  - Interacción: clic en un partido precarga `"analizá A vs B"` en el input y
    cambia a Dashboard (no dispara solo; el usuario confirma con enviar).

- **AI Combos** (`#combos-panel`)
  - Panel lanzador (sin backend nuevo). Botones:
    - "Mejor combinada del día" → mensaje `"armame la mejor combinada del día"`.
    - Una por liga principal (Premier League, La Liga, Serie A, Bundesliga,
      Ligue 1, Copa Libertadores, Champions League) → `"combinada de <liga>"`.
  - Al hacer clic: cambia a Dashboard y llama al pipeline de chat existente
    (`send()` con el texto prefijado), de modo que la combinada llega como
    burbuja en el chat y consume cuota igual que hoy.

- **Settings** (`#settings-panel`)
  - Datos de cuenta: email y nombre (de `_session.user`).
  - Zona horaria detectada (`Intl.DateTimeFormat().resolvedOptions().timeZone`
    + offset), solo informativa.
  - Cuota del día (lee `GET /api/quota`).
  - "Borrar historial de esta sesión" → `DELETE /api/session/{sessionId}` con
    `Authorization: Bearer`; al volver 200, resetea el chat (`newChat()`).
  - "Cerrar sesión" → `logout()`.
  - Sin theme toggle (el diseño es dark-only; un light parcial se vería roto).

### 2. Barra de uso → cuota real

- Nuevo endpoint **`GET /api/quota`** en `main.py`:
  - Autenticado vía `verificar_token` (igual que `/api/stats`). Sin token válido
    en producción → 401; en dev (`default`) → devuelve límites con uso 0.
  - Devuelve `{ "analisis_usados", "analisis_limite", "combinadas_usados",
    "combinadas_limite" }`.
  - Implementación: función nueva en `quota.py` que **solo lee** (`_get_uso`),
    sin consumir cupo: `get_estado(user_id) -> dict`. Admin → límites altos o
    flag `ilimitado: true`.
- Frontend: `refreshQuota()` se llama al entrar a la app y después de cada
  respuesta de tipo `analysis`/`combinada`. La barra muestra el uso de análisis
  (principal) y el label incluye análisis + combinadas. Se elimina el
  `_usageCount` client-side y el hardcode `/3`.

### 3. Botones decorativos

- **Google**: `_sb.auth.signInWithOAuth({ provider: 'google', options: { redirectTo: window.location.origin } })`.
  Depende de habilitar el provider Google en Supabase (paso manual del usuario,
  ver "Dependencias externas"). Si no está habilitado, Supabase devuelve error
  que se muestra en `auth-msg`.
- **API Key** (auth) y **API Access** (footer): se **eliminan** (no hay API pública).
- **Legal**: rutas FastAPI nuevas que sirven HTML styled (Tailwind CDN, mismo
  look): `GET /terminos`, `GET /privacidad`, `GET /datos`. Contenido:
  - `/terminos`: términos de uso + disclaimer de apuestas (mayores de edad, sin
    garantía de resultados, uso informativo).
  - `/privacidad`: qué datos se guardan (email vía Supabase, predicciones,
    sesiones) y cómo.
  - `/datos`: fuente de datos (SofaScore, no afiliado) + disclaimer estadístico.
  - Los archivos viven en `backend/legal/{terminos,privacidad,datos}.html` y se
    sirven con `FileResponse`. Footer links (landing + auth) apuntan a estas rutas
    (target `_blank`). "Soporte" → `mailto:lucasileoni694@gmail.com`.
- **Upgrade to Elite / Pro**: quedan como "Próximamente". El botón "Upgrade to
  Elite" del sidebar muestra un aviso inline/toast "Plan Pro — próximamente"
  (en la vista de app no existe sección Pricing a la cual scrollear). No promete
  nada que no exista.

### 4. Nav de la landing

- IDs en secciones existentes (`#features`, `#pricing`). Links del navbar con
  scroll suave (`scroll-behavior` o handler). Renombrados al español y al
  contenido real:
  - `Markets` → "Cómo funciona" (→ `#features`)
  - `Predictions` → "Predicciones" (→ `#features`)
  - `Insights` → "Insights" (→ `#features`)
  - `Pricing` → "Planes" (→ `#pricing`)
  (Si dos apuntan a lo mismo se puede reducir el set; se mantienen por estética
  del navbar.)

### 5. Quick-actions del chat

- "Hot Matches" → `send()` con `"qué partidos hay hoy"`.
- "High Confidence" → `send()` con `"armame una combinada de alta confianza"`.
- Ambos requieren sesión iniciada (igual que `send()`).

## Componentes y límites

- `showView(name)` — único punto de cambio de vista; mapa `VIEWS = {dashboard,
  history, live, combos, settings}` con su panel y su ítem de nav. Limpia el
  interval de Live Match al salir.
- `renderLive(fixturesText)` — puro: texto → DOM de Live Match. Testeable a ojo.
- `refreshQuota()` — fetch + pinta barra/label. Independiente.
- Backend: `quota.get_estado(user_id)` (lee, no consume) y endpoint que lo expone.
  Rutas legales son estáticas e independientes.

## Errores y bordes

- `/api/fixtures` puede dar 503 (cargando) → Live Match muestra "cargando…".
- `/api/quota` falla → la barra queda en su último valor o "—"; no rompe la app.
- Google OAuth sin provider habilitado → error visible en `auth-msg`.
- `DELETE /api/session` 403 (no debería pasar para el dueño) → aviso y no resetea.
- Auto-refresh: se limpia al cambiar de vista y al `exitApp` para no acumular timers.

## Dependencias externas (acción del usuario)

- **Google OAuth**: habilitar el provider Google en Supabase
  (Authentication → Providers → Google) con Client ID/Secret de Google Cloud, y
  agregar el dominio/redirect (`https://scoutai-b7gn.onrender.com`) a las URLs
  permitidas. Sin esto, el botón muestra error en vez de loguear.

## Fuera de alcance (YAGNI)

- Theme claro / toggle de tema.
- Marcadores en vivo reales (minuto a minuto): Live Match usa los fixtures
  existentes con tags `[EN CURSO]`, no un feed en vivo nuevo.
- Plan Pro real / pagos.
- API pública.

## Verificación

- Backend: arrancar la app, `GET /api/quota` con y sin token (200/401),
  `GET /terminos|/privacidad|/datos` devuelven HTML 200. `py_compile` limpio y
  los tests existentes siguen verdes.
- Frontend: cada ítem del sidebar abre su vista; cada link/boton del inventario
  hace algo coherente; barra de uso refleja `/api/quota`; Live Match parsea y
  refresca; AI Combos dispara combinada; quick-actions envían. Verificación
  manual en navegador (no hay suite de UI).
