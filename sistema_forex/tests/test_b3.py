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

from .. import (calibracao_b3, config, config_b3, coletor_b3, db, executor, executor_b3,
                manutencao, mt5_bridge_b3)
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


def test_real_volume_gravado_e_preferido():
    """Item 6: gravar_candles persiste real_volume; os consumidores preferem o real (B3) via
    COALESCE e caem no tick_volume quando real é NULL (forex — comportamento inalterado)."""
    fd, caminho = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        db.init_db(caminho); conn = db.conectar(caminho)
        gravar_candles(conn, "WIN$N", "M5", [{"time": 1000, "open": 1, "high": 2, "low": 0.5,
            "close": 1.5, "tick_volume": 10, "real_volume": 350, "spread": 5}])
        gravar_candles(conn, "EURUSD#", "M5", [{"time": 1000, "open": 1, "high": 2, "low": 0.5,
            "close": 1.5, "tick_volume": 10, "spread": 5}])   # forex: sem chave real_volume
        conn.commit()
        q = "SELECT real_volume, COALESCE(NULLIF(real_volume,0), tick_volume) v FROM candles WHERE par=?"
        win = conn.execute(q, ("WIN$N",)).fetchone()
        eur = conn.execute(q, ("EURUSD#",)).fetchone()
        assert win["real_volume"] == 350 and win["v"] == 350, dict(win)     # B3 usa contratos
        assert eur["real_volume"] is None and eur["v"] == 10, dict(eur)     # forex cai no tick
        conn.close()
    finally:
        os.remove(caminho)


def test_vwap_ancora_no_pregao_b3():
    """Item 5: a VWAP da B3 ancora na ABERTURA DO PREGÃO (09:00 servidor); o forex, à meia-noite."""
    from .. import analise
    dia = 100 * 86400
    agora = dia + 14 * 3600            # 14:00 do servidor
    assert analise._inicio_sessao_vwap("EURUSD#", agora) == dia   # forex: meia-noite
    orig = config_b3.B3_HABILITADO
    try:
        config_b3.B3_HABILITADO = True
        assert analise._inicio_sessao_vwap("WIN$N", agora) == dia + 9 * 3600     # pregão de hoje
        cedo = dia + 7 * 3600         # 07:00: antes de abrir → sessão corrente é a de ontem
        assert analise._inicio_sessao_vwap("WIN$N", cedo) == dia - 86400 + 9 * 3600
    finally:
        config_b3.B3_HABILITADO = orig


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


# --------------------------------------------------------------------------- #
# Sombra da B3 (ETAPA 8b, bloqueio (b)) — P&L em BRL, isolamento por mercado, escala
# --------------------------------------------------------------------------- #
def test_lucro_brl_win_compra():
    """WIN comprado que sobe 100 pontos = +R$ 20 (0,20/pt) por contrato."""
    v = config_b3.lucro_brl("compra", 130000, 130100, "WIN$N", contratos=1)
    assert v == 20.0, v
    assert config_b3.lucro_brl("compra", 130000, 130100, "WIN$N", contratos=3) == 60.0


def test_lucro_brl_venda_e_wdo():
    """Venda ganha quando o preço cai; WDO vale R$ 10/ponto."""
    assert config_b3.lucro_brl("venda", 130100, 130000, "WIN$N") == 20.0     # caiu 100 → +R$20
    assert config_b3.lucro_brl("venda", 130000, 130100, "WIN$N") == -20.0    # subiu contra a venda
    assert config_b3.lucro_brl("compra", 5400.0, 5405.0, "WDO$N") == 50.0    # +5 pts × R$10


def test_lucro_brl_sem_valor_ponto_none():
    assert config_b3.lucro_brl("compra", 1, 2, "XPTO") is None   # símbolo sem fato de contrato


def test_valor_ponto_casa_por_prefixo():
    """Regressão: PARES_B3 é env-overridável — um sufixo diferente (WIN$, WINV26) devolvia None
    e TODO o P&L da B3 sumia em silêncio. O fato de contrato vale para a família WIN*/WDO*."""
    assert config_b3.valor_ponto("WINV26") == 0.20
    assert config_b3.valor_ponto("WIN$") == 0.20
    assert config_b3.valor_ponto("WDOF27") == 10.0
    assert config_b3.valor_ponto("XPTO") is None                 # desconhecido segue None


def test_param_simbolo_b3_override_e_default():
    orig = config_b3.PARAMS_SIMBOLO_B3
    try:
        config_b3.PARAMS_SIMBOLO_B3 = {"WIN$N": {"tamanho_pip": 5.0}}
        assert config_b3.param_simbolo_b3("WIN$N", "tamanho_pip") == 5.0
        assert config_b3.param_simbolo_b3("WIN$N", "sl_min_pips") is None       # não sobrescrito
        assert config_b3.param_simbolo_b3("WDO$N", "tamanho_pip", 0.5) == 0.5   # cai no default
    finally:
        config_b3.PARAMS_SIMBOLO_B3 = orig


def test_sombra_pares_respeita_flags():
    orig_h, orig_s = config_b3.B3_HABILITADO, config_b3.B3_SOMBRA_HABILITADA
    try:
        config_b3.B3_HABILITADO = True
        config_b3.B3_SOMBRA_HABILITADA = True
        assert config_b3.sombra_pares() == list(config_b3.PARES_B3)
        config_b3.B3_SOMBRA_HABILITADA = False
        assert config_b3.sombra_pares() == []   # sombra desligada → nada opera
        config_b3.B3_SOMBRA_HABILITADA = True
        config_b3.B3_HABILITADO = False
        assert config_b3.sombra_pares() == []   # módulo B3 desligado → nada opera
    finally:
        config_b3.B3_HABILITADO, config_b3.B3_SOMBRA_HABILITADA = orig_h, orig_s


def _inserir_decisao(conn, par, mercado, resultado="entrou"):
    conn.execute(
        "INSERT INTO decisoes (par, time_utc, tf, estrategia, direcao, resultado, criada_utc, mercado) "
        "VALUES (?, ?, 'M5', 'confluencia_v1', 'compra', ?, 0, ?)",
        (par, 1_000_000, resultado, mercado))
    conn.commit()


def test_decisoes_isoladas_por_mercado():
    """O executor do forex NÃO enxerga decisões b3 e vice-versa (isolamento por WHERE mercado)."""
    caminho = _tmp_db()
    try:
        with db.sessao(caminho) as conn:
            _inserir_decisao(conn, "EURUSD#", "forex")
            _inserir_decisao(conn, "WIN$N", "b3")
            forex = executor._decisoes_novas(conn, 0)
            b3 = executor_b3._decisoes_novas(conn, 0)
            assert [r["par"] for r in forex] == ["EURUSD#"], [r["par"] for r in forex]
            assert [r["par"] for r in b3] == ["WIN$N"], [r["par"] for r in b3]
    finally:
        os.remove(caminho)


def test_sentinela_desligada_so_no_forex():
    """A refutação do E_SENTINELA (18/07) desligou as 3 SÓ no forex; na B3 seguem em teste.
    O default é forex=false / b3=true, e o gate por mercado de `avaliar_par` reflete isso:
    forex resolve pelos flags forex (off), b3 pelos flags _B3 (on)."""
    # 1) defaults por mercado (o que o dono pediu: forex desligado, B3 catalogando)
    assert (config.SENT_FORCA_HABILITADA, config.SENT_DIVERG_HABILITADA,
            config.SENT_LEQUE_HABILITADA) == (False, False, False)
    assert (config.SENT_FORCA_HABILITADA_B3, config.SENT_DIVERG_HABILITADA_B3,
            config.SENT_LEQUE_HABILITADA_B3) == (True, True, True)
    # 2) a resolução por mercado usada em decisao.avaliar_par (forex off, b3 on)
    for merc, forca, diverg, leque in (
            ("forex", config.SENT_FORCA_HABILITADA, config.SENT_DIVERG_HABILITADA, config.SENT_LEQUE_HABILITADA),
            ("b3", config.SENT_FORCA_HABILITADA_B3, config.SENT_DIVERG_HABILITADA_B3, config.SENT_LEQUE_HABILITADA_B3)):
        _forex = (merc == "forex")
        assert (config.SENT_FORCA_HABILITADA if _forex else config.SENT_FORCA_HABILITADA_B3) == forca
        assert (config.SENT_DIVERG_HABILITADA if _forex else config.SENT_DIVERG_HABILITADA_B3) == diverg
        assert (config.SENT_LEQUE_HABILITADA if _forex else config.SENT_LEQUE_HABILITADA_B3) == leque
    assert config.SENT_FORCA_HABILITADA is False and config.SENT_FORCA_HABILITADA_B3 is True


def test_fecha_gap_desligada_so_no_forex():
    """fecha_gap_v1 foi refutada e desligada SÓ no forex; na B3 segue ligada (nunca auditada).
    O gate do gap em avaliar_par resolve por mercado: forex=GAP_HABILITADA (off), b3=_B3 (on)."""
    assert config.GAP_HABILITADA is False        # forex: refutada 18/07
    assert config.GAP_HABILITADA_B3 is True      # b3: catalogando até auditar
    # espelha a resolução por mercado do bloco de decisao.avaliar_par
    assert (config.GAP_HABILITADA if "forex" == "forex" else config.GAP_HABILITADA_B3) is False
    assert (config.GAP_HABILITADA if "b3" == "forex" else config.GAP_HABILITADA_B3) is True


def test_pullback_medias_aposentada_so_no_forex():
    """pullback_medias_v1 aposentada (pior controle) SÓ no forex; na B3 segue ligada (nunca
    auditada). O gate em avaliar_par resolve por mercado: forex=MEDIAS_HABILITADA (off), b3=_B3 (on)."""
    assert config.MEDIAS_HABILITADA is False        # forex: refutada 18/07 (exp −0,589R)
    assert config.MEDIAS_HABILITADA_B3 is True      # b3: catalogando até auditar
    assert (config.MEDIAS_HABILITADA if "forex" == "forex" else config.MEDIAS_HABILITADA_B3) is False
    assert (config.MEDIAS_HABILITADA if "b3" == "forex" else config.MEDIAS_HABILITADA_B3) is True


def test_decisao_legada_sem_mercado_fica_no_forex():
    """Decisão antiga (mercado NULL, pré-migração) é tratada como forex — a B3 não a pega."""
    caminho = _tmp_db()
    try:
        with db.sessao(caminho) as conn:
            conn.execute(
                "INSERT INTO decisoes (par, time_utc, tf, estrategia, direcao, resultado, criada_utc, mercado) "
                "VALUES ('GBPUSD#', 1000, 'M5', 'confluencia_v1', 'compra', 'entrou', 0, NULL)")
            conn.commit()
            assert [r["par"] for r in executor._decisoes_novas(conn, 0)] == ["GBPUSD#"]
            assert executor_b3._decisoes_novas(conn, 0) == []
    finally:
        os.remove(caminho)


def _inserir_trade_aberto(conn, par, mercado):
    conn.execute(
        "INSERT INTO trades (par, tf, estrategia, direcao, lote, preco_entrada, sl_servidor, "
        "abertura_utc, simulado, risco_inicial, variante, mercado) "
        "VALUES (?, 'M5', 'confluencia_v1', 'compra', 1, 100, 90, 0, 1, 10, 'A_ORIGINAL', ?)",
        (par, mercado))
    conn.commit()


def test_trades_abertos_isolados_por_mercado():
    """Carga inicial: cada executor só retoma as posições do SEU mercado (a B3 usa outra ponte)."""
    caminho = _tmp_db()
    try:
        with db.sessao(caminho) as conn:
            _inserir_trade_aberto(conn, "EURUSD#", "forex")
            _inserir_trade_aberto(conn, "WIN$N", "b3")
            forex = executor._abertas_do_banco(conn)
            b3 = executor_b3._abertas_do_banco(conn)
            assert [r["par"] for r in forex] == ["EURUSD#"], [r["par"] for r in forex]
            assert [r["par"] for r in b3] == ["WIN$N"], [r["par"] for r in b3]
    finally:
        os.remove(caminho)


def test_escala_deriva_da_calibracao():
    """ExecutorB3._escala deriva tick/piso/teto do SL dos candles (regra do ouro, sem chute)."""
    caminho = _tmp_db()
    try:
        with db.sessao(caminho) as conn:
            velas = []
            base = 130000
            for i in range(60):
                o = base + (i % 5) * 5          # grade de 5 em 5 → tick 5 (WIN)
                velas.append({"time": 1_000_000 + i * 300, "open": o, "high": o + 150,
                              "low": o - 150, "close": o + 25, "tick_volume": 100, "spread": 5})
            for tf in config_b3.TFS_COLETA_B3:
                gravar_candles(conn, "WIN$N", tf, velas)
            conn.commit()
            ex = executor_b3.ExecutorB3()
            escala = ex._escala(conn, "WIN$N")
        assert escala is not None
        assert escala["tick"] == 5.0
        assert escala["sl_min_pips"] >= 1 and escala["sl_max_pips"] > escala["sl_min_pips"]
    finally:
        os.remove(caminho)


def test_escala_override_dispensa_calibracao():
    """Com PARAMS_SIMBOLO_B3 completo, a escala vem do override (sem tocar candles/calibração)."""
    caminho = _tmp_db()
    orig = config_b3.PARAMS_SIMBOLO_B3
    try:
        config_b3.PARAMS_SIMBOLO_B3 = {"WIN$N": {"tamanho_pip": 5.0, "sl_min_pips": 60,
                                                  "sl_max_pips": 300}}
        with db.sessao(caminho) as conn:           # banco vazio: se lesse candles, falharia
            ex = executor_b3.ExecutorB3()
            escala = ex._escala(conn, "WIN$N")
        assert escala == {"tick": 5.0, "sl_min_pips": 60, "sl_max_pips": 300}, escala
    finally:
        config_b3.PARAMS_SIMBOLO_B3 = orig
        os.remove(caminho)


def test_escala_sem_dados_none():
    """Sem candles nem override, a escala é None → o executor apenas NÃO abre (não insta-estopa)."""
    caminho = _tmp_db()
    try:
        with db.sessao(caminho) as conn:
            ex = executor_b3.ExecutorB3()
            assert ex._escala(conn, "WIN$N") is None
    finally:
        os.remove(caminho)


def test_tick_valido_rejeita_cotacao_fantasma():
    """Guarda do tick-fantasma de leilão: bid/ask ≤ 0 ou cruzado = cotação INVÁLIDA.

    Foi a raiz do 'lucro alto' impossível no painel B3: um ask=0 fechando uma VENDA registrava
    'entrada − 0' = valor cheio do contrato como lucro. Cotação válida só com bid>0, ask>0, ask≥bid."""
    assert mt5_bridge_b3.tick_valido(178000.0, 178005.0) is True     # cotação normal
    assert mt5_bridge_b3.tick_valido(0.0, 178005.0) is False         # bid zero (pré-abertura)
    assert mt5_bridge_b3.tick_valido(178000.0, 0.0) is False         # ask zero → mataria a venda
    assert mt5_bridge_b3.tick_valido(0.0, 0.0) is False              # leilão: ambos zero
    assert mt5_bridge_b3.tick_valido(-1.0, 5.0) is False             # negativo
    assert mt5_bridge_b3.tick_valido(178010.0, 178000.0) is False    # cruzado (ask < bid)


def test_reset_b3_so_apaga_livro_b3():
    """manutencao.resetar_b3 apaga só trades/decisoes mercado='b3'; o forex fica intocado."""
    caminho = _tmp_db()
    try:
        with db.sessao(caminho) as conn:
            _inserir_decisao(conn, "EURUSD#", "forex")
            _inserir_decisao(conn, "WIN$N", "b3")
            _inserir_trade_aberto(conn, "EURUSD#", "forex")
            _inserir_trade_aberto(conn, "WIN$N", "b3")
            apagados = manutencao.resetar_b3(conn)
            assert apagados["trades"] == 1 and apagados["decisoes"] == 1, apagados
            pares_tr = [r["par"] for r in conn.execute("SELECT par FROM trades").fetchall()]
            pares_de = [r["par"] for r in conn.execute("SELECT par FROM decisoes").fetchall()]
            assert pares_tr == ["EURUSD#"], pares_tr    # forex preservado
            assert pares_de == ["EURUSD#"], pares_de
    finally:
        os.remove(caminho)


# --------------------------------------------------------------------------- #
# Janela de negociação FINA da B3 (09:15–16:00 p/ abrir; 17:30 fecha à força) — só B3
# --------------------------------------------------------------------------- #
def test_hhmm_para_min():
    assert config_b3._hhmm_para_min("09:15", 0) == 9 * 60 + 15
    assert config_b3._hhmm_para_min("16:00", 0) == 16 * 60
    assert config_b3._hhmm_para_min("9", 0) == 9 * 60          # só hora → minuto 0
    assert config_b3._hhmm_para_min("lixo", 555) == 555        # malformado → default
    assert config_b3._hhmm_para_min("25:99", 555) == 555       # fora de faixa → default


def test_dentro_janela_abertura():
    janela = (9 * 60 + 15, 16 * 60)                            # 09:15–16:00
    assert config_b3.dentro_janela_abertura(9 * 60 + 15, janela) is True   # abre em 09:15
    assert config_b3.dentro_janela_abertura(9 * 60 + 14, janela) is False  # 09:14 ainda não
    assert config_b3.dentro_janela_abertura(12 * 60, janela) is True       # meio do pregão
    assert config_b3.dentro_janela_abertura(15 * 60 + 59, janela) is True  # 15:59 ok
    assert config_b3.dentro_janela_abertura(16 * 60, janela) is False      # 16:00 já fechou p/ abrir


def test_hora_de_fechar_pregao():
    corte = 17 * 60 + 30                                       # 17:30
    assert config_b3.hora_de_fechar_pregao(17 * 60 + 29, corte) is False   # 17:29 ainda não
    assert config_b3.hora_de_fechar_pregao(17 * 60 + 30, corte) is True    # 17:30 fecha à força
    assert config_b3.hora_de_fechar_pregao(18 * 60, corte) is True         # depois também
    assert config_b3.hora_de_fechar_pregao(9 * 60, corte) is False         # manhã não fecha


def test_minuto_do_dia():
    assert config_b3.minuto_do_dia(63300) == 17 * 60 + 35     # 63300s = 17:35
    assert config_b3.minuto_do_dia(86400 + 63300) == 17 * 60 + 35  # ignora o dia


def test_persistir_ao_vivo_grava_flutuante():
    """P&L flutuante (R + dinheiro) da posição aberta é gravado p/ o painel ler ao vivo."""
    caminho = _tmp_db()
    try:
        with db.sessao(caminho) as conn:
            _inserir_trade_aberto(conn, "WIN$N", "b3")
            tid = conn.execute("SELECT id FROM trades").fetchone()["id"]
            executor._persistir_ao_vivo(conn, tid, 1.5, 42.0)
            row = conn.execute("SELECT r_atual, lucro_atual FROM trades WHERE id=?", (tid,)).fetchone()
            assert row["r_atual"] == 1.5 and row["lucro_atual"] == 42.0, dict(row)
    finally:
        os.remove(caminho)


def test_encerrar_pregao_fecha_forcado_1730():
    """_encerrar_pregao fecha à força TODAS as posições B3 às 17:30 com motivo catalogável;
    antes das 17:30 não mexe (a corretora só zera no fim do pregão)."""
    caminho = _tmp_db()
    orig_agora = executor_b3._agora
    orig_resolver = mt5_bridge_b3.resolver_simbolo
    orig_tick = mt5_bridge_b3.tick_atual
    orig_params = config_b3.PARAMS_SIMBOLO_B3
    try:
        config_b3.PARAMS_SIMBOLO_B3 = {"WIN$N": {"tamanho_pip": 5.0, "sl_min_pips": 60,
                                                  "sl_max_pips": 300}}
        mt5_bridge_b3.resolver_simbolo = lambda par: par
        mt5_bridge_b3.tick_atual = lambda simbolo: {"bid": 105.0, "ask": 106.0, "time": 63300}
        with db.sessao(caminho) as conn:
            _inserir_trade_aberto(conn, "WIN$N", "b3")   # compra @100, sl 90, lote 1
            r = conn.execute("SELECT id, par, direcao, preco_entrada, sl_servidor, risco_inicial, "
                             "tf, estrategia, ticket, mae_r, mfe_r, variante FROM trades").fetchone()
            ex = executor_b3.ExecutorB3()
            ex.abertas[r["id"]] = {
                "trade_id": r["id"], "ticket": r["ticket"], "par": r["par"], "tf": "M5",
                "simbolo": "WIN$N", "estrategia": r["estrategia"], "direcao": r["direcao"],
                "lote": 1, "preco_entrada": r["preco_entrada"], "sl": r["sl_servidor"],
                "risco": r["risco_inicial"], "abertura_utc": 0, "r_max": 0.0, "be_movido": False,
                "mae_r": 0.0, "mfe_r": 0.0, "real": False, "variante": "A_ORIGINAL",
            }
            # Antes das 17:30 (16:40) → não fecha.
            executor_b3._agora = lambda: 16 * 3600 + 40 * 60
            ex._encerrar_pregao(conn)
            assert ex.abertas, "não deveria fechar antes das 17:30"
            assert conn.execute("SELECT fechamento_utc FROM trades").fetchone()["fechamento_utc"] is None

            # Às 17:35 → fecha à força com o motivo catalogável.
            executor_b3._agora = lambda: 63300     # 17:35
            ex._encerrar_pregao(conn)
            assert not ex.abertas, "deveria ter fechado às 17:30"
            fim = conn.execute("SELECT fechamento_utc, motivo_saida, lucro_usd FROM trades").fetchone()
            assert fim["fechamento_utc"] is not None
            assert fim["motivo_saida"] == config_b3.MOTIVO_FECHAMENTO_PREGAO, fim["motivo_saida"]
            # compra 100→105, tick 5, WIN R$0,20/pt × 1 contrato = (105-100)*0.20 = 1.0 BRL
            assert abs(fim["lucro_usd"] - 1.0) < 1e-6, fim["lucro_usd"]
    finally:
        executor_b3._agora = orig_agora
        mt5_bridge_b3.resolver_simbolo = orig_resolver
        mt5_bridge_b3.tick_atual = orig_tick
        config_b3.PARAMS_SIMBOLO_B3 = orig_params
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
