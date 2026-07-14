"""Testes do reset de manutenção — sem pytest.

    python -m sistema_forex.tests.test_manutencao
"""

import os
import tempfile

from .. import db, manutencao


def _tmp_db():
    fd, caminho = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(caminho)
    return caminho


def test_reset_apaga_operacao_e_preserva_candles():
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        conn.execute("INSERT INTO candles (par,tf,time_utc,open,high,low,close,tick_volume,spread) "
                     "VALUES ('X','M5',1,1,1,1,1,10,8)")
        conn.execute("INSERT INTO trades (par,tf,estrategia,direcao) VALUES ('X','M5','confluencia_v1','compra')")
        conn.execute("INSERT INTO decisoes (par,time_utc,resultado) VALUES ('X',1,'entrou')")
        conn.execute("INSERT INTO regime_log (par,time_utc,regime) VALUES ('X',1,'lateral')")
        conn.commit()

        antes = manutencao.contar(conn)
        assert antes["trades"] == 1 and antes["decisoes"] == 1 and antes["candles"] == 1, antes

        apagados = manutencao.resetar(conn)
        depois = manutencao.contar(conn)
        assert depois["trades"] == 0 and depois["decisoes"] == 0, depois
        assert depois["regime_log"] == 0, depois
        assert depois["candles"] == 1, "candles NÃO podem ser apagados"
        assert apagados["trades"] == 1 and apagados["decisoes"] == 1, apagados
        conn.close()
    finally:
        os.remove(caminho)


def test_reset_por_mercado_nao_encosta_no_outro():
    """CRÍTICO (bug 14/07): limpar a B3 NÃO pode apagar o forex, e vice-versa. `resetar_forex` e
    `resetar_b3` são estritamente escopados por mercado (legado NULL = forex)."""
    caminho = _tmp_db()
    try:
        conn = db.conectar(caminho)
        conn.execute("INSERT INTO trades (par,estrategia,mercado) VALUES ('EURUSD#','confluencia_v1','forex')")
        conn.execute("INSERT INTO trades (par,estrategia,mercado) VALUES ('LEGADO','x',NULL)")  # legado = forex
        conn.execute("INSERT INTO trades (par,estrategia,mercado) VALUES ('WIN$N','sweep_choch_v1','b3')")
        conn.execute("INSERT INTO decisoes (par,time_utc,resultado,mercado) VALUES ('EURUSD#',1,'entrou','forex')")
        conn.execute("INSERT INTO decisoes (par,time_utc,resultado,mercado) VALUES ('WIN$N',1,'entrou','b3')")
        conn.commit()

        # Limpar a B3: some só o WIN$N; o forex (EURUSD# + legado NULL) fica intacto.
        ap_b3 = manutencao.resetar_b3(conn)
        assert ap_b3["trades"] == 1 and ap_b3["decisoes"] == 1, ap_b3
        assert conn.execute("SELECT COUNT(*) c FROM trades WHERE mercado='b3'").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM trades").fetchone()["c"] == 2, "forex intacto"
        assert conn.execute("SELECT COUNT(*) c FROM decisoes").fetchone()["c"] == 1, "decisão forex intacta"

        # Agora limpar o forex: some EURUSD# + legado NULL; nada de b3 sobrou mesmo.
        ap_fx = manutencao.resetar_forex(conn)
        assert ap_fx["trades"] == 2, ap_fx   # EURUSD# + legado NULL
        assert conn.execute("SELECT COUNT(*) c FROM trades").fetchone()["c"] == 0
        conn.close()
    finally:
        os.remove(caminho)


def test_backup_gera_arquivo_com_os_dados():
    caminho = _tmp_db()
    bak = None
    try:
        conn = db.conectar(caminho)
        conn.execute("INSERT INTO candles (par,tf,time_utc,open,high,low,close,tick_volume,spread) "
                     "VALUES ('X','M5',1,1,1,1,1,10,8)")
        conn.commit()
        conn.close()

        bak = manutencao._backup(caminho)
        assert os.path.exists(bak), "backup não foi criado"
        # o backup contém os candles preservados
        bconn = db.conectar(bak)
        assert bconn.execute("SELECT COUNT(*) c FROM candles").fetchone()["c"] == 1
        bconn.close()
    finally:
        os.remove(caminho)
        if bak and os.path.exists(bak):
            os.remove(bak)


def main() -> int:
    testes = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in testes:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(testes)} testes de manutenção passaram ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
