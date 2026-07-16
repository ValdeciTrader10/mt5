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
# Estratégia 2b — caça-stops COM filtro de absorção (sweep_choch_abs_v1)
# --------------------------------------------------------------------------- #
# A vela de SWEEP é a @13 (open=close=1.1004 → corpo doji). Volume plano com PICO nessa vela
# → absorção (volume alto + corpo fraco); volume plano em tudo → sem absorção.
_VOL_ABS = [100] * 13 + [320] + [100] * 4
_VOL_FLAT = [100] * 18
CFG_SWEEP_ABS = {**CFG_SWEEP, "absorcao_janela": 20}


def _jan_sweep(volume):
    return {"open": _CLOSE, "high": _HIGH, "low": _LOW, "close": _CLOSE, "volume": volume}


def test_avaliar_sweep_abs_entra_com_absorcao():
    snap = _snap_sweep(suportes=[(1.1000, 5)], m5_janela=_jan_sweep(_VOL_ABS))
    d = e.avaliar_sweep_choch_abs(snap, **CFG_SWEEP_ABS)
    assert d["resultado"] == "entrou" and d["direcao"] == "compra", d
    assert d["estrategia"] == "sweep_choch_abs_v1", d
    assert "absorcao" in d["confluencias"] and "sweep+choch" in d["confluencias"], d


def test_avaliar_sweep_abs_sem_absorcao_nao_entra():
    # mesmo sweep, mas volume plano na varredura → sem absorção → não entra (é o A/B vs sweep_choch_v1)
    snap = _snap_sweep(m5_janela=_jan_sweep(_VOL_FLAT))
    d = e.avaliar_sweep_choch_abs(snap, **CFG_SWEEP_ABS)
    assert d["resultado"] == "nao_entrou" and "absorção" in d["motivo"], d


def test_avaliar_sweep_abs_sem_volume_nao_entra():
    # janela sem coluna de volume → não dá p/ medir absorção → não entra (seguro, sem crash)
    d = e.avaliar_sweep_choch_abs(_snap_sweep(), **CFG_SWEEP_ABS)
    assert d["resultado"] == "nao_entrou" and "volume" in d["motivo"], d


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


# --------------------------------------------------------------------------- #
# Estratégia 8 — pullback a médias (EMA9/EMA20 do TF acima) em tendência
# --------------------------------------------------------------------------- #
CFG_MED = dict(sessao_utc=(7, 20), spread_max_pips=2.0, nivel_prox_atr=0.5, pavio_min=0.5)


def _snap_med(**kw):
    # tendência de alta; preço recua e toca a EMA20 do TF acima (1.1000), rejeitando (pavio inf.).
    base = dict(close=1.1003, open=1.1004, high=1.1006, low=1.0998,
                atr=0.0010, regime="tendencia_alta", hora_utc=10, spread_pips=1.0,
                suportes=[], resistencias=[], fvgs=[], obs=[],
                medias_acima={"ema9": 1.1020, "ema20": 1.1000, "ema45": 1.1050,
                              "sma50": 1.1080, "sma200": 1.1200})
    base.update(kw)
    return base


def test_medias_entra_no_toque_da_ema():
    d = e.avaliar_pullback_medias(_snap_med(), **CFG_MED)
    assert d["resultado"] == "entrou" and d["direcao"] == "compra", d
    assert d["estrategia"] == "pullback_medias_v1" and d["variante"] == "A_ORIGINAL", d
    assert "toque_ema20" in d["confluencias"] and "rejeicao" in d["confluencias"], d


def test_medias_fora_de_tendencia_nao_entra():
    d = e.avaliar_pullback_medias(_snap_med(regime="lateral"), **CFG_MED)
    assert d["resultado"] == "nao_entrou" and "tendência" in d["motivo"], d


def test_medias_longe_das_medias_nao_entra():
    d = e.avaliar_pullback_medias(_snap_med(close=1.1500, open=1.1499, high=1.1502,
                                            low=1.1498), **CFG_MED)
    assert d["resultado"] == "nao_entrou" and "longe" in d["motivo"], d


def test_medias_fvg_confluente_dobra_score():
    base = e.avaliar_pullback_medias(_snap_med(), **CFG_MED)
    dobrado = e.avaliar_pullback_medias(
        _snap_med(fvgs=[{"tipo": "fvg_bull", "base": 1.0995, "topo": 1.1005}]), **CFG_MED)
    assert "fvg_confluente" in dobrado["confluencias"], dobrado
    assert dobrado["score"] > base["score"], (dobrado["score"], base["score"])


# --------------------------------------------------------------------------- #
# Estratégia 9 — toque em pivot confluente com S/R/OB + rejeição
# --------------------------------------------------------------------------- #
CFG_PIV = dict(sessao_utc=(7, 20), spread_max_pips=2.0, nivel_prox_atr=0.5, pivot_sr_atr=0.5,
               pavio_min=0.5)


def _snap_piv(**kw):
    # pivot S1 em 1.1000 confluente com um suporte forte; preço toca por cima e REJEITA → compra.
    base = dict(close=1.1003, open=1.1004, high=1.1006, low=1.0998,
                atr=0.0010, regime="lateral", hora_utc=10, spread_pips=1.0,
                suportes=[(1.1000, 4)], resistencias=[], obs=[],
                pivots=[(1.1000, "s1"), (1.1090, "r1")])
    base.update(kw)
    return base


def test_pivot_entra_com_confluencia_e_rejeicao():
    d = e.avaliar_pivot_confluencia(_snap_piv(), **CFG_PIV)
    assert d["resultado"] == "entrou" and d["direcao"] == "compra", d
    assert d["estrategia"] == "pivot_confluencia_v1", d
    assert "pivot" in d["confluencias"] and "rejeicao" in d["confluencias"], d
    assert any(c.startswith("confluencia_sr") for c in d["confluencias"]), d


def test_pivot_sem_confluencia_sr_nao_entra():
    d = e.avaliar_pivot_confluencia(_snap_piv(suportes=[], obs=[]), **CFG_PIV)
    assert d["resultado"] == "nao_entrou" and "confluência" in d["motivo"], d


def test_pivot_longe_de_pivot_nao_entra():
    d = e.avaliar_pivot_confluencia(_snap_piv(close=1.1050, open=1.1051, high=1.1053,
                                              low=1.1049), **CFG_PIV)
    assert d["resultado"] == "nao_entrou" and "longe de pivot" in d["motivo"], d


def test_pivot_sem_rejeicao_nao_entra():
    # preço no pivot mas fecha no fundo (sem pavio inferior) → não confirma o fade de compra.
    d = e.avaliar_pivot_confluencia(_snap_piv(close=1.1000, open=1.1004, high=1.1005,
                                              low=1.1000), **CFG_PIV)
    assert d["resultado"] == "nao_entrou" and "rejeição" in d["motivo"], d


# --------------------------------------------------------------------------- #
# VARIANTE B — Fuzzy Puro (ETAPA 5)
# --------------------------------------------------------------------------- #
CFG_B = dict(sessao_utc=(7, 20), spread_max_pips=2.0, mare_min=60, corrente_min=55,
             timing_min=58, std_k=1.0, checklist_min=5)

# 20 closes com desvio-padrão ~0.0005 (alterna ±0.0005 em torno de 1.0990).
_CLOSES20 = [1.0985, 1.0995] * 10


def _snap_b(**kw):
    base = dict(
        close=1.0998, open=1.0990, high=1.0999, low=1.0989,
        spread_pips=1.0, hora_utc=10, regime="tendencia_alta",
        fuzzy={"M15": {"score": 70}, "M5": {"score": 62}, "M1": {"score": 65}},
        vwap={"vwap": 1.1000, "sup1": 1.1010, "inf1": 1.0990, "sup2": 1.1020, "inf2": 1.0980},
    )
    base.update(kw)
    base["m5_janela"] = {"close": kw.get("_closes", _CLOSES20)}
    return base


def test_fuzzy_puro_entra_pullback_vwap():
    d = e.avaliar_fuzzy_puro(_snap_b(), **CFG_B)
    assert d["resultado"] == "entrou" and d["direcao"] == "compra", d
    assert d["variante"] == "B_FUZZY_PURO" and d["estrategia"] == "fuzzy_puro_v1", d
    assert any(c.startswith("cenario_") for c in d["confluencias"]), d
    assert "cenario_PULLBACK_VWAP" in d["confluencias"], d


def test_fuzzy_puro_b2_lima_e_mais_seletiva_e_rotula_o_livro():
    # A/B do item 4: MESMO snapshot (M15=70). A Variante B (maré 60/verde) ENTRA; a B2
    # (maré 76/Lima) NÃO (70<76) — exigir Lima seca sinais. E o rótulo do livro paralelo propaga.
    d_verde = e.avaliar_fuzzy_puro(_snap_b(), **CFG_B)
    cfg_lima = dict(CFG_B); cfg_lima["mare_min"] = 76
    d_lima = e.avaliar_fuzzy_puro(_snap_b(), estrategia=e.ESTRATEGIA_FUZZY_PURO_LIMA, **cfg_lima)
    assert d_verde["resultado"] == "entrou" and d_verde["estrategia"] == "fuzzy_puro_v1", d_verde
    assert d_lima["resultado"] == "nao_entrou" and "maré" in d_lima["motivo"], d_lima
    assert d_lima["estrategia"] == "fuzzy_puro_lima_v1" and d_lima["variante"] == "B_FUZZY_PURO", d_lima
    # com M15=80 (Lima), a B2 volta a entrar, ainda com o rótulo do livro paralelo
    d_lima2 = e.avaliar_fuzzy_puro(
        _snap_b(fuzzy={"M15": {"score": 80}, "M5": {"score": 62}, "M1": {"score": 65}}),
        estrategia=e.ESTRATEGIA_FUZZY_PURO_LIMA, **cfg_lima)
    assert d_lima2["estrategia"] == "fuzzy_puro_lima_v1", d_lima2


def test_fuzzy_puro_estouro_venda():
    # Maré vendedora (M15<=40), gatilho forte abaixo da VWAP → ESTOURO de venda.
    d = e.avaliar_fuzzy_puro(_snap_b(
        close=1.0980, open=1.0995, high=1.0996, low=1.0979,
        fuzzy={"M15": {"score": 30}, "M5": {"score": 38}, "M1": {"score": 35}}), **CFG_B)
    assert d["resultado"] == "entrou" and d["direcao"] == "venda", d
    assert "cenario_ESTOURO" in d["confluencias"], d


def test_fuzzy_puro_bloqueia_exaustao():
    d = e.avaliar_fuzzy_puro(_snap_b(
        fuzzy={"M15": {"score": 70}, "M5": {"score": 62}, "M1": {"score": 65, "exaustao": 1}}), **CFG_B)
    assert d["resultado"] == "nao_entrou" and "EXAUSTAO" in d["motivo"], d


def test_fuzzy_puro_bloqueia_absorcao_topo():
    # Compra, absorção no M5 e preço acima da banda +1σ → ABSORÇÃO DE TOPO (bloqueio).
    d = e.avaliar_fuzzy_puro(_snap_b(
        close=1.1012, open=1.1005, high=1.1013, low=1.1004,
        fuzzy={"M15": {"score": 70}, "M5": {"score": 62, "absorcao": 1}, "M1": {"score": 65}}), **CFG_B)
    assert d["resultado"] == "nao_entrou" and "ABSORCAO_TOPO" in d["motivo"], d


def test_fuzzy_puro_sem_mare():
    d = e.avaliar_fuzzy_puro(_snap_b(
        fuzzy={"M15": {"score": 50}, "M5": {"score": 62}, "M1": {"score": 65}}), **CFG_B)
    assert d["resultado"] == "nao_entrou" and "maré" in d["motivo"], d


def test_fuzzy_puro_sem_fuzzy_mtf():
    d = e.avaliar_fuzzy_puro(_snap_b(fuzzy={}), **CFG_B)
    assert d["resultado"] == "nao_entrou" and "MTF" in d["motivo"], d


def test_fuzzy_puro_checklist_insuficiente():
    # Sem força (corpo minúsculo < σ) e sem timing → cai abaixo de 5/6.
    d = e.avaliar_fuzzy_puro(_snap_b(
        close=1.09995, open=1.09990,   # corpo 0.00005 << σ (~0.0005) → sem força
        fuzzy={"M15": {"score": 70}, "M5": {"score": 62}, "M1": {"score": 50}}), **CFG_B)
    assert d["resultado"] == "nao_entrou", d
    assert "checklist" in d["motivo"] or "cenário" in d["motivo"], d


def test_fuzzy_puro_gate_sessao_e_spread():
    d1 = e.avaliar_fuzzy_puro(_snap_b(hora_utc=3), **CFG_B)
    assert d1["resultado"] == "nao_entrou" and "sessão" in d1["motivo"], d1
    d2 = e.avaliar_fuzzy_puro(_snap_b(spread_pips=3.0), **CFG_B)
    assert d2["resultado"] == "nao_entrou" and "spread" in d2["motivo"], d2


def test_saida_tecnica_fuzzy_puro():
    assert e.saida_tecnica_fuzzy_puro("compra", 1.0990, sma50=1.1000) is True   # perdeu a SMA50
    assert e.saida_tecnica_fuzzy_puro("compra", 1.1010, sma50=1.1000, vwap=1.1005) is False
    assert e.saida_tecnica_fuzzy_puro("venda", 1.1010, vwap=1.1000) is True     # rompeu a VWAP p/ cima
    assert e.saida_tecnica_fuzzy_puro("venda", 1.0990, sma50=1.1000) is False


# --------------------------------------------------------------------------- #
# VARIANTE C — Híbrida (ETAPA 6): camada fuzzy sobre a decisão da Variante A
# --------------------------------------------------------------------------- #
CFG_C = dict(mare_min=60, corrente_min=55)


def _dec_base(**kw):
    """Decisão-base 'entrou' de uma estratégia da Variante A (só o que a camada C lê)."""
    base = dict(resultado="entrou", direcao="compra", estrategia=e.ESTRATEGIA,
                regime="tendencia_alta", score=2, confluencias=["regime", "nivel_forca_5"],
                motivo="regime+nivel_forca_5", variante=e.VARIANTE_A)
    base.update(kw)
    return base


def _snap_c(**kw):
    base = dict(
        close=1.0995,   # aquém da VWAP (1.1000) → localização de valor p/ compra
        fuzzy={"M15": {"score": 70}, "M5": {"score": 62}, "M1": {"score": 65}},
        vwap={"vwap": 1.1000, "sup1": 1.1010, "inf1": 1.0990, "sup2": 1.1020, "inf2": 1.0980},
    )
    base.update(kw)
    return base


def test_hibrida_confirma_e_soma_confluencias():
    # Compra: M15 a favor + localização de valor (aquém da VWAP) → C entra com confluências fuzzy.
    d = e.avaliar_hibrida(_dec_base(), _snap_c(), **CFG_C)
    assert d is not None and d["resultado"] == "entrou", d
    assert d["variante"] == "C_HIBRIDA" and d["estrategia"] == e.ESTRATEGIA, d
    assert "fuzzy_m15" in d["confluencias"] and "fuzzy_vwap_valor" in d["confluencias"], d
    assert d["score"] > 2, d   # somou bônus fuzzy sobre o score-base


def test_hibrida_virada_na_zona_soma():
    # Estratégia de zona + transição (virada de causa) no M5 → confluência fuzzy_virada.
    snap = _snap_c(fuzzy={"M15": {"score": 70}, "M5": {"score": 62, "transicao": 1}, "M1": {"score": 65}})
    d = e.avaliar_hibrida(_dec_base(estrategia=e.ESTRATEGIA_OB), snap, **CFG_C)
    assert d["resultado"] == "entrou" and "fuzzy_virada" in d["confluencias"], d


def test_hibrida_sweep_esforco_soma():
    d = e.avaliar_hibrida(_dec_base(estrategia=e.ESTRATEGIA_SWEEP, direcao="compra"),
                          _snap_c(), **CFG_C)
    assert d["resultado"] == "entrou" and "fuzzy_esforco" in d["confluencias"], d


def test_hibrida_veta_m15_contra():
    # Compra, mas M15 fuzzy claramente vendedor (score<=40) → veta.
    d = e.avaliar_hibrida(_dec_base(), _snap_c(
        fuzzy={"M15": {"score": 30}, "M5": {"score": 62}, "M1": {"score": 65}}), **CFG_C)
    assert d["resultado"] == "nao_entrou" and "M15 contra" in d["motivo"], d


def test_hibrida_veta_exaustao():
    d = e.avaliar_hibrida(_dec_base(), _snap_c(
        fuzzy={"M15": {"score": 70}, "M5": {"score": 62}, "M1": {"score": 65, "exaustao": 1}}), **CFG_C)
    assert d["resultado"] == "nao_entrou" and "exaustão" in d["motivo"], d


def test_hibrida_veta_absorcao_topo():
    # Compra com absorção no M5 e preço acima da banda +1σ da VWAP → veto de absorção de topo.
    d = e.avaliar_hibrida(_dec_base(), _snap_c(
        close=1.1012,
        fuzzy={"M15": {"score": 70}, "M5": {"score": 62, "absorcao": 1}, "M1": {"score": 65}}), **CFG_C)
    assert d["resultado"] == "nao_entrou" and "absorção de topo" in d["motivo"], d


def test_hibrida_none_quando_base_nao_entrou():
    assert e.avaliar_hibrida(_dec_base(resultado="nao_entrou"), _snap_c(), **CFG_C) is None


def test_hibrida_saida_antecipada():
    # Compra: M5 fuzzy vira vendedor forte (<=40) → saída antecipada; a favor (>=60) → segura.
    assert e.saida_antecipada_hibrida("compra", 35, minimo=60) is True
    assert e.saida_antecipada_hibrida("compra", 70, minimo=60) is False
    assert e.saida_antecipada_hibrida("venda", 70, minimo=60) is True
    assert e.saida_antecipada_hibrida("venda", None, minimo=60) is False


def test_hibrida_ajuste_stop_exaustao():
    # Compra, SL 10 pips abaixo; sob exaustão aperta para metade da distância (só aproxima do preço).
    novo = e.ajuste_stop_exaustao(1.0990, "compra", 1.1000, True, aperto=0.5)
    assert abs(novo - 1.0995) < 1e-9, novo
    assert 1.0990 <= novo <= 1.1000, novo   # nunca afrouxa (fica entre o SL antigo e o preço)
    # sem exaustão, mantém o SL
    assert e.ajuste_stop_exaustao(1.0990, "compra", 1.1000, False) == 1.0990
    # venda é o espelho: SL acima do preço, aperta para baixo (em direção ao preço)
    nv = e.ajuste_stop_exaustao(1.1010, "venda", 1.1000, True, aperto=0.5)
    assert abs(nv - 1.1005) < 1e-9 and 1.1000 <= nv <= 1.1010, nv


def test_gestao_saida_variante_c_antecipada():
    # C, compra: M5 fuzzy vira vendedor forte → fecha antecipado (integração 5), SL inalterado.
    d = e.gestao_saida_variante("C_HIBRIDA", "compra", 1.1000, 1.0990, fuzzy_m5=30,
                                exausto=False, m5_min=60, aperto=0.5)
    assert d["fechar"] is True and "antecipada C" in d["motivo"] and d["novo_sl"] == 1.0990


def test_gestao_saida_variante_c_aperta_stop_na_exaustao():
    # C, compra, M5 a favor (não fecha): sob exaustão aperta o stop (integração 6), sem fechar.
    d = e.gestao_saida_variante("C_HIBRIDA", "compra", 1.1000, 1.0990, fuzzy_m5=70,
                                exausto=True, m5_min=60, aperto=0.5)
    assert d["fechar"] is False and abs(d["novo_sl"] - 1.0995) < 1e-9, d
    # sem exaustão e M5 a favor: não mexe em nada
    d2 = e.gestao_saida_variante("C_HIBRIDA", "compra", 1.1000, 1.0990, fuzzy_m5=70,
                                 exausto=False, m5_min=60, aperto=0.5)
    assert d2 == {"novo_sl": 1.0990, "fechar": False, "motivo": ""}, d2


def test_gestao_saida_variante_b_tecnica_vwap():
    # B, compra: preço cruzou ABAIXO da VWAP → perdeu a referência de valor → fecha.
    d = e.gestao_saida_variante("B_FUZZY_PURO", "compra", 1.0990, 1.0980, vwap=1.1000,
                                m5_min=60, aperto=0.5)
    assert d["fechar"] is True and "técnica B" in d["motivo"]
    # preço ainda ACIMA da VWAP → segura
    d2 = e.gestao_saida_variante("B_FUZZY_PURO", "compra", 1.1010, 1.0980, vwap=1.1000,
                                 m5_min=60, aperto=0.5)
    assert d2["fechar"] is False


def test_gestao_saida_variante_c_carencia_nao_fecha_no_1o_ciclo():
    # BUG (sombra B3 14/07): C fechava no MESMO ciclo da abertura com ~0 de movimento porque o M5
    # fuzzy no instante da entrada estava contra. Com a carência, a posição jovem NÃO fecha antecipado.
    d = e.gestao_saida_variante("C_HIBRIDA", "compra", 1.1000, 1.0990, fuzzy_m5=30,
                                exausto=False, m5_min=60, aperto=0.5,
                                idade_candles=0.5, min_candles=2)
    assert d["fechar"] is False, d          # jovem demais → segura, deixa andar
    # passada a carência, o mesmo M5 contra fecha normalmente.
    d2 = e.gestao_saida_variante("C_HIBRIDA", "compra", 1.1000, 1.0990, fuzzy_m5=30,
                                 exausto=False, m5_min=60, aperto=0.5,
                                 idade_candles=3, min_candles=2)
    assert d2["fechar"] is True and "antecipada C" in d2["motivo"], d2
    # exaustão dentro da carência AINDA aperta o stop (integração 6 é conservadora, não é fechamento).
    d3 = e.gestao_saida_variante("C_HIBRIDA", "compra", 1.1000, 1.0990, fuzzy_m5=70,
                                 exausto=True, m5_min=60, aperto=0.5,
                                 idade_candles=0.0, min_candles=2)
    assert d3["fechar"] is False and abs(d3["novo_sl"] - 1.0995) < 1e-9, d3


def test_gestao_saida_variante_b_carencia_nao_fecha_no_1o_ciclo():
    # B jovem: mesmo com o preço já cruzado a VWAP, a carência segura a saída técnica.
    d = e.gestao_saida_variante("B_FUZZY_PURO", "compra", 1.0990, 1.0980, vwap=1.1000,
                                m5_min=60, aperto=0.5, idade_candles=0.5, min_candles=2)
    assert d["fechar"] is False, d
    d2 = e.gestao_saida_variante("B_FUZZY_PURO", "compra", 1.0990, 1.0980, vwap=1.1000,
                                 m5_min=60, aperto=0.5, idade_candles=3, min_candles=2)
    assert d2["fechar"] is True and "técnica B" in d2["motivo"], d2


def test_gestao_saida_variante_a_controle_nunca_mexe():
    # A_ORIGINAL (controle) nunca passa pela gestão por variante: no-op mesmo com fuzzy contra.
    d = e.gestao_saida_variante("A_ORIGINAL", "compra", 1.1000, 1.0990, fuzzy_m5=10,
                                exausto=True, vwap=1.2000, m5_min=60, aperto=0.5)
    assert d == {"novo_sl": 1.0990, "fechar": False, "motivo": ""}, d


def test_gestao_saida_variante_c_corre_deixa_andar():
    # EXPERIMENTO: C_CORRE NÃO tem saída fuzzy (só o gestor genérico) — no-op mesmo com M5 contra e
    # exaustão. Prova que o livro "deixa correr" não é cortado cedo pela camada fuzzy.
    d = e.gestao_saida_variante("C_CORRE", "compra", 1.1000, 1.0990, fuzzy_m5=10,
                                exausto=True, vwap=1.2000, m5_min=60, aperto=0.5)
    assert d == {"novo_sl": 1.0990, "fechar": False, "motivo": ""}, d


# --------------------------------------------------------------------------- #
# FAMÍLIA D_LINHAS — dinâmica das curvas de score fuzzy
# --------------------------------------------------------------------------- #
_VW = {"vwap": 100.0, "sup1": 105.0, "inf1": 95.0, "sup2": 110.0, "inf2": 90.0}


def _base_linhas(**kw):
    b = dict(regime="tendencia_alta", hora_utc=10, spread_pips=1.0, close=100.0, vwap=_VW)
    b.update(kw)
    return b


CFG_LINHAS = dict(sessao_utc=(7, 20), spread_max_pips=2.0)


def test_divergencia_baixa_entra_venda():
    # 2 topos de preço ASCENDENTES (i=2:15, i=8:18) + score DESCENDENTE neles (70→60), preço no topo
    # de valor (>= sup1) → divergência de baixa = VENDA. O 2º topo (i=8) é RECÉM-confirmado
    # (i == len-1-n_swing) — a entrada só vale no candle da confirmação. (n_swing=2.)
    highs = [10, 11, 15, 11, 10, 10, 11, 12, 18, 12, 11]
    lows = [9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9]
    score = [50, 50, 70, 50, 50, 50, 50, 50, 60, 50, 50]
    snap = _base_linhas(close=106.0, serie_op={"high": highs, "low": lows,
                        "close": [100.0] * 11, "score": score})
    d = e.avaliar_divergencia_fuzzy(snap, n_swing=2, **CFG_LINHAS)
    assert d["resultado"] == "entrou" and d["direcao"] == "venda", d
    assert d["variante"] == "D_LINHAS" and d["estrategia"] == "fuzzy_divergencia_v1", d
    # sem divergência (score sobe junto com o preço) → não entra
    d2 = e.avaliar_divergencia_fuzzy(_base_linhas(close=106.0, serie_op={"high": highs, "low": lows,
                        "close": [100.0] * 11, "score": [50, 50, 60, 50, 50, 50, 50, 50, 70, 50, 50]}),
                        n_swing=2, **CFG_LINHAS)
    assert d2["resultado"] == "nao_entrou", d2
    # FRESCOR: 2 candles depois (mesma divergência, swing antigo) → NÃO re-entra (anti re-entrada serial)
    velho = {"high": highs + [10, 10], "low": lows + [9, 9], "close": [100.0] * 13,
             "score": score + [50, 50]}
    d3 = e.avaliar_divergencia_fuzzy(_base_linhas(close=106.0, serie_op=velho), n_swing=2, **CFG_LINHAS)
    assert d3["resultado"] == "nao_entrou", d3


def test_pullback_leque_entra_compra():
    # Maré M15 comprada (65≥60); rápida recuou abaixo da lenta e REENGATA acima agora; preço ≤ VWAP.
    snap = _base_linhas(close=98.0, fuzzy={"M15": {"score": 65}},
                        serie_op={"high": [], "low": [], "close": [], "score": [45, 48, 50, 55]},
                        score_acima=[53, 53, 53, 53])
    d = e.avaliar_pullback_leque(snap, mare_min=60, dip_janela=6, **CFG_LINHAS)
    assert d["resultado"] == "entrou" and d["direcao"] == "compra", d
    assert d["variante"] == "D_LINHAS", d
    # sem reengate (rápida segue abaixo da lenta) → não entra
    snap2 = _base_linhas(close=98.0, fuzzy={"M15": {"score": 65}},
                         serie_op={"score": [45, 48, 50, 52]}, score_acima=[53, 53, 53, 53])
    d2 = e.avaliar_pullback_leque(snap2, mare_min=60, dip_janela=6, **CFG_LINHAS)
    assert d2["resultado"] == "nao_entrou", d2


def test_sync_flip_entra_na_convergencia():
    # Sync amarelo→verde (flip), maré M15 a favor, rompendo a VWAP p/ cima → COMPRA.
    snap = _base_linhas(close=101.0, sync_ult=["amarelo", "verde"], fuzzy={"M15": {"score": 65}})
    d = e.avaliar_sync_flip(snap, mare_min=60, **CFG_LINHAS)
    assert d["resultado"] == "entrou" and d["direcao"] == "compra", d
    # sem flip (já estava verde) → não entra
    d2 = e.avaliar_sync_flip(_base_linhas(close=101.0, sync_ult=["verde", "verde"],
                             fuzzy={"M15": {"score": 65}}), mare_min=60, **CFG_LINHAS)
    assert d2["resultado"] == "nao_entrou", d2


def test_exaustao_entra_venda_no_climax():
    # Score preso alto (85,86,87,88) por 4 velas e ROLA (82<88), preço na banda +2σ → fade = VENDA.
    snap = _base_linhas(close=111.0, serie_op={"score": [70, 85, 86, 87, 88, 82]})
    d = e.avaliar_exaustao_fuzzy(snap, sat_candles=4, sat_alto=80, sat_baixo=20, **CFG_LINHAS)
    assert d["resultado"] == "entrou" and d["direcao"] == "venda", d
    assert d["variante"] == "D_LINHAS", d
    # ainda subindo (sem rollover) → não entra
    d2 = e.avaliar_exaustao_fuzzy(_base_linhas(close=111.0, serie_op={"score": [70, 85, 86, 87, 88, 90]}),
                                  sat_candles=4, sat_alto=80, sat_baixo=20, **CFG_LINHAS)
    assert d2["resultado"] == "nao_entrou", d2


# --------------------------------------------------------------------------- #
# FAMÍLIA E_SENTINELA — força contínua (micro/macro) + leque
# --------------------------------------------------------------------------- #
def test_sentinela_forca_entra_no_cruzamento():
    # Força alinhada (verde) cruzando de <60 p/ >=60, rompendo a VWAP p/ cima → COMPRA.
    serie = [{"forca": 55, "estado": "verde", "micro": 5, "macro": 4, "leque": 12},
             {"forca": 66, "estado": "verde", "micro": 16, "macro": 12, "leque": 34}]
    snap = _base_linhas(close=101.0, forca=serie[-1], forca_serie=serie)
    d = e.avaliar_sentinela_forca(snap, forca_min=60, **CFG_LINHAS)
    assert d["resultado"] == "entrou" and d["direcao"] == "compra", d
    assert d["variante"] == "E_SENTINELA" and d["estrategia"] == "sentinela_forca_v1", d
    # sem cruzamento (já estava acima) → não entra
    serie2 = [{"forca": 66, "estado": "verde", "leque": 34}, {"forca": 68, "estado": "verde", "leque": 35}]
    d2 = e.avaliar_sentinela_forca(_base_linhas(close=101.0, forca=serie2[-1], forca_serie=serie2),
                                   forca_min=60, **CFG_LINHAS)
    assert d2["resultado"] == "nao_entrou", d2


def test_sentinela_divergencia_fade_no_extremo():
    # Divergência micro(+)×macro(−) no topo da banda → segue a maré macro = VENDA.
    f = {"micro": 15, "macro": -15, "forca": 50, "estado": "amarelo", "divergencia": True}
    snap = _base_linhas(close=106.0, forca=f)   # close >= sup1 (105)
    d = e.avaliar_sentinela_divergencia(snap, **CFG_LINHAS)
    assert d["resultado"] == "entrou" and d["direcao"] == "venda", d
    assert d["variante"] == "E_SENTINELA", d
    # sem divergência → não entra
    d2 = e.avaliar_sentinela_divergencia(_base_linhas(close=106.0,
            forca={"micro": 10, "macro": 8, "estado": "verde", "divergencia": False}), **CFG_LINHAS)
    assert d2["resultado"] == "nao_entrou", d2


def test_sentinela_leque_compressao_expansao():
    # Leque comprimido (10/12/14 <= 15) e agora expande (35 >= 30 e > 14), força verde, rompe VWAP → COMPRA.
    serie = [{"leque": 10}, {"leque": 12}, {"leque": 14}, {"leque": 35}]
    snap = _base_linhas(close=101.0, forca={"estado": "verde"}, forca_serie=serie)
    d = e.avaliar_sentinela_leque(snap, estreito=15, largo=30, **CFG_LINHAS)
    assert d["resultado"] == "entrou" and d["direcao"] == "compra", d
    # sem expansão (leque segue estreito) → não entra
    serie2 = [{"leque": 10}, {"leque": 12}, {"leque": 14}, {"leque": 16}]
    d2 = e.avaliar_sentinela_leque(_base_linhas(close=101.0, forca={"estado": "verde"}, forca_serie=serie2),
                                   estreito=15, largo=30, **CFG_LINHAS)
    assert d2["resultado"] == "nao_entrou", d2


# --------------------------------------------------------------------------- #
# FAMÍLIA F_BREAKOUT — rompimento da faixa de abertura de Londres
# --------------------------------------------------------------------------- #
def test_breakout_entra_na_direcao_do_rompimento():
    # OR rompida p/ cima → COMPRA, carimba sl_pips (a OR oposta), variante F_BREAKOUT.
    snap = {"regime": "tendencia_alta",
            "or_londres": {"entrar": True, "direcao": "compra", "sl_pips": 12.0,
                           "or_high": 1.1000, "or_low": 1.0988},
            "spread_pips": 1.0}
    d = e.avaliar_breakout_londres(snap, estrategia=e.ESTRATEGIA_BREAKOUT, spread_max_pips=2.0)
    assert d["resultado"] == "entrou" and d["direcao"] == "compra", d
    assert d["variante"] == "F_BREAKOUT" and d["sl_pips"] == 12.0, d


def test_breakout_nao_entra_sem_rompimento_ou_spread_alto():
    # Sem rompimento (entrar=False) → não entra.
    d = e.avaliar_breakout_londres({"or_londres": {"entrar": False}},
                                   estrategia=e.ESTRATEGIA_BREAKOUT, spread_max_pips=2.0)
    assert d["resultado"] == "nao_entrou", d
    # Rompeu, mas spread acima do teto → não entra (edge morre no custo).
    snap = {"or_londres": {"entrar": True, "direcao": "venda", "sl_pips": 10.0}, "spread_pips": 3.5}
    d2 = e.avaliar_breakout_londres(snap, estrategia=e.ESTRATEGIA_BREAKOUT, spread_max_pips=2.0)
    assert d2["resultado"] == "nao_entrou" and d2["motivo"] == "spread alto", d2


def test_gerir_breakout_fecha_no_fim_da_janela():
    # No fim da janela de Londres (hora >= fim_hora) fecha, qualquer estratégia.
    d = e.gerir_breakout(e.ESTRATEGIA_BREAKOUT, "compra", 1.1050, 1.1000, 1.0988,
                         hora=17, fim_hora=17, mfe_pips=30, trig_pips=10, lock_pips=2,
                         pip=0.0001, prot_estrategia=e.ESTRATEGIA_BREAKOUT_PROT)
    assert d["fechar"] and d["motivo"] == "fim da janela de Londres", d


def test_gerir_breakout_protecao_trava_lucro_so_no_prot():
    # Variante de PROTEÇÃO: MFE >= 10p → sobe o stop p/ entrada + 2p (só aperta a favor).
    d = e.gerir_breakout(e.ESTRATEGIA_BREAKOUT_PROT, "compra", 1.1015, 1.1000, 1.0988,
                         hora=12, fim_hora=17, mfe_pips=12, trig_pips=10, lock_pips=2,
                         pip=0.0001, prot_estrategia=e.ESTRATEGIA_BREAKOUT_PROT)
    assert not d["fechar"] and abs(d["novo_sl"] - 1.1002) < 1e-9, d
    # Mesma situação, mas SEM proteção (livro _v1) → deixa correr, stop intocado.
    d2 = e.gerir_breakout(e.ESTRATEGIA_BREAKOUT, "compra", 1.1015, 1.1000, 1.0988,
                          hora=12, fim_hora=17, mfe_pips=12, trig_pips=10, lock_pips=2,
                          pip=0.0001, prot_estrategia=e.ESTRATEGIA_BREAKOUT_PROT)
    assert not d2["fechar"] and d2["novo_sl"] == 1.0988, d2
    # Proteção ainda NÃO acionada (MFE < trigger) → stop intocado.
    d3 = e.gerir_breakout(e.ESTRATEGIA_BREAKOUT_PROT, "compra", 1.1005, 1.1000, 1.0988,
                          hora=12, fim_hora=17, mfe_pips=5, trig_pips=10, lock_pips=2,
                          pip=0.0001, prot_estrategia=e.ESTRATEGIA_BREAKOUT_PROT)
    assert not d3["fechar"] and d3["novo_sl"] == 1.0988, d3


def main() -> int:
    testes = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in testes:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(testes)} testes passaram ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
