# Plan estético de la web — para ejecutar al pie de la letra

Redactado el 18-nov-2026. Ejecutor previsto: un agente (Sonnet) en una sola jornada.
Alcance: **solo presentación** (HTML de plantillas, CSS, `api/static/charts.js`, textos de
títulos y bajadas). No cambia datos, cálculos ni rutas.

## Estado (actualízalo al cerrar cada fase)

| Fase | Contenido | Estado | Commit |
|---|---|---|---|
| F0 | Preparación y línea base | hecha (sin cambios de código) | — |
| F1 | Tokens, tipografía y base | hecha | bdb5693 |
| F2 | Encabezado y navegación | hecha | 02de8ad |
| F3 | Componentes (CSS + macros) | hecha | 8eb93dc |
| F4 | Estándar de gráficos (`charts.js`) | hecha | 4da81dd |
| F5a | Páginas grupo A | hecha | 9ef836d |
| F5b | Páginas grupo B | hecha | bb7733d |
| F5c | Páginas grupo C | hecha | 08a77c1 |
| F6 | Celular y accesibilidad | hecha | 484f8c6 |
| F7 | Limpieza y registros | hecha | ver git log |

Si se acaba el tiempo, el orden ya prioriza lo más visible: F1–F4 mejoran todas las páginas a
la vez (los estilos viejos quedan mapeados a los nuevos); F5 migra página por página.

---

## 0. Reglas que no se discuten

1. **Rama:** trabaja y haz commit **solo** en `feature/web-api-skeleton`. Nunca en `main`.
   Un commit por fase (o subfase), con push al terminar cada una.
2. **Servidor:** el usuario tiene el suyo en `:8000`; **no lo toques ni lo reinicies**. Levanta
   el tuyo en `:8001` en segundo plano:
   `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m uvicorn api.main:app --port 8001`.
   Las plantillas y el CSS se leen del disco en cada request; **cambios en `api/deps.py` u otro
   `.py` exigen reiniciar tu servidor**. Al terminar el día, mátalo.
3. **Contrato de plantillas:** no borres ni renombres ningún `id=`, `name=`, atributo `hx-*` ni
   el primer argumento de `Plotly.newPlot(...)`. Se verifica con `scripts/ui_guard.py`
   (ver F0). Puedes mover elementos y cambiar clases, salvo las **clases con comportamiento**:
   `eeff-form` (la usa `hx-include=".eeff-form"` en `compare.html`), `cmp-chk`, `row-click`,
   `htmx-indicator`, `combo`, `combo-results`, `combo-list`, `combo-item`, `js-plotly-plot`.
   Esas se conservan; si quieres el estilo nuevo, **agrega** la clase nueva al lado.
4. **Atributos `value=`:** nunca apliques filtros de presentación (`|nombre`, `|mes_es`…) dentro
   de un `value="..."` de `<input>` u `<option>`: el servidor busca por ese valor exacto. Solo en
   el texto visible.
5. **Nada de texto inventado:** no agregues cifras ni conclusiones nuevas. Las frases de lectura
   (`.lectura`) solo se escriben con variables que la plantilla **ya recibe**, y solo donde este
   plan lo indica. Los párrafos explicativos existentes no se borran: se pueden mover a un
   bloque plegable `ui.metodo`.
6. **Formato chileno siempre:** miles con punto y decimal con coma (filtro `es`, `INF.fmt` en JS).
   Fechas con `|mes_es` ("sep 2026"). Excepción: valores numéricos dentro de CSS (`--w: 45.0%`)
   usan punto: genéralos con `'%.1f'|format(x)`, nunca con `es`.
7. **Verde y rojo solo para signo o mejora/deterioro**, nunca para destacar una cifra
   "importante". Lo importante se destaca con peso tipográfico y tamaño.
8. **Sin dependencias en tiempo de ejecución desde internet.** La web debe funcionar sin red:
   fuentes e íconos se descargan una vez a `api/static/vendor/` (F1, F3). Sin frameworks nuevos
   (nada de Tailwind, Bootstrap, React). Plotly sigue en 2.35.2 local.
9. **Idioma:** todo texto visible en castellano de Chile, oración simple, sin mayúsculas sostenidas
   (salvo siglas). Las respuestas al usuario, en castellano.
10. **Después de cada fase:** `pytest -q` (76 tests deben pasar), `ui_guard.py check` (debe decir
    "Contrato intacto"), capturas de las páginas tocadas y lectura de los recortes. No hagas
    commit con un 500 o con una regresión visual que viste.
11. **No toques:** `db/`, `collectors/`, `models/`, `scheduler/`, `config/` (salvo lo indicado),
    la lógica de `api/routers/` (salvo los campos `"icon"` de `api/routers/web/_shared.py`, F3),
    los tests.

## 1. Diagnóstico (por qué la web se ve poco amigable)

Visto en capturas del 17 y 18-nov (1.440 px y 390 px):

1. **Navegación plana de 13 enlaces** sin agrupar ni marcar la página actual. En celular ocupa
   cinco líneas antes del contenido.
2. **Jerarquía tipográfica débil:** hay muchos tamaños sueltos (0,66 a 1,85 rem) y 455 estilos
   en línea. Los textos explicativos (`.lead`, 0,72 rem ≈ 11,5 px) son muros de letra chica:
   `/derivados` tiene 155 textos de menos de 12 px.
3. **Cinco estilos de tabla distintos** (`eeff-table`, `bank-table`, `evol-table`, `rank-table`,
   `cmp-ratios`), 262 `text-align` en línea, sin encabezados fijos en tablas largas.
4. **Tres maneras de mostrar una cifra clave** (`kpi-card`/`kpi-big`, `afp-stats`, `kpi-row`)
   con alturas y alineaciones distintas.
5. **Gráficos sin marco narrativo:** título en gris tenue, sin unidad ni período uniformes, sin
   fuente ni fecha al pie; algunos con leyendas donde convendría etiqueta directa, uno con
   doble eje (`/banco-central`, comparar) y uno con color redundante (`/salud`: el ROE va en el
   eje y además en el color).
6. **Nombres en mayúsculas sostenidas** (CMF: "BANCO DE CRÉDITO E INVERSIONES", "CUPRUM"),
   que se leen como gritos y alargan las filas.
7. **Hubs con emojis** que Windows dibuja distinto de un lugar a otro; la marca usa la bandera 🇨🇱,
   que en Windows aparece como "CL".
8. **Estados vacíos y de carga iguales** (`.loading` para "Cargando…" y para "No hay datos").
9. **Contenedor de 1.100 px:** las tablas anchas (`/productos`, FECU) quedan apretadas; los
   textos largos no tienen un ancho de lectura cómodo.
10. **Celular:** sin menú plegable, cifras en filas desordenadas y títulos de tres líneas.

## 2. Principios (storytelling financiero aplicado)

Toda página y toda sección responden, en este orden: **¿qué pasó? → ¿cuánto? → ¿comparado con
qué? → ¿a qué fecha y según quién?** La referencia de estilo es la de los informes de
estabilidad financiera de los bancos centrales y la prensa económica de primer nivel (FT,
The Economist): títulos que dicen qué se ve, subtítulo con unidad y período, fuente al pie,
anotaciones en lo que importa y tinta mínima en lo demás (Tufte, Few; IBCS para tablas).

Reglas operativas:

- **Pirámide invertida por página:** encabezado (título, bajada de 1–2 líneas, fechas) →
  franja de 3 a 5 cifras clave → gráfico principal → desgloses → tablas de detalle →
  metodología plegada.
- **Una pregunta por sección.** La sección abre con `sec-head` (título + fecha de corte) y,
  donde el plan lo indica, una `.lectura` de 1 a 3 frases con cifras de la propia plantilla.
- **Cada cifra con su fecha y su fuente:** usa el global `corte(fecha, fuente)` (ya existe en
  `api/deps.py`; produce "dato a sep 2026 · Banco Central").
- **Comparación siempre presente:** vs. 12 meses, vs. mediana, vs. referencia (0, 100, meta 3%).
- **El color sigue a la entidad**, no al rango: Cuprum es siempre del mismo color en todas las
  páginas (`INF.AFP`), el Fondo A siempre rojo, el E siempre índigo (`INF.FONDO`).
- **Foco y contexto:** a lo más 5–7 series con color; el resto en gris de contexto.
- **Etiqueta directa antes que leyenda** cuando hay 5 series o menos.
- **Nada de doble eje, tortas ni 3D.** Barras desde cero. Orden por valor salvo que exista un
  orden natural (tiempo, plazo, Fondo A→E).
- **Diferencias, no repeticiones:** en tablas financieras, columnas Δ con signo y color; totales
  en negrita y fondo levemente distinto; números alineados a la derecha con dígitos tabulares.
- **Letra mínima: 12 px.** Nada visible por debajo (se mide con `ui_capturas.py`).

---

## F0 · Preparación y línea base (15 min)

1. `git status` limpio y rama `feature/web-api-skeleton` (`git branch --show-current`).
2. Levanta tu servidor en `:8001` (regla 2) y espera el 200 de `http://127.0.0.1:8001/`.
3. Línea base del contrato: `.venv/Scripts/python.exe scripts/ui_guard.py snapshot`.
   (Reporta la deuda actual: 455 estilos en línea, 262 `text-align`, 272 colores fijos.)
4. Capturas de antes: `.venv/Scripts/python.exe scripts/ui_capturas.py --label antes`
   (26 vistas × escritorio y celular, ~20 min; puedes lanzarlo en segundo plano y seguir).
   Quedan en `%TEMP%\inf_capturas\antes\` con recortes `_0.png`, `_1.png`… legibles con Read, y
   `chequeo.txt` con `desborde`, `letra_chica` y `culpables` por vista.
5. Lee al menos: `panel_escritorio_0`, `derivados_escritorio_0`, `afp_movil_0`,
   `productos_escritorio_0`, para tener la referencia visual.

Nota sobre las herramientas (ya probadas):
- `ui_capturas.py --solo /afp,/derivados` limita a esas URLs; `--sin-movil` acelera.
- La vista de celular sale de `api/static/ui_movil.html` (iframe de 390 px), porque Chrome sin
  ventana no baja de 500 px. No la borres.
- El chequeo lo calcula un script al final de `base.html` (`?chequeo_ui=1`). No lo borres.

## F1 · Tokens, tipografía y base (40 min)

### 1.1 Fuente Inter (local)

```bash
mkdir -p api/static/vendor/fonts
curl -sSfL -o api/static/vendor/fonts/inter-latin-wght-normal.woff2 \
  https://cdn.jsdelivr.net/npm/@fontsource-variable/inter@5.1.0/files/inter-latin-wght-normal.woff2
curl -sSfL -o api/static/vendor/fonts/inter-latin-ext-wght-normal.woff2 \
  https://cdn.jsdelivr.net/npm/@fontsource-variable/inter@5.1.0/files/inter-latin-ext-wght-normal.woff2
```
Verifica que pesen ~48 KB y ~80 KB y que empiecen con `wOF2` (`head -c 4`). Crea
`api/static/vendor/LICENCIAS.md` con: Inter (SIL Open Font License 1.1, rsms/inter, vía
@fontsource-variable/inter 5.1.0) y Lucide (ISC, lucide-static 0.460.0, F3).

En `base.html`, dentro de `<head>`, **antes** del `<link rel="stylesheet">`:
```html
<link rel="preload" href="/static/vendor/fonts/inter-latin-wght-normal.woff2" as="font" type="font/woff2" crossorigin>
<meta name="theme-color" content="#0b1220">
```

### 1.2 Reemplaza el bloque `:root` y la base de `api/static/app.css`

Reemplaza desde el inicio del archivo hasta antes de `.topbar {` (incluye `:root`, `*`, `a`,
`body`, el bloque de `td, .kpi-big…`, el de campos de formulario y `input::placeholder`) por:

```css
/* =========================================================================================
   1 · TOKENS (docs/PLAN_ESTETICA.md). Contrastes WCAG verificados sobre --bg/--s1/--s2:
   --ink 16:1 · --ink-2 12:1 · --muted 7,3:1 · --faint 5,5:1 (no usar --faint sobre --s3).
   ========================================================================================= */
@font-face {
    font-family: "Inter"; font-style: normal; font-weight: 100 900; font-display: swap;
    src: url("/static/vendor/fonts/inter-latin-wght-normal.woff2") format("woff2");
    unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC,
                   U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;
}
@font-face {
    font-family: "Inter"; font-style: normal; font-weight: 100 900; font-display: swap;
    src: url("/static/vendor/fonts/inter-latin-ext-wght-normal.woff2") format("woff2");
    unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308,
                   U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF;
}

:root {
    /* Superficies (de atrás hacia adelante) */
    --bg: #0b1220;  --s1: #111a2c;  --s2: #172238;  --s3: #1e2b45;
    --line: #2a3a56;  --line-strong: #3b4d6b;
    /* Tinta */
    --ink: #e8eef7;  --ink-2: #c5d0e0;  --muted: #94a3b8;  --faint: #7d8ca3;
    /* Acento y semántica */
    --accent: #5cb3ff;  --accent-weak: rgba(92, 179, 255, 0.14);
    --pos: #4ade80;  --neg: #f87171;  --warn: #fbbf24;  --info: #60a5fa;
    --pos-bg: rgba(74, 222, 128, 0.10);  --neg-bg: rgba(248, 113, 113, 0.10);  --warn-bg: rgba(251, 191, 36, 0.10);
    /* Alias de compatibilidad: las plantillas y reglas viejas usan estos nombres */
    --panel: var(--s1);  --border: var(--line);  --ok: var(--pos);

    /* Tipografía: escala modular sobre 16 px. Mínimo visible: --fs-xs (12 px). */
    --font: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
    --font-mono: "Cascadia Mono", Consolas, ui-monospace, monospace;
    --fs-xs: 0.75rem;   /* 12 */  --fs-sm: 0.8125rem; /* 13 */  --fs-base: 0.9375rem; /* 15 */
    --fs-md: 1.0625rem; /* 17 */  --fs-lg: 1.25rem;   /* 20 */  --fs-xl: 1.5rem;      /* 24 */
    --fs-2xl: 1.875rem; /* 30 */
    --lh-tight: 1.2;  --lh: 1.5;  --lh-prose: 1.65;

    /* Espaciado (base 4 px) */
    --sp-1: 0.25rem; --sp-2: 0.5rem; --sp-3: 0.75rem; --sp-4: 1rem; --sp-5: 1.5rem;
    --sp-6: 2rem; --sp-7: 3rem; --sp-8: 4rem;

    /* Forma */
    --r-sm: 6px; --r: 10px; --r-lg: 14px;
    --shadow-pop: 0 12px 32px rgba(0, 0, 0, 0.45);

    /* Diseño */
    --w-page: 1320px; --w-prose: 72ch; --gutter: clamp(16px, 3vw, 32px); --header-h: 56px;
    --viz-sm: 240px; --viz-md: 320px; --viz-lg: 420px;
}

/* =========================================================================================
   2 · BASE
   ========================================================================================= */
* { box-sizing: border-box; }
html { font-size: 100%; -webkit-text-size-adjust: 100%; scroll-padding-top: calc(var(--header-h) + 64px); }
body {
    margin: 0; font-family: var(--font); font-size: var(--fs-base); line-height: var(--lh);
    color: var(--ink); background: var(--bg); -webkit-font-smoothing: antialiased;
}
h1, h2, h3, h4 { color: var(--ink); line-height: var(--lh-tight); }
h1 { font-size: var(--fs-2xl); font-weight: 700; letter-spacing: -0.015em; margin: 0 0 var(--sp-2); }
h2 { font-size: var(--fs-lg); font-weight: 650; letter-spacing: -0.01em; margin: 0 0 var(--sp-3); }
h3 { font-size: var(--fs-md); font-weight: 600; margin: 0 0 var(--sp-3); }
h4 { font-size: var(--fs-base); font-weight: 600; color: var(--ink-2); margin: 0 0 var(--sp-2); }
p { margin: 0 0 var(--sp-3); }
a { color: var(--accent); text-underline-offset: 2px; text-decoration-thickness: 1px; }
a:hover { text-decoration-thickness: 2px; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
code { font-family: var(--font-mono); font-size: 0.9em; background: var(--s2); padding: 0.05em 0.35em; border-radius: 4px; }
strong, b { font-weight: 650; }
td, .num, .stat-valor, .kpi-big, .afp-stats b, .kpi-row b { font-variant-numeric: tabular-nums; }
.num, td.num, th.num { text-align: right; white-space: nowrap; }

select, input[type="text"], input[type="search"], input[type="number"], input[type="date"],
input:not([type]), textarea {
    background: var(--s2); color: var(--ink); border: 1px solid var(--line-strong);
    border-radius: var(--r-sm); padding: 0.45rem 0.7rem; font: inherit; font-size: var(--fs-sm);
    min-height: 38px; color-scheme: dark;
}
select {
    appearance: none; -webkit-appearance: none; padding-right: 2rem;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
    background-repeat: no-repeat; background-position: right 0.65rem center;
}
select:hover, input:hover { border-color: var(--muted); }
select:focus, input:focus, textarea:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-weak); }
input::placeholder { color: var(--faint); }
input[type="checkbox"] { accent-color: var(--accent); width: 16px; height: 16px; }

@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { transition: none !important; animation: none !important; scroll-behavior: auto !important; }
}
```

### 1.3 Ajustes inmediatos en reglas existentes de `app.css`

- `.container`: `max-width: var(--w-page); margin: 0 auto; padding: var(--sp-5) var(--gutter) var(--sp-8);`
- `.subtitle`: `font-size: var(--fs-md); color: var(--ink-2); max-width: 68ch; line-height: 1.55; margin: 0 0 var(--sp-4);`
- `.lead`: `font-size: var(--fs-sm); color: var(--muted); font-style: normal; line-height: 1.55;`
- Toda regla con `font-size` menor a `0.75rem` sube a `var(--fs-xs)`: `.lead`, `.combo-meta`,
  `.hub-meta`, `.kpi-sub`, `.eeff-meta`, `.minimap-top`, `.corte`, `.badge`, `.mock-note`, etc.
  Comprueba con `ui_guard.py check` → `font_size_css_menor_a_12px` debe quedar en 0.
- Reemplaza en el CSS los colores fijos que ya tienen token: `#0f172a`→`var(--bg)`,
  `#1e293b`→`var(--s1)`, `#334155`→`var(--line)`, `#e2e8f0`→`var(--ink)`, `#94a3b8`→`var(--muted)`,
  `#38bdf8`→`var(--accent)`, `#64748b`→`var(--faint)` (este último no cumple contraste como texto).
- Borra la regla `.container > h2, section > h2:first-child { … }` (la reemplaza `.sec-head` en F3)
  **solo al terminar F5**; mientras, déjala.

### 1.4 Verificación F1
`pytest -q`; `ui_guard.py check`; capturas `--solo /,/afp,/derivados --sin-movil`; revisa que la
fuente cambió (Inter tiene la "1" con base y la "a" de dos pisos) y que nada se descuadró.
Commit: `Estética F1: tokens de diseño, Inter local y base tipográfica`.

## F2 · Encabezado y navegación (45 min)

### 2.1 Definición del menú en `api/deps.py`

Agrega al final de los filtros y globales (antes o después de `corte`):

```python
# Menú principal (docs/PLAN_ESTETICA.md F2). "hijos": (texto, url, descripción, prefijos extra).
NAV = [
    {"t": "Panel", "url": "/"},
    {"t": "Mercados", "hijos": [
        {"t": "Curvas de tasas", "url": "/curvas", "d": "Nominal y real: swaps y bonos del Estado"},
        {"t": "Banco Central", "url": "/banco-central", "d": "Series macro y expectativas"},
        {"t": "Derivados", "url": "/derivados", "d": "Exposición y riesgo de toda la economía"},
    ]},
    {"t": "Empresas", "hijos": [
        {"t": "Estados financieros", "url": "/eeff", "d": "Corporativos, seguros, intermediarios y AGF"},
        {"t": "Bancos", "url": "/banca", "d": "Balance y resultados por moneda"},
        {"t": "Comparar empresas", "url": "/comparar", "d": "Hasta tres empresas lado a lado"},
        {"t": "Ranking", "url": "/ranking", "d": "Las 15 mejores por indicador", "extra": ["/evolucion"]},
        {"t": "Salud financiera", "url": "/salud", "d": "Rentabilidad contra endeudamiento"},
        {"t": "Proyecciones", "url": "/proyecciones", "d": "Ingresos y resultados proyectados"},
    ]},
    {"t": "Inversionistas", "hijos": [
        {"t": "Inversión institucional", "url": "/inversion-institucional", "d": "AFP, seguros y fondos en un vistazo"},
        {"t": "AFP", "url": "/afp", "d": "Rentabilidad, comisiones y cartera"},
        {"t": "Fondos de pensiones", "url": "/fondos", "d": "Retornos y composición por fondo"},
        {"t": "Cartera de seguros", "url": "/seguros/cartera", "d": "Asset allocation de las aseguradoras"},
        {"t": "Derivados institucionales", "url": "/inversion-institucional/derivados", "d": "Monitor de swaps de las AFP"},
        {"t": "Inversión por emisor", "url": "/inversion-institucional/emisor", "d": "Quién invierte en cada emisor"},
        {"t": "Emisores en común", "url": "/inversion-institucional/emisores", "d": "Aseguradoras y fondos mutuos"},
    ]},
    {"t": "Fondos y productos", "hijos": [
        {"t": "Productos de inversión", "url": "/productos", "d": "Rentabilidad, riesgo y costo comparables", "extra": ["/producto"]},
        {"t": "Fondos mutuos", "url": "/fondos-mutuos", "d": "Rendimiento y cartera por fondo"},
    ]},
    {"t": "Metodología", "url": "/metodologia"},
]


def _nav_urls():
    for g in NAV:
        if "url" in g:
            yield g["url"], [g["url"]]
        for h in g.get("hijos", []):
            yield h["url"], [h["url"], *h.get("extra", [])]


def _nav_activo(path: str, url: str) -> bool:
    """Activo = el ítem de prefijo más largo que calza con la ruta (así
    /inversion-institucional/derivados no marca también /inversion-institucional)."""
    def calza(p):
        return path == p or (p != "/" and path.startswith(p.rstrip("/") + "/"))
    mejor, largo = None, -1
    for u, prefijos in _nav_urls():
        for p in prefijos:
            if calza(p) and len(p) > largo:
                mejor, largo = u, len(p)
    return mejor == url


def _nav_grupo_activo(path: str, grupo: dict) -> bool:
    return any(_nav_activo(path, h["url"]) for h in grupo.get("hijos", []))


templates.env.globals.update(NAV=NAV, nav_activo=_nav_activo, nav_grupo_activo=_nav_grupo_activo)
```

Ojo: la clave es `"hijos"`, no `"items"` (en Jinja `g.items` es el método del dict).
Reinicia tu servidor después de editar `deps.py`.

### 2.2 Encabezado nuevo en `base.html`

Reemplaza el bloque `<header class="topbar"> … </header>` completo por:

```html
<a class="skip" href="#contenido">Saltar al contenido</a>
<header class="app-bar">
    <div class="app-bar-in">
        <a class="marca" href="/" aria-label="INF_FIN_IA, inicio">
            <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="13" width="4" height="9" rx="1"/><rect x="10" y="8" width="4" height="14" rx="1"/><rect x="18" y="3" width="4" height="19" rx="1"/></svg>
            <span>INF_FIN_IA</span><span class="marca-tag">Chile</span>
        </a>
        <button class="nav-toggle" type="button" aria-expanded="false" aria-controls="nav-principal">Menú</button>
        <nav id="nav-principal" class="nav" aria-label="Principal">
            {% set ruta = request.url.path %}
            {% for g in NAV %}
                {% if g.url %}
                <a class="nav-a{{ ' is-activo' if nav_activo(ruta, g.url) }}" href="{{ g.url }}"{% if nav_activo(ruta, g.url) %} aria-current="page"{% endif %}>{{ g.t }}</a>
                {% else %}
                <details class="nav-g{{ ' is-activo' if nav_grupo_activo(ruta, g) }}">
                    <summary>{{ g.t }}</summary>
                    <div class="nav-pop">
                        {% for h in g['hijos'] %}
                        <a href="{{ h.url }}"{% if nav_activo(ruta, h.url) %} aria-current="page"{% endif %}>{{ h.t }}<small>{{ h.d }}</small></a>
                        {% endfor %}
                    </div>
                </details>
                {% endif %}
            {% endfor %}
            <a class="nav-a nav-ext" href="/docs" target="_blank" rel="noopener">API ↗</a>
        </nav>
    </div>
</header>
```

- `<main class="container">` pasa a `<main class="container" id="contenido">`.
- Mantén `{{ asof_banner(request.url.path) }}` como primera línea de `<main>`.
- El `<footer>` se mantiene; solo cambia su CSS (2.4).
- Al final de `<body>` (después del script de chequeo, antes de `</body>`), agrega:

```html
<script>
/* Menú: botón en celular, un solo desplegable abierto, cierre con clic afuera o Escape.
   Índice de página (.toc): marca la sección visible. */
(function () {
    var nav = document.getElementById("nav-principal"), btn = document.querySelector(".nav-toggle");
    if (btn && nav) {
        btn.addEventListener("click", function () {
            var abierto = nav.classList.toggle("is-abierto");
            btn.setAttribute("aria-expanded", String(abierto));
        });
    }
    var grupos = [].slice.call(document.querySelectorAll(".nav-g"));
    grupos.forEach(function (g) {
        g.addEventListener("toggle", function () {
            if (g.open) { grupos.forEach(function (o) { if (o !== g) { o.open = false; } }); }
        });
    });
    document.addEventListener("click", function (e) {
        if (!e.target.closest(".nav-g")) { grupos.forEach(function (g) { g.open = false; }); }
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { grupos.forEach(function (g) { g.open = false; }); }
    });

    var links = [].slice.call(document.querySelectorAll(".toc a[href^='#'], .banca-minimap a[href^='#']"));
    if (!links.length || !("IntersectionObserver" in window)) { return; }
    var mapa = {};
    links.forEach(function (a) {
        var el = document.getElementById(a.getAttribute("href").slice(1));
        if (el) { mapa[el.id] = a; }
    });
    var obs = new IntersectionObserver(function (entradas) {
        entradas.forEach(function (e) {
            if (e.isIntersecting && mapa[e.target.id]) {
                links.forEach(function (l) { l.classList.remove("is-activo"); });
                mapa[e.target.id].classList.add("is-activo");
            }
        });
    }, { rootMargin: "-30% 0px -60% 0px" });
    Object.keys(mapa).forEach(function (id) { obs.observe(document.getElementById(id)); });
})();
</script>
```

### 2.3 CSS del encabezado

Reemplaza las reglas `.topbar`, `.brand`, `.topbar nav`, `.topbar nav a`, `.topbar nav a:hover` por:

```css
/* =========================================================================================
   3 · ENCABEZADO Y NAVEGACIÓN
   ========================================================================================= */
.skip { position: absolute; left: -999px; top: 8px; z-index: 100; background: var(--accent); color: #04111f;
        padding: 0.5rem 0.8rem; border-radius: var(--r-sm); font-weight: 600; text-decoration: none; }
.skip:focus { left: 8px; }
.app-bar { position: sticky; top: 0; z-index: 50; background: rgba(11, 18, 32, 0.92);
           backdrop-filter: saturate(140%) blur(8px); border-bottom: 1px solid var(--line); }
.app-bar-in { max-width: var(--w-page); margin: 0 auto; padding: 0 var(--gutter); height: var(--header-h);
              display: flex; align-items: center; gap: var(--sp-5); }
.marca { display: flex; align-items: center; gap: 0.55rem; color: var(--ink); text-decoration: none;
         font-weight: 700; letter-spacing: -0.01em; white-space: nowrap; }
.marca svg { width: 22px; height: 22px; fill: var(--accent); }
.marca-tag { font-size: var(--fs-xs); font-weight: 600; color: var(--muted); border: 1px solid var(--line);
             border-radius: 999px; padding: 0 0.45rem; }
.nav { display: flex; align-items: center; gap: var(--sp-1); margin-left: auto; }
.nav-a, .nav-g > summary {
    display: inline-flex; align-items: center; gap: 0.35rem; height: 36px; padding: 0 0.7rem;
    border-radius: var(--r-sm); color: var(--ink-2); font-size: var(--fs-sm); font-weight: 500;
    text-decoration: none; cursor: pointer; list-style: none; white-space: nowrap;
}
.nav-g > summary::-webkit-details-marker { display: none; }
.nav-g > summary::after { content: ""; width: 6px; height: 6px; border-right: 1.5px solid currentColor;
    border-bottom: 1.5px solid currentColor; transform: rotate(45deg) translateY(-2px); opacity: 0.7; }
.nav-a:hover, .nav-g > summary:hover { background: var(--s2); color: var(--ink); }
.nav-a.is-activo, .nav-g.is-activo > summary { color: var(--ink); background: var(--s2); box-shadow: inset 0 -2px 0 var(--accent); }
.nav-g { position: relative; }
.nav-pop { position: absolute; top: calc(100% + 6px); left: 0; min-width: 300px; z-index: 60;
           background: var(--s1); border: 1px solid var(--line-strong); border-radius: var(--r);
           box-shadow: var(--shadow-pop); padding: var(--sp-2); display: grid; gap: 2px; }
.nav-pop a { display: block; padding: 0.55rem 0.7rem; border-radius: var(--r-sm); color: var(--ink);
             text-decoration: none; font-size: var(--fs-sm); font-weight: 500; }
.nav-pop a small { display: block; color: var(--muted); font-size: var(--fs-xs); font-weight: 400; margin-top: 2px; }
.nav-pop a:hover { background: var(--s2); }
.nav-pop a[aria-current="page"] { background: var(--accent-weak); }
.nav-ext { color: var(--muted); }
.nav-toggle { display: none; margin-left: auto; height: 36px; padding: 0 0.9rem; border-radius: var(--r-sm);
              background: var(--s2); color: var(--ink); border: 1px solid var(--line-strong); font: inherit;
              font-size: var(--fs-sm); font-weight: 600; cursor: pointer; }
@media (max-width: 1080px) {
    .nav-toggle { display: inline-flex; align-items: center; }
    .nav { display: none; position: absolute; top: var(--header-h); left: 0; right: 0; flex-direction: column;
           align-items: stretch; gap: 0; background: var(--s1); border-bottom: 1px solid var(--line);
           padding: var(--sp-3) var(--gutter) var(--sp-4); max-height: calc(100vh - var(--header-h)); overflow: auto; }
    .nav.is-abierto { display: flex; }
    .nav-a, .nav-g > summary { height: 44px; font-size: var(--fs-base); }
    .nav-pop { position: static; box-shadow: none; border: 0; background: transparent; min-width: 0;
               padding: 0 0 var(--sp-2) var(--sp-3); }
}
```

### 2.4 Pie y franja de frescura

Reemplaza `.footer` y `.footer .disclaimer`, y las reglas `.asof`, `.asof-warn`, `.asof-stale` por:

```css
.footer { border-top: 1px solid var(--line); color: var(--muted); font-size: var(--fs-xs);
          text-align: center; padding: var(--sp-6) var(--gutter); margin-top: var(--sp-7); line-height: 1.6; }
.footer .disclaimer { display: block; max-width: 72ch; margin: var(--sp-2) auto 0; }

/* "Datos al …" (api/freshness_banner.py): fila de chips; el atraso se marca en ámbar */
.asof { display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0 0 var(--sp-4); font-size: var(--fs-xs); }
.asof-item { display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.2rem 0.6rem;
             border: 1px solid var(--line); border-radius: 999px; color: var(--muted); background: var(--s1); }
.asof-item::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--pos); flex: none; }
.asof-item.asof-stale { color: var(--warn); border-color: rgba(251, 191, 36, 0.45); background: var(--warn-bg); }
.asof-item.asof-stale::before { background: var(--warn); }
```

### 2.5 Verificación F2
Reinicia tu servidor. Captura `/`, `/derivados`, `/inversion-institucional/derivados`, `/afp`,
`/producto/…` (usa `--solo /,/derivados,/inversion-institucional/derivados,/afp`) en escritorio
**y celular**. Revisa: el grupo correcto marcado, un solo ítem `aria-current`, el menú plegado
en celular, `desborde=false`. Abre a mano un desplegable en la captura: no se puede; revisa el
HTML con `curl -s http://127.0.0.1:8001/derivados | grep -c nav-pop` (debe dar 4).
Commit: `Estética F2: navegación agrupada, página activa, menú de celular y franja de frescura`.

## F3 · Componentes (90 min)

### 3.1 Íconos locales (Lucide)

```bash
mkdir -p api/static/vendor/icons
for i in building-2 landmark shield trending-up briefcase chart-pie piggy-bank chart-column scale search link-2 chart-line activity layers; do
  curl -sSfL -o api/static/vendor/icons/$i.svg https://cdn.jsdelivr.net/npm/lucide-static@0.460.0/icons/$i.svg
done
```
En `api/routers/web/_shared.py` cambia **solo** el valor de `"icon"`:

- `EEFF_CLASSES` y `PROY_CLASSES`: corporativos → `building-2`, bancos → `landmark`,
  seguros → `shield`, intermediarios → `trending-up`, agf → `briefcase`.
- `INST_CLASSES`:
  - Cartera de seguros → `chart-pie`
  - AFP → `piggy-bank`
  - Fondos de pensiones → `chart-column`
  - Derivados → `scale`
  - Inversión por emisor → `search`
  - Emisores en común → `link-2`

En las tres plantillas hub, `<span class="hub-icon">{{ c.icon }}</span>` (o equivalente) pasa a
`<span class="hub-ico ico-{{ c.icon }}" aria-hidden="true"></span>`.

### 3.2 Macros: crea `api/templates/partials/_ui.html`

```jinja
{#
  partials/_ui.html — Componentes de interfaz (docs/PLAN_ESTETICA.md F3).
  Uso: {% import "partials/_ui.html" as ui %} al inicio de cada plantilla que los use; también en
  los partials que llegan por HTMX (los macros no se heredan). `corte` es global (api/deps.py).
#}

{# Encabezado de página. migas = [("Estados financieros", "/eeff"), …]. id_titulo conserva el id
   que la página ya tenga en su <h1> (der-top, banca-top, fecu-top…). #}
{% macro page_head(titulo, bajada=none, migas=none, id_titulo=none) -%}
<header class="page-head">
    {% if migas %}<nav class="migas" aria-label="Ruta">{% for texto, url in migas %}<a href="{{ url }}">{{ texto }}</a><span aria-hidden="true">›</span>{% endfor %}<span aria-current="page">{{ titulo|striptags }}</span></nav>{% endif %}
    <h1{% if id_titulo %} id="{{ id_titulo }}"{% endif %}>{{ titulo }}</h1>
    {% if bajada %}<p class="bajada">{{ bajada }}</p>{% endif %}
    {% if caller is defined %}<div class="page-acciones">{{ caller() }}</div>{% endif %}
</header>
{%- endmacro %}

{# Cabecera de sección. `id` va en el <h2> (conserva los ids de ancla existentes). #}
{% macro sec_head(titulo, id=none, num=none, fecha=none, fuente=none, arriba="#contenido") -%}
<div class="sec-head">
    <h2{% if id %} id="{{ id }}"{% endif %}>{% if num %}<span class="sec-num">{{ num }}</span>{% endif %}{{ titulo }}</h2>
    {{ corte(fecha, fuente) if fecha }}
    {% if arriba %}<a href="{{ arriba }}" class="sec-arriba">↑ arriba</a>{% endif %}
</div>
{%- endmacro %}

{# Cifra clave. `valor` llega YA formateado (con |es). delta: número con signo para decidir
   flecha y color; delta_txt: el texto a mostrar. invertir=true si bajar es bueno (costos). #}
{% macro stat(label, valor, unidad=none, delta=none, delta_txt=none, ctx=none, fecha=none, fuente=none, invertir=false, tono=none) -%}
<div class="stat">
    <div class="stat-label">{{ label }}</div>
    <div class="stat-valor{{ ' ' ~ tono if tono }}">{{ valor }}{% if unidad %}<span class="stat-unidad">{{ unidad }}</span>{% endif %}</div>
    {% if delta is not none %}{% set bueno = (delta > 0) != invertir %}
    <div class="stat-delta {{ '' if delta == 0 else ('pos' if bueno else 'neg') }}">{{ '▲' if delta > 0 else ('▼' if delta < 0 else '■') }} {{ delta_txt if delta_txt is not none else delta }}</div>
    {% endif %}
    {% if ctx %}<div class="stat-ctx">{{ ctx }}</div>{% endif %}
    {{ corte(fecha, fuente) if fecha }}
</div>
{%- endmacro %}

{# Tarjeta de gráfico. Conserva el id del div del gráfico. alto: sm | md | lg (tokens). #}
{% macro viz(id, titulo, sub=none, fuente=none, fecha=none, alto="md", nota=none, clase="") -%}
<figure class="viz {{ clase }}">
    <figcaption class="viz-head">
        <span class="viz-titulo">{{ titulo }}</span>
        {% if sub %}<span class="viz-sub">{{ sub }}</span>{% endif %}
    </figcaption>
    <div id="{{ id }}" class="chart viz-cuerpo viz-{{ alto }}"></div>
    {% if fuente or fecha or nota %}
    <div class="viz-pie">{% if nota %}<span>{{ nota }}</span>{% endif %}{% if fuente %}<span>Fuente: {{ fuente }}</span>{% endif %}{{ corte(fecha) if fecha }}</div>
    {% endif %}
</figure>
{%- endmacro %}

{% macro aviso(nivel="info") -%}
<div class="aviso aviso--{{ nivel }}" role="note"><div>{{ caller() }}</div></div>
{%- endmacro %}

{% macro metodo(titulo="Cómo se calcula y de dónde sale", abierto=false) -%}
<details class="metodo"{% if abierto %} open{% endif %}><summary>{{ titulo }}</summary><div>{{ caller() }}</div></details>
{%- endmacro %}

{% macro vacio(texto) -%}<p class="vacio">{{ texto }}</p>{%- endmacro %}

{# Barra proporcional dentro de una celda. tono: '' | 'pos' | 'neg'. El ancho usa punto decimal. #}
{% macro barra(valor, maximo, tono="") -%}
{%- set w = ((valor|abs) / maximo * 100) if (maximo and valor is not none) else 0 -%}
<span class="barra {{ tono }}" aria-hidden="true"><i style="--w: {{ '%.1f'|format(w if w < 100 else 100) }}%"></i></span>
{%- endmacro %}
```

### 3.3 Filtros nuevos en `api/deps.py`

```python
_SIGLAS = {"AFP", "AGF", "BCI", "BICE", "BTG", "CMF", "SP", "CLP", "USD", "UF", "ETF", "ESG", "EEUU",
           "EE.UU.", "FM", "FI", "FIP", "APV", "LP", "CP", "USA", "II", "III", "IV", "VI", "BCH", "CCS",
           "HSBC", "BBVA", "BNP", "JP", "IFRS", "ECC", "NDF", "OIS", "CGE", "ENAP", "LATAM", "SQM", "CMPC",
           "CAP", "SMU", "CCU", "AES", "IPC", "TPM", "PIB", "ADR", "REIT", "RV", "RF", "MM", "MMM"}
_ESPECIALES = {"PLANVITAL": "PlanVital", "ITAU": "Itaú", "ITAÚ": "Itaú", "LARRAINVIAL": "LarrainVial",
               "BANCOESTADO": "BancoEstado", "METLIFE": "MetLife", "4LIFE": "4Life", "SCOTIABANK": "Scotiabank",
               "S.A.": "S.A.", "S.A": "S.A.", "SA": "S.A.", "SPA": "SpA", "LTDA": "Ltda.", "LTDA.": "Ltda.",
               "CIA": "Cía.", "CIA.": "Cía.", "CÍA": "Cía.", "CÍA.": "Cía."}
_MENORES = {"de", "del", "la", "las", "los", "el", "y", "e", "en", "para", "por", "con", "a", "al", "o", "u"}


def _nombre(s) -> str:
    """Nombre propio legible: 'BANCO DE CRÉDITO E INVERSIONES' → 'Banco de Crédito e Inversiones'.
    Solo actúa si el texto viene entero en mayúsculas; respeta siglas, números y casos especiales."""
    t = " ".join(str(s or "").split())
    if not t or t != t.upper() or not any(c.isalpha() for c in t):
        return t
    out = []
    for i, w in enumerate(t.split(" ")):
        uw, lw = w.upper(), w.lower()
        if uw in _ESPECIALES:
            out.append(_ESPECIALES[uw])
        elif uw.strip(".,()") in _SIGLAS or any(c.isdigit() for c in w):
            out.append(w)
        elif i and lw in _MENORES:
            out.append(lw)
        else:
            out.append("-".join(p[:1].upper() + p[1:].lower() for p in w.split("-")))
    return " ".join(out)


_MESES_LARGO = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_es(x) -> str:
    """'2026-11-15' → '15 nov 2026'. Si no es una fecha, la devuelve tal cual."""
    t = str(x or "")[:10]
    if len(t) == 10 and t[4] == "-" and t[7] == "-" and t[:4].isdigit():
        return f"{int(t[8:10])} {_MESES_ES[int(t[5:7]) - 1]} {t[:4]}"
    return str(x or "—")


templates.env.filters["nombre"] = _nombre
templates.env.filters["fecha_es"] = _fecha_es
```
Prueba rápida (sin servidor): `python -c "from api.deps import _nombre as n; print(n('BANCO DE CRÉDITO E INVERSIONES'), '|', n('AFP PLANVITAL S.A.'), '|', n('FM ACCIONES EEUU'))"`
→ `Banco de Crédito e Inversiones | AFP PlanVital S.A. | FM Acciones EEUU`.

### 3.4 CSS de componentes (agrega al final de `app.css`, en este orden)

```css
/* =========================================================================================
   4 · ESTRUCTURA DE PÁGINA
   ========================================================================================= */
.page-head { margin: var(--sp-2) 0 var(--sp-5); }
.migas { font-size: var(--fs-sm); color: var(--muted); margin-bottom: var(--sp-2); display: flex; flex-wrap: wrap; gap: 0.35rem; }
.migas a { color: var(--muted); text-decoration: none; }
.migas a:hover { color: var(--accent); }
.migas span[aria-hidden] { color: var(--faint); }
.bajada, .subtitle { font-size: var(--fs-md); color: var(--ink-2); max-width: 68ch; line-height: 1.55; margin: 0 0 var(--sp-4); }
.page-acciones { display: flex; flex-wrap: wrap; gap: var(--sp-2); align-items: center; margin-top: var(--sp-3); }
.prosa { max-width: var(--w-prose); line-height: var(--lh-prose); }

.sec { margin-top: var(--sp-7); }
.sec-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--sp-2) var(--sp-3);
            padding-bottom: var(--sp-2); border-bottom: 1px solid var(--line); margin-bottom: var(--sp-4); }
.sec-head h2 { margin: 0; font-size: var(--fs-lg); }
.sec-head h2[id] { scroll-margin-top: calc(var(--header-h) + 64px); }
.sec-num { color: var(--accent); margin-right: 0.4rem; font-variant-numeric: tabular-nums; }
.sec-arriba { margin-left: auto; font-size: var(--fs-xs); color: var(--muted); text-decoration: none; }
.sec-arriba:hover { color: var(--accent); }
.sub-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--sp-2) var(--sp-3);
            font-size: var(--fs-md); margin: var(--sp-6) 0 var(--sp-3); }

.grid-viz { display: grid; gap: var(--sp-4); margin: var(--sp-4) 0;
            grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr)); }
.grid-viz--2 { grid-template-columns: repeat(auto-fit, minmax(min(100%, 480px), 1fr)); }
.grid-viz--1 { grid-template-columns: 1fr; }

/* Índice de página pegajoso (reemplaza a .banca-minimap; se mantiene el alias) */
.toc, .banca-minimap {
    position: sticky; top: var(--header-h); z-index: 40; display: flex; align-items: center; gap: 0.25rem;
    overflow-x: auto; scrollbar-width: none; padding: 0.45rem 0; margin: 0 0 var(--sp-4);
    background: var(--bg); border: 0; border-bottom: 1px solid var(--line); border-radius: 0;
}
.toc::-webkit-scrollbar, .banca-minimap::-webkit-scrollbar { display: none; }
.toc a, .banca-minimap a { flex: none; font-size: var(--fs-sm); color: var(--muted); text-decoration: none;
    padding: 0.3rem 0.7rem; border: 0; border-radius: 999px; }
.toc a:hover, .banca-minimap a:hover { color: var(--ink); background: var(--s2); }
.toc a.is-activo, .banca-minimap a.is-activo { color: var(--ink); background: var(--accent-weak); }
.toc-label, .banca-minimap .minimap-label { flex: none; font-size: var(--fs-xs); color: var(--faint);
    text-transform: uppercase; letter-spacing: 0.06em; margin-right: 0.25rem; }

/* =========================================================================================
   5 · COMPONENTES
   ========================================================================================= */
/* Cifra clave */
.stats { display: grid; gap: var(--sp-3); margin: var(--sp-4) 0 var(--sp-5);
         grid-template-columns: repeat(auto-fit, minmax(min(100%, 210px), 1fr)); }
.stat { background: var(--s1); border: 1px solid var(--line); border-radius: var(--r); padding: var(--sp-4);
        display: flex; flex-direction: column; gap: var(--sp-1); min-width: 0; }
.stat-label { font-size: var(--fs-xs); font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); }
.stat-valor { font-size: 1.75rem; font-weight: 700; line-height: 1.15; letter-spacing: -0.02em; color: var(--ink); }
.stat-valor.pos { color: var(--pos); }  .stat-valor.neg { color: var(--neg); }
.stat-unidad { font-size: var(--fs-sm); font-weight: 500; color: var(--muted); margin-left: 0.35rem; letter-spacing: 0; }
.stat-delta { font-size: var(--fs-sm); font-weight: 600; color: var(--muted); }
.stat-delta.pos { color: var(--pos); }  .stat-delta.neg { color: var(--neg); }
.stat-ctx { font-size: var(--fs-sm); color: var(--muted); line-height: 1.45; }
.stat > .corte { margin: auto 0 0; padding-top: var(--sp-2); align-self: flex-start; }
.stats--compactas .stat { padding: var(--sp-3); }
.stats--compactas .stat-valor { font-size: var(--fs-xl); }

/* Tarjeta de gráfico */
.viz { margin: 0; background: var(--s1); border: 1px solid var(--line); border-radius: var(--r-lg);
       padding: var(--sp-4) var(--sp-4) var(--sp-3); display: flex; flex-direction: column; min-width: 0; }
.viz-head { display: flex; flex-direction: column; gap: 2px; margin-bottom: var(--sp-3); }
.viz-titulo { font-size: var(--fs-base); font-weight: 600; color: var(--ink); line-height: 1.35; }
.viz-sub { font-size: var(--fs-sm); color: var(--muted); }
.viz-cuerpo { margin-top: auto; min-width: 0; }
.viz-sm { height: var(--viz-sm); }  .viz-md { height: var(--viz-md); }  .viz-lg { height: var(--viz-lg); }
.viz .chart { background: transparent; border: 0; border-radius: 0; padding: 0; }
.viz-pie { display: flex; flex-wrap: wrap; align-items: center; gap: 0.3rem 0.8rem; margin-top: var(--sp-3);
           padding-top: var(--sp-2); border-top: 1px dashed var(--line); font-size: var(--fs-xs); color: var(--muted); }
.viz-pie .corte { margin-left: auto; }

/* Chips, fechas de corte, insignias */
.chip, .corte { display: inline-flex; align-items: center; gap: 0.3rem; vertical-align: middle;
    font-size: var(--fs-xs); font-weight: 500; letter-spacing: 0.01em; line-height: 1.5; color: var(--muted);
    border: 1px solid var(--line); border-radius: 999px; padding: 0.05rem 0.6rem; white-space: nowrap; text-transform: none; }
.corte { margin-left: 0.5rem; }
.chip--acento { color: var(--accent); border-color: rgba(92, 179, 255, 0.4); background: var(--accent-weak); }
.badge { font-size: var(--fs-xs); }

/* Avisos y notas */
.aviso { display: flex; gap: var(--sp-3); padding: var(--sp-3) var(--sp-4); margin: var(--sp-4) 0; max-width: 90ch;
         border: 1px solid var(--line); border-left-width: 3px; border-radius: var(--r); background: var(--s1);
         font-size: var(--fs-sm); line-height: 1.55; color: var(--ink-2); }
.aviso strong { color: var(--ink); }
.aviso--info { border-left-color: var(--info); }
.aviso--warn { border-color: rgba(251, 191, 36, 0.35); border-left-color: var(--warn); background: var(--warn-bg); }
.aviso--neg  { border-color: rgba(248, 113, 113, 0.35); border-left-color: var(--neg); background: var(--neg-bg); }
.warn-box { /* alias */ border-radius: var(--r); font-size: var(--fs-sm); }
.nota { font-size: var(--fs-sm); color: var(--muted); line-height: 1.55; max-width: 90ch; margin: var(--sp-3) 0; }
.nota strong { color: var(--ink-2); }
.metodo { border: 1px solid var(--line); border-radius: var(--r); background: var(--s1); margin: var(--sp-5) 0; }
.metodo > summary { cursor: pointer; padding: var(--sp-3) var(--sp-4); font-weight: 600; font-size: var(--fs-sm);
                    color: var(--ink-2); list-style: none; }
.metodo > summary::-webkit-details-marker { display: none; }
.metodo > summary::before { content: "▸"; display: inline-block; margin-right: 0.5rem; transition: transform 0.15s; }
.metodo[open] > summary::before { transform: rotate(90deg); }
.metodo > div { padding: 0 var(--sp-4) var(--sp-4); font-size: var(--fs-sm); color: var(--ink-2); line-height: var(--lh-prose); max-width: 90ch; }

/* Carga y vacío (antes ambos eran .loading) */
.loading { display: flex; align-items: center; gap: 0.6rem; color: var(--muted); font-size: var(--fs-sm);
           padding: var(--sp-5); border: 1px dashed var(--line); border-radius: var(--r); }
.loading::before { content: ""; width: 14px; height: 14px; flex: none; border-radius: 50%;
                   border: 2px solid var(--line-strong); border-top-color: var(--accent); animation: girar 0.8s linear infinite; }
@keyframes girar { to { transform: rotate(360deg); } }
.vacio { color: var(--muted); font-size: var(--fs-sm); padding: var(--sp-5); border: 1px dashed var(--line);
         border-radius: var(--r); text-align: center; }
form.htmx-request { background-image: linear-gradient(90deg, transparent, var(--accent), transparent);
                    background-size: 40% 2px; background-repeat: no-repeat; background-position: 0 100%;
                    animation: progreso 1s linear infinite; }
@keyframes progreso { from { background-position: -40% 100%; } to { background-position: 140% 100%; } }

/* Botones */
.btn { display: inline-flex; align-items: center; gap: 0.4rem; height: 36px; padding: 0 0.9rem; font: inherit;
       font-size: var(--fs-sm); font-weight: 600; border-radius: var(--r-sm); cursor: pointer; text-decoration: none;
       background: var(--accent); color: #04111f; border: 1px solid var(--accent); }
.btn:hover { filter: brightness(1.08); }
.btn--sec { background: var(--s2); color: var(--ink); border-color: var(--line-strong); }
.btn--sec:hover { border-color: var(--muted); filter: none; }

/* Filtros (formularios HTMX). .eeff-form es alias y NO se quita de compare.html */
.filtros, .eeff-form { display: flex; flex-wrap: wrap; align-items: flex-end; gap: var(--sp-3) var(--sp-4);
    padding: var(--sp-4); margin: 0 0 var(--sp-5); background: var(--s1); border: 1px solid var(--line); border-radius: var(--r); }
.filtros label, .eeff-form label { display: flex; flex-direction: column; gap: 0.35rem; flex: 0 1 240px; min-width: 170px;
    font-size: var(--fs-xs); font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; color: var(--muted); }
.filtros label.ancho, .eeff-form label.ancho { flex-basis: 380px; }
.filtros select, .filtros input, .eeff-form select, .eeff-form input { width: 100%; min-width: 0; text-transform: none; letter-spacing: 0; font-weight: 400; }
.filtros .ayuda { flex-basis: 100%; font-size: var(--fs-sm); color: var(--muted); text-transform: none; letter-spacing: 0; font-weight: 400; }

/* Hubs */
.hub-grid { display: grid; gap: var(--sp-4); margin-top: var(--sp-5); grid-template-columns: repeat(auto-fill, minmax(min(100%, 280px), 1fr)); }
.hub-card { display: flex; flex-direction: column; gap: var(--sp-2); padding: var(--sp-5); color: inherit; text-decoration: none;
            background: var(--s1); border: 1px solid var(--line); border-radius: var(--r-lg); transition: border-color 0.15s, background 0.15s; }
.hub-card:hover { border-color: var(--accent); background: var(--s2); transform: none; }
.hub-ico { width: 40px; height: 40px; border-radius: 10px; background: var(--accent-weak); display: grid; place-items: center; }
.hub-ico::before { content: ""; width: 22px; height: 22px; background: var(--accent);
                   -webkit-mask: var(--ico) center / contain no-repeat; mask: var(--ico) center / contain no-repeat; }
.ico-building-2 { --ico: url("/static/vendor/icons/building-2.svg"); }
.ico-landmark { --ico: url("/static/vendor/icons/landmark.svg"); }
.ico-shield { --ico: url("/static/vendor/icons/shield.svg"); }
.ico-trending-up { --ico: url("/static/vendor/icons/trending-up.svg"); }
.ico-briefcase { --ico: url("/static/vendor/icons/briefcase.svg"); }
.ico-chart-pie { --ico: url("/static/vendor/icons/chart-pie.svg"); }
.ico-piggy-bank { --ico: url("/static/vendor/icons/piggy-bank.svg"); }
.ico-chart-column { --ico: url("/static/vendor/icons/chart-column.svg"); }
.ico-scale { --ico: url("/static/vendor/icons/scale.svg"); }
.ico-search { --ico: url("/static/vendor/icons/search.svg"); }
.ico-link-2 { --ico: url("/static/vendor/icons/link-2.svg"); }
.ico-chart-line { --ico: url("/static/vendor/icons/chart-line.svg"); }
.ico-activity { --ico: url("/static/vendor/icons/activity.svg"); }
.ico-layers { --ico: url("/static/vendor/icons/layers.svg"); }
.hub-card h3 { font-size: var(--fs-md); color: var(--ink); margin: var(--sp-2) 0 0; }
.hub-card p { font-size: var(--fs-sm); color: var(--ink-2); line-height: 1.55; margin: 0; }
.hub-meta { margin-top: auto; padding-top: var(--sp-3); font-size: var(--fs-xs); color: var(--muted); }

/* Ficha clave-valor (ficha de producto, tarjetas de lista del panel) */
.kv { display: grid; grid-template-columns: 1fr auto; gap: 0; margin: 0; }
.kv dt, .kv dd { padding: 0.45rem 0; border-bottom: 1px solid var(--line); font-size: var(--fs-sm); margin: 0; }
.kv dt { color: var(--muted); }
.kv dd { color: var(--ink); font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }
.kv > :nth-last-child(-n + 2) { border-bottom: 0; }
.panel { background: var(--s1); border: 1px solid var(--line); border-radius: var(--r); padding: var(--sp-4); min-width: 0; }
.panel > h3 { font-size: var(--fs-xs); font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--accent); margin: 0 0 var(--sp-2); }

/* =========================================================================================
   6 · TABLAS (una sola familia; las clases viejas son alias)
   ========================================================================================= */
.tbl-wrap { overflow: auto; border: 1px solid var(--line); border-radius: var(--r); background: var(--s1); margin: var(--sp-3) 0; }
.tbl-wrap--alto { max-height: 70vh; }
:is(.tbl, .eeff-table, .bank-table, .evol-table, .rank-table, .cmp-ratios) {
    width: 100%; border-collapse: separate; border-spacing: 0; font-size: var(--fs-sm);
}
:is(.tbl, .eeff-table, .bank-table, .evol-table, .rank-table, .cmp-ratios) th {
    position: sticky; top: 0; z-index: 1; background: var(--s2); color: var(--muted); font-size: var(--fs-xs);
    font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; text-align: left; padding: 0.55rem 0.75rem;
    border-bottom: 1px solid var(--line-strong); vertical-align: bottom; line-height: 1.25;
}
:is(.tbl, .eeff-table, .bank-table, .evol-table, .rank-table, .cmp-ratios) td {
    padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--line); color: var(--ink-2); vertical-align: middle;
}
:is(.tbl, .eeff-table, .bank-table, .evol-table, .rank-table, .cmp-ratios) tbody tr:hover td { background: rgba(148, 163, 184, 0.06); }
:is(.tbl, .eeff-table, .bank-table, .evol-table, .rank-table, .cmp-ratios) tbody tr:last-child td { border-bottom: 0; }
/* Compatibilidad: en estas tablas viejas el encabezado iba a la derecha salvo la columna .acct */
:is(.bank-table, .evol-table, .cmp-ratios) th { text-align: right; }
:is(.bank-table, .evol-table, .cmp-ratios) th.acct { text-align: left; }
.tbl td.ent, :is(.eeff-table, .bank-table, .evol-table, .cmp-ratios) td.acct { color: var(--ink); font-weight: 500; }
.tbl .sub { display: block; font-size: var(--fs-xs); color: var(--muted); font-weight: 400; margin-top: 2px; }
.tbl tr.total td, .total-row td { font-weight: 700; color: var(--ink); background: var(--s2); border-top: 1px solid var(--line-strong); }
.tbl tr.grupo td, .section-head td { color: var(--accent); font-size: var(--fs-xs); font-weight: 700; letter-spacing: 0.06em;
    text-transform: uppercase; background: var(--bg); padding-top: var(--sp-4); border-bottom: 1px solid var(--accent); }
.tbl td.num, :is(.eeff-table, .bank-table, .evol-table) td.num { color: var(--ink-2); }
.tbl td.fuerte, .tbl td.num.fuerte { color: var(--ink); font-weight: 700; }
.tbl td.lider { color: var(--ink); font-weight: 700; }
.tbl td.lider::after { content: " ●"; color: var(--accent); font-size: 0.7em; vertical-align: middle; }
.tbl--densa td { padding: 0.35rem 0.6rem; }
.tbl--fija-1 :is(td, th):first-child { position: sticky; left: 0; z-index: 2; background: var(--s1); }
.tbl--fija-1 th:first-child { z-index: 3; background: var(--s2); }
.tbl caption { caption-side: top; text-align: left; padding: var(--sp-3) var(--sp-4) var(--sp-2); font-size: var(--fs-sm); color: var(--muted); }
.barra { display: block; position: relative; height: 6px; min-width: 60px; border-radius: 3px; background: var(--s3); margin-top: 5px; }
.barra > i { position: absolute; inset: 0 auto 0 0; width: var(--w); border-radius: 3px; background: var(--accent); }
.barra.pos > i { background: var(--pos); }  .barra.neg > i { background: var(--neg); }

/* Utilidades */
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.muted { color: var(--muted); }  .ink { color: var(--ink); }  .nowrap { white-space: nowrap; }
.mt-0 { margin-top: 0 !important; }  .mt-3 { margin-top: var(--sp-3) !important; }  .mt-5 { margin-top: var(--sp-5) !important; }
.mt-6 { margin-top: var(--sp-6) !important; }  .mb-0 { margin-bottom: 0 !important; }  .mb-3 { margin-bottom: var(--sp-3) !important; }
```

### 3.5 Mapeo de clases viejas → nuevas (para F5)

| Vieja | Nueva | Nota |
|---|---|---|
| `kpi-grid` | `stats` | |
| `kpi-card` + `kpi-big` + `kpi-sub` | `ui.stat(...)` | Tarjetas con lista de filas (`kpi-row`) → `.panel` + `<dl class="kv">` |
| `afp-stats` | `stats stats--compactas` + `ui.stat` | |
| `chart-block` + `<h4>` + `div.chart` | `ui.viz(id, titulo, sub, fuente, fecha, alto)` | Conserva el id |
| `chart-grid` | `grid-viz` (o `grid-viz--2`) | |
| `eeff-table`, `bank-table`, `evol-table`, `rank-table`, `cmp-ratios` | `tbl` (+ `tbl--densa`, `tbl--fija-1`) dentro de `.tbl-wrap` | `acct` → `ent`; `total-row` → `total`; `section-head` → `grupo` |
| `table-scroll` | `tbl-wrap` (o `tbl-wrap tbl-wrap--alto` si > 25 filas) | |
| `eeff-form`, `eeff-graf`, `filtros` | `filtros` | En `compare.html` deja también `eeff-form` |
| `warn-box` | `ui.aviso('warn')` | |
| `mock-note`, `afp-hint` | `nota` | |
| `.lead` en párrafos explicativos bajo tablas/gráficos | `nota`, o dentro de `ui.metodo` si son más de 3 líneas | `.lead` sigue válida para textos cortos dentro de celdas |
| `banca-minimap` | `toc` | El JS marca la sección activa |
| `<h2 style="…">` sueltos | `ui.sec_head(...)` dentro de `<section class="sec">` | |
| `<p class="loading">Sin …</p>` / "No hay …" | `ui.vacio("…")` | Solo los de "Cargando…"/"Ajustando…" siguen como `.loading` |
| `hub-icon` con emoji | `hub-ico ico-<clave>` | |

### 3.6 Conversión mecánica de `text-align` en línea (hazlo en F3, una vez)

Guarda como script temporal en tu scratchpad y córrelo desde la raíz del repo:

```python
import re, pathlib
pat = re.compile(r'<(td|th)([^>]*?)\sstyle="\s*text-align:\s*(right|left)\s*;?\s*"([^>]*)>')
def rep(m):
    tag, a, al, b = m.groups()
    attrs = a + b
    if al == "left":
        return f"<{tag}{attrs}>"
    if 'class="' in attrs:
        attrs = re.sub(r'class="([^"]*)"', lambda c: f'class="{c.group(1)} num"', attrs, count=1)
    else:
        attrs += ' class="num"'
    return f"<{tag}{attrs}>"
n = 0
for f in pathlib.Path("api/templates").rglob("*.html"):
    s = f.read_text(encoding="utf-8")
    s2, k = pat.subn(rep, s)
    if k:
        f.write_text(s2, encoding="utf-8", newline="\n"); n += k; print(f, k)
print("total", n)
```
Después: `git diff --stat`, revisa 3 archivos al azar en el diff (que ningún `class="..."` haya quedado
roto por comillas anidadas) y `ui_guard.py check` → `text_align_en_linea` cerca de 0.
**Ojo:** en las tablas `bank-table`/`evol-table`/`cmp-ratios` el `<th>` sin clase ahora queda a
la derecha por la regla de compatibilidad; los `<th>` de texto deben llevar `class="acct"`.

### 3.7 Verificación F3
Reinicia el servidor (deps.py). Capturas de `/`, `/productos`, `/banca`, `/eeff`, `/inversion-institucional`,
`/derivados` en escritorio. Las páginas aún no migradas deben verse **mejor o igual** (las clases
viejas ya toman el estilo nuevo). Hubs con íconos SVG (no emojis). `pytest`, `ui_guard`.
Commit: `Estética F3: componentes (cifras, gráficos, tablas, filtros, avisos), macros e íconos locales`.

## F4 · Estándar de gráficos en `api/static/charts.js` (45 min)

### 4.1 Cambios en el archivo

1. `var FONT = '"Inter", "Segoe UI", system-ui, -apple-system, sans-serif';`
2. Colores de tema alineados a los tokens: `INK = "#e8eef7"`, `MUTED = "#94a3b8"`,
   `GRID = "#24324d"`, `LINE = "#3b4d6b"`; `hoverlabel.bgcolor = "#0b1220"`.
3. En `altoInterno(gd)`, acepta además los divs con clase `viz-sm|viz-md|viz-lg` (alto definido
   por CSS). Reemplaza la condición de entrada por:
   ```js
   var conAlto = el && el.style && (el.style.height || /\bviz-(sm|md|lg)\b/.test(el.className));
   if (!conAlto) { return undefined; }
   ```
4. Agrega, antes del bloque `["newPlot", "react"].forEach(…)`, el espacio de nombres `INF`:

```js
    // ---------------------------------------------------------------------------------------
    // INF: colores por entidad y ayudas de storytelling, compartidas por todas las plantillas.
    // ---------------------------------------------------------------------------------------
    var INF = window.INF = window.INF || {};
    INF.PALETA = COLORWAY.slice();
    INF.COLOR = { foco: "#5cb3ff", contexto: "#56657d", pos: "#4ade80", neg: "#f87171",
                  ref: MUTED, tinta: INK, grilla: GRID,
                  // Netos con signo neutro (comprador/vendedor): par divergente azul/naranjo.
                  compra: "#3987e5", vende: "#d95926" };
    // El color sigue a la entidad en todo el sitio.
    INF.AFP = { CAPITAL: "#3987e5", CUPRUM: "#d95926", HABITAT: "#199e70", MODELO: "#c98500",
                PLANVITAL: "#d55181", PROVIDA: "#9085e9", UNO: "#e66767" };
    INF.FONDO = { A: "#ef4444", B: "#f97316", C: "#eab308", D: "#38bdf8", E: "#6366f1" };  // riesgo: cálido → frío
    INF.SECTOR = { "AFP": "#3987e5", "Seguros": "#9085e9", "AGF (fondos)": "#199e70", "Corredoras": "#c98500",
                   "Otros sectores": "#7d8ca3", "Empresas": "#d95926", "No residentes": "#d55181", "Bancos": "#e8eef7" };
    INF.colorAFP = function (n) {
        return INF.AFP[String(n || "").toUpperCase().replace(/^AFP\s+/, "").trim()] || INF.COLOR.contexto;
    };
    INF.esMovil = function () { return window.matchMedia("(max-width: 640px)").matches; };
    INF.fmt = function (v, d, signo) {
        if (v === null || v === undefined || isNaN(v)) { return "—"; }
        d = d || 0;
        var s = Number(v).toLocaleString("es-CL", { minimumFractionDigits: d, maximumFractionDigits: d });
        return (signo && v > 0 ? "+" : "") + s;
    };
    // Rango con holgura para barras con etiqueta afuera (incluye el cero).
    INF.rango = function (vals, pad) {
        var xs = vals.filter(function (v) { return v !== null && v !== undefined && !isNaN(v); }).concat([0]);
        var lo = Math.min.apply(null, xs), hi = Math.max.apply(null, xs), p = (hi - lo) * (pad || 0.22) || 1;
        return [lo < 0 ? lo - p : 0, hi > 0 ? hi + p : p * 0.2];
    };
    // Línea de referencia horizontal (0, 100, meta 3%…): {shape, annotation}.
    INF.refH = function (y, texto) {
        return { shape: { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: y, y1: y,
                          line: { color: MUTED, width: 1, dash: "dot" } },
                 annotation: texto ? { xref: "paper", x: 0, xanchor: "left", y: y, yanchor: "bottom", showarrow: false,
                                       text: texto, font: { size: 11, color: MUTED } } : null };
    };
    // Línea vertical "hoy" que separa lo observado de lo proyectado.
    INF.hoy = function (x, texto) {
        return { shape: { type: "line", xref: "x", yref: "paper", x0: x, x1: x, y0: 0, y1: 1,
                          line: { color: "#64748b", width: 1, dash: "dot" } },
                 annotation: { x: x, xref: "x", y: 1, yref: "paper", yanchor: "top", xanchor: "left", xshift: 4,
                               showarrow: false, text: texto || "proyección →", font: { size: 11, color: MUTED } } };
    };
    // Etiquetas directas al final de cada serie, apiladas si chocan (12 px por nivel).
    // series = [{x: [...], y: [...], texto: "Cuprum 10,9%", color: "#…"}]. Requiere margin.r ≥ 80.
    INF.etiquetasFinales = function (series, umbral) {
        var anns = [];
        series.forEach(function (s) {
            for (var i = s.y.length - 1; i >= 0; i--) {
                if (s.y[i] !== null && s.y[i] !== undefined) {
                    anns.push({ x: s.x[i], y: s.y[i], xanchor: "left", xshift: 6, showarrow: false,
                                text: s.texto, font: { size: 11, color: s.color || INK } });
                    break;
                }
            }
        });
        if (anns.length < 2) { return anns; }
        var ys = anns.map(function (a) { return a.y; });
        var rango = (Math.max.apply(null, ys) - Math.min.apply(null, ys)) || 1;
        anns.sort(function (a, b) { return b.y - a.y; });
        var ancla = null, nivel = 0;
        anns.forEach(function (a) {
            if (ancla !== null && (ancla - a.y) < rango * (umbral || 0.04)) { nivel += 1; a.yshift = -12 * nivel; }
            else { nivel = 0; ancla = a.y; }
        });
        return anns;
    };
    window.INF_PALETA = INF.PALETA;   // compatibilidad con plantillas existentes
```
5. Dentro de `tematizar`, al final (antes de `return layout;`), ajustes de celular:
```js
        if (INF.esMovil && INF.esMovil()) {
            layout.margin = layout.margin || {};
            if (layout.margin.l === undefined || layout.margin.l > 48) { layout.margin.l = 48; }
            if (layout.margin.r !== undefined && layout.margin.r > 40) { layout.margin.r = 40; }
            if (layout.legend) { layout.legend.font = Object.assign({}, layout.legend.font, { size: 10 }); }
        }
```
(Como `INF` se define después de `tematizar`, la llamada es segura: `tematizar` solo se ejecuta
cuando una plantilla dibuja.)

### 4.2 Reglas para todo gráfico (aplícalas al migrar cada página en F5)

1. **Título fuera del gráfico**, en `ui.viz`: `titulo` = qué y de quién; `sub` = unidad · período ·
   cómo leer ("MMUSD; negativo = vende dólares"). Nunca `layout.title`.
2. **Alto por token**: usa `alto="sm|md|lg"` y **borra** `height:` del layout; borra también
   `paper_bgcolor`, `plot_bgcolor`, `font`, `gridcolor` y `separators` del layout (los pone
   `charts.js`). Conserva `margin` solo si el gráfico lo necesita.
3. **Color**: AFP con `INF.colorAFP(nombre)`, fondos con `INF.FONDO[letra]`, sectores con
   `INF.SECTOR[nombre]`, foco/contexto con `INF.COLOR`. Nada de `#hex` sueltos nuevos.
4. **Etiqueta directa** con `INF.etiquetasFinales` cuando hay ≤ 5 líneas: entonces `showlegend: false`
   y `margin.r: 90`.
5. **Referencias**: `INF.refH(0)` en series con signo; `INF.refH(100, "base 100")` en índices;
   `INF.hoy(x)` en proyecciones.
6. **Tiempo**: eje con meses en castellano (lo hace `charts.js`), `tickangle: 0`; con más de 3
   años, un rótulo por año (`dtick: "M12"` en ejes de fecha; en ejes de categoría, `tickvals` con
   los T1 o eneros, como ya hace `proyecciones_chart.html`).
7. **Barras**: desde cero; ordenadas por valor salvo orden natural; etiqueta del valor con
   `text: valores.map(v => INF.fmt(v, d, signo))` y `textposition: "outside"`, `cliponaxis: true`
   y rango `INF.rango(...)` si hay negativos.
8. **Hover**: `hovermode: "x unified"` en series de tiempo; `hovertemplate` con unidad y formato
   (`%{y:,.1f}` usa separadores chilenos por `charts.js`).
9. **Prohibido**: doble eje Y, tortas, 3D, colores por rango, más de 7 series de color.

Commit F4: `Estética F4: estándar de gráficos (INF: colores por entidad, etiquetas, referencias, alto por token)`.

## F5 · Migración página por página

Procedimiento **igual para cada página**:
1. `{% import "partials/_ui.html" as ui %}` al inicio (y en cada partial que uses macros).
2. Encabezado con `ui.page_head` (título y bajada de la tabla de abajo; migas en subpáginas).
3. Índice `.toc` si la página tiene 4 secciones o más (reusa el `banca-minimap` existente
   cambiando su clase a `toc`).
4. Cada sección: `<section class="sec">` + `ui.sec_head(titulo, id=…, fecha=…, fuente=…)`.
5. Cifras clave con `ui.stat` dentro de `<div class="stats">`.
6. Gráficos con `ui.viz` (reglas 4.2). Tablas con `.tbl` dentro de `.tbl-wrap`.
7. Nombres en mayúsculas con `|nombre` (texto visible, nunca `value=`); AGF con `|agf_corto`;
   fechas con `|mes_es` / `|fecha_es`.
8. Párrafos explicativos largos a `ui.metodo(...)`; cortos a `.nota`.
9. Quita estilos en línea (salvo `--w` de barras y alturas dinámicas).
10. Verifica (capturas escritorio + celular de esa URL, `pytest`, `ui_guard`) y pasa a la siguiente.

### Títulos y bajadas (texto exacto)

| URL | Título (`h1`) | Bajada |
|---|---|---|
| `/` | Panel de mercado | Lo último en actividad, inflación, tasas, dólar, AFP y bancos, con la fecha y la fuente de cada cifra. |
| `/productos` | Productos de inversión | (conserva la actual, sin cambios de contenido) |
| `/curvas` | Curvas de tasas | Curvas nominal (pesos) y real (UF) de Chile, ajustadas cada día sobre swaps cámara y bonos del Banco Central y la Tesorería. |
| `/banco-central` | Banco Central: series y expectativas | (conserva el texto actual) |
| `/derivados` | Derivados en Chile: exposición y riesgo | (conserva) |
| `/eeff` | Estados financieros | Elige el tipo de entidad: cada una tiene su plan de cuentas y su fuente. |
| `/eeff/corporativos` | Estados financieros corporativos | (conserva `page_subtitle`) |
| `/banca` | Estados financieros de bancos | Balance y resultados de cada banco, con desglose por moneda (pesos, UF, dólar y otras monedas). |
| `/comparar` | Comparar empresas | Hasta tres empresas en la misma partida y los mismos indicadores. |
| `/ranking` | Ranking de empresas por indicador | Las 15 empresas con el valor más alto del indicador elegido en el período. |
| `/salud` | Salud financiera: rentabilidad contra endeudamiento | Cada burbuja es una empresa: rentabilidad sobre patrimonio (ROE) en el eje vertical, deuda sobre patrimonio en el horizontal y tamaño según liquidez. |
| `/proyecciones` | Proyecciones | (conserva) |
| `/inversion-institucional` | Inversión institucional | (conserva) |
| `/afp` | AFP: rentabilidad, comisiones y cartera | Valor cuota y patrimonio de cada AFP y fondo, qué AFP conviene una vez descontadas las comisiones y en qué invierte cada fondo. |
| `/fondos` | Fondos de pensiones: retornos y composición | Retorno de cada fondo por AFP en varios horizontes y en qué está invertido. |
| `/seguros/cartera` | Cartera de las compañías de seguros | (conserva) |
| `/fondos-mutuos` | Fondos mutuos | (conserva) |
| `/metodologia` | Metodología y fuentes | (conserva) |

### F5a · Grupo A (lo más visto) — 2 h

**Panel `/`** (`overview.html`, `partials/kpi_cards.html`)
- Encabezado con acciones (`{% call ui.page_head(...) %}`): botón "Actualizar indicadores"
  (`btn btn--sec`, conserva sus atributos `hx-*`) y el enlace de calidad de datos como `.chip`.
- `kpi_cards.html`: abre `build_overview_context` en `api/routers/web/_shared.py` **solo para leer**
  qué campos llegan (incluidas fechas). Reorganiza en:
  1. `stats` con `ui.stat` para PIB (var. anual), IMACEC (var. anual; minero y no minero en
     `ctx`), IPC 12 meses (sin color), USD/CLP (sin color). Color solo para PIB e IMACEC (signo).
     Si el contexto trae fechas por indicador, pásalas a `fecha=`; si no, no inventes.
  2. `grid-viz` de tres `.panel`:
     - **Tasas:** tabla `tbl tbl--densa` con BCP y BCU en filas y 5 y 10 años en columnas, más
       tres filas de colocación.
     - **AFP:** tabla de los cinco fondos con columnas Fondo, Sistema 12m, Líder y Retorno del
       líder, con signo coloreado.
     - **Bancos:** dos tablas cortas (top 5 por activos y por resultado), cada una con
       `ui.barra` y `|nombre` en los bancos.
  3. "Mercado · SP: N instrumentos en cinta" pasa a `.nota` bajo el panel AFP (no es una cifra clave).
- Gráficos UF y TPM con `ui.viz(alto="sm")`:
  - TPM con `line.shape: "hv"`.
  - Ambos con etiqueta del último valor (`INF.etiquetasFinales` con una serie: texto `INF.fmt(último, 2)`).
  - Quita sus `height` y colores fijos.

**Derivados `/derivados`** (`derivados.html`) y **monitor `/inversion-institucional/derivados`**
(`institucional_derivados.html`, el de más estilos en línea: 118)
- `banca-minimap` → `toc`. Conserva la franja `.cortes` y todos los `corte(...)` ya puestos.
- Tarjetas → `ui.stat` (mismos textos; `fecha` y `fuente` en los parámetros).
- Secciones → `<section class="sec">` + `ui.sec_head` conservando los ids (`der-mercado`, etc.).
  En el monitor, cada `<h2 style=…>` pasa a `ui.sec_head` con su `corte` actual.
- Gráficos → `ui.viz`. En `derivados.html` reemplaza las funciones locales `txt`/`rango` por
  `INF.fmt`/`INF.rango`, la paleta local por `INF.SECTOR` (serie de sectores) y `INF.COLOR.compra`/
  `vende` (barras de neto). Los small multiples de sectores del monitor (`d-sec-i`) usan
  `INF.SECTOR[s.nombre]`.
- Tablas → `tbl` en `tbl-wrap`; la de seguros con `|nombre` en la compañía; la de AFP con
  `ui.barra` en la columna "Cobertura" (máximo 100).
- Alertas: lista sin cambios de contenido; cada alerta ya trae `corte`.
- "6 · Cómo se lee" → `ui.metodo` abierto=false.

**Productos `/productos`** (`productos.html`, `partials/productos_tabla.html`) y **ficha `/producto/<id>`**
- Formulario `#prod-filtros` → `filtros` (conserva `hx-*` e ids). Campo de búsqueda con `label.ancho`.
- Barra de resultados: "N productos · ordenados por …" a la izquierda y "Comparar seleccionados"
  (`btn`) a la derecha.
- Tabla:
  - `tbl tbl--fija-1` en `tbl-wrap tbl-wrap--alto`.
  - Nombre del producto en `ink` (no subrayado azul; subrayado al pasar), con `|nombre`. Serie como `.chip`, AGF en `.sub` con `|agf_corto`.
  - Rentabilidades con signo y color.
  - Volatilidad y costo sin color.
  - "Posición" (percentil 0–100) con `ui.barra(v, 100)`.
  - Unidades en los encabezados ("12 meses (%)").
- El aviso de orden/recomendación → `ui.aviso('info')`.
- Ficha `producto.html`:
  - `ui.page_head` con migas (Productos › nombre) y chips de serie, tipo, moneda y AGF.
  - Las 5 tarjetas (Rentabilidad, Frente a su categoría, Riesgo, Costo, Tamaño) → `grid-viz` de `.panel` con `<dl class="kv">`.
  - Gráfico → `ui.viz(alto="lg", sub="base 100; línea punteada = mediana de su categoría")` con `INF.refH(100)`.

**AFP `/afp`** y **Fondos de pensiones `/fondos`**
- `/afp`:
  - Índice con cinco entradas: Evolución, Comparativa, Rentabilidad 12 meses, Neta de comisiones y Cartera.
  - Formularios → `filtros`; `afp-hint` → `.nota` dentro del formulario (`.ayuda`).
- `afp_chart.html`:
  - Cifras → `stats stats--compactas` con `ui.stat`. El campo "Al" pasa a `fecha=` del primer stat.
  - Colores: `INF.FONDO` (modo fondos) e `INF.colorAFP` (modo AFP).
  - Etiquetas directas con `INF.etiquetasFinales` y `showlegend:false` si hay ≤ 5 series.
- `afp_comparativa.html`:
  - Tabla `tbl` con `|nombre` en AFP y la fila del líder marcada con `td.lider` en Rent. 12m.
  - La nota de corte pasa a `.nota` con `|fecha_es`.
- `afp_rentabilidad.html`, `afp_neta.html` y `afp_cartera.html`: solo envoltura `ui.viz` y `tbl`; los gráficos ya cumplen.
- `fondos_panel.html`:
  - **Cambia el gráfico de barras agrupadas (7 AFP × 7 horizontes) por un gráfico de puntos:**
    - x = horizontes (1M, 3M…), un trace `scatter` `mode: "markers"` por AFP.
    - `marker: {size: 11, color: INF.colorAFP(afp), line: {color: "#111a2c", width: 1.5}}`.
    - Línea de referencia `INF.refH(0)` y leyenda arriba.
  - Composición: horizontal apilado con los colores de sección desde `INF.PALETA` en orden fijo.
  - Tabla de retornos:
    - Marca el máximo de cada columna con `td.lider`; calcula los máximos en Jinja con el código de abajo.
    - `|nombre` en AFP.

```jinja
{% set maximos = [] %}
{% for j in range(ret.horizons|length) %}
  {% set vals = [] %}
  {% for a in ret.afps %}{% if a['values'][j] is not none %}{% set _ = vals.append(a['values'][j]) %}{% endif %}{% endfor %}
  {% set _ = maximos.append(vals|max if vals else none) %}
{% endfor %}
…
<td class="num{{ ' lider' if v is not none and v == maximos[loop.index0] }}">…</td>
```

**Fondos mutuos `/fondos-mutuos`**
- Secciones `sec` con sus ids; filtros `#fm-filtros` → `filtros`.
- `fondos_mutuos_comparado.html`: `ui.viz(alto="lg")`; la leyenda HTML `.cmp-legend` se
  conserva (se estiliza como chips en CSS); las etiquetas finales locales pasan a `INF.etiquetasFinales`.
- `fondos_mutuos_rend.html`:
  - Tabla `tbl tbl--fija-1` en `tbl-wrap tbl-wrap--alto`; conserva `row-click` y sus `hx-*`.
  - Fondo en `|nombre` (la AGF ya va con `agf_corto`).
  - Los tres párrafos de abajo → `ui.metodo("Qué mide esta cifra y cómo se validó")`.
- `fondos_mutuos_panel.html`: `kpi-card` → `ui.stat`; tablas → `tbl`; gráficos → `ui.viz`.

### F5b · Grupo B (empresas, bancos, macro) — 1 h 30

**EEFF `/eeff/corporativos`, `/eeff/agf`** (`eeff.html`, `partials/eeff_table.html`, `statement_matrix.html`, `eeff_serie.html`, `ratios_serie.html`, `eeff_static.html`)
- Encabezado de empresa:
  - Nombre con `|nombre`.
  - Chips de sector, RUT, moneda y número de períodos.
  - La reseña queda como `.bajada` chica.
- Indicadores (`.ratios`) → `stats stats--compactas` con `ui.stat`.
  - **Con color por signo:** ROE, ROA, márgenes y capital de trabajo.
  - **Sin color** (salvo negativos, que van en rojo): liquidez corriente, test ácido, deuda/patrimonio y cobertura de intereses.
- Dupont: conserva la fórmula, pasa sus estilos en línea a clases (`.dupont`, `.dupont-factor`, `.dupont-resultado`) definidas en el CSS.
- `statement_matrix.html`:
  - Tabla `tbl tbl--densa tbl--fija-1` en `tbl-wrap tbl-wrap--alto`, con `section-head` → `tr.grupo` y `total-row` → `tr.total`.
  - Nombres de cuenta con `max-width: 420px` (clase `ent`).
  - **Casilla "Ocultar cuentas sin datos"** (marcada por defecto) sobre cada matriz, con este JS
    al final del partial:
    ```html
    <script>
    (function () {
        document.querySelectorAll(".js-ocultar-vacias").forEach(function (chk) {
            var tabla = document.getElementById(chk.dataset.tabla);
            function aplicar() {
                tabla.querySelectorAll("tbody tr:not(.grupo):not(.total)").forEach(function (tr) {
                    var celdas = [].slice.call(tr.querySelectorAll("td.num"));
                    var vacia = celdas.length && celdas.every(function (td) { return td.textContent.trim() === "—"; });
                    tr.hidden = chk.checked && vacia;
                });
            }
            chk.addEventListener("change", aplicar); aplicar();
        });
    })();
    </script>
    ```
    Marcado: `<label class="nota"><input type="checkbox" class="js-ocultar-vacias" data-tabla="ID_DE_LA_TABLA" checked> Ocultar cuentas sin datos</label>`
    (da a cada `<table>` un id único, por ejemplo `mat-{{ st.key }}`; si no hay clave, `mat-{{ loop.index }}`).
- Selectores de gráfico (`eeff-graf`) → `filtros` en línea.

**FECU `/eeff/seguros`, `/eeff/intermediarios`** (`fecu_entidad.html`, `fecu_statements.html`): igual
que EEFF. Los nombres de cuenta pegados ("Totalinversionesfinancieras") son un problema de
**datos**: no los toques; anótalo en el pendiente (F7).

**Bancos `/banca`** (`banca.html`, `banca_table.html`, `banca_evolutiva.html`)
- `banca-minimap` → `toc`; filtros → `filtros` (conserva ids `banca-*-sel`).
- `<option>` de bancos: texto con `|nombre`, `value` intacto.
- El bloque `<style>` de la página se elimina: su `max-height` lo da `tbl-wrap--alto` y su
  encabezado fijo ya es global.
- Tablas → `tbl tbl--fija-1`; mantiene `strong` (columna Total) como `fuerte`.

**Comparar `/comparar`** (`compare.html`, `compare_chart.html`, `compare_ratios.html`)
- **Conserva `class="eeff-form"`** en el formulario (agrega `filtros` al lado).
- El gráfico pasa a `ui.viz(alto="lg")`, con etiquetas finales y los colores de `INF.PALETA` por orden de empresa.
- Ratios: `tbl`, con el mejor valor de cada fila marcado con `td.lider`, calculado en Jinja como en `/fondos` pero por fila.

**Ranking `/ranking`** (`ranking_table.html`)
- Lista → `tbl` con columnas #, Empresa (`|nombre`, sector en `.sub` si llega) y Valor (`num`).
- La columna Valor lleva `ui.barra(r.value, maximo_abs, 'pos' if r.value >= 0 else 'neg')`; calcula `maximo_abs` en Jinja con
  `{% set maximo_abs = rows|map(attribute='value')|map('abs')|max %}` (ajusta el nombre de la lista).

**Salud `/salud`** (`salud.html`, `partials/salud_chart.html`)
- Título y bajada de la tabla.
- Gráfico en `ui.viz(alto="lg")`:
  - **Color:** quita la escala de color por ROE, que ya está en el eje. Pinta por cuadrante:
    - Verde `INF.COLOR.pos` si deuda < 1 y ROE > 15.
    - Rojo `INF.COLOR.neg` si deuda > 2,5 y ROE < 0.
    - `INF.COLOR.contexto` en el resto.
    - Opacidad 0,75 y borde `#111a2c`.
  - **Anotaciones dentro del gráfico:** "Rentables y poco endeudadas" arriba a la izquierda y "Endeudadas y con pérdidas" abajo a la derecha, en `MUTED`, tamaño 11.
  - **Nombres:** rotula con su nombre (`|nombre` en JS no existe; usa el texto tal cual, acortado a 22 caracteres) las 6 empresas de mayor |ROE| dentro del rango visible.
  - **Eje Y:** de los percentiles 2 a 98 del ROE, calculados en JS. Bajo el gráfico, `.nota` "N empresas fuera del rango visible".
- Las dos tarjetas de cuadrantes de abajo se eliminan: su texto pasa a la `.nota` de lectura.

**Proyecciones** (`proyecciones.html`, `proyecciones_simple.html` y sus partials)
- `ui.page_head` con migas (Proyecciones › …); filtros; `warn-box` → `ui.aviso('warn')`.
- Gráficos (ya cumplen) → envolver en `ui.viz(alto="lg")` y quitar `height`.
- Tablas de supuestos, estructural y no operacional → `tbl tbl--densa`.
- Se elimina el espacio vacío entre el formulario y el resultado: `#proy-loading` y `#proys-loading` llevan `htmx-indicator`; ocúltalos con CSS cuando no hay request (`.htmx-indicator { display: none } .htmx-request .htmx-indicator, .htmx-request.htmx-indicator { display: flex }`).

**Banco Central `/banco-central`** (`banco_central.html`, `bc_serie.html`, `bc_comparar.html`, `bc_catalogo.html`)
- `section.section-head` → `sec` + `ui.sec_head`.
- `bc_serie.html`:
  - Tarjetas → `ui.stat`, con `fecha=stats.last_date` en "Último dato" y `delta` = variación 12 meses.
  - Gráfico → `ui.viz` con etiqueta del último valor y un marcador (rombo) en el valor de hace 12 meses, si está en la serie.
- `bc_comparar.html`: **elimina el doble eje.**
  - **Misma unidad:** un eje, sin cambios.
  - **Unidades distintas:** dos paneles apilados que comparten el eje X, con `grid: {rows: 2, columns: 1, shared_xaxes: true}` o `yaxis.domain: [0.55, 1]` / `yaxis2.domain: [0, 0.45]` y `xaxis2.matches: "x"`.
  - En ese caso, cada serie en su panel con su título de unidad. Ajusta el texto explicativo para describir paneles.
- `bc_catalogo.html`:
  - Tabla `tbl` en `tbl-wrap tbl-wrap--alto`.
  - Categoría como `.chip`.
  - Código en `<code>`.

**Curvas `/curvas`** (`curvas.html`: 26 estilos en línea)
- Formulario GET → `filtros`.
- Curva cero cupón → `ui.viz(alto="lg")`:
  - **Etiquetas directas** al final de cada curva (`INF.etiquetasFinales`, texto = nombre corto: "Swap pesos", "Swap UF", "Bonos pesos", "Bonos UF"), con `showlegend: false` y `margin.r: 100`.
  - El título del eje X va a `sub`.
- Tablas → `tbl`; diferencias en pb con signo.
- TPM e historia → `ui.viz`. En historia, etiquetas finales con la tasa a 2 y 10 años.

### F5c · Grupo C — 1 h

**Hubs** (`eeff_hub.html`, `proyecciones_hub.html`, `institucional_hub.html`): `ui.page_head`, tarjetas
con `hub-ico`, `hub-meta` abajo; el aviso de proyecciones a `ui.aviso('warn')`.

**Cartera de seguros `/seguros/cartera`** (`seguros_cartera.html` y 4 partials)
- Filtros; cifras (`kpi-card` del panel) → `ui.stat`.
- Asset allocation → `ui.viz`.
- Tablas → `tbl` con `|nombre` en compañías y `ui.barra` en "% del mercado".
- Deriva → `ui.viz(alto="lg")`.
- Emisores → `tbl` con `|nombre`.

**Inversión por emisor `/inversion-institucional/emisor`** (`institucional_emisor.html` y su panel)
- Bloques: `afp-stats` → `stats stats--compactas`.
- Gráficos → `ui.viz(alto="sm")`.
- Tablas → `tbl` con `|nombre` en tenedores.
- "Cómo leer los montos" → `ui.metodo`.

**Emisores en común `/inversion-institucional/emisores`**: tabla `tbl tbl--fija-1` en `tbl-wrap--alto`, `|nombre`.

**Metodología `/metodologia`**
- En pantallas ≥ 1.100 px, dos columnas: a la izquierda un índice pegajoso (`<nav class="toc-lateral">`) con enlaces a cada `h2`; a la derecha el texto en `.prosa` (72 caracteres, 15 px, interlínea 1,65).
- Las listas con `li + li { margin-top: var(--sp-2) }`.
- Tablas de fuentes → `tbl`.
- CSS nuevo:
  ```css
  .doc { display: grid; gap: var(--sp-6); }
  @media (min-width: 1100px) { .doc { grid-template-columns: 220px minmax(0, 1fr); } }
  .toc-lateral { position: sticky; top: calc(var(--header-h) + 16px); align-self: start; display: grid; gap: 2px; font-size: var(--fs-sm); }
  .toc-lateral a { color: var(--muted); text-decoration: none; padding: 0.3rem 0.6rem; border-left: 2px solid var(--line); }
  .toc-lateral a:hover, .toc-lateral a.is-activo { color: var(--ink); border-left-color: var(--accent); }
  .prosa li + li { margin-top: var(--sp-2); }
  ```
  (Agrega `.toc-lateral a` al selector del JS de índice en `base.html`: `".toc a[href^='#'], .banca-minimap a[href^='#'], .toc-lateral a[href^='#']"`.)

**Comparar productos `/productos/comparar`**: `tbl`, `ui.viz`.

Commits: uno por grupo, `Estética F5a: …`, `F5b: …`, `F5c: …`, listando las páginas.

## F6 · Celular y accesibilidad (45 min)

1. Agrega al final de `app.css`:
```css
/* =========================================================================================
   7 · CELULAR
   ========================================================================================= */
@media (max-width: 640px) {
    :root { --viz-sm: 200px; --viz-md: 260px; --viz-lg: 320px; --fs-2xl: 1.5rem; }
    .container { padding-top: var(--sp-4); }
    .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .stat-valor { font-size: 1.4rem; }
    .filtros label, .eeff-form label { flex: 1 1 100%; }
    .viz { padding: var(--sp-3); }
    .sec { margin-top: var(--sp-6); }
    .sec-arriba { display: none; }
    .bajada, .subtitle { font-size: var(--fs-base); }
    .hub-card { padding: var(--sp-4); }
}
```
2. Corre `ui_capturas.py --label f6` completo. En `chequeo.txt` exige `desborde=false` en todas
   las vistas y ambos anchos. Para cada `culpables=…` busca el elemento y corrígelo (típicos: una
   tabla sin `.tbl-wrap`, un `min-width` fijo, un `<pre>`/`code` largo → `overflow-wrap: anywhere`).
3. `letra_chica`: debe ser 0 en todas las vistas. Busca el selector responsable en el CSS o el
   estilo en línea.
4. Accesibilidad:
   - El orden de foco con Tab es lógico y el foco es visible (`:focus-visible` global).
   - El enlace "Saltar al contenido" funciona.
   - Cada `<table>` grande lleva `aria-label` o `<caption class="sr-only">`.
   - Cada `ui.viz` tiene su título visible, que ya actúa como etiqueta. Agrega `role="img"` y `aria-label="{{ titulo }}"` al div del gráfico en el macro.
   - Los colores de estado nunca solos: los deltas llevan flecha ▲▼, las alertas su texto de nivel.
5. Commit: `Estética F6: celular sin desbordes, letra mínima 12 px y accesibilidad`.

## F7 · Limpieza y registros (30 min)

1. `ui_guard.py check`: contrato intacto. Metas de deuda:
   - estilos en línea < 60 (solo `--w` de barras, `--ico`, alturas dinámicas);
   - `text-align` en línea = 0;
   - colores fijos en plantillas < 80 (solo en JS de gráficos que aún no usan `INF.*`);
   - `font-size` CSS menor a 12 px = 0.
2. Borra del CSS las reglas viejas que ya no usa ninguna plantilla. Para cada clase candidata:
   `grep -rn "nombre-clase" api/templates api/static/charts.js api/*.py` → si no aparece, se borra
   (candidatas: `topbar`, `brand`, `kpi-section-title`, `ratio-grid`, `hub-icon`, `mock-note`,
   `afp-hint`, la regla `.container > h2, section > h2:first-child`). **No** borres los alias
   listados en la regla 3 ni `eeff-form`.
3. Capturas finales completas: `ui_capturas.py --label final`. Compara 6 vistas con `antes`
   (panel, productos, derivados, afp, eeff corporativos, banca) leyendo los `_0.png` de ambas.
4. Registros:
   - `docs/PLAN_ESTETICA.md`: tabla de estado al día.
   - `docs/PLAN_SEMANA.md` §7: una entrada de bitácora del día con el cambio, las métricas de deuda antes/después y los commits.
   - `docs/wiki/Hoja-de-Ruta.md`: marca las vistas rediseñadas y deja como pendientes de datos:
     - nombres de cuenta FECU sin espacios;
     - unidades mixtas del evolutivo de bancos (plan de cuentas pre/post 2022).
5. Mata tu servidor de `:8001`. Commit y push: `Estética F7: limpieza de estilos viejos y registros`.

## Criterio de terminado (por página)

- [ ] Encabezado `page-head` (migas si es subpágina, `h1`, bajada ≤ 2 líneas).
- [ ] Cada sección con `sec-head` y fecha de corte cuando el dato tiene fecha.
- [ ] Cifras clave en `ui.stat`, con unidad y fecha.
- [ ] Cada gráfico en `ui.viz` con título, subtítulo (unidad · período), fuente y, cuando
      aplica, fecha; sin `height` ni colores fijos en el layout; color por entidad.
- [ ] Tablas `tbl`: números a la derecha con dígitos tabulares, unidades en el encabezado, totales
      marcados, encabezado fijo si hay más de 25 filas.
- [ ] Nombres sin mayúsculas sostenidas (`|nombre`), fechas con `|mes_es`/`|fecha_es`.
- [ ] Verde/rojo solo para signo o mejora/deterioro.
- [ ] `desborde=false` y `letra_chica=0` en 1.440 y 390 px.
- [ ] `pytest -q` verde, `ui_guard.py check` intacto, sin errores 500 en el log del servidor.

## Fuera de alcance de este plan

- Tema claro y modo impresión (los tokens dejan la puerta abierta; no se hace hoy).
- Cambios de datos o de cálculo (nombres de cuenta FECU pegados, unidades del evolutivo bancario,
  nuevas métricas, lecturas que requieran datos que la plantilla no recibe).
- Reemplazar Plotly o agregar librerías.
