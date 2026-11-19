/*
 * api/static/charts.js — Tema común de los gráficos Plotly del sitio.
 *
 * Envuelve Plotly.newPlot y Plotly.react para que TODO gráfico herede, salvo que la plantilla
 * diga otra cosa explícitamente:
 *   - fondo transparente, tipografía y tinta del sitio, grilla recesiva;
 *   - separadores chilenos (miles '.', decimal ',') en ejes y tooltips;
 *   - leyenda horizontal ARRIBA del área de datos (una leyenda bajo el eje con y negativa
 *     queda montada sobre las fechas apenas el eje rota sus etiquetas: es el descuadre que
 *     se veía en varias vistas), y nunca a la derecha robando ancho;
 *   - ejes con automargin, para que etiquetas largas no se corten;
 *   - paleta categórica validada para el fondo oscuro (CVD y contraste, skill dataviz);
 *   - tooltip oscuro legible y sin barra de herramientas.
 * Así una vista nueva sale bien por defecto y las correcciones viven en un solo lugar.
 */
(function () {
    "use strict";
    if (!window.Plotly) { return; }

    var FONT = '"Inter", "Segoe UI", system-ui, -apple-system, sans-serif';
    var INK = "#e8eef7", MUTED = "#94a3b8", GRID = "#24324d", LINE = "#3b4d6b";
    // Orden fijo (no se cicla por rango): azul, naranjo, aqua, amarillo, magenta, verde,
    // violeta, rojo. Validado sobre #1e293b: CVD ΔE ≥ 8,4 entre vecinos, contraste ≥ 3:1.
    var COLORWAY = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c",
                    "#9085e9", "#e66767"];
    window.INF_PALETA = COLORWAY.slice();

    // Fechas en castellano en ejes y tooltips ("ene 2026" y no "Jan 2026").
    try {
        window.Plotly.register({
            moduleType: "locale", name: "es",
            dictionary: {},
            format: {
                days: ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"],
                shortDays: ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"],
                months: ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                         "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
                shortMonths: ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep",
                              "oct", "nov", "dic"],
                date: "%d-%m-%Y", decimal: ",", thousands: "."
            }
        });
    } catch (e) { /* versión de Plotly sin register: quedan en inglés */ }

    function isObj(o) { return o && typeof o === "object" && !Array.isArray(o); }
    function def(o, k, v) { if (o[k] === undefined) { o[k] = v; } }

    function tematizarEje(ax, esX) {
        def(ax, "automargin", true);
        def(ax, "gridcolor", GRID);
        def(ax, "zerolinecolor", LINE);
        def(ax, "linecolor", LINE);
        def(ax, "tickcolor", LINE);
        // Sin sufijos SI ingleses (k, M, G, T = "trillion"): miles con punto.
        if (ax.tickformat === undefined) { def(ax, "exponentformat", "none"); def(ax, "separatethousands", true); }
        ax.tickfont = Object.assign({ size: 11, color: MUTED }, ax.tickfont || {});
        if (isObj(ax.title)) {
            ax.title.font = Object.assign({ size: 11, color: MUTED }, ax.title.font || {});
        }
        if (esX) { def(ax, "showgrid", false); }
    }

    function tematizar(layout, data) {
        layout = layout || {};
        def(layout, "paper_bgcolor", "rgba(0,0,0,0)");
        def(layout, "plot_bgcolor", "rgba(0,0,0,0)");
        def(layout, "separators", ",.");
        def(layout, "colorway", COLORWAY);
        layout.font = Object.assign({ family: FONT, color: MUTED, size: 12 }, layout.font || {});
        layout.hoverlabel = Object.assign({ bgcolor: "#0b1220", bordercolor: LINE,
                                            font: { family: FONT, color: INK, size: 12 } },
                                          layout.hoverlabel || {});

        // Ejes: los declarados y los implícitos (xaxis/yaxis siempre existen en cartesianos).
        var cartesiano = !(data || []).length || (data || []).some(function (t) {
            return !t.type || !/^(pie|sunburst|treemap|sankey|indicator|table|funnelarea)$/.test(t.type);
        });
        if (cartesiano) {
            if (!layout.xaxis) { layout.xaxis = {}; }
            if (!layout.yaxis) { layout.yaxis = {}; }
        }
        Object.keys(layout).forEach(function (k) {
            var m = /^([xy])axis\d*$/.exec(k);
            if (m && isObj(layout[k])) { tematizarEje(layout[k], m[1] === "x"); }
        });

        // Leyenda arriba, horizontal, alineada a la izquierda del área de datos.
        if (layout.showlegend !== false) {
            var lg = layout.legend = layout.legend || {};
            // Una leyenda "h" sin y explícita también cae bajo el eje (Plotly la pone en y≈−0,1).
            var abajo = lg.orientation === "h" && (lg.y === undefined || lg.y < 0);
            var lateral = lg.orientation !== "h" && lg.x === undefined;
            if (abajo || lateral) {
                lg.orientation = "h";
                lg.x = 0; lg.xanchor = "left";
                lg.y = 1.0; lg.yanchor = "bottom";
            }
            def(lg, "bgcolor", "rgba(0,0,0,0)");
            lg.font = Object.assign({ size: 11, color: INK }, lg.font || {});
            layout.margin = layout.margin || {};
            if (lg.y >= 1 && (layout.margin.t === undefined || layout.margin.t < 28)) {
                layout.margin.t = 28;
            }
        }

        if (INF.esMovil && INF.esMovil()) {
            layout.margin = layout.margin || {};
            if (layout.margin.l === undefined || layout.margin.l > 48) { layout.margin.l = 48; }
            if (layout.margin.r !== undefined && layout.margin.r > 40) { layout.margin.r = 40; }
            if (layout.legend) { layout.legend.font = Object.assign({}, layout.legend.font, { size: 10 }); }
        }
        return layout;
    }

    // Un div .chart con alto fijo en línea (style="height:380px") y padding: Plotly dibuja
    // 380 px a partir del padding y el título del eje X se sale por abajo del panel. Se le
    // da el alto interno real.
    function altoInterno(gd) {
        var el = typeof gd === "string" ? document.getElementById(gd) : gd;
        var conAlto = el && el.style && (el.style.height || /\bviz-(sm|md|lg)\b/.test(el.className));
        if (!conAlto) { return undefined; }
        var cs = window.getComputedStyle(el);
        var h = el.clientHeight - parseFloat(cs.paddingTop || 0) - parseFloat(cs.paddingBottom || 0);
        return h > 80 ? h : undefined;
    }

    function config(c) {
        return Object.assign({ displayModeBar: false, responsive: true, locale: "es" }, c || {});
    }

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
    // Nombre propio legible en textos que arma JavaScript (leyendas, rótulos, tooltips):
    // 'BANCO DE CRÉDITO E INVERSIONES' → 'Banco de Crédito e Inversiones'. Es el mismo criterio
    // que el filtro `nombre` de api/deps.py; las tres listas deben ser idénticas a las de allá
    // (lo verifica tests/test_web.py::test_nombre_js_igual_a_python).
    var NOMBRE_SIGLAS = ["AFP", "AGF", "BCI", "BICE", "BTG", "CMF", "SP", "CLP", "USD", "UF", "ETF", "ESG", "EEUU",
        "EE.UU.", "FM", "FI", "FIP", "APV", "LP", "CP", "USA", "II", "III", "IV", "VI", "BCH", "CCS",
        "HSBC", "BBVA", "BNP", "JP", "IFRS", "ECC", "NDF", "OIS", "CGE", "ENAP", "LATAM", "SQM", "CMPC",
        "CAP", "SMU", "CCU", "AES", "IPC", "TPM", "PIB", "ADR", "REIT", "RV", "RF", "MM", "MMM", "MBI", "CMB"];
    var NOMBRE_ESPECIALES = {"PLANVITAL": "PlanVital", "ITAU": "Itaú", "ITAÚ": "Itaú", "LARRAINVIAL": "LarrainVial",
        "BANCOESTADO": "BancoEstado", "METLIFE": "MetLife", "4LIFE": "4Life", "SCOTIABANK": "Scotiabank",
        "S.A.": "S.A.", "S.A": "S.A.", "SA": "S.A.", "SPA": "SpA", "LTDA": "Ltda.", "LTDA.": "Ltda.",
        "CIA": "Cía.", "CIA.": "Cía.", "CÍA": "Cía.", "CÍA.": "Cía."};
    var NOMBRE_MENORES = ["de", "del", "la", "las", "los", "el", "y", "e", "en", "para", "por", "con", "a", "al", "o", "u"];
    INF.nombre = function (s) {
        var t = String(s === null || s === undefined ? "" : s).split(/\s+/).filter(Boolean).join(" ");
        if (!t || t !== t.toUpperCase() || t === t.toLowerCase()) { return t; }   // ya legible, o sin letras
        return t.split(" ").map(function (w, i) {
            var uw = w.toUpperCase(), lw = w.toLowerCase();
            if (Object.prototype.hasOwnProperty.call(NOMBRE_ESPECIALES, uw)) { return NOMBRE_ESPECIALES[uw]; }
            if (NOMBRE_SIGLAS.indexOf(uw.replace(/^[.,()]+|[.,()]+$/g, "")) >= 0 || /\d/.test(w)) { return w; }
            if (i && NOMBRE_MENORES.indexOf(lw) >= 0) { return lw; }
            return w.split("-").map(function (p) { return NOMBRE_SIGLAS.indexOf(p.toUpperCase()) >= 0 ? p : p.charAt(0).toUpperCase() + p.slice(1).toLowerCase(); }).join("-");
        }).join(" ");
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

    ["newPlot", "react"].forEach(function (fn) {
        var orig = window.Plotly[fn];
        if (!orig) { return; }
        window.Plotly[fn] = function (gd, data, layout, cfg) {
            if (isObj(data) && !Array.isArray(data)) {          // forma figura {data, layout, config}
                var fig = data;
                fig.layout = tematizar(fig.layout, fig.data);
                fig.config = config(fig.config);
                return orig.call(this, gd, fig);
            }
            var lay = tematizar(layout, data);
            if (lay.height === undefined) { var h = altoInterno(gd); if (h) { lay.height = h; } }
            return orig.call(this, gd, data, lay, config(cfg));
        };
    });
})();
