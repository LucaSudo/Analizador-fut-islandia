# ScoutAI Frontend Redesign — Spec

**Date:** 2026-06-02  
**File objetivo:** `backend/test_chat.html`  
**Enfoque:** Opción A — rewrite completo con Tailwind CDN, archivo único

---

## 1. Contexto

El frontend actual (`test_chat.html`) usa CSS vanilla con variables custom. El rediseño aplica los diseños generados en Stitch: nueva landing, auth card, y dashboard con sidebar + widgets estadísticos.

No se cambia nada en el backend. Toda la lógica de Supabase auth y el fetch SSE al `/api/chat` se mantiene idéntica.

---

## 2. Stack frontend

| Elemento | Actual | Nuevo |
|---|---|---|
| CSS | Variables custom | Tailwind CDN (`cdn.tailwindcss.com?plugins=forms,container-queries`) |
| Tipografía | System font | Sora (display/headlines), Inter (body), JetBrains Mono (labels/datos) |
| Íconos | Emojis | Material Symbols Outlined (Google CDN) |
| Estructura | 3 divs en 1 archivo | 3 divs en 1 archivo (sin cambio estructural) |

**Paleta de colores (Tailwind custom config):**
```
background:              #0e1417
surface-container:       #1a2123
surface-container-low:   #161d1f
surface-container-lowest:#090f12
surface-variant:         #2f3639
charcoal:                #121214
primary:                 #a4e6ff
primary-container:       #00d1ff
neon-accent:             #00FFC2
on-surface:              #dde3e7
on-surface-variant:      #bbc9cf
outline-variant:         #3c494e
error:                   #ffb4ab
glass-border:            rgba(255,255,255,0.1)
```

**Clases utilitarias custom (CSS puro, no Tailwind):**
- `.glass-card` / `.glass-panel` — `background: rgba(18,18,20,0.7); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.1)`
- `.glow-button` — `box-shadow: 0 0 20px rgba(0,209,255,0.2)`
- `.bg-grid` — grid de líneas sutiles `rgba(255,255,255,0.03)`
- `.hero-glow` — `text-shadow: 0 0 30px rgba(0,209,255,0.5)`

---

## 3. Sección: Landing Page (`#landing`)

### Navbar (sticky, blur)
- Logo `ScoutAI` en `font-display-lg text-primary`
- Links decorativos: Markets, Predictions, Insights, Pricing (sin funcionalidad)
- Botones: **Login** (texto) → abre auth overlay en modo login | **Get Started** (filled) → abre auth en modo register

### Hero
- Badge: `auto_awesome` icon + "NUEVA INTELIGENCIA PREDICTIVA"
- H1: "Análisis de fútbol con **inteligencia artificial**" (palabra resaltada en `text-primary-container`)
- Párrafo descriptivo
- 2 CTAs: "Empezar gratis" (filled, glow) + "Ya tengo cuenta" (ghost)
- Mockup image card con badge flotante "Win Probability 87.4%" (imagen placeholder de Google AI)
- Fondo: `.bg-grid` + radial glow azul centrado

### Features (3 cards glass)
| Card | Ícono MS | Título | Color icono |
|---|---|---|---|
| SofaScore data | `database` | Datos reales de SofaScore | `text-primary` |
| IA avanzada | `psychology` | IA avanzada | `text-neon-accent` |
| Combinadas | `auto_awesome_motion` | Combinadas automáticas | `text-primary-container` |

### Pricing (2 cards)
- **Free:** border `glass-border`, label "PLAN ACTUAL", $0/mes, 3 features con `check_circle` icon, botón outline primary
- **Pro:** borde con gradiente `from-primary to-neon-accent` (1px), bg `background/90`, label "ELITE ACCESS", badge "PRÓXIMAMENTE" animated-pulse, botón filled primary

### CTA Section
- Bloque glass con radial glow decorativo
- Texto: "¿Listo para transformar tu análisis deportivo?"
- Botón: "Empezar ahora gratis"

### Footer
- Logo + copyright "© 2026 ScoutAI Intelligence Platform"
- Links: Términos · Privacidad · Datos · API Access (decorativos)

---

## 4. Sección: Auth Overlay (`#auth-overlay`)

Reemplaza el `#auth-modal` actual. Misma lógica de apertura/cierre y toda la lógica Supabase se mantiene intacta.

**Estructura visual:**
- Fondo: `position: fixed; inset: 0; backdrop-filter: blur(8px); background: rgba(0,0,0,0.65)`
- Tarjeta `.glass-panel` centrada, `max-w-[440px]`, `rounded-xl p-8`
- Header: ícono `auto_awesome` + "ScoutAI" + subtítulo "Plataforma de Inteligencia"
- Tabs: "Iniciar sesión" / "Registrarse" con underline animado
- Campo email: ícono `person` prefijo, input `bg-charcoal`
- Campo contraseña: ícono `lock` prefijo + toggle `visibility` (show/hide)
- Botón submit: filled `bg-primary-container`, ícono `arrow_forward` sufijo
- Separador "O CONTINÚA CON" + 2 botones decorativos (Google, API Key) — **sin funcionalidad real**
- Footer: links Términos · Privacidad · Soporte (decorativos)

**Campos adicionales en modo registro:**
- Campo "Nombre de usuario" (mismo estilo, ícono `badge`)

**Lógica Supabase:** idéntica al código actual (`signInWithPassword`, `signUp`, `onAuthStateChange`).

---

## 5. Sección: Dashboard (`#app`)

Layout de pantalla completa (`height: 100vh; display: flex`).

### Sidebar (264px, fixed left)
**Nav items** (solo Dashboard es activo; resto decorativos):
| Ícono MS | Label | Estado |
|---|---|---|
| `dashboard` | Dashboard | Activo (highlight primary) |
| `settings_input_component` | Live Match | Decorativo |
| `auto_awesome` | AI Combos | Decorativo |
| `history` | History | Decorativo |
| `settings` | Settings | Decorativo |

- Botón "Upgrade to Elite" (decorativo)
- Link `help` Support (decorativo)
- Link `logout` Sign Out → llama a `logout()` existente

### Header (topbar)
- Badge plan: "PLAN ACTUAL · Gratuito"
- Barra de progreso: análisis usados en la sesión actual (ej: "2/10 Analyses Used") — contador JS que incrementa con cada respuesta recibida, se resetea al recargar. No está sincronizado con el backend (no hay endpoint de quota), es orientativo.
- Avatar circular (inicial del nombre) + nombre de usuario
- Botón "↺ Nueva sesión" → llama a `newChat()` existente

### Panel principal (2 columnas)

#### Columna izquierda — Chat (flex-1.5)
- Header: "Predictive Analysis Engine" + subtítulo + ícono `bolt`
- Área de mensajes scrollable
- Burbujas rediseñadas:
  - **Usuario:** `bg-surface-variant`, rounded, sin etiqueta
  - **Bot:** glass panel con borde izquierdo `border-l-2 border-l-primary`, ícono `auto_awesome` circular
  - **Analysis type:** mantiene estructura actual (header + body párrafos) con el nuevo estilo
  - **Loading:** 3 dots bouncing + ícono `psychology` animado (reemplaza spinner actual)
- Input: `bg-charcoal`, rounded-xl, botón send con ícono `send`
- Quick actions debajo: "Hot Matches" + "High Confidence" (decorativos)

#### Columna derecha — Widgets (flex-1, scrollable)

Los 3 widgets arrancan con estado vacío (placeholder) y se actualizan cuando el backend devuelve `type: "analysis"`.

**Widget 1 — Corners Density**
- Header: ícono `flag` + "Corners Density" + valor promedio `text-neon-accent`
- Visualización: 7 barras verticales (últimos N partidos)
- Datos parseados: regex busca "corners" y valores numéricos en el texto del análisis
- Estado vacío: barras al 10% con `opacity-30`

**Widget 2 — Shots on Target**
- Header: ícono `sports_soccer` + "Shots on Target" + % conversión
- Visualización: 2 barras horizontales (local vs visitante)
- Datos parseados: regex busca "remates" o "disparos" y valores por equipo
- Estado vacío: barras al 0%

**Widget 3 — Discipline Index**
- Header: ícono `style` + "Discipline Index" + tarjeta amarilla/roja decorativas
- Visualización: donut SVG + texto de riesgo
- Datos parseados: regex busca "tarjetas" y nivel de riesgo
- Estado vacío: donut al 0%

**Parseo de respuesta:** función `parseAnalysisForWidgets(text)` que extrae stats con regex del texto del análisis. Si no encuentra datos, los widgets permanecen en estado vacío sin error.

---

## 6. Animaciones

- Scroll reveal en secciones de landing (IntersectionObserver, `opacity-0 translate-y-10` → visible)
- Hover glow en cards
- Badge "PRÓXIMAMENTE" con `animate-pulse`
- Hero badge con `auto_awesome` icon
- Loading dots con `animate-bounce` con delays escalonados

---

## 7. Responsive

- Mobile (<640px): sidebar oculto → topbar con hamburger (decorativo), layout de columna única
- Tablet (640–1024px): sidebar colapsa, layout adaptado
- Desktop (>1024px): layout completo con sidebar visible

---

## 8. Lo que NO cambia

- Toda la lógica Supabase (`signIn`, `signUp`, `onAuthStateChange`, `signOut`)
- El fetch SSE a `/api/chat` y el procesamiento de eventos (`status`, `response`, `error`, `done`)
- La lógica de `newChat()` y `sessionId`
- El nombre del archivo (`test_chat.html`)
- El `BASE_URL` y las keys de Supabase

---

## 9. Criterio de éxito

1. La landing se ve idéntica al diseño Stitch (glassmorphism, colores, tipografía)
2. El auth overlay funciona con Supabase (login + register)
3. El dashboard muestra el chat funcional con el nuevo layout de sidebar
4. Los widgets muestran datos reales al recibir una respuesta de análisis
5. No hay regresiones en la funcionalidad de chat/análisis/combinadas
