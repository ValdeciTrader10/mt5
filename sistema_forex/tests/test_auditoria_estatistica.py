"""ETAPA 9 — testes do gate de aprovação estatística (sombra → demo). Sem pytest/MT5/rede.

Cobre o que decide se uma célula (variante × estratégia × TF × par) vai para o demo:
  - os 4 critérios (amostra, expectância, profit factor, split-half) — cada um reprova sozinho;
  - o split-half como guardião anti-sorte (metade negativa reprova mesmo com o total positivo);
  - a nota de MÚLTIPLOS TESTES (falsos esperados ≈ testadas × prob_acaso);
  - a config sugerida (só Variante A é promovível hoje; B/C listadas à parte).

    python -m sistema_forex.tests.test_auditoria_estatistica
"""

import os
import tempfile

from .. import auditoria_estatistica as ae
from .. import config, db


# --------------------------------------------------------------------------- #
# Trades sintéticos (dicts prontos p/ as funções PURAS — sem tocar no banco)
# --------------------------------------------------------------------------- #
def _t(r, t):
    """Um trade com R e USD conhecidos (USD = R×10, risco uniforme → PF_usd == PF_r) e um
    timestamp de fechamento `t` (para o split-half ordenar/dividir)."""
    return {"r": float(r), "lucro_usd": round(r * 10.0, 2),
            "mae_r": None, "mfe_r": None, "fechamento_utc": int(t)}


def _serie(rs, base=1_000):
    """Lista de trades a partir de uma sequência de R, com fechamentos crescentes."""
    return [_t(r, base + i) for i, r in enumerate(rs)]


# --------------------------------------------------------------------------- #
# avaliar_celula — cada critério reprova sozinho
# --------------------------------------------------------------------------- #
def test_celula_aprovada_completa():
    """60 trades, expectância e PF folgados, positivo nas duas metades → APROVADA (alta)."""
    # padrão que repete: 3 ganhos de +1R e 2 perdas de -0.5R → exp +0.4R, PF = 3/1 = 3.0
    rs = [1, 1, 1, -0.5, -0.5] * 12          # 60 trades
    v = ae.avaliar_celula(_serie(rs), min_sinais=50, pf_min=1.3, exige_split=True)
    assert v["n"] == 60, v["n"]
    assert v["exp_r"] > 0 and v["profit_factor"] >= 1.3, v
    assert v["criterios"] == {"amostra": True, "expectancia": True,
                              "profit_factor": True, "split_half": True}, v["criterios"]
    assert v["aprovada"] is True
    assert v["confianca"] == "média", v["confianca"]   # aprovada, mas n=60 < 2×50 → 'média'


def test_confianca_alta_exige_amostra_robusta():
    """'alta' só com N ≥ 2×mín; entre mín e 2×mín a aprovada é 'média'."""
    rs = [1, 1, 1, -0.5, -0.5] * 12          # 60 trades → aprovada, mas 60 < 2×50
    v = ae.avaliar_celula(_serie(rs), min_sinais=25, pf_min=1.3, exige_split=True)
    assert v["aprovada"] and v["n"] == 60
    assert v["confianca"] == "alta", v["confianca"]   # 60 ≥ 2×25=50 → alta
    v2 = ae.avaliar_celula(_serie(rs), min_sinais=50, pf_min=1.3, exige_split=True)
    assert v2["aprovada"] and v2["confianca"] == "média", v2["confianca"]  # 60 < 2×50


def test_reprova_amostra():
    """Boa expectância mas amostra pequena → reprova por amostra."""
    v = ae.avaliar_celula(_serie([1, 1, -0.5] * 6), min_sinais=50, pf_min=1.3, exige_split=True)
    assert v["n"] == 18 and v["aprovada"] is False
    assert v["criterios"]["amostra"] is False
    assert any("amostra" in m for m in v["motivos_reprova"]), v["motivos_reprova"]


def test_reprova_profit_factor_mesmo_com_expectancia_positiva():
    """exp R > 0 mas PF entre 1,0 e 1,3 → reprova SÓ por profit factor (o gate mais fino)."""
    # 55 de +1R e 45 de -1R: exp +0.10R (>0), PF = 55/45 ≈ 1.22 (<1.3)
    rs = [1] * 55 + [-1] * 45
    # intercala p/ o split-half não desqualificar por outra razão (metades equilibradas)
    misturado = []
    for i in range(45):
        misturado += [1, -1]
    misturado += [1] * 10                    # sobra dos ganhos → total 55/45
    v = ae.avaliar_celula(_serie(misturado), min_sinais=50, pf_min=1.3, exige_split=True)
    assert v["exp_r"] > 0, v["exp_r"]
    assert v["profit_factor"] is not None and v["profit_factor"] < 1.3, v["profit_factor"]
    assert v["criterios"]["expectancia"] is True and v["criterios"]["profit_factor"] is False
    assert v["aprovada"] is False


def test_reprova_split_half_metade_negativa():
    """Total positivo e PF ok, MAS a 1ª metade é negativa → o guardião anti-sorte reprova."""
    # 1ª metade perdedora (-0.2R média), 2ª metade muito ganhadora → total > 0 e PF ≥ 1.3
    h1 = [1, -1, -1, 1, -1, -1] * 5          # 30 trades, soma -2R/30 < 0
    h2 = [2, 2, 2, -0.5] * 8                 # 32 trades, fortemente positivo
    v = ae.avaliar_celula(_serie(h1 + h2), min_sinais=50, pf_min=1.3, exige_split=True)
    assert v["exp_r"] > 0 and v["profit_factor"] >= 1.3, v
    assert v["split"]["exp_r1"] is not None and v["split"]["exp_r1"] <= 0, v["split"]
    assert v["criterios"]["split_half"] is False and v["aprovada"] is False
    assert any("split" in m for m in v["motivos_reprova"]), v["motivos_reprova"]
    # sem exigir split-half, a MESMA célula passaria (mostra que é o split que barra)
    v2 = ae.avaliar_celula(_serie(h1 + h2), min_sinais=50, pf_min=1.3, exige_split=False)
    assert v2["aprovada"] is True


def test_pf_infinito_sem_perdas_passa():
    """Célula só de ganhos: PF é None ('infinito') → passa o critério de PF."""
    v = ae.avaliar_celula(_serie([1] * 60), min_sinais=50, pf_min=1.3, exige_split=True)
    assert v["profit_factor"] is None and v["criterios"]["profit_factor"] is True
    assert v["aprovada"] is True


# --------------------------------------------------------------------------- #
# auditar — integração com o banco (múltiplos testes + config sugerida)
# --------------------------------------------------------------------------- #
def _tmp_db():
    fd, caminho = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(caminho)
    return caminho


def _inserir_trades(conn, variante, estrategia, par, tf, rs, base_t=2_000_000):
    """Grava trades FECHADOS de sombra (simulado=1) com R conhecido: compra com risco 0.0010 e
    preço de saída = entrada + r×risco → _res_r devolve exatamente r."""
    risco = 0.0010
    entrada = 1.1000
    for i, r in enumerate(rs):
        saida = entrada + r * risco
        conn.execute(
            "INSERT INTO trades (par, tf, estrategia, direcao, preco_entrada, preco_saida, pips, "
            "lucro_usd, risco_inicial, mae_r, mfe_r, variante, abertura_utc, fechamento_utc, "
            "simulado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (par, tf, estrategia, "compra", entrada, round(saida, 5), r * 10.0, r * 10.0,
             risco, None, None, variante, base_t + i, base_t + i + 1),
        )
    conn.commit()


def test_auditar_conta_testadas_e_aprova_A():
    """Duas células: uma A vencedora com amostra (aprova) e uma pequena (não testável). A nota de
    múltiplos testes conta só as com amostra; a config sugerida promove só a Variante A."""
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        # Célula A boa: 60 trades, exp +0.4R, PF 3.0, estável nas duas metades.
        _inserir_trades(conn, "A_ORIGINAL", "confluencia_v1", "EURUSD#", "M5",
                        [1, 1, 1, -0.5, -0.5] * 12, base_t=2_000_000)
        # Célula C pequena (18 trades) — não entra em 'testadas' nem aprova.
        _inserir_trades(conn, "C_HIBRIDA", "confluencia_v1", "EURUSD#", "M5",
                        [1, 1, -0.5] * 6, base_t=3_000_000)

        d = ae.auditar(conn, min_sinais=50, pf_min=1.3, exige_split=True)
        assert d["n_celulas"] == 2, d["n_celulas"]
        assert d["n_testadas"] == 1, d["n_testadas"]        # só a A tem N≥50
        assert d["n_aprovadas"] == 1, d["n_aprovadas"]
        aprovada = d["aprovadas"][0]
        assert aprovada["variante"] == "A_ORIGINAL" and aprovada["tf"] == "M5"
        # múltiplos testes: 1 testada × 0.05 ≈ 0.1 esperado; 1 aprovada supera → sem alerta
        assert d["falsos_esperados"] == round(1 * config.APROVACAO_PROB_ACASO, 1), d["falsos_esperados"]
        assert d["multiple_testing_alerta"] is False
        # config sugerida: promove a A; sem aprovadas em B/C
        cs = d["config_sugerida"]
        assert cs["EXEC_REAL_ESTRATEGIAS"] == "confluencia_v1"
        assert cs["EXEC_REAL_TFS"] == "M5"
        assert cs["aprovadas_outras_variantes"] == []
        conn.close()
    finally:
        os.remove(caminho)


def test_auditar_sem_aprovadas_nao_promove():
    """Sem nenhuma célula passando, a config sugerida fica vazia e nada é promovido."""
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        _inserir_trades(conn, "A_ORIGINAL", "sweep_choch_v1", "GBPUSD#", "M15",
                        [-1, -1, 1, -1] * 15, base_t=4_000_000)   # perdedora
        d = ae.auditar(conn, min_sinais=50, pf_min=1.3, exige_split=True)
        assert d["n_aprovadas"] == 0
        assert d["config_sugerida"]["promovivel_agora"] == []
        assert d["config_sugerida"]["EXEC_REAL_ESTRATEGIAS"] == ""
        # texto não quebra e orienta a esperar mais amostra
        txt = ae.auditoria_texto(d)
        assert "Nenhuma célula aprovada" in txt
        conn.close()
    finally:
        os.remove(caminho)


def main() -> int:
    testes = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in testes:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(testes)} testes da auditoria estatística passaram ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
