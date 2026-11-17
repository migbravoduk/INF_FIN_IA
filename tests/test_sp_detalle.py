"""Parser del detalle de la cartera desagregada de la SP (collectors/sp_cartera_detalle.py)."""

import io

from collectors.sp_cartera_detalle import COLUMNAS, clasificar, iter_listados, parse_listado

_AFP = '<afp><rut><numero>98000000</numero><dv>1</dv></rut><nombre>CAPITAL</nombre>{}</afp>'
XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<constitucion_cartera_desagregada xmlns="http://www.spensiones.cl/xml"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <encabezado><periodo>2026-06</periodo></encabezado>
 <listado numero="26"><listado_por_afp>
  <tipofondo codigo="C"><titulo>CUADRO N° 26: DIVERSIFICACION ... TIPO C ... EN SWAPS NACIONALES POR AFP (1)</titulo>
   <agrupacion>
    <fila numero="1"><glosa>SWAP NACIONAL DE TASAS DE INTERES:BANCO DE CHILE</glosa><unidad_indexada>NO</unidad_indexada>
     <columnas>{_AFP.format('<monto_dolares>-2.50</monto_dolares>')}<total><monto_dolares>-2.50</monto_dolares><porcentaje>0.00</porcentaje></total></columnas></fila>
    <fila numero="2"><glosa>SCOTIABANK CHILE</glosa><unidad_indexada>US$</unidad_indexada>
     <columnas>{_AFP.format('<monto_dolares xsi:nil="true"></monto_dolares>')}<total><monto_dolares>1.00</monto_dolares></total></columnas></fila>
   </agrupacion></tipofondo>
  <total_fondos><titulo>total</titulo><agrupacion><fila numero="1"><glosa>X</glosa><columnas><total><monto_dolares>9</monto_dolares></total></columnas></fila></agrupacion></total_fondos>
 </listado_por_afp></listado>
 <listado numero="27"><listado_por_fondo><afp><titulo>CUADRO N° 27: ... EN SWAPS NACIONALES POR TIPO DE FONDO</titulo></afp></listado_por_fondo></listado>
 <listado numero="11"><listado_por_instrumento>
  <tipofondo codigo="A"><titulo>CUADRO N° 11: CARTERA ... TIPO A, POR EMISOR E INSTRUMENTO Y AFP</titulo>
   <instrumentos><glosa>BCU</glosa><glosa>DPF</glosa></instrumentos>
   {_AFP.format('<agrupacion nombre="FINANCIERO"><fila numero="1"><glosa>BANCO DE CHILE</glosa><columnas><instrumento><porcentaje xsi:nil="true"/></instrumento><instrumento><porcentaje>1.20</porcentaje></instrumento></columnas><total><monto_pesos>500.0</monto_pesos><monto_dolares>0.54</monto_dolares></total></fila></agrupacion>')}
   <total_afps><agrupacion><fila numero="1"><glosa>BANCO DE CHILE</glosa><total><monto_pesos>999</monto_pesos></total></fila></agrupacion></total_afps>
  </tipofondo></listado_por_instrumento></listado>
 <listado numero="22"><listado_por_instrumento_monedaobjeto_afp>
  <tipofondo codigo="E"><titulo>CUADRO N° 22: ... INVERSIÓN EN INSTRUMENTOS FORWARD NACIONALES ...</titulo>
   <afp_por_moneda_objeto moneda_objeto="Dólar estadounidense(US$)"><afp_por_agrupacion agrupacion_instrumentos="Forward">
    <fila numero="1"><glosa>WNMC</glosa><columnas>{_AFP.format('<monto_dolares>29.49</monto_dolares>')}<total><monto_dolares>29.49</monto_dolares></total></columnas></fila>
    <fila numero="2"><glosa>Pesos</glosa><columnas>{_AFP.format('<monto_dolares>29.49</monto_dolares>')}</columnas></fila>
   </afp_por_agrupacion></afp_por_moneda_objeto>
  </tipofondo></listado_por_instrumento_monedaobjeto_afp></listado>
</constitucion_cartera_desagregada>"""


def _filas():
    out, per = [], None
    for n, el in iter_listados(io.BytesIO(XML.encode("utf-8"))):
        if n == "periodo":
            per = int(el.replace("-", ""))
            continue
        out += [dict(zip(COLUMNAS, r)) for r in parse_listado(el, per)]
    return out


def test_clasifica_por_titulo():
    assert clasificar("CUADRO N° 28: ... EN SWAPS EXTRANJEROS POR AFP") == "swap_ext"
    assert clasificar("INVERSIÓN EN EL EXTRANJERO, POR EMISOR E INSTRUMENTO") == "emisor_ext"
    assert clasificar("CARTERA ... POR EMISOR E INSTRUMENTO Y AFP") == "emisor_nac"
    assert clasificar("INVERSION EN INSTRUMENTOS DERIVADOS NACIONALES EN FUNCION") == "deriv_nac"


def test_swaps_grupo_afp_y_total():
    f = [r for r in _filas() if r["tabla"] == "swap_nac"]
    chile = [r for r in f if r["glosa"] == "BANCO DE CHILE" and r["afp"] == "CAPITAL"]
    assert chile[0]["valor"] == -2.5 and chile[0]["grupo"] == "SWAP NACIONAL DE TASAS DE INTERES"
    assert chile[0]["unidad"] == "NO" and chile[0]["period"] == 202606 and chile[0]["fondo"] == "C"
    # el grupo se arrastra a las filas siguientes; nil no genera fila; total_fondos se omite
    scotia = [r for r in f if r["glosa"] == "SCOTIABANK CHILE"]
    assert [(r["afp"], r["grupo"]) for r in scotia] == [("TOTAL", "SWAP NACIONAL DE TASAS DE INTERES")]
    assert not [r for r in f if r["glosa"] == "X"]


def test_emisor_por_afp_sin_total_afps():
    f = [r for r in _filas() if r["tabla"] == "emisor_nac"]
    assert {(r["columna"], r["metrica"], r["valor"]) for r in f} == {
        ("DPF", "porcentaje", 1.2), ("total", "monto_pesos", 500.0), ("total", "monto_dolares", 0.54)}
    assert {r["afp"] for r in f} == {"CAPITAL"} and {r["grupo"] for r in f} == {"FINANCIERO"}


def test_forwards_codigo_y_moneda_objeto():
    f = [r for r in _filas() if r["tabla"] == "fwd_nac"]
    assert {(r["glosa"], r["codigo"], r["afp"]) for r in f} == {
        ("WNMC", "WNMC", "CAPITAL"), ("WNMC", "WNMC", "TOTAL"), ("Pesos", "WNMC", "CAPITAL")}
    assert {r["grupo"] for r in f} == {"Dólar estadounidense(US$)"}


def test_transpuestos_se_omiten():
    assert not [r for r in _filas() if r["fila"] is None]
    assert {r["tabla"] for r in _filas()} == {"swap_nac", "emisor_nac", "fwd_nac"}


def test_norm_emisor_iguala_variantes():
    from db.cross import _norm_emisor
    assert _norm_emisor("CIA. CHILENA DE FOSFOROS S.A.") == _norm_emisor("Compañía Chilena de Fósforos SA")
    assert _norm_emisor("AGUAS MAGALLANES S. A.") == "AGUAS MAGALLANES"
    assert _norm_emisor("SODIMAC SA") == "SODIMAC"
