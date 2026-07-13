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


def main() -> int:
    testes = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in testes:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(testes)} testes passaram ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
