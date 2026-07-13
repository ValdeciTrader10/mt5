"""Testes dos indicadores (Fase 2) — sem pytest, rodam com python puro.

    python -m sistema_forex.tests.test_indicadores

Cada caso usa dados sintéticos com resposta conhecida. Falha = AssertionError.
"""

import itertools

from .. import indicadores as ind


def test_atr_plano():
    # candles planos: high-low=1.0, close no meio, sem tendência → ATR = 1.0
    a = ind.atr([1.0] * 30, [0.0] * 30, [0.5] * 30, 14)
    assert a is not None and abs(a - 1.0) < 1e-9, a


def test_adx_tendencia_vs_lateral():
    h = [100 + i * 2 for i in range(60)]
    l = [99 + i * 2 for i in range(60)]
    c = [99.5 + i * 2 for i in range(60)]
    adx_v, pdi, mdi = ind.adx(h, l, c, 14)
    assert adx_v > 40 and pdi > mdi, (adx_v, pdi, mdi)
    assert ind.classificar_regime(adx_v, pdi, mdi, 25, 20) == "tendencia_alta"

    chop = list(itertools.islice(itertools.cycle([100, 101, 100, 101]), 60))
    adx_lat, *_ = ind.adx([x + 0.5 for x in chop], [x - 0.5 for x in chop], chop, 14)
    assert adx_lat < 30, adx_lat
    assert ind.classificar_regime(adx_lat, 0, 0, 25, 20) == "lateral"


def test_swings_rotulos_e_eventos():
    seq = [10, 11, 12, 13, 12, 11, 10, 11, 12, 13, 14, 15, 14, 13, 12, 13, 14, 15, 16, 17, 16, 15]
    H = [x + 0.2 for x in seq]
    L = [x - 0.2 for x in seq]
    sw = ind.rotular_swings(ind.swings(H, L, 2))
    assert any(s["tipo"] == "high" for s in sw) and any(s["tipo"] == "low" for s in sw)
    assert "HH" in {s["label"] for s in sw}
    ev = ind.eventos_estrutura(sw)
    assert any(e["evento"] == "BOS" for e in ev)


def test_sr_clusteriza_dois_niveis():
    sw = [
        {"i": 0, "tipo": "high", "preco": 1.1000},
        {"i": 5, "tipo": "high", "preco": 1.1002},
        {"i": 9, "tipo": "high", "preco": 1.1001},
        {"i": 3, "tipo": "low", "preco": 1.0900},
        {"i": 7, "tipo": "low", "preco": 1.0901},
        {"i": 11, "tipo": "low", "preco": 1.0899},
    ]
    sr = ind.niveis_sr(sw, atr_val=0.0010, cluster_atr=0.5, forca_min=3)
    assert len(sr["resistencia"]) == 1 and sr["resistencia"][0][1] == 3, sr
    assert len(sr["suporte"]) == 1 and sr["suporte"][0][1] == 3, sr
    assert abs(sr["resistencia"][0][0] - 1.1001) < 2e-4


def test_fvg_bull():
    fv = ind.fvgs([1.10, 1.115, 1.13], [1.09, 1.105, 1.12], atr_val=0.005, min_atr=0.3)
    assert any(f["tipo"] == "fvg_bull" for f in fv), fv


def test_gaps():
    g = ind.gaps([1.1000, 1.1000, 1.1015], [1.1000, 1.1000, 1.1000], 0.0001, 5, 20)
    assert len(g) == 1 and g[0]["direcao"] == "alta" and abs(g[0]["pips"] - 15) < 0.1, g
    fora = ind.gaps([1.1000, 1.1003, 1.1030], [1.1000, 1.1000, 1.1000], 0.0001, 5, 20)
    assert fora == [], fora


def test_qualidade_sr_mede_toques_e_rejeicoes():
    # Resistência em 1.1000: candles que furam por cima e FECHAM abaixo = rejeição forte.
    highs = [1.1002, 1.1005, 1.0990, 1.1003]
    lows = [1.0980, 1.0985, 1.0970, 1.0985]
    closes = [1.0985, 1.0988, 1.0975, 1.0987]   # sempre fecha abaixo do nível
    q = ind.qualidade_sr(1.1000, "resistencia", highs, lows, closes, 0.0005)
    assert q["toques"] >= 2 and q["rejeicoes"] >= 2 and q["respeito"] > 0, q
    # Suporte em 1.0000: um toque que fecha acima = rejeição; um rompimento (fecha abaixo) não.
    qs = ind.qualidade_sr(
        1.0000, "suporte", [1.0010, 1.0005], [0.9998, 0.9990], [1.0008, 0.9985], 0.0005)
    assert qs["rejeicoes"] == 1, qs


def test_order_block_bull():
    # Vela 0 = OB de BAIXA (open>close). Velas 1-3 = impulso de alta que deixa FVG bull
    # (low[3]=1.1016 > high[1]=1.1008). Nada reentra na zona → OB fresco na vela 0.
    o = [1.1010, 1.1000, 1.1007, 1.1017]
    h = [1.1012, 1.1008, 1.1017, 1.1032]
    l = [1.0998, 1.0999, 1.1006, 1.1016]
    c = [1.1000, 1.1006, 1.1016, 1.1030]
    obs = ind.order_blocks(o, h, l, c, atr_val=0.0010, min_atr=0.3)
    assert len(obs) == 1 and obs[0]["tipo"] == "ob_bull" and obs[0]["i"] == 0, obs
    assert abs(obs[0]["base"] - 1.0998) < 1e-9 and abs(obs[0]["topo"] - 1.1012) < 1e-9, obs


def test_order_block_bear_espelho():
    # Espelho do bull em torno de P: vira um OB de alta antes de um impulso de baixa.
    p = 2.2020
    o = [1.1010, 1.1000, 1.1007, 1.1017]
    h = [1.1012, 1.1008, 1.1017, 1.1032]
    l = [1.0998, 1.0999, 1.1006, 1.1016]
    c = [1.1000, 1.1006, 1.1016, 1.1030]
    oo = [round(p - x, 5) for x in o]
    hh = [round(p - x, 5) for x in l]   # high refletido = P - low
    ll = [round(p - x, 5) for x in h]   # low refletido  = P - high
    cc = [round(p - x, 5) for x in c]
    obs = ind.order_blocks(oo, hh, ll, cc, atr_val=0.0010, min_atr=0.3)
    assert len(obs) == 1 and obs[0]["tipo"] == "ob_bear", obs


def test_order_block_mitigado_nao_conta():
    # Igual ao bull, mas uma vela posterior REENTRA na zona (low volta a 1.1005 < topo 1.1012).
    o = [1.1010, 1.1000, 1.1007, 1.1017, 1.1020]
    h = [1.1012, 1.1008, 1.1017, 1.1032, 1.1025]
    l = [1.0998, 1.0999, 1.1006, 1.1016, 1.1005]
    c = [1.1000, 1.1006, 1.1016, 1.1030, 1.1010]
    obs = ind.order_blocks(o, h, l, c, atr_val=0.0010, min_atr=0.3)
    assert obs == [], obs


def test_sma_e_ema():
    vals = [float(i) for i in range(1, 21)]        # 1..20
    assert abs(ind.sma(vals, 5) - 18.0) < 1e-9, ind.sma(vals, 5)   # média de 16..20
    assert ind.sma([1, 2], 5) is None
    # EMA de série crescente fica entre a SMA e o último valor, e responde mais ao recente.
    e = ind.ema(vals, 10)
    assert e is not None and 15.0 < e < 20.0, e
    # Série constante: EMA = a própria constante.
    assert abs(ind.ema([5.0] * 30, 10) - 5.0) < 1e-9


def test_medias_conjunto():
    closes = [float(i) for i in range(1, 61)]      # 60 closes → SMA200 fica None
    m = ind.medias(closes)
    assert m["ema9"] is not None and m["ema20"] is not None and m["sma50"] is not None
    assert m["sma200"] is None, m["sma200"]


def test_pivots_classicos():
    pv = ind.pivots_classicos(110.0, 90.0, 100.0)
    assert abs(pv["pp"] - 100.0) < 1e-9, pv        # (110+90+100)/3
    assert abs(pv["r1"] - 110.0) < 1e-9, pv        # 2*100 - 90
    assert abs(pv["s1"] - 90.0) < 1e-9, pv         # 2*100 - 110
    assert abs(pv["r2"] - 120.0) < 1e-9, pv        # pp + (h-l)
    assert abs(pv["s2"] - 80.0) < 1e-9, pv
    assert pv["r3"] > pv["r2"] and pv["s3"] < pv["s2"]


def main() -> int:
    testes = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in testes:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(testes)} testes passaram ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
