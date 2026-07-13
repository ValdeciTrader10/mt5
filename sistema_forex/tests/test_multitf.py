"""Testes das operações de sombra INDEPENDENTES por timeframe (M1/M5/M15) — sem pytest.

Cobre o que a feature adiciona sem depender de MT5/rede:
  - migração idempotente da coluna `tf` (bancos antigos ganham a coluna);
  - o estrategista avalia por (par, tf) e grava cada decisão marcada com o `tf` certo;
  - o snapshot usa a vela/ATR do TF de operação (contexto S/R/regime é par-level).

    python -m sistema_forex.tests.test_multitf
"""

import os
import tempfile

from .. import config, db, decisao


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


def _pos(par, tf, estrat):
    return {"par": par, "tf": tf, "estrategia": estrat}


def test_pode_abrir_sombra_cataloga_cada_estrategia():
    """Sombra: cada (par,tf,ESTRATÉGIA) roda sua própria operação; não duplica a mesma."""
    from ..executor import pode_abrir
    caps = dict(max_pos_por_par=1, max_pos_total=2, max_pos_sombra=200)
    abertas = [_pos("EURUSD#", "M5", "confluencia_v1")]
    # outra estratégia no mesmo (par,tf) PODE abrir (catálogo independente, sem correlação)
    assert pode_abrir(abertas, "EURUSD#", "M5", "sweep_choch_v1", ativa=False, **caps) is True
    # a MESMA (par,tf,estrategia) já viva NÃO empilha
    assert pode_abrir(abertas, "EURUSD#", "M5", "confluencia_v1", ativa=False, **caps) is False
    # só o teto amplo de segurança limita
    cheia = [_pos("EURUSD#", "M5", f"e{i}") for i in range(3)]
    caps_baixo = dict(max_pos_por_par=1, max_pos_total=2, max_pos_sombra=3)
    assert pode_abrir(cheia, "EURUSD#", "M5", "novo", ativa=False, **caps_baixo) is False


def test_pode_abrir_real_respeita_cap_por_livro():
    """Real: travas por LIVRO de TF continuam valendo (proteção de conta)."""
    from ..executor import pode_abrir
    caps = dict(max_pos_por_par=1, max_pos_total=2, max_pos_sombra=200)
    abertas = [_pos("EURUSD#", "M5", "a"), _pos("GBPUSD#", "M5", "b")]
    # livro M5 cheio (2) → 3ª no M5 bloqueada, mesmo sendo de outra estratégia/par
    assert pode_abrir(abertas, "USDCAD", "M5", "c", ativa=True, **caps) is False
    # M15 é livro à parte → permite
    assert pode_abrir(abertas, "USDCAD", "M15", "c", ativa=True, **caps) is True
    # mesmo (par,tf) 2x no real → bloqueia por MAX_POS_POR_PAR
    assert pode_abrir([_pos("EURUSD#", "M5", "a")], "EURUSD#", "M5", "b", ativa=True, **caps) is False


def main() -> int:
    testes = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in testes:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(testes)} testes passaram ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
