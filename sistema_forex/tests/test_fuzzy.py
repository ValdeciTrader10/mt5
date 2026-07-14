"""Testes do fuzzy_score / VWAP (ETAPA 3) — sem pytest, dados sintéticos conhecidos.

    python -m sistema_forex.tests.test_fuzzy

Cobre os critérios de aceite do roadmap: rally com volume → score alto (>76); absorção →
flag; exaustão → puxa o score de volta ao neutro (50). Mais VWAP, Sync Line e EV score.
"""

from .. import fuzzy_score as fz
from .. import indicadores as ind


# --------------------------------------------------------------------------- #
# Helpers para montar janelas de candles sintéticas
# --------------------------------------------------------------------------- #
def _dojis(n, base=100.0):
    """n candles doji (corpo ~0), range 1.0, volume 100 — a "referência" média."""
    o = [base] * n
    c = [base] * n
    h = [base + 0.5] * n
    l = [base - 0.5] * n
    v = [100] * n
    return o, h, l, c, v


def _rally():
    """20 dojis + 1 candle forte de ALTA (corpo 1.8× range médio, volume 2.5×)."""
    o, h, l, c, v = _dojis(20)
    o.append(100.0); c.append(101.8); h.append(101.9); l.append(99.95); v.append(250)
    return o, h, l, c, v


# --------------------------------------------------------------------------- #
# VWAP
# --------------------------------------------------------------------------- #
def test_vwap_constante_sigma_zero():
    vb = ind.vwap_bandas([2, 2], [1, 1], [1.5, 1.5], [10, 10])
    assert vb is not None and abs(vb["vwap"] - 1.5) < 1e-9, vb
    assert abs(vb["sigma"]) < 1e-9 and abs(vb["sup1"] - 1.5) < 1e-9, vb


def test_vwap_variando_tem_bandas():
    vb = ind.vwap_bandas([1, 3], [1, 3], [1, 3], [10, 10])   # tp 1 e 3, peso igual
    assert abs(vb["vwap"] - 2.0) < 1e-9, vb
    assert vb["sigma"] > 0 and vb["sup1"] > vb["vwap"] > vb["inf1"], vb
    assert vb["sup2"] > vb["sup1"], vb


def test_vwap_sem_volume_none():
    assert ind.vwap_bandas([1, 2], [1, 2], [1, 2], [0, 0]) is None


# --------------------------------------------------------------------------- #
# Fuzzy — critérios de aceite
# --------------------------------------------------------------------------- #
def test_rally_com_volume_score_alto():
    r = fz.avaliar_candle(*_rally())
    assert r is not None and r["score"] > 76, r
    assert r["estado"] == "lima" and not r["absorcao"] and not r["exaustao"], r


def test_absorcao_levanta_flag():
    o, h, l, c, v = _dojis(20)
    # Candle de volume ALTO e corpo minúsculo (esforço sem resultado), range grande.
    o.append(100.0); c.append(100.05); h.append(101.0); l.append(99.0); v.append(300)
    r = fz.avaliar_candle(o, h, l, c, v)
    assert r["absorcao"] is True, r
    assert 40 <= r["score"] <= 60, r        # sem convicção direcional → perto do neutro


def test_exaustao_puxa_para_neutro():
    # Sequência LONGA de altas + candle clímax (forte, volume alto) → exaustão.
    base = 100.0
    o = []; h = []; l = []; c = []; v = []
    for k in range(20):
        b = base + k * 0.3
        o.append(b); c.append(b + 0.3); l.append(b - 0.35); h.append(b + 0.65); v.append(100)
    b = base + 20 * 0.3
    o.append(b); c.append(b + 1.8); h.append(b + 1.9); l.append(b - 0.05); v.append(250)
    r = fz.avaliar_candle(o, h, l, c, v)
    rally = fz.avaliar_candle(*_rally())
    assert r["exaustao"] is True, r
    assert 50 <= r["score"] < 70, r                 # puxado de volta ao neutro
    assert r["score"] < rally["score"], (r["score"], rally["score"])


def test_estado_por_score():
    # Bandas FIÉIS ao PDF (item 1): lima 76+ · verde 56–75 · branco 46–55 · fúcsia 26–45 · vermelho ≤25
    assert fz.estado_por_score(90) == "lima"
    assert fz.estado_por_score(76) == "lima"
    assert fz.estado_por_score(75) == "verde"      # topo da banda verde
    assert fz.estado_por_score(56) == "verde"      # piso da banda verde (era branco antes)
    assert fz.estado_por_score(55) == "branco"     # topo do branco
    assert fz.estado_por_score(46) == "branco"     # piso do branco (era fúcsia? não; era branco 40+)
    assert fz.estado_por_score(45) == "fucsia"     # topo da fúcsia (era branco antes)
    assert fz.estado_por_score(26) == "fucsia"     # piso da fúcsia
    assert fz.estado_por_score(25) == "vermelho"   # topo do vermelho
    assert fz.estado_por_score(10) == "vermelho"


def test_pontuar_baixa_score_menor_que_50():
    o, h, l, c, v = _dojis(20)
    o.append(100.0); c.append(98.2); h.append(100.05); l.append(98.1); v.append(250)  # queda forte
    r = fz.avaliar_candle(o, h, l, c, v)
    assert r["score"] < 24 and r["estado"] == "vermelho", r


# --------------------------------------------------------------------------- #
# Sync Line
# --------------------------------------------------------------------------- #
def test_sync_line_alinhada_e_divergente():
    sl = fz.sync_line({"M1": 80, "M5": 75, "M15": 70, "H1": 65})
    assert sl["micro"] == "verde" and sl["macro"] == "verde" and sl["estado"] == "verde", sl
    sl2 = fz.sync_line({"M1": 80, "M5": 30, "M15": 70, "H1": 65})
    assert sl2["micro"] == "amarelo" and sl2["estado"] == "amarelo", sl2
    sl3 = fz.sync_line({"M1": 20, "M5": 15, "M15": 25, "H1": 10})
    assert sl3["estado"] == "vermelho", sl3


# --------------------------------------------------------------------------- #
# EV score + componentes
# --------------------------------------------------------------------------- #
def test_ev_score_extremos():
    assert fz.ev_score(confluencia=1, fuzzy=1, sync=1, localizacao=1)["ev"] == 100.0
    assert fz.ev_score(confluencia=0.5, fuzzy=0.5, sync=0.5, localizacao=0.5)["ev"] == 50.0


def test_componentes_ev():
    assert fz.componente_fuzzy("compra", 100) == 1.0
    assert fz.componente_fuzzy("venda", 100) == 0.0
    assert fz.componente_fuzzy("compra", None) == 0.5
    assert fz.componente_sync("compra", "verde") == 1.0
    assert fz.componente_sync("venda", "verde") == 0.0
    assert fz.componente_sync("compra", "amarelo") == 0.5
    assert fz.componente_localizacao("compra", 1.0, 1.1) == 0.7    # comprou abaixo da VWAP
    assert fz.componente_localizacao("compra", 1.2, 1.1) == 0.35   # comprou acima
    assert fz.componente_localizacao("venda", 1.0, 1.1) == 0.35


def main() -> int:
    testes = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in testes:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(testes)} testes passaram ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
