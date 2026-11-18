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

    var FONT = '"Segoe UI", system-ui, -apple-system, sans-serif';
    var INK = "#e2e8f0", MUTED = "#94a3b8", GRID = "#273449", LINE = "#475569";
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
        layout.hoverlabel = Object.assign({ bgcolor: "#0b1222", bordercolor: LINE,
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
        return layout;
    }

    // Un div .chart con alto fijo en línea (style="height:380px") y padding: Plotly dibuja
    // 380 px a partir del padding y el título del eje X se sale por abajo del panel. Se le
    // da el alto interno real.
    function altoInterno(gd) {
        var el = typeof gd === "string" ? document.getElementById(gd) : gd;
        if (!el || !el.style || !el.style.height) { return undefined; }
        var cs = window.getComputedStyle(el);
        var h = el.clientHeight - parseFloat(cs.paddingTop || 0) - parseFloat(cs.paddingBottom || 0);
        return h > 80 ? h : undefined;
    }

    function config(c) {
        return Object.assign({ displayModeBar: false, responsive: true, locale: "es" }, c || {});
    }

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
