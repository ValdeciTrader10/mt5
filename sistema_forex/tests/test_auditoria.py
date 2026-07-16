"""Testes da auditoria de calibração (dossiê das perdedoras) — sem pytest.

    python -m sistema_forex.tests.test_auditoria

Cobrem a lógica pura (classificação da perda por MAE/MFE, veredito mantém/calibra/retira) e a
montagem do dossiê a partir de um banco em memória com trades de resultado conhecido.
"""

import json
import sqlite3

from .. import auditoria as aud


def _conn():
    """SQLite em memória com as colunas de trades/decisões que o dossiê consulta."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket INTEGER, par TEXT, "
        "tf TEXT, estrategia TEXT, direcao TEXT, lote REAL, pips REAL, lucro_usd REAL, "
        "motivo_saida TEXT, sl_servidor REAL, preco_entrada REAL, preco_saida REAL, "
        "risco_inicial REAL, mae_r REAL, mfe_r REAL, regime_entrada TEXT, "
        "abertura_utc INTEGER, fechamento_utc INTEGER, simulado INTEGER, decisao_id INTEGER, "
        "mercado TEXT)"
    )
    c.execute(
        "CREATE TABLE decisoes (id INTEGER PRIMARY KEY AUTOINCREMENT, par TEXT, time_utc INTEGER, "
        "tf TEXT, estrategia TEXT, direcao TEXT, resultado TEXT, motivo TEXT, dados_json TEXT)"
    )
    c.execute("CREATE TABLE candles (par TEXT, tf TEXT, time_utc INTEGER, open REAL, "
              "high REAL, low REAL, close REAL)")
    c.execute("CREATE TABLE niveis (par TEXT, tipo TEXT, preco REAL, preco2 REAL, "
              "tf_origem TEXT, forca REAL, n_toques INTEGER, meta_json TEXT, ativo INTEGER)")
    return c


def _trade(c, **kw):
    campos = {
        "par": "EURUSD#", "tf": "M5", "estrategia": "confluencia_v1", "direcao": "compra",
        "pips": -10, "lucro_usd": -10.0, "motivo_saida": "stop", "preco_entrada": 1.1000,
        "preco_saida": 1.0990, "risco_inicial": 0.0010, "mae_r": -1.0, "mfe_r": 0.0,
        "regime_entrada": "tendencia", "abertura_utc": 1000, "fechamento_utc": 2000,
        "simulado": 1,   # dossiê audita o livro SOMBRA (produção sempre grava simulado)
    }
    campos.update(kw)
    cols = ", ".join(campos)
    ph = ", ".join("?" for _ in campos)
    c.execute(f"INSERT INTO trades ({cols}) VALUES ({ph})", tuple(campos.values()))
    c.commit()


# --------------------------------------------------------------------------- #
# classificar_perda
# --------------------------------------------------------------------------- #
def test_classificar_alvo_curto():
    # andou +1.4R a favor e virou perdedora → o edge existiu, saída ruim.
    assert aud.classificar_perda(-0.6, 1.4) == "alvo_curto"
    assert aud.classificar_perda(-1.0, 1.0) == "alvo_curto"


def test_classificar_devolveu_parcial():
    assert aud.classificar_perda(-0.8, 0.7) == "devolveu_parcial"
    assert aud.classificar_perda(-1.0, 0.5) == "devolveu_parcial"


def test_classificar_entrada_adiantada():
    # foi contra de imediato: MFE baixo e MAE fundo.
    assert aud.classificar_perda(-1.0, 0.0) == "entrada_adiantada"
    assert aud.classificar_perda(-0.95, 0.2) == "entrada_adiantada"


def test_classificar_perda_ordenada():
    # excursão contra modesta, sem grande MFE nem MAE fundo o bastante.
    assert aud.classificar_perda(-0.5, 0.3) == "perda_ordenada"


def test_classificar_sem_dados():
    assert aud.classificar_perda(None, 0.5) == "sem_dados"
    assert aud.classificar_perda(-1.0, None) == "sem_dados"


# --------------------------------------------------------------------------- #
# veredito
# --------------------------------------------------------------------------- #
def test_veredito_amostra_pequena():
    kpi = {"n": 3, "expectativa": -2.0}
    assert aud._veredito(kpi, {}).startswith("AMOSTRA PEQUENA")


def test_veredito_mantem_expectancia_positiva():
    kpi = {"n": 20, "expectativa": 1.5}
    assert aud._veredito(kpi, {"perda_ordenada": 5}).startswith("MANTÉM")


def test_veredito_calibra_saida():
    # negativa, mas metade das perdas é alvo_curto/devolveu → conserto é na saída.
    kpi = {"n": 20, "expectativa": -1.0}
    flags = {"alvo_curto": 4, "devolveu_parcial": 2, "entrada_adiantada": 1, "perda_ordenada": 3}
    assert aud._veredito(kpi, flags).startswith("CALIBRA SAÍDA")


def test_veredito_calibra_entrada():
    kpi = {"n": 20, "expectativa": -1.0}
    flags = {"alvo_curto": 1, "devolveu_parcial": 0, "entrada_adiantada": 6, "perda_ordenada": 3}
    assert aud._veredito(kpi, flags).startswith("CALIBRA ENTRADA")


def test_veredito_retira():
    kpi = {"n": 20, "expectativa": -1.0}
    flags = {"alvo_curto": 1, "devolveu_parcial": 1, "entrada_adiantada": 1, "perda_ordenada": 8}
    assert aud._veredito(kpi, flags).startswith("RETIRA")


# --------------------------------------------------------------------------- #
# dossiê completo
# --------------------------------------------------------------------------- #
def test_dossie_conta_perdedoras_e_ignora_ganhos():
    c = _conn()
    _trade(c, lucro_usd=-10.0, mae_r=-1.0, mfe_r=1.3)   # perdedora alvo_curto
    _trade(c, lucro_usd=-8.0, mae_r=-1.0, mfe_r=0.0)    # perdedora entrada_adiantada
    _trade(c, lucro_usd=25.0, mae_r=-0.4, mfe_r=2.0)    # GANHADORA (ignorada nas flags)
    d = aud.dossie_perdedores(c)
    assert d["resumo"]["n"] == 3
    assert d["resumo"]["n_perdedoras"] == 2
    assert d["flags_perdedoras"]["alvo_curto"] == 1
    assert d["flags_perdedoras"]["entrada_adiantada"] == 1
    assert len(d["perdedores"]) == 2
    # detalhe só traz perdedoras
    assert all(t["usd"] < 0 for t in d["perdedores"])


def test_dossie_r_resultado_e_contexto_decisao():
    c = _conn()
    # perda de meio R (entrada 1.1000, saída 1.0995, risco 0.0010) na compra.
    _trade(c, lucro_usd=-5.0, preco_entrada=1.1000, preco_saida=1.0995, risco_inicial=0.0010,
           mae_r=-0.5, mfe_r=0.3, abertura_utc=1000)
    c.execute("INSERT INTO decisoes (par,time_utc,tf,estrategia,direcao,resultado,motivo,dados_json) "
              "VALUES (?,?,?,?,?,?,?,?)",
              ("EURUSD#", 990, "M5", "confluencia_v1", "compra", "entrou", "score alto",
               json.dumps({"score": 4, "confluencias": ["rejeição S/R"], "regime": "tendencia"})))
    c.commit()
    d = aud.dossie_perdedores(c)
    t = d["perdedores"][0]
    assert abs(t["R"] - (-0.5)) < 1e-9, t["R"]
    assert t["score"] == 4
    assert "rejeição S/R" in t["confluencias"]


def test_contexto_por_decisao_id_casa_direto():
    """Com a FK decisao_id, casa a decisão EXATA — mesmo quando a heurística por tempo
    falharia (decisão gravada DEPOIS da abertura registrada)."""
    c = _conn()
    c.execute("INSERT INTO decisoes (par,time_utc,tf,estrategia,direcao,resultado,motivo,dados_json) "
              "VALUES (?,?,?,?,?,?,?,?)",
              ("EURUSD#", 999999, "M5", "confluencia_v1", "compra", "entrou", "x",
               json.dumps({"score": 3, "confluencias": ["regime", "sr_forte"],
                           "regime": "tendencia_alta"})))
    did = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    _trade(c, decisao_id=did, abertura_utc=1000)   # abertura MUITO antes do time_utc da decisão
    t = dict(c.execute("SELECT * FROM trades").fetchone())
    ctx = aud._contexto_decisao(c, t)
    assert ctx and ctx["score"] == 3 and "sr_forte" in ctx["confluencias"], ctx


def _serie_choch_alta(c, par="Z"):
    """20 velas M5 com pivôs A(high) B(low) C(high<A) D(low<B) E(high>A): gera um CHoCH ALTA
    confirmado (HH depois de bias baixa). Preço = 1.10 + v/1000; range fixo."""
    vals = [10, 11, 12, 15, 12, 11, 10, 8, 10, 11, 13, 10, 8, 6, 9, 13, 17, 14, 12, 11]
    for i, v in enumerate(vals):
        cl = 1.10 + v * 0.001
        c.execute("INSERT INTO candles VALUES (?,?,?,?,?,?,?)",
                  (par, "M5", 1000 + i * 300, cl, cl + 0.0002, cl - 0.0002, cl))
    c.commit()


def _trade_venda_choch(**kw):
    t = dict(par="Z", tf="M5", direcao="venda", preco_entrada=1.111, sl_servidor=1.1300,
             risco_inicial=0.0050, abertura_utc=1000 + 5 * 300, fechamento_utc=1000 + 19 * 300,
             lucro_usd=-5.0, id=1, r_result=-1.0, preco_saida=1.111)
    t.update(kw)
    return t


def test_invalidacao_choch_oposto_salva():
    # VENDA: um CHoCH ALTA (oposto) confirma antes do stop (sl distante) → reduz a perda.
    c = _conn(); _serie_choch_alta(c)
    s = aud.simular_saida_invalidacao(c, _trade_venda_choch())
    assert s["status"] == "salvaria", s
    assert s["r_saida"] is not None and s["saved_r"] > 0, s


def test_invalidacao_stop_antes_do_sinal():
    # sl apertado (1.1150) → o stop é furado ANTES do CHoCH confirmar → sem sinal aproveitável.
    c = _conn(); _serie_choch_alta(c)
    s = aud.simular_saida_invalidacao(c, _trade_venda_choch(sl_servidor=1.1150))
    assert s["status"] == "sem_sinal", s


def test_invalidacao_janela_desalinhada_e_descartada():
    # entrada MUITO distante da vela de entrada (janela deslocada, ex.: bug de fuso) → descarta.
    c = _conn(); _serie_choch_alta(c)
    s = aud.simular_saida_invalidacao(c, _trade_venda_choch(preco_entrada=1.140))  # ~30 pips longe
    assert s["status"] == "janela_suspeita", s
    r = aud.resumo_invalidacao(c, [_trade_venda_choch(preco_entrada=1.140)])
    assert r["janela_suspeita"] == 1 and r["salvaria"] == 0, r


def test_invalidacao_sem_dados_e_resumo():
    c = _conn(); _serie_choch_alta(c)
    faltando = dict(par="Z", tf="M5", direcao="venda", preco_entrada=None, sl_servidor=None,
                    risco_inicial=None, abertura_utc=1, fechamento_utc=2)
    assert aud.simular_saida_invalidacao(c, faltando)["status"] == "sem_dados"
    r = aud.resumo_invalidacao(c, [_trade_venda_choch()])
    assert r["n_avaliadas"] == 1 and r["salvaria"] == 1 and r["usd_salvo_total"] > 0, r


def test_dossie_por_estrategia_tf_traz_veredito():
    c = _conn()
    for _ in range(6):  # 6 perdas alvo_curto na mesma (estratégia, TF) → CALIBRA SAÍDA
        _trade(c, lucro_usd=-10.0, mae_r=-0.7, mfe_r=1.3)
    d = aud.dossie_perdedores(c)
    linha = d["por_estrategia_tf"][0]
    assert linha["perd_alvo_curto"] == 6
    assert linha["veredito"].startswith("CALIBRA SAÍDA")


def test_dossie_texto_contem_secoes():
    c = _conn()
    _trade(c, lucro_usd=-10.0, mae_r=-1.0, mfe_r=1.3)
    txt = aud.dossie_texto(aud.dossie_perdedores(c))
    assert "DOSSIÊ DE CALIBRAÇÃO" in txt
    assert "Por estratégia × timeframe" in txt
    assert "alvo_curto" in txt


def test_filtro_datas_exclui_fora_do_intervalo():
    c = _conn()
    _trade(c, lucro_usd=-10.0, fechamento_utc=1_000_000)          # 1970 (fora)
    _trade(c, lucro_usd=-10.0, fechamento_utc=1_752_000_000)      # 2025-07 (dentro)
    d = aud.dossie_perdedores(c, de="2025-01-01")
    assert d["resumo"]["n_perdedoras"] == 1


# --------------------------------------------------------------------------- #
# Raio-X textual (candles em pips) — a "visão do gráfico" para a IA
# --------------------------------------------------------------------------- #
def _cndl(c, par, tf, t, o, h, l, cl):
    c.execute("INSERT INTO candles VALUES (?,?,?,?,?,?,?)", (par, tf, t, o, h, l, cl))


def _cenario_stop_apertado(c):
    """Compra parada no ruído: anda +8 a favor, o preço FURA o SL em 2 pips e sai; DEPOIS
    da saída recupera +15 pips a favor (clássico stop apertado / saída cedo)."""
    P = "EURUSD#"
    _trade(c, id=None, direcao="compra", preco_entrada=1.1000, sl_servidor=1.0990,
           preco_saida=1.0990, risco_inicial=0.0010, pips=-10, lucro_usd=-10.0,
           mae_r=-1.2, mfe_r=0.8, abertura_utc=1000, fechamento_utc=1300,
           motivo_saida="stop (r=-1.0)")
    velas = [
        (820, 1.0999, 1.1001, 1.0998, 1.1000), (880, 1.1000, 1.1001, 1.0999, 1.1000),
        (940, 1.1000, 1.1001, 1.0999, 1.1000), (1000, 1.1000, 1.1002, 1.0999, 1.1001),  # entrada
        (1060, 1.1001, 1.1008, 1.1001, 1.1006),  # +8 a favor
        (1120, 1.1004, 1.1006, 1.1003, 1.1004),
        (1180, 1.1004, 1.1004, 1.0995, 1.0996),
        (1240, 1.0996, 1.0997, 1.0988, 1.0991),  # low 1.0988 < SL 1.0990 → furou 2 pips
        (1300, 1.0991, 1.0992, 1.0989, 1.0990),  # saída (stop)
        (1360, 1.0991, 1.1010, 1.0991, 1.1008),  # recupera +10
        (1420, 1.1008, 1.1015, 1.1006, 1.1012),  # +15 a favor após sair
        (1480, 1.1012, 1.1013, 1.1009, 1.1011),
    ]
    for v in velas:
        _cndl(c, P, "M5", *v)
    c.commit()


def test_pip_de_trade_back_out():
    # |1.0990-1.1000| / |−10| = 0.0001 (respeita o pip real gravado)
    t = {"preco_entrada": 1.1000, "preco_saida": 1.0990, "pips": -10}
    assert abs(aud._pip_de_trade(t) - 0.0001) < 1e-12
    # sem pips → heurística por casas (ouro ~2000 → 0.01)
    assert aud._pip_de_trade({"preco_entrada": 2000.0, "preco_saida": None, "pips": None}) == 0.01


def test_raiox_candles_em_pips_e_marcas():
    c = _conn(); _cenario_stop_apertado(c)
    d = aud.raiox_de_id(c, 1)
    assert d["sl_pips"] == -10.0        # SL 10 pips abaixo da entrada
    # candle de entrada e de saída marcados
    marcas = {cd["marca"] for cd in d["candles"]}
    assert "E" in marcas and "X" in marcas
    # o candle +1 tem high a +8 pips vs entrada
    c1 = [cd for cd in d["candles"] if cd["off"] == 1][0]
    assert c1["h"] == 8.0, c1


def test_raiox_detecta_furo_de_stop():
    c = _conn(); _cenario_stop_apertado(c)
    d = aud.raiox_de_id(c, 1)
    # low 1.0988 vs SL 1.0990 → furou 2 pips
    assert d["furou_sl_pips"] == 2.0, d["furou_sl_pips"]


def test_raiox_mfe_mae_recomputados():
    c = _conn(); _cenario_stop_apertado(c)
    d = aud.raiox_de_id(c, 1)
    assert d["mfe_pips"] == 8.0 and d["mfe_offset"] == 1
    assert d["mae_pips"] == -12.0 and d["mae_offset"] == 4


def test_raiox_excursao_pos_saida():
    c = _conn(); _cenario_stop_apertado(c)
    d = aud.raiox_de_id(c, 1)
    # após a saída o preço foi a +15 a favor → sinal de stop apertado
    assert d["pos_saida_favor_pips"] == 15.0, d["pos_saida_favor_pips"]


def test_raiox_venda_favor_e_niveis():
    c = _conn()
    _trade(c, direcao="venda", preco_entrada=1.2000, sl_servidor=1.2010, preco_saida=1.2010,
           risco_inicial=0.0010, pips=-10, lucro_usd=-10.0, abertura_utc=1000,
           fechamento_utc=1120, mae_r=-1.0, mfe_r=0.2)
    for v in [(1000, 1.2000, 1.2001, 1.1999, 1.2000), (1060, 1.2000, 1.2004, 1.1997, 1.2003),
              (1120, 1.2003, 1.2011, 1.2002, 1.2010), (1180, 1.2009, 1.2010, 1.2005, 1.2006)]:
        _cndl(c, "EURUSD#", "M5", *v)
    # nível de resistência 3 pips acima da entrada
    c.execute("INSERT INTO niveis VALUES (?,?,?,?,?,?,?,?,?)",
              ("EURUSD#", "resistencia", 1.2003, None, "H1", 5, 3, None, 1))
    c.commit()
    d = aud.raiox_de_id(c, 1)
    # venda: a favor = descer; no candle +1 o low 1.1997 = +3 pips a favor (MFE)
    assert d["mfe_pips"] == 3.0, d["mfe_pips"]
    # furou o SL (high 1.2011 > 1.2010) por 1 pip
    assert d["furou_sl_pips"] == 1.0, d["furou_sl_pips"]
    # nível perto aparece com distância +3 pips
    tipos = {(n["tipo"], n["dist_pips"]) for n in d["niveis"]}
    assert ("resistencia", 3.0) in tipos, d["niveis"]


def test_raiox_texto_tem_fatos_e_candles():
    c = _conn(); _cenario_stop_apertado(c)
    txt = aud.raiox_texto(aud.raiox_de_id(c, 1))
    assert "RAIO-X #1" in txt
    assert "FUROU o SL" in txt
    assert "FATOS" in txt and "CANDLES" in txt


def test_dossie_embute_raiox():
    c = _conn(); _cenario_stop_apertado(c)
    d = aud.dossie_perdedores(c, raiox_trades=3)
    assert len(d["raiox"]) == 1
    assert d["raiox"][0]["furou_sl_pips"] == 2.0
    assert "Raio-X das perdedoras" in aud.dossie_texto(d)


def test_dossie_isola_mercado_forex_e_b3():
    """O dossiê escopa por mercado: 'forex' (default, inclui legado NULL) ignora a B3 e vice-versa."""
    c = _conn()
    _trade(c, par="EURUSD#", lucro_usd=-10.0, mae_r=-1.0, mfe_r=0.2)          # forex (NULL = legado)
    _trade(c, par="GBPUSD#", lucro_usd=-7.0, mae_r=-1.0, mfe_r=0.1, mercado="forex")
    _trade(c, par="WIN$N", lucro_usd=-69.0, mae_r=-1.0, mfe_r=0.3, mercado="b3")
    _trade(c, par="WDO$N", lucro_usd=-43.0, mae_r=-1.0, mfe_r=0.1, mercado="b3")

    forex = aud.dossie_perdedores(c)                       # default forex
    assert forex["resumo"]["n"] == 2
    assert {t["par"] for t in forex["perdedores"]} == {"EURUSD#", "GBPUSD#"}

    b3 = aud.dossie_perdedores(c, mercado="b3")
    assert b3["resumo"]["n"] == 2
    assert {t["par"] for t in b3["perdedores"]} == {"WIN$N", "WDO$N"}


def run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} testes da auditoria passaram.")


if __name__ == "__main__":
    run()
