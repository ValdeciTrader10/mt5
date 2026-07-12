"""Testes da gestão de risco/saída (Fase 5) — sem pytest.

    python -m sistema_forex.tests.test_gestao
"""

from .. import gestao as g

PIP = 0.0001
SAIDA = dict(be_trigger_r=1.0, giveback_r=0.7, tempo_max_h=8)


def test_sl_respeita_min_e_max():
    # ATR grande → distância limitada ao máximo (40 pips)
    sl = g.calcular_sl("compra", 1.1000, 0.0100, mult=3.0, min_pips=12, max_pips=40, pip=PIP)
    assert abs((1.1000 - sl) - 40 * PIP) < 1e-9, sl
    # ATR minúsculo → distância no mínimo (12 pips), e do lado certo p/ venda
    sl_v = g.calcular_sl("venda", 1.1000, 0.00001, mult=3.0, min_pips=12, max_pips=40, pip=PIP)
    assert abs((sl_v - 1.1000) - 12 * PIP) < 1e-9, sl_v


def test_r_por_risco():
    # risco 20p (0.0020), ganho 20p → 1R; e o R usa o risco FIXO, não o stop atual
    r = g.r_por_risco("compra", 1.1000, 1.1020, 0.0020)
    assert abs(r - 1.0) < 1e-9, r
    # venda: entrada 1.1000, atual 1.0990 (ganho 10p), risco 0.0020 → 0.5R
    assert abs(g.r_por_risco("venda", 1.1000, 1.0990, 0.0020) - 0.5) < 1e-9


def test_saida_por_tempo():
    acao, motivo = g.avaliar_saida(direcao="compra", r=0.2, r_max=0.5, idade_h=8.1,
                                   ultimo_evento=None, be_movido=False, **SAIDA)
    assert acao == "fechar" and "tempo" in motivo


def test_saida_por_forca_contraria_estrutura():
    acao, motivo = g.avaliar_saida(direcao="compra", r=0.5, r_max=0.6, idade_h=1,
                                   ultimo_evento={"evento": "CHOCH", "direcao": "baixa"},
                                   be_movido=True, **SAIDA)
    assert acao == "fechar" and "força contrária" in motivo


def test_nao_sai_no_ruido_inicial_mesmo_com_evento_contrario():
    # r <= 0 → não fecha por evento contrário (evita saída no ruído do início)
    acao, _ = g.avaliar_saida(direcao="compra", r=-0.2, r_max=0.0, idade_h=0.1,
                              ultimo_evento={"evento": "BOS", "direcao": "baixa"},
                              be_movido=False, **SAIDA)
    assert acao == "manter"


def test_saida_por_reversao_giveback():
    # pico 2.0R, atual 1.2R → cedeu 0.8R (> 0.7) → fecha
    acao, motivo = g.avaliar_saida(direcao="venda", r=1.2, r_max=2.0, idade_h=2,
                                   ultimo_evento=None, be_movido=True, **SAIDA)
    assert acao == "fechar" and "reversão" in motivo


def test_move_break_even():
    acao, _ = g.avaliar_saida(direcao="compra", r=1.05, r_max=1.05, idade_h=1,
                              ultimo_evento=None, be_movido=False, **SAIDA)
    assert acao == "mover_be"
    # já movido → mantém
    acao2, _ = g.avaliar_saida(direcao="compra", r=1.05, r_max=1.05, idade_h=1,
                               ultimo_evento=None, be_movido=True, **SAIDA)
    assert acao2 == "manter"


def test_drawdown():
    assert g.drawdown_estourou(1000, 949, 5.0) is True     # -5.1%
    assert g.drawdown_estourou(1000, 960, 5.0) is False    # -4%
    assert g.drawdown_estourou(0, 0, 5.0) is False


def main() -> int:
    testes = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in testes:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(testes)} testes passaram ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
