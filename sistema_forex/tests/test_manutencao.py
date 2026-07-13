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
