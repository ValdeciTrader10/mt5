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


def main() -> int:
    testes = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in testes:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(testes)} testes passaram ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
