"""Testes da gestão de risco/saída (Fase 5) — sem pytest.

    python -m sistema_forex.tests.test_gestao
"""

from .. import config as cfg
from .. import gestao as g

PIP = 0.0001
SAIDA = dict(be_trigger_r=1.0, giveback_r=0.7, tempo_max_h=8)


def test_param_simbolo_override_e_default():
    # OURO tem override; forex cai no default global.
    assert cfg.param_simbolo("GOLD", "sl_max_pips", cfg.SL_MAX_PIPS) == 800
    assert cfg.param_simbolo("GOLD", "spread_max_pips", cfg.SPREAD_MAX_PIPS) == 6.0
    assert cfg.param_simbolo("EURUSD#", "sl_max_pips", cfg.SL_MAX_PIPS) == cfg.SL_MAX_PIPS
    # GBPJPY: só sobrescreve o spread (e o sl_max); o sl_min cai no default global.
    assert cfg.param_simbolo("GBPJPY#", "spread_max_pips", cfg.SPREAD_MAX_PIPS) == 4.5
    assert cfg.param_simbolo("GBPJPY#", "sl_min_pips", cfg.SL_MIN_PIPS) == cfg.SL_MIN_PIPS
    # chave inexistente também cai no default
    assert cfg.param_simbolo("GOLD", "inexistente", 123) == 123


def test_sl_ouro_muito_mais_largo_que_forex():
    # Mesmo ATR: no ouro (pip 0.01, min 100) o stop mínimo é ~$1; no forex (pip 0.0001,
    # min 12) é ~0.0012. Prova que o override evita o insta-stop do ouro.
    sl_ouro = g.calcular_sl("compra", 2600.0, atr=0.0, mult=3,
                            min_pips=800, max_pips=800, pip=0.01)   # clampa no min=max
    assert abs((2600.0 - sl_ouro) - 8.0) < 1e-9, sl_ouro          # 800 * 0.01 = $8


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


def test_choch_contrario_sai_com_lucro_desenvolvido():
    # CHOCH (reversão) contra a posição, com r ≥ estrut_min_r → fecha, mesmo com espaço.
    acao, motivo = g.avaliar_saida(direcao="compra", r=1.2, r_max=1.3, idade_h=1,
                                   ultimo_evento={"evento": "CHOCH", "direcao": "baixa"},
                                   be_movido=True, estrut_min_r=1.0, **SAIDA)
    assert acao == "fechar" and "CHOCH" in motivo


def test_nao_sai_no_ruido_com_lucro_de_centavos():
    # O bug relatado: BOS contrário com r minúsculo (centavos) NÃO pode fechar.
    acao, _ = g.avaliar_saida(direcao="venda", r=0.03, r_max=0.05, idade_h=0.2,
                              ultimo_evento={"evento": "BOS", "direcao": "alta"},
                              be_movido=False, espaco_r=0.2, estrut_min_r=1.0,
                              espaco_segurar_r=1.0, **SAIDA)
    assert acao == "manter"


def test_bos_contrario_nunca_fecha():
    # "Ativou, deixa o preço andar": um BOS de continuação contra NÃO fecha, mesmo no lucro
    # e mesmo perto de um nível — só a reversão (CHOCH) fecha. Protege quem deixa correr.
    acao, _ = g.avaliar_saida(direcao="compra", r=1.2, r_max=1.3, idade_h=1,
                              ultimo_evento={"evento": "BOS", "direcao": "baixa"},
                              be_movido=True, estrut_min_r=1.0, **SAIDA)
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


def test_exposicao_e_correlacao():
    # EURUSD e GBPUSD comprados → ambos short USD (net -2)
    exp = g.exposicao_moedas([{"par": "EURUSD#", "direcao": "compra"},
                              {"par": "GBPUSD#", "direcao": "compra"}])
    assert exp["USD"] == -2 and exp["EUR"] == 1 and exp["GBP"] == 1
    # com limite 1, abrir a 2ª compra (mesmo short-USD) VIOLA
    assert g.viola_correlacao([{"par": "EURUSD#", "direcao": "compra"}], "GBPUSD#", "compra", 1) is True
    # USDCAD comprado + EURUSD comprado se cancelam no USD (net 0) → NÃO viola
    assert g.viola_correlacao([{"par": "EURUSD#", "direcao": "compra"}], "USDCAD", "compra", 1) is False
    # primeira posição nunca viola (limite 1)
    assert g.viola_correlacao([], "EURUSD#", "compra", 1) is False


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
