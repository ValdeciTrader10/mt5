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


def test_impulso_forte_sem_volume_alto_nao_degenera():
    """Regressão (auditoria 16/07): impulso FORTE (corpo 1.5× o range médio, marubozu) com volume
    apenas mediano (1.15×) caía num BURACO das regras → score 50 (igual a um doji) e, cruzando
    vol=1.2, saltava a ~100. Agora as regras complementares dão convicção alta e CONTÍNUA."""
    def vela(vol):
        o, h, l, c, v = _dojis(20)
        o.append(100.0); c.append(101.5); h.append(101.55); l.append(99.98); v.append(vol)
        return fz.avaliar_candle(o, h, l, c, v)
    sem_vol = vela(115)     # vol 1.15× (abaixo do gatilho de v_alto)
    com_vol = vela(125)     # vol 1.25× (v_alto começa a ativar)
    assert sem_vol["score"] > 76, sem_vol            # impulso forte NÃO é neutro
    assert abs(com_vol["score"] - sem_vol["score"]) < 10, (sem_vol["score"], com_vol["score"])


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


def test_forca_sync_alinhada_e_divergente():
    # Micro (M1/M5) e macro (M15/H1) ambos > 50 → verde; força > 50.
    a = fz.forca_sync({"M1": 70, "M5": 66, "M15": 62, "H1": 60})
    assert a["estado"] == "verde" and a["micro"] > 0 and a["macro"] > 0 and a["forca"] > 50, a
    assert not a["divergencia"], a
    # micro comprador, macro vendedor → amarelo + divergência (o "ATENÇÃO" do Sentinela).
    d = fz.forca_sync({"M1": 70, "M5": 66, "M15": 35, "H1": 30})
    assert d["estado"] == "amarelo" and d["divergencia"] and d["micro"] > 0 and d["macro"] < 0, d
    # ambos vendedores → vermelho.
    v = fz.forca_sync({"M1": 30, "M5": 34, "M15": 38, "H1": 40})
    assert v["estado"] == "vermelho" and v["forca"] < 50, v


def test_leque_spread():
    assert fz.leque_spread({"M1": 70, "M5": 50, "M15": 40, "H1": 80}) == 40.0
    assert fz.leque_spread({"M1": 55, "M5": 53, "M15": 52, "H1": 54}) == 3.0   # comprimido
    assert fz.leque_spread({"M1": 60}) == 0.0                                   # < 2 TFs


def test_forca_serie_asof():
    """A série de força usa, em cada instante, o último score de candle que já FECHOU em t
    (time_utc + minutos do TF ≤ t) — asof pelo FECHAMENTO, sem look-ahead: o candle H1 das
    10h (dados até 11h) só entra na série a partir das 11h."""
    import os
    import tempfile
    from .. import db
    fd, caminho = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        db.init_db(caminho); conn = db.conectar(caminho)
        def ins(tf, t, score):
            conn.execute("INSERT INTO fuzzy_scores (par,tf,time_utc,score,estado,delta,rng,vol,corpo,"
                         "seq,absorcao,exaustao,transicao,criado_em) VALUES "
                         "('X',?,?,?,'',0,0,0,0,0,0,0,0,0)", (tf, t, score))
        base = 100 * 86400
        for tf in ("M1", "M5", "M15", "H1"):
            ins(tf, base, 70)                       # candles abrem em t=base (H1 fecha base+3600)
        ins("M15", base + 7200, 30); ins("H1", base + 7200, 30)   # macro vira vendedor depois
        conn.commit()
        serie = fz.forca_serie(conn, "X", [base + 3600, base + 10800])
        assert serie[0]["estado"] == "verde", serie[0]      # 1º instante: tudo alinhado (fechado)
        assert serie[1]["estado"] == "amarelo" and serie[1]["divergencia"], serie[1]  # macro virou
        # LOOK-AHEAD: em t = base+60 o H1 de `base` ainda NÃO fechou → macro indisponível.
        cedo = fz.forca_serie(conn, "X", [base + 60])
        assert cedo[0]["macro"] == 0.0, cedo[0]             # score do H1 aberto não vaza p/ trás
        conn.close()
    finally:
        os.remove(caminho)


def test_forca_linha_acumulador_balanca():
    """A LINHA de força é um ACUMULADOR (balança com a tendência), não a média estática (quase plana).
    Numa alta sustentada ela SOBE bem acima de 50 e bem acima do nível instantâneo (forca_inst)."""
    import os
    import tempfile
    from .. import db
    fd, caminho = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        db.init_db(caminho); conn = db.conectar(caminho)
        base = 100 * 86400
        for tf in ("M1", "M5", "M15", "H1"):
            for k in range(1, 13):        # 12 velas de consenso comprador (score 70), 1/h
                conn.execute("INSERT INTO fuzzy_scores (par,tf,time_utc,score,estado,delta,rng,vol,"
                             "corpo,seq,absorcao,exaustao,transicao,criado_em) VALUES "
                             "('X',?,?,70,'',0,0,0,0,0,0,0,0,0)", (tf, base + k * 3600))
        conn.commit()
        # Instantes APÓS o fechamento de cada rodada (H1 do instante k fecha em k+1h).
        serie = fz.forca_serie(conn, "X", [base + (k + 1) * 3600 + 1 for k in range(1, 13)],
                               decay=0.85, escala=40.0)
        assert serie[-1]["forca"] > 85, serie[-1]           # acumulador subiu forte
        assert serie[-1]["forca"] > serie[0]["forca"], (serie[0]["forca"], serie[-1]["forca"])  # balançou
        assert serie[-1]["forca_inst"] == 70.0, serie[-1]   # o nível estático fica ~plano (70) — daí a média era chata
        conn.close()
    finally:
        os.remove(caminho)


def main() -> int:
    testes = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in testes:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(testes)} testes passaram ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
