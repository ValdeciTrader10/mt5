"""Testes da lógica de decisão (Fase 4) — sem pytest.

    python -m sistema_forex.tests.test_estrategias
"""

from .. import estrategias as e

# Config de teste (espelha os defaults relevantes do config.py)
CFG = dict(sessao_utc=(7, 20), spread_max_pips=2.0, score_min=2, nivel_prox_atr=0.5,
           forca_min=3, pavio_min=0.5)


def _snap(**kw):
    base = dict(
        close=1.1000, open=1.1000, high=1.1005, low=1.0995,
        spread_pips=1.0, hora_utc=10, atr=0.0010,
        regime="tendencia_alta", suportes=[], resistencias=[], fvgs=[], ultimo_evento=None,
    )
    base.update(kw)
    return base


def test_entra_com_confluencias():
    # tendência de alta + preço perto de suporte forte + BOS de alta = 3 confluências
    snap = _snap(
        regime="tendencia_alta",
        suportes=[(1.0999, 5)],
        ultimo_evento={"evento": "BOS", "direcao": "alta", "tf": "M5"},
    )
    d = e.avaliar(snap, **CFG)
    assert d["resultado"] == "entrou" and d["direcao"] == "compra", d
    assert d["score"] >= 2 and "regime" in d["confluencias"]


def test_nao_entra_fora_da_sessao():
    snap = _snap(hora_utc=3, suportes=[(1.0999, 5)],
                 ultimo_evento={"evento": "BOS", "direcao": "alta", "tf": "M5"})
    d = e.avaliar(snap, **CFG)
    assert d["resultado"] == "nao_entrou" and "sessão" in d["motivo"], d


def test_nao_entra_spread_alto():
    snap = _snap(spread_pips=3.5, suportes=[(1.0999, 5)],
                 ultimo_evento={"evento": "BOS", "direcao": "alta", "tf": "M5"})
    d = e.avaliar(snap, **CFG)
    assert d["resultado"] == "nao_entrou" and "spread" in d["motivo"], d


def test_nao_entra_confluencia_insuficiente():
    # só o regime conta (sem nível perto, sem evento) → score 1 < 2
    snap = _snap(regime="tendencia_alta", suportes=[(1.2000, 5)], ultimo_evento=None)
    d = e.avaliar(snap, **CFG)
    assert d["resultado"] == "nao_entrou" and "insuficientes" in d["motivo"], d


def test_sem_vies_quando_indefinido():
    d = e.avaliar(_snap(regime="indefinido"), **CFG)
    assert d["resultado"] == "nao_entrou" and d["direcao"] is None


def test_lateral_rejeicao_conta_como_confluencia():
    # lateral, preço na resistência forte, candle de REJEIÇÃO → entra e 'rejeicao' no score
    snap = _snap(regime="lateral", open=1.1050, high=1.1055, low=1.1045, close=1.1047,
                 resistencias=[(1.1051, 4)], suportes=[(1.0900, 4)],
                 ultimo_evento={"evento": "CHOCH", "direcao": "baixa", "tf": "M15"})
    d = e.avaliar(snap, **CFG)
    assert d["direcao"] == "venda" and d["resultado"] == "entrou", d
    assert "rejeicao" in d["confluencias"], d


def test_lateral_sem_rejeicao_ainda_entra_soft():
    # SEM rejeição, mas com confluências suficientes → ENTRA (rejeição não é obrigatória)
    snap = _snap(regime="lateral", open=1.1046, high=1.1052, low=1.1045, close=1.1051,
                 resistencias=[(1.1051, 4)], suportes=[(1.0900, 4)],
                 ultimo_evento={"evento": "CHOCH", "direcao": "baixa", "tf": "M15"})
    d = e.avaliar(snap, **CFG)
    assert d["resultado"] == "entrou" and "rejeicao" not in d["confluencias"], d


def test_lateral_modo_estrito_exige_rejeicao():
    # com exigir_rejeicao=True e sem rejeição → não entra
    snap = _snap(regime="lateral", open=1.1046, high=1.1052, low=1.1045, close=1.1051,
                 resistencias=[(1.1051, 4)], suportes=[(1.0900, 4)],
                 ultimo_evento={"evento": "CHOCH", "direcao": "baixa", "tf": "M15"})
    d = e.avaliar(snap, **{**CFG, "exigir_rejeicao": True})
    assert d["resultado"] == "nao_entrou" and "estrito" in d["motivo"], d


# --------------------------------------------------------------------------- #
# Estratégia 2 — liquidity sweep + CHoCH (M5)
# --------------------------------------------------------------------------- #
# Cenário de COMPRA montado à mão (n_swing=3): swing high A@3, swing low B@8 (=1.1000),
# swing high C@11 (=1.1038, ref. do CHoCH), vela de SWEEP@13 (fura 1.1000, fecha acima),
# e a vela atual @17 FECHA acima de C (CHoCH de alta fresco).
_HIGH = [1.1015, 1.1020, 1.1025, 1.1040, 1.1030, 1.1022, 1.1015, 1.1010, 1.1008,
         1.1014, 1.1022, 1.1038, 1.1020, 1.1010, 1.1018, 1.1028, 1.1030, 1.1045]
_LOW = [1.1008, 1.1013, 1.1018, 1.1030, 1.1022, 1.1014, 1.1008, 1.1003, 1.1000,
        1.1006, 1.1014, 1.1028, 1.1010, 1.0997, 1.1006, 1.1018, 1.1024, 1.1030]
_CLOSE = [1.1012, 1.1018, 1.1023, 1.1032, 1.1025, 1.1016, 1.1010, 1.1005, 1.1002,
          1.1010, 1.1018, 1.1030, 1.1014, 1.1004, 1.1012, 1.1024, 1.1029, 1.1040]

CFG_SWEEP = dict(sessao_utc=(7, 20), spread_max_pips=2.0, n_swing=3, sweep_min_atr=0.1,
                 sweep_recente=6, nivel_prox_atr=0.5, forca_min=3)


def _snap_sweep(**kw):
    base = dict(atr=0.0010, regime="lateral", hora_utc=10, spread_pips=1.0,
                suportes=[], resistencias=[],
                m5_janela={"open": _CLOSE, "high": _HIGH, "low": _LOW, "close": _CLOSE})
    base.update(kw)
    return base


def test_sweep_choch_detecta_compra():
    det = e.detectar_sweep_choch(_CLOSE, _HIGH, _LOW, _CLOSE, 0.0010, n_swing=3,
                                 sweep_min_atr=0.1, sweep_recente=6)
    assert det is not None and det["direcao"] == "compra", det
    assert abs(det["nivel_sweep"] - 1.1000) < 1e-9 and det["i_sweep"] == 13, det


def test_sweep_choch_espelho_venda():
    # Reflexão em torno de P: vira o cenário de compra num de venda perfeito.
    p = 2.2040
    sh = [round(p - x, 5) for x in _LOW]    # highs refletidos
    sl = [round(p - x, 5) for x in _HIGH]   # lows refletidos
    sc = [round(p - x, 5) for x in _CLOSE]
    det = e.detectar_sweep_choch(sc, sh, sl, sc, 0.0010, n_swing=3, sweep_min_atr=0.1,
                                 sweep_recente=6)
    assert det is not None and det["direcao"] == "venda", det


def test_sweep_choch_sem_padrao_none():
    flat_h = [1.1002] * 18
    flat_l = [1.0998] * 18
    flat_c = [1.1000] * 18
    det = e.detectar_sweep_choch(flat_c, flat_h, flat_l, flat_c, 0.0010, n_swing=3,
                                 sweep_min_atr=0.1, sweep_recente=6)
    assert det is None, det


def test_avaliar_sweep_entra_compra_com_sr_reforco():
    snap = _snap_sweep(suportes=[(1.1000, 5)])   # sweep bate num suporte forte → reforço
    d = e.avaliar_sweep_choch(snap, **CFG_SWEEP)
    assert d["resultado"] == "entrou" and d["direcao"] == "compra", d
    assert d["estrategia"] == "sweep_choch_v1", d
    assert "sweep+choch" in d["confluencias"] and "sr_confluente_5" in d["confluencias"], d


def test_avaliar_sweep_fora_da_sessao_nao_entra():
    d = e.avaliar_sweep_choch(_snap_sweep(hora_utc=3), **CFG_SWEEP)
    assert d["resultado"] == "nao_entrou" and "sessão" in d["motivo"], d


def test_avaliar_sweep_sem_janela_nao_entra():
    snap = _snap_sweep(m5_janela={"open": [], "high": [], "low": [], "close": []})
    d = e.avaliar_sweep_choch(snap, **CFG_SWEEP)
    assert d["resultado"] == "nao_entrou" and "janela" in d["motivo"], d


def test_avaliar_sweep_sem_padrao_nao_entra():
    snap = _snap_sweep(m5_janela={"open": [1.1] * 18, "high": [1.1002] * 18,
                                  "low": [1.0998] * 18, "close": [1.1] * 18})
    d = e.avaliar_sweep_choch(snap, **CFG_SWEEP)
    assert d["resultado"] == "nao_entrou" and "sweep" in d["motivo"], d


# --------------------------------------------------------------------------- #
# Estratégia 3 — reteste de Order Block
# --------------------------------------------------------------------------- #
CFG_OB = dict(sessao_utc=(7, 20), spread_max_pips=2.0, nivel_prox_atr=0.5, forca_min=3,
              pavio_min=0.5)


def _snap_ob(**kw):
    # OB bull na zona [1.0998, 1.1012]; vela de decisão retesta e REJEITA (pavio inferior).
    base = dict(close=1.1013, open=1.1010, high=1.1014, low=1.0999,
                atr=0.0010, regime="tendencia_alta", hora_utc=10, spread_pips=1.0,
                suportes=[], resistencias=[],
                obs=[{"tipo": "ob_bull", "base": 1.0998, "topo": 1.1012}])
    base.update(kw)
    return base


def test_ob_entra_compra_no_reteste():
    d = e.avaliar_order_block(_snap_ob(), **CFG_OB)
    assert d["resultado"] == "entrou" and d["direcao"] == "compra", d
    assert d["estrategia"] == "order_block_v1" and "order_block" in d["confluencias"], d
    assert "rejeicao" in d["confluencias"] and "a_favor_regime" in d["confluencias"], d


def test_ob_sr_reforco_soma():
    d = e.avaliar_order_block(_snap_ob(suportes=[(1.0998, 4)]), **CFG_OB)
    assert "sr_confluente_4" in d["confluencias"], d


def test_ob_preco_fora_da_zona_nao_entra():
    d = e.avaliar_order_block(_snap_ob(close=1.1050, low=1.1045, high=1.1052, open=1.1048),
                              **CFG_OB)
    assert d["resultado"] == "nao_entrou" and "fora das zonas" in d["motivo"], d


def test_ob_sem_ob_nao_entra():
    d = e.avaliar_order_block(_snap_ob(obs=[]), **CFG_OB)
    assert d["resultado"] == "nao_entrou" and "sem OB" in d["motivo"], d


def test_ob_modo_estrito_exige_rejeicao():
    # sem rejeição (fecha longe da borda, sem pavio) e modo estrito → não entra
    snap = _snap_ob(close=1.1011, open=1.1009, high=1.1012, low=1.1008)
    d = e.avaliar_order_block(snap, **{**CFG_OB, "exigir_rejeicao": True})
    assert d["resultado"] == "nao_entrou" and "estrito" in d["motivo"], d


# --------------------------------------------------------------------------- #
# Estratégia 4 — pullback a favor da tendência + rejeição em S/R forte
# --------------------------------------------------------------------------- #
CFG_PB = dict(sessao_utc=(7, 20), spread_max_pips=2.0, nivel_prox_atr=0.5, forca_min=3,
              pavio_min=0.5)


def _snap_pb(**kw):
    # tendência de alta; preço recua ao suporte forte 1.1000 e REJEITA (pavio inferior).
    base = dict(close=1.1003, open=1.1004, high=1.1006, low=1.0998,
                atr=0.0010, regime="tendencia_alta", hora_utc=10, spread_pips=1.0,
                suportes=[(1.1000, 5)], resistencias=[], obs=[])
    base.update(kw)
    return base


def test_pullback_entra_a_favor_da_tendencia():
    d = e.avaliar_pullback_tendencia(_snap_pb(), **CFG_PB)
    assert d["resultado"] == "entrou" and d["direcao"] == "compra", d
    assert d["estrategia"] == "pullback_tendencia_v1", d
    assert "regime" in d["confluencias"] and "rejeicao" in d["confluencias"], d


def test_pullback_fora_de_tendencia_nao_entra():
    d = e.avaliar_pullback_tendencia(_snap_pb(regime="lateral"), **CFG_PB)
    assert d["resultado"] == "nao_entrou" and "tendência" in d["motivo"], d


def test_pullback_sem_sr_forte_nao_entra():
    d = e.avaliar_pullback_tendencia(_snap_pb(suportes=[(1.1000, 1)]), **CFG_PB)
    assert d["resultado"] == "nao_entrou" and "S/R forte" in d["motivo"], d


def test_pullback_sem_rejeicao_nao_entra():
    # preço no suporte forte mas SEM rejeição (fecha no fundo) → não é a tese, não entra
    snap = _snap_pb(close=1.0999, open=1.1005, high=1.1006, low=1.0998)
    d = e.avaliar_pullback_tendencia(snap, **CFG_PB)
    assert d["resultado"] == "nao_entrou" and "rejeição" in d["motivo"], d


def test_pullback_ob_confluente_soma():
    d = e.avaliar_pullback_tendencia(
        _snap_pb(obs=[{"tipo": "ob_bull", "base": 1.0996, "topo": 1.1004}]), **CFG_PB)
    assert "ob_confluente" in d["confluencias"], d


# --------------------------------------------------------------------------- #
# Estratégia 5 — fechamento de gap (fade rumo ao fechamento anterior)
# --------------------------------------------------------------------------- #
CFG_GAP = dict(sessao_utc=(7, 20), spread_max_pips=2.0, nivel_prox_atr=0.5, forca_min=3,
               gap_min_atr=0.5)


def _snap_gap(**kw):
    # gap de ALTA (alvo do fill 1.0990, abaixo); vela virou p/ baixo (momentum de fill).
    base = dict(close=1.1000, open=1.1005, high=1.1006, low=1.0999,
                atr=0.0010, regime="lateral", hora_utc=10, spread_pips=1.0,
                suportes=[], resistencias=[],
                gaps=[{"direcao": "alta", "nivel": 1.0990}])
    base.update(kw)
    return base


def test_gap_entra_venda_com_momentum():
    d = e.avaliar_fecha_gap(_snap_gap(), **CFG_GAP)
    assert d["resultado"] == "entrou" and d["direcao"] == "venda", d
    assert d["estrategia"] == "fecha_gap_v1", d
    assert "gap" in d["confluencias"] and "momentum_fill" in d["confluencias"], d


def test_gap_sem_espaco_nao_entra():
    d = e.avaliar_fecha_gap(_snap_gap(gaps=[{"direcao": "alta", "nivel": 1.0997}]), **CFG_GAP)
    assert d["resultado"] == "nao_entrou" and "espaço" in d["motivo"], d


def test_gap_ja_preenchido_nao_entra():
    # gap de alta mas preço JÁ está abaixo do alvo → preenchido, sem trade.
    d = e.avaliar_fecha_gap(_snap_gap(gaps=[{"direcao": "alta", "nivel": 1.1010}]), **CFG_GAP)
    assert d["resultado"] == "nao_entrou" and "preenchido" in d["motivo"], d


def test_gap_sem_momentum_nao_entra():
    # vela subindo (contra o fill de um gap de alta) → sem momentum p/ o fill.
    d = e.avaliar_fecha_gap(_snap_gap(open=1.0999, close=1.1000), **CFG_GAP)
    assert d["resultado"] == "nao_entrou" and "momentum" in d["motivo"], d


def test_gap_sr_alvo_reforco():
    d = e.avaliar_fecha_gap(_snap_gap(resistencias=[(1.0990, 4)]), **CFG_GAP)
    assert "sr_alvo_4" in d["confluencias"], d


# --------------------------------------------------------------------------- #
# Estratégia 6 — pullback ao rompimento (reteste com inversão de polaridade)
# --------------------------------------------------------------------------- #
CFG_ROMP = dict(sessao_utc=(7, 20), spread_max_pips=2.0, nivel_prox_atr=0.5, forca_min=3,
                pavio_min=0.5)


def _snap_romp(**kw):
    # BOS de alta rompeu a resistência 1.1000; preço retesta por cima e REJEITA (pavio inferior).
    base = dict(close=1.1003, open=1.1001, high=1.1004, low=1.0998,
                atr=0.0010, regime="tendencia_alta", hora_utc=10, spread_pips=1.0,
                suportes=[], resistencias=[(1.1000, 4)],
                ultimo_evento={"evento": "BOS", "direcao": "alta", "tf": "M15"})
    base.update(kw)
    return base


def test_rompimento_entra_compra_no_reteste():
    d = e.avaliar_pullback_rompimento(_snap_romp(), **CFG_ROMP)
    assert d["resultado"] == "entrou" and d["direcao"] == "compra", d
    assert d["estrategia"] == "pullback_rompimento_v1", d
    assert "reteste_rompimento" in d["confluencias"] and "rejeicao" in d["confluencias"], d


def test_rompimento_sem_bos_nao_entra():
    d = e.avaliar_pullback_rompimento(_snap_romp(ultimo_evento=None), **CFG_ROMP)
    assert d["resultado"] == "nao_entrou" and "BOS" in d["motivo"], d


def test_rompimento_choch_nao_dispara():
    ev = {"evento": "CHOCH", "direcao": "alta", "tf": "M15"}
    d = e.avaliar_pullback_rompimento(_snap_romp(ultimo_evento=ev), **CFG_ROMP)
    assert d["resultado"] == "nao_entrou" and "BOS" in d["motivo"], d


def test_rompimento_sem_nivel_no_reteste():
    d = e.avaliar_pullback_rompimento(_snap_romp(resistencias=[(1.2000, 4)]), **CFG_ROMP)
    assert d["resultado"] == "nao_entrou" and "sem nível" in d["motivo"], d


def test_rompimento_sem_rejeicao_nao_entra():
    # preço no nível invertido mas sem rejeição (sem pavio) → não confirma o reteste.
    snap = _snap_romp(close=1.1003, open=1.1002, high=1.1004, low=1.1002)
    d = e.avaliar_pullback_rompimento(snap, **CFG_ROMP)
    assert d["resultado"] == "nao_entrou" and "rejeição" in d["motivo"], d


# --------------------------------------------------------------------------- #
# Estratégia 7 — rompimento da máx/mín do dia anterior (PDH/PDL) + reteste
# --------------------------------------------------------------------------- #
CFG_EXT = dict(sessao_utc=(7, 20), spread_max_pips=2.0, nivel_prox_atr=0.5, pavio_min=0.5)


def _snap_ext(**kw):
    # rompeu a máxima do dia (1.1000) e retesta por cima, REJEITANDO (pavio inferior).
    base = dict(close=1.1003, open=1.1001, high=1.1004, low=1.0998,
                atr=0.0010, regime="tendencia_alta", hora_utc=10, spread_pips=1.0,
                max_dia=1.1000, min_dia=1.0950)
    base.update(kw)
    return base


def test_extremos_entra_compra_na_pdh():
    d = e.avaliar_rompimento_extremos(_snap_ext(), **CFG_EXT)
    assert d["resultado"] == "entrou" and d["direcao"] == "compra", d
    assert d["estrategia"] == "rompimento_extremos_v1", d
    assert "rompeu_extremo_dia" in d["confluencias"] and "rejeicao" in d["confluencias"], d


def test_extremos_entra_venda_na_pdl():
    # rompeu a mínima do dia (1.0950) e retesta por baixo, rejeitando (pavio superior).
    snap = _snap_ext(close=1.0947, open=1.0949, high=1.0952, low=1.0946,
                     regime="tendencia_baixa")
    d = e.avaliar_rompimento_extremos(snap, **CFG_EXT)
    assert d["resultado"] == "entrou" and d["direcao"] == "venda", d


def test_extremos_sem_reteste_nao_entra():
    # preço já correu bem além da PDH → passou do reteste.
    d = e.avaliar_rompimento_extremos(_snap_ext(close=1.1050), **CFG_EXT)
    assert d["resultado"] == "nao_entrou" and "reteste" in d["motivo"], d


def test_extremos_sem_extremos_nao_entra():
    d = e.avaliar_rompimento_extremos(_snap_ext(max_dia=None), **CFG_EXT)
    assert d["resultado"] == "nao_entrou" and "máx/mín" in d["motivo"], d


def main() -> int:
    testes = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in testes:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(testes)} testes passaram ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
