"""Testes das operações de sombra INDEPENDENTES por timeframe (M1/M5/M15) — sem pytest.

Cobre o que a feature adiciona sem depender de MT5/rede:
  - migração idempotente da coluna `tf` (bancos antigos ganham a coluna);
  - o estrategista avalia por (par, tf) e grava cada decisão marcada com o `tf` certo;
  - o snapshot usa a vela/ATR do TF de operação (contexto S/R/regime é par-level).

    python -m sistema_forex.tests.test_multitf
"""

import os
import tempfile

import json

from .. import analise, config, db, decisao


def _tmp_db():
    fd, caminho = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(caminho)
    return caminho


def _inserir_candles(conn, par, tf, n, base=1.1000):
    """N candles fechados sintéticos (com range p/ ATR > 0), timestamps espaçados por TF."""
    passo = {"M1": 60, "M5": 300, "M15": 900}.get(tf, 300)
    linhas = []
    for i in range(n):
        o = base + i * 0.00001
        h, l, c = o + 0.0003, o - 0.0003, o + 0.0001
        linhas.append((par, tf, 1_000_000 + i * passo, o, h, l, c, 100, 8))
    conn.executemany(
        "INSERT OR IGNORE INTO candles (par, tf, time_utc, open, high, low, close, tick_volume, spread) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        linhas,
    )
    conn.commit()


def test_migracao_adiciona_tf():
    """Banco no schema ANTIGO (sem `tf`) ganha a coluna via _migrar, sem perder dados."""
    fd, caminho = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = db.conectar(caminho)
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, par TEXT)")
        conn.execute("CREATE TABLE decisoes (id INTEGER PRIMARY KEY, par TEXT)")
        conn.commit()
        db._migrar(conn)
        tcols = {r["name"] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        dcols = {r["name"] for r in conn.execute("PRAGMA table_info(decisoes)").fetchall()}
        assert "tf" in tcols, tcols
        assert "tf" in dcols, dcols
        conn.close()
    finally:
        os.remove(caminho)


def test_variante_migracao_e_default():
    """A coluna `variante` é adicionada a bancos antigos (decisoes/trades), sem perder dados."""
    fd, caminho = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = db.conectar(caminho)
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, par TEXT)")
        conn.execute("CREATE TABLE decisoes (id INTEGER PRIMARY KEY, par TEXT)")
        conn.commit()
        db._migrar(conn)
        tcols = {r["name"] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        dcols = {r["name"] for r in conn.execute("PRAGMA table_info(decisoes)").fetchall()}
        assert "variante" in tcols and "variante" in dcols, (tcols, dcols)
        conn.close()
    finally:
        os.remove(caminho)


def test_niveis_periodo_grava_pivots_e_extremos():
    """analise.niveis_periodo grava pivots diários + máx/mín asiática/semana/mês em `niveis`."""
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        par = "EURUSD#"
        dia0 = 40 * 86400
        for i in range(40):                       # 40 dias de D1 (meia-noite de servidor)
            t = dia0 + i * 86400
            conn.execute(
                "INSERT INTO candles (par, tf, time_utc, open, high, low, close, tick_volume, spread) "
                "VALUES (?, 'D1', ?, 1.10, ?, ?, 1.10, 100, 8)",
                (par, t, 1.11 + i * 0.001, 1.09 - i * 0.001))
        ult_dia = dia0 + 40 * 86400
        for h in range(0, 7):                     # M15 da sessão asiática (00–07) do dia corrente
            conn.execute(
                "INSERT INTO candles (par, tf, time_utc, open, high, low, close, tick_volume, spread) "
                "VALUES (?, 'M15', ?, 1.10, 1.105, 1.095, 1.10, 100, 8)",
                (par, ult_dia + h * 3600))
        conn.commit()
        agora_srv = ult_dia + 8 * 3600            # já passou das 07h → sessão asiática de hoje
        n = analise.niveis_periodo(conn, par, agora_srv, agora_srv)
        assert n > 0, n
        tipos = {r["tipo"] for r in conn.execute("SELECT DISTINCT tipo FROM niveis WHERE par=?", (par,))}
        assert {"pivot_pp", "pivot_r1", "pivot_s1"} <= tipos, tipos
        assert {"max_asia", "min_asia", "max_semana", "max_mes"} <= tipos, tipos
        conn.close()
    finally:
        os.remove(caminho)


def test_variante_c_espelha_a_quando_entra():
    """ETAPA 6: quando uma estratégia da Variante A ENTRA, o avaliar_par também grava a decisão
    espelhada da Variante C (mesma (par,tf,estratégia), variante=C_HIBRIDA)."""
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        par = "EURUSD#"
        _inserir_candles(conn, par, "M5", 30)
        candle = decisao._ultimo(conn, par, "M5")
        # contexto p/ a confluencia_v1 entrar: regime de alta + suporte forte junto ao preço.
        conn.execute("INSERT INTO regime_log (par, time_utc, regime) VALUES (?,?,?)",
                     (par, candle["time_utc"], "tendencia_alta"))
        analise._grava_nivel(conn, par, "suporte", candle["close"] - 0.0001, "H1", 1, 6.0)
        conn.commit()
        decs = decisao.avaliar_par(conn, par, "M5", candle)
        entrou_a = [d for d in decs if d["variante"] == "A_ORIGINAL" and d["resultado"] == "entrou"]
        assert entrou_a, "a confluencia_v1 deveria entrar no cenário montado"
        # cada A que entrou tem uma C espelhada (entrou ou vetada pelo fuzzy), com a mesma estratégia.
        cs = [d for d in decs if d["variante"] == "C_HIBRIDA"]
        assert cs, "a Variante C deveria espelhar as decisões que a A tomou"
        assert {d["estrategia"] for d in cs} <= {d["estrategia"] for d in entrou_a}, (cs, entrou_a)
        # persistiu no banco com a variante certa
        n_c = conn.execute("SELECT COUNT(*) c FROM decisoes WHERE variante='C_HIBRIDA'").fetchone()["c"]
        assert n_c == len(cs), (n_c, len(cs))
        # EXPERIMENTO "deixa correr": cada C_HIBRIDA tem um gêmeo C_CORRE (mesma entrada), p/ isolar
        # o efeito da SAÍDA no /relatorio.
        ccorre = [d for d in decs if d["variante"] == "C_CORRE"]
        assert len(ccorre) == len(cs), (len(ccorre), len(cs))
        assert {(d["estrategia"], d["direcao"], d["resultado"]) for d in ccorre} == \
               {(d["estrategia"], d["direcao"], d["resultado"]) for d in cs}, "C_CORRE espelha a entrada da C"
        conn.close()
    finally:
        os.remove(caminho)


def test_snapshot_tem_pivots_e_medias():
    """O snapshot carrega `pivots` (do motor) e `medias_acima` (EMAs do TF superior)."""
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        par = "EURUSD#"
        for tf in ("M1", "M5"):
            _inserir_candles(conn, par, tf, 30)
        candle = decisao._ultimo(conn, par, "M1")
        snap = decisao.montar_snapshot(conn, par, "M1", candle)
        assert "pivots" in snap and "medias_acima" in snap
        # M1 lê as médias do M5; com 30 closes a EMA9/EMA20 já saem preenchidas.
        assert snap["medias_acima"].get("ema9") is not None, snap["medias_acima"]
        conn.close()
    finally:
        os.remove(caminho)


def test_decisao_grava_tf_por_livro():
    """avaliar_par em M1 e M15 grava decisões marcadas com o TF de cada livro."""
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        par = "EURUSD#"
        for tf in ("M1", "M5", "M15"):
            _inserir_candles(conn, par, tf, 30)

        vistos = {}
        for tf in ("M1", "M15"):
            candle = decisao._ultimo(conn, par, tf)
            assert candle is not None, tf
            decs = decisao.avaliar_par(conn, par, tf, candle)
            assert decs, "cada estratégia habilitada gera uma decisão"
            vistos[tf] = len(decs)

        for tf in ("M1", "M15"):
            n = conn.execute(
                "SELECT COUNT(*) c FROM decisoes WHERE par=? AND tf=?", (par, tf)
            ).fetchone()["c"]
            assert n == vistos[tf], (tf, n, vistos[tf])
        # Nenhuma decisão deve ficar sem TF (o default do schema é M5, nunca NULL).
        nulos = conn.execute("SELECT COUNT(*) c FROM decisoes WHERE tf IS NULL").fetchone()["c"]
        assert nulos == 0, nulos
        conn.close()
    finally:
        os.remove(caminho)


def test_snapshot_usa_atr_do_tf():
    """O snapshot carrega o `tf` e um ATR calculado a partir das velas daquele TF."""
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        par = "EURUSD#"
        _inserir_candles(conn, par, "M1", 30)
        candle = decisao._ultimo(conn, par, "M1")
        snap = decisao.montar_snapshot(conn, par, "M1", candle)
        assert snap["tf"] == "M1"
        assert snap["atr"] is not None and snap["atr"] > 0, snap["atr"]
        assert "m5_janela" in snap and snap["m5_janela"]["close"], "janela do TF preenchida"
        conn.close()
    finally:
        os.remove(caminho)


def _pos(par, tf, estrat, real=False, variante="A_ORIGINAL"):
    return {"par": par, "tf": tf, "estrategia": estrat, "real": real, "variante": variante}


def test_pode_abrir_variante_e_livro_independente():
    """ETAPA 6: A_ORIGINAL e C_HIBRIDA da MESMA (par,tf,estratégia) são livros separados — o gêmeo
    fuzzy-filtrado convive com o original (é o par A↔C a comparar)."""
    from ..executor import pode_abrir
    abertas = [_pos("EURUSD#", "M5", "confluencia_v1", variante="A_ORIGINAL")]
    # a C_HIBRIDA da mesma combinação PODE abrir (variante entra na chave de dedup)
    assert pode_abrir(abertas, "EURUSD#", "M5", "confluencia_v1", livro="sombra", cap=400,
                      variante="C_HIBRIDA") is True
    # mas a MESMA variante já viva NÃO empilha
    assert pode_abrir(abertas, "EURUSD#", "M5", "confluencia_v1", livro="sombra", cap=400,
                      variante="A_ORIGINAL") is False
    # default de variante = A_ORIGINAL (compatível com o comportamento anterior)
    assert pode_abrir(abertas, "EURUSD#", "M5", "confluencia_v1", livro="sombra", cap=400) is False


def test_pode_abrir_sombra_cataloga_cada_estrategia():
    """Sombra: cada (par,tf,ESTRATÉGIA) roda sua própria operação; não duplica a mesma."""
    from ..executor import pode_abrir
    abertas = [_pos("EURUSD#", "M5", "confluencia_v1")]
    # outra estratégia no mesmo (par,tf) PODE abrir (catálogo independente, sem correlação)
    assert pode_abrir(abertas, "EURUSD#", "M5", "sweep_choch_v1", livro="sombra", cap=200) is True
    # a MESMA (par,tf,estrategia) já viva NÃO empilha no mesmo livro
    assert pode_abrir(abertas, "EURUSD#", "M5", "confluencia_v1", livro="sombra", cap=200) is False
    # só o teto amplo de segurança limita
    cheia = [_pos("EURUSD#", "M5", f"e{i}") for i in range(3)]
    assert pode_abrir(cheia, "EURUSD#", "M5", "novo", livro="sombra", cap=3) is False


def test_pode_abrir_livros_sombra_e_real_sao_independentes():
    """Sombra e real são livros SEPARADOS: um gêmeo real convive com o virtual da mesma
    combinação, e cada livro tem o seu próprio teto."""
    from ..executor import pode_abrir
    # uma posição VIRTUAL de (EURUSD#, M5, confluencia_v1) não bloqueia o gêmeo REAL
    virtual = [_pos("EURUSD#", "M5", "confluencia_v1", real=False)]
    assert pode_abrir(virtual, "EURUSD#", "M5", "confluencia_v1", livro="real", cap=12) is True
    # mas dois reais da MESMA combinação, não
    real = [_pos("EURUSD#", "M5", "confluencia_v1", real=True)]
    assert pode_abrir(real, "EURUSD#", "M5", "confluencia_v1", livro="real", cap=12) is False
    # o teto do livro real conta só posições reais (as virtuais não ocupam a vaga do demo)
    mistas = [_pos("EURUSD#", "M5", f"e{i}", real=False) for i in range(20)] + \
             [_pos("GBPUSD#", "M5", "r0", real=True)]
    assert pode_abrir(mistas, "USDCAD", "M5", "r1", livro="real", cap=2) is True   # só 1 real ainda
    reais_cheio = [_pos("EURUSD#", "M5", "r0", real=True), _pos("GBPUSD#", "M5", "r1", real=True)]
    assert pode_abrir(reais_cheio, "USDCAD", "M5", "r2", livro="real", cap=2) is False


def test_agora_carimba_hora_do_servidor():
    """`_agora()` devolve a hora do SERVIDOR (candles/MetaTrader): offset arredondado à hora.
    Guarda de tick VELHO: depois de definido, um salto > 1h (tick de sexta no sábado) é
    IGNORADO — o offset não deriva nem congela o relógio no fim de semana."""
    import time as _t
    from ..executor import _atualizar_offset, _agora
    from .. import executor as ex
    try:
        _atualizar_offset(int(_t.time()) + 3 * 3600 + 41)   # servidor +3h (com 41s de latência)
        assert ex._OFFSET_SERVIDOR == 3 * 3600
        assert abs((_agora() - int(_t.time())) - 3 * 3600) <= 1
        _atualizar_offset(int(_t.time()) + 3 * 3600 - 30 * 3600)  # tick 30h velho → rejeitado
        assert ex._OFFSET_SERVIDOR == 3 * 3600, "tick velho não pode mover o offset"
        _atualizar_offset(int(_t.time()) + 2 * 3600)        # DST do broker (−1h) → aceito
        assert ex._OFFSET_SERVIDOR == 2 * 3600
    finally:
        ex._OFFSET_SERVIDOR = 0                              # reset p/ não afetar outros testes
        ex._OFFSET_DEFINIDO = False


def test_confluencia_reforca_zonas_alinhadas():
    """Níveis do mesmo tipo (de TFs diferentes) dentro da tolerância ganham força; nível isolado
    fica igual. Reforça as ZONAS de confluência (topos/fundos alinhados)."""
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        par = "EURUSD#"
        # dois suportes MUITO próximos (H1 e D1) = confluência; um isolado longe.
        analise._grava_nivel(conn, par, "suporte", 1.10000, "H1", 1, 4.0)
        analise._grava_nivel(conn, par, "suporte", 1.10010, "D1", 1, 3.0)  # 10 pips do 1º
        analise._grava_nivel(conn, par, "suporte", 1.20000, "W1", 1, 5.0)  # isolado
        conn.commit()
        atr = 0.0030  # tol = 0.5*ATR = 0.0015 (15 pips) → os dois primeiros são vizinhos
        n = analise._marcar_confluencia(conn, par, atr)
        assert n == 2, n
        rows = {round(r["preco"], 5): r for r in
                conn.execute("SELECT preco, forca, meta_json FROM niveis WHERE par=?", (par,))}
        assert rows[1.10000]["forca"] > 4.0 and rows[1.10010]["forca"] > 3.0, "confluentes reforçados"
        assert rows[1.20000]["forca"] == 5.0, "isolado inalterado"
        assert json.loads(rows[1.10000]["meta_json"])["confluencia"] == 1
        conn.close()
    finally:
        os.remove(caminho)


def test_combo_real_so_curadas():
    """O livro real curado só aceita as (estratégia, tf) configuradas (positivas, sem M1)."""
    assert config.combo_real("confluencia_v1", "M5") is True
    assert config.combo_real("fecha_gap_v1", "M15") is True
    assert config.combo_real("confluencia_v1", "M1") is False   # M1 fora
    assert config.combo_real("sweep_choch_v1", "M5") is False   # estratégia não-curada


def test_watermark_inicial_nao_redecide_candle_ja_decidido():
    """Regressão: todo restart reavaliava o candle corrente e gravava ~20 decisões DUPLICADAS
    por livro. A marca-d'água semeia ultimo_visto do banco (por par/tf, F_BREAKOUT à parte)."""
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        conn.execute("INSERT INTO decisoes (par, tf, time_utc, estrategia, resultado, direcao, "
                     "variante, mercado) VALUES ('EURUSD#','M5',5000,'confluencia_v1','nao_entrou',"
                     "NULL,'A_ORIGINAL','forex')")
        conn.execute("INSERT INTO decisoes (par, tf, time_utc, estrategia, resultado, direcao, "
                     "variante, mercado) VALUES ('EURUSD#','M15',9000,'breakout_londres_v1','entrou',"
                     "'compra','F_BREAKOUT','forex')")
        conn.execute("INSERT INTO decisoes (par, tf, time_utc, estrategia, resultado, direcao, "
                     "variante, mercado) VALUES ('WIN$N','M5',7000,'confluencia_v1','nao_entrou',"
                     "NULL,'A_ORIGINAL','b3')")
        conn.commit()
        m = decisao.watermark_inicial(conn)                    # forex (default)
        assert m[("EURUSD#", "M5")] == 5000, m
        assert m[("BRK", "EURUSD#", "M15")] == 9000, m         # livro breakout tem chave própria
        assert ("WIN$N", "M5") not in m, m                     # b3 não vaza no forex
        mb = decisao.watermark_inicial(conn, mercado="b3")
        assert mb[("WIN$N", "M5")] == 7000, mb
        conn.close()
    finally:
        os.remove(caminho)


def test_or_londres_nao_entra_em_candle_que_fecha_no_fim_da_janela():
    """Regressão do trade-lixo: candle M15 das 16:45 (fecha 17:00) passava no gate e o executor
    abria/fechava em segundos ("fim da janela"). Agora o candle tem de FECHAR antes das 17h."""
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        dia0 = 300 * 86400
        or_ini = dia0 + config.BREAKOUT_OR_HORA * 3600
        def ins(t, o, h, l, c):
            conn.execute("INSERT INTO candles (par, tf, time_utc, open, high, low, close, "
                         "tick_volume, spread) VALUES ('EURUSD#','M15',?,?,?,?,?,100,8)",
                         (t, o, h, l, c))
        ins(or_ini,        1.0992, 1.1000, 1.0990, 1.0995)
        ins(or_ini + 900,  1.0995, 1.0998, 1.0988, 1.0993)
        ins(or_ini + 1800, 1.0993, 1.0999, 1.0991, 1.0996)
        # 1º rompimento do dia SÓ às 16:45 (fecha 17:00 = fim da janela) → NÃO entra.
        t_borda = dia0 + 16 * 3600 + 45 * 60
        ins(t_borda, 1.0996, 1.1012, 1.0995, 1.1010)
        conn.commit()
        candle = conn.execute("SELECT * FROM candles WHERE time_utc=?", (t_borda,)).fetchone()
        assert decisao._or_londres(conn, "EURUSD#", "M15", candle) == {}, "borda das 17h não entra"
        conn.close()
    finally:
        os.remove(caminho)


def test_gravar_candles_backfill_saneia_parcial_congelado():
    """Regressão: o backfill gravava a barra em formação e o OR IGNORE a congelava p/ sempre.
    Com substituir=True (backfill), o histórico do broker SOBRESCREVE o parcial."""
    from ..coletor_mt5 import gravar_candles
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        parcial = [{"time": 1000, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05,
                    "tick_volume": 10, "spread": 8}]
        gravar_candles(conn, "X", "M5", parcial)               # snapshot parcial (bug antigo)
        cheio = [{"time": 1000, "open": 1.0, "high": 1.5, "low": 0.8, "close": 1.4,
                  "tick_volume": 99, "spread": 8}]
        gravar_candles(conn, "X", "M5", cheio)                 # OR IGNORE não conserta…
        r = conn.execute("SELECT high, close FROM candles WHERE par='X'").fetchone()
        assert r["high"] == 1.1, "sem substituir, o parcial fica congelado (comportamento antigo)"
        gravar_candles(conn, "X", "M5", cheio, substituir=True)  # …o backfill novo conserta
        r = conn.execute("SELECT high, close FROM candles WHERE par='X'").fetchone()
        assert r["high"] == 1.5 and r["close"] == 1.4, dict(r)
        conn.close()
    finally:
        os.remove(caminho)


def test_or_londres_detecta_primeiro_rompimento():
    """A faixa de abertura de Londres (10:00–10:45 servidor) é medida e o PRIMEIRO fechamento
    que a rompe, dentro da janela, gera a entrada — com sl_pips = amplitude da OR."""
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        dia0 = 100 * 86400
        or_ini = dia0 + config.BREAKOUT_OR_HORA * 3600
        def ins(t, o, h, l, c):
            conn.execute("INSERT INTO candles (par, tf, time_utc, open, high, low, close, "
                         "tick_volume, spread) VALUES ('EURUSD#','M15',?,?,?,?,?,100,8)",
                         (t, o, h, l, c))
        # 3 candles DENTRO da OR definem a faixa 1.0988–1.1000.
        ins(or_ini,        1.0992, 1.1000, 1.0990, 1.0995)
        ins(or_ini + 900,  1.0995, 1.0998, 1.0988, 1.0993)
        ins(or_ini + 1800, 1.0993, 1.0999, 1.0991, 1.0996)
        # 1º candle APÓS a OR fecha ACIMA do topo → rompimento de compra.
        brk_t = or_ini + 2700
        ins(brk_t, 1.0996, 1.1010, 1.0995, 1.1008)
        conn.commit()
        candle = conn.execute("SELECT * FROM candles WHERE time_utc=?", (brk_t,)).fetchone()
        orl = decisao._or_londres(conn, "EURUSD#", "M15", candle)
        assert orl.get("entrar") and orl["direcao"] == "compra", orl
        assert abs(orl["sl_pips"] - 12.0) < 0.2, orl      # (1.1000-1.0988)/0.0001 ≈ 12 pips
        # Um candle DENTRO da OR (ainda formando a faixa) não gera entrada.
        dentro = conn.execute("SELECT * FROM candles WHERE time_utc=?", (or_ini,)).fetchone()
        assert decisao._or_londres(conn, "EURUSD#", "M15", dentro) == {}, "dentro da OR não entra"
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
