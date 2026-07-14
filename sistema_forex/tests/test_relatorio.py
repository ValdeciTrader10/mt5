"""Testes do relatório sombra multi-variante (ETAPA 7) — sem pytest.

Cobre as agregações puras (expectância R por célula, por variante, split-half) e as leituras
que precisam do banco (A vs C = efeito do filtro fuzzy; distribuição de bloqueio).

    python -m sistema_forex.tests.test_relatorio
"""

import os
import tempfile

from .. import db, relatorio as rel


def _tmp_db():
    fd, caminho = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(caminho)
    return caminho


def _trade(var, est, tf, par, usd, r, fech, mae=-0.5, mfe=0.8):
    """Trade dict já no formato que as funções puras consomem (com R calculado)."""
    return {"variante": var, "estrategia": est, "tf": tf, "par": par, "lucro_usd": usd, "r": r,
            "mae_r": mae, "mfe_r": mfe, "fechamento_utc": fech}


# --------------------------------------------------------------------------- #
# Agregações puras
# --------------------------------------------------------------------------- #
def test_agg_expectancia_r_e_pf():
    trades = [_trade("A_ORIGINAL", "confluencia_v1", "M5", "EURUSD#", 10, 1.0, 100),
              _trade("A_ORIGINAL", "confluencia_v1", "M5", "EURUSD#", -5, -1.0, 200),
              _trade("A_ORIGINAL", "confluencia_v1", "M5", "EURUSD#", 20, 2.0, 300)]
    a = rel._agg(trades)
    assert a["n"] == 3 and a["winrate"] == round(200 / 3, 1), a
    assert a["exp_r"] == round((1.0 - 1.0 + 2.0) / 3, 3), a          # expectância em R
    assert a["profit_factor"] == round(30 / 5, 2), a                # 30 ganho / 5 perda
    assert a["usd"] == 25, a


def test_ranking_celulas_ordena_e_marca_amostra():
    trades = ([_trade("A_ORIGINAL", "confluencia_v1", "M5", "EURUSD#", 5, 0.5, i) for i in range(3)]
              + [_trade("A_ORIGINAL", "sweep_choch_v1", "M5", "EURUSD#", 30, 3.0, 500)])
    linhas = rel.ranking_celulas(trades, min_sinais=3)
    # a célula com N>=3 é "suficiente" e vem antes da de N=1 (mesmo com exp R menor)
    assert linhas[0]["suficiente"] is True and linhas[0]["n"] == 3, linhas[0]
    assert linhas[1]["suficiente"] is False and linhas[1]["n"] == 1, linhas[1]


def test_por_variante_separa_livros():
    trades = [_trade("A_ORIGINAL", "confluencia_v1", "M5", "EURUSD#", 10, 1.0, 100),
              _trade("C_HIBRIDA", "confluencia_v1", "M5", "EURUSD#", -5, -1.0, 200)]
    linhas = rel.por_variante(trades)
    assert [l["variante"] for l in linhas] == ["A_ORIGINAL", "C_HIBRIDA"], linhas
    assert linhas[0]["usd"] == 10 and linhas[1]["usd"] == -5, linhas


def test_split_half_detecta_estavel():
    # Mesma célula positiva nas duas metades (>= meia amostra) → estável.
    trades = [_trade("A_ORIGINAL", "confluencia_v1", "M5", "EURUSD#", 5, 0.5, t)
              for t in range(1, 13)]
    linhas = rel.split_half(trades, min_sinais=6)   # meia = 3
    alvo = next(x for x in linhas if x["estrategia_nome"] and x["tf"] == "M5")
    assert alvo["estavel"] is True and alvo["n1"] >= 3 and alvo["n2"] >= 3, alvo


def test_heatmap_por_variante():
    trades = [_trade("A_ORIGINAL", "confluencia_v1", "M5", "EURUSD#", 10, 1.0, 100),
              _trade("A_ORIGINAL", "confluencia_v1", "M15", "EURUSD#", -5, -0.5, 200)]
    hm = rel.heatmap_estrategia_tf(trades)
    assert "A_ORIGINAL" in hm and len(hm["A_ORIGINAL"]) == 2, hm
    # ordenado por exp R desc → o M5 (exp +1.0) vem antes do M15 (exp -0.5)
    assert hm["A_ORIGINAL"][0]["tf"] == "M5", hm["A_ORIGINAL"]


# --------------------------------------------------------------------------- #
# A vs C — leitura do banco (decisões A/C casadas + desfecho do trade A)
# --------------------------------------------------------------------------- #
def _decisao(conn, par, tf, est, t_utc, resultado, variante):
    cur = conn.execute(
        "INSERT INTO decisoes (par, time_utc, tf, estrategia, direcao, resultado, motivo, variante) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (par, t_utc, tf, est, "compra", resultado, "m", variante))
    return cur.lastrowid


def _trade_db(conn, par, tf, est, usd, fech, decisao_id, variante="A_ORIGINAL"):
    conn.execute(
        "INSERT INTO trades (par, tf, estrategia, direcao, lucro_usd, fechamento_utc, decisao_id, "
        "simulado, variante) VALUES (?,?,?,?,?,?,?,1,?)",
        (par, tf, est, "compra", usd, fech, decisao_id, variante))


def test_a_vs_c_conta_evitados_e_perdidos():
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        par, tf, est = "EURUSD#", "M5", "confluencia_v1"
        # Setup 1 @1000: A entrou e PERDEU; C bloqueou → perda EVITADA (bom veto).
        da1 = _decisao(conn, par, tf, est, 1000, "entrou", "A_ORIGINAL")
        _decisao(conn, par, tf, est, 1000, "nao_entrou", "C_HIBRIDA")
        _trade_db(conn, par, tf, est, -8.0, 1000, da1)
        # Setup 2 @2000: A entrou e GANHOU; C bloqueou → ganho PERDIDO (veto ruim).
        da2 = _decisao(conn, par, tf, est, 2000, "entrou", "A_ORIGINAL")
        _decisao(conn, par, tf, est, 2000, "nao_entrou", "C_HIBRIDA")
        _trade_db(conn, par, tf, est, 12.0, 2000, da2)
        # Setup 3 @3000: A entrou, C MANTEVE (entrou).
        da3 = _decisao(conn, par, tf, est, 3000, "entrou", "A_ORIGINAL")
        _decisao(conn, par, tf, est, 3000, "entrou", "C_HIBRIDA")
        _trade_db(conn, par, tf, est, 5.0, 3000, da3)
        conn.commit()

        ac = rel.a_vs_c(conn, None, None)
        assert ac["c_manteve"] == 1 and ac["c_bloqueou"] == 2, ac
        assert ac["ruins_evitados"] == 1 and ac["bons_perdidos"] == 1, ac
        assert ac["usd_evitado"] == 8.0 and ac["usd_perdido"] == 12.0, ac
        assert ac["beneficio_liquido"] == -4.0, ac      # evitou 8, perdeu 12 → -4 (filtro atrapalhou aqui)
        conn.close()
    finally:
        os.remove(caminho)


def test_distribuicao_bloqueio_agrupa_motivos():
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        par, tf, est = "EURUSD#", "M5", "confluencia_v1"
        for t in (10, 20):
            conn.execute(
                "INSERT INTO decisoes (par, time_utc, tf, estrategia, direcao, resultado, motivo, variante) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (par, t, tf, est, "compra", "nao_entrou", "fuzzy veto: M15 contra a direção", "C_HIBRIDA"))
        conn.execute(
            "INSERT INTO decisoes (par, time_utc, tf, estrategia, direcao, resultado, motivo, variante) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (par, 30, tf, est, "compra", "nao_entrou", "fuzzy veto: exaustão (clímax)", "C_HIBRIDA"))
        conn.commit()
        dist = rel.distribuicao_bloqueio(conn, None, None)
        assert dist[0]["n"] == 2, dist          # o motivo mais frequente primeiro
        assert sum(x["n"] for x in dist) == 3, dist
        conn.close()
    finally:
        os.remove(caminho)


def test_montar_relatorio_integra():
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        _trade_db(conn, "EURUSD#", "M5", "confluencia_v1", 10.0, 1000, None, "A_ORIGINAL")
        conn.commit()
        d = rel.montar_relatorio(conn, "", "")
        assert d["geral"]["n"] == 1, d["geral"]
        assert "por_variante" in d and "ranking" in d and "a_vs_c" in d, d.keys()
        txt = rel.relatorio_texto(d)
        assert "RELATÓRIO SOMBRA MULTI-VARIANTE" in txt, txt[:80]
        assert rel.resumo_curto(d).startswith("📊"), rel.resumo_curto(d)[:20]
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
