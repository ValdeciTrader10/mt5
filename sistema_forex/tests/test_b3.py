"""Testes do módulo B3 (ETAPA 8 — fundação de dados) — sem pytest, sem MT5/rede.

Cobre o que a fundação adiciona de forma PURA/determinística:
  - config_b3.candidatos_simbolo: par + aliases, sem duplicar, na ordem;
  - coletor_b3._alvo_barras: teto do M1 e piso dos TFs;
  - coletor_b3._tf_gatilho: escolhe o TF mais fino coletado;
  - persistência de candles B3 na tabela `candles` (reuso de gravar_candles/contar),
    provando que WIN/WDO coexistem com o forex sem colidir.

    python -m sistema_forex.tests.test_b3
"""

import os
import tempfile

from .. import calibracao_b3, config_b3, coletor_b3, db
from ..coletor_mt5 import contar, gravar_candles


def _tmp_db():
    fd, caminho = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(caminho)
    return caminho


def test_candidatos_par_mais_aliases_sem_duplicar():
    cands = config_b3.candidatos_simbolo("WIN$N")
    assert cands[0] == "WIN$N", cands            # o próprio par vem primeiro
    assert "WINFUT" in cands and "WIN$" in cands, cands
    assert len(cands) == len(set(cands)), cands  # sem duplicatas


def test_candidatos_par_desconhecido_so_ele_mesmo():
    assert config_b3.candidatos_simbolo("XPTO") == ["XPTO"]


def test_candidatos_aliases_customizados():
    cands = config_b3.candidatos_simbolo("FOO", aliases={"FOO": ["BAR", "BAR", "FOO"]})
    assert cands == ["FOO", "BAR"], cands  # dedup preserva ordem e ignora repetidos


def test_pares_ativos_respeita_flag():
    """pares_ativos() = PARES_B3 quando habilitado; vazio quando desligado (isola o forex)."""
    orig = config_b3.B3_HABILITADO
    try:
        config_b3.B3_HABILITADO = True
        assert config_b3.pares_ativos() == list(config_b3.PARES_B3)
        config_b3.B3_HABILITADO = False
        assert config_b3.pares_ativos() == []
    finally:
        config_b3.B3_HABILITADO = orig


def test_alvo_barras_teto_m1():
    """M1 é limitado pelo teto; TFs maiores respeitam o piso mínimo."""
    assert coletor_b3._alvo_barras("M1") == config_b3.BACKFILL_M1_BARRAS_B3
    assert coletor_b3._alvo_barras("D1") >= config_b3.BACKFILL_MIN_BARRAS_B3


def test_tf_gatilho_mais_fino():
    """O gatilho do loop é o TF de menor granularidade coletado (M1 quando presente)."""
    assert coletor_b3._tf_gatilho() == "M1"


def test_candles_b3_coexistem_com_forex():
    """WIN/WDO gravam na mesma tabela sem colidir com um par forex de mesmo TF/time."""
    caminho = _tmp_db()
    try:
        with db.sessao(caminho) as conn:
            forex = [{"time": 1_000_000, "open": 1.1, "high": 1.2, "low": 1.0,
                      "close": 1.15, "tick_volume": 10, "spread": 8}]
            win = [{"time": 1_000_000, "open": 130000, "high": 130500, "low": 129800,
                    "close": 130200, "tick_volume": 500, "spread": 5}]
            assert gravar_candles(conn, "EURUSD#", "M5", forex) == 1
            assert gravar_candles(conn, "WIN$N", "M5", win) == 1     # mesmo tf/time, par diferente
            conn.commit()
            assert contar(conn, "WIN$N", "M5") == 1
            assert contar(conn, "EURUSD#", "M5") == 1
            # reprocessar não duplica (idempotente)
            assert gravar_candles(conn, "WIN$N", "M5", win) == 0
            conn.commit()
            assert contar(conn, "WIN$N", "M5") == 1
    finally:
        os.remove(caminho)


# --------------------------------------------------------------------------- #
# Calibração de escala (Etapa 8b) — funções PURAS
# --------------------------------------------------------------------------- #
def test_percentil_interpola():
    xs = [10, 20, 30, 40, 50]
    assert calibracao_b3.percentil(xs, 0) == 10
    assert calibracao_b3.percentil(xs, 100) == 50
    assert calibracao_b3.percentil(xs, 50) == 30
    assert calibracao_b3.percentil([], 50) is None


def test_passo_preco_win_5_pontos():
    """WIN move de 5 em 5 pontos → tick derivado = 5 (GCD dos deltas da grade)."""
    precos = [130000, 130005, 130010, 130200, 129800, 130005]
    assert calibracao_b3.passo_preco(precos) == 5.0


def test_passo_preco_wdo_meio_ponto():
    """WDO move de 0,5 em 0,5 → tick = 0,5 (robusto a ruído de float)."""
    precos = [5400.0, 5400.5, 5401.0, 5399.5, 5400.5]
    assert calibracao_b3.passo_preco(precos) == 0.5


def test_passo_preco_poucos_precos_none():
    assert calibracao_b3.passo_preco([130000, 130000]) is None


def test_estatisticas_tf_basico():
    candles = {
        "open":  [130000, 130050, 130100, 130020, 130080],
        "high":  [130100, 130150, 130200, 130120, 130180],
        "low":   [129900, 129950, 130000, 129920, 129980],
        "close": [130050, 130100, 130020, 130080, 130120],
    }
    est = calibracao_b3.estatisticas_tf(candles, [5, 5, 6, 5, 7], periodo_atr=3)
    assert est["n"] == 5
    assert est["tick"] == 10.0           # grade de 10 em 10 nos preços de exemplo
    assert est["range_med"] == 200.0     # todas as velas têm range 200
    assert est["spread_p90"] is not None


def test_sugerir_params_regra_do_ouro():
    """O piso do SL nunca fica abaixo de uma vela p90 (senão insta-estopa) e o teto é largo."""
    por_tf = {"M5": {"n": 300, "tick": 5.0, "range_med": 150.0, "range_p90": 300.0,
                     "range_max": 500.0, "atr_atual": 200.0, "atr_med": 200.0,
                     "atr_p90": 350.0, "spread_med": 5, "spread_p90": 8, "spread_max": 20}}
    s = calibracao_b3.sugerir_params(por_tf, tf_base="M5", mult_sl=3.0)
    assert s["suficiente"]
    assert s["tamanho_pip"] == 5.0
    # piso >= max(atr_med, range_p90) = 300; SL típico = 3*200 = 600
    assert s["sl_min_pts"] >= 300.0
    assert s["sl_alvo_pts"] == 600.0
    # teto largo o bastante para o ATR×3 do p90 caber: 3*350*1.3 = 1365
    assert s["sl_max_pts"] >= 1300.0
    assert s["sl_max_pts"] > s["sl_min_pts"]
    # em "pips" (=tick): 300/5 = 60 no piso
    assert s["sl_min_pips"] == 60
    assert s["spread_max_pontos"] == 8


def test_sugerir_params_sem_dados():
    assert calibracao_b3.sugerir_params({}, tf_base="M5")["suficiente"] is False


def test_calibrar_par_do_banco():
    """Calibra WIN a partir de candles reais gravados — integra leitura do banco + puras."""
    caminho = _tmp_db()
    try:
        with db.sessao(caminho) as conn:
            velas = []
            base = 130000
            for i in range(60):
                o = base + (i % 5) * 5
                velas.append({"time": 1_000_000 + i * 300, "open": o, "high": o + 150,
                              "low": o - 150, "close": o + 25, "tick_volume": 100, "spread": 5})
            gravar_candles(conn, "WIN$N", "M5", velas)
            conn.commit()
            res = calibracao_b3.calibrar_par(conn, "WIN$N", tfs=["M5"], tf_base="M5",
                                             janela=1000, periodo_atr=14)
        assert res["par"] == "WIN$N"
        assert "M5" in res["por_tf"]
        assert res["por_tf"]["M5"]["tick"] == 5.0
        assert res["sugestao"]["suficiente"]
        assert res["valor_ponto_contrato"] == 0.20   # default de contrato do WIN
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
