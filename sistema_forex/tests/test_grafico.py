"""Testes do raio-X do trade (Fase 5 — auditoria) — sem pytest.

    python -m sistema_forex.tests.test_grafico

Cobrem a lógica pura/consulta: janela de candles (antes/durante/depois sem sobreposição),
resultado em R e recuperação do contexto da decisão de entrada. O desenho Plotly (I/O) não
é testado aqui — a matemática/consulta fica isolada e verificável com valores conhecidos.
"""

import json
import sqlite3

from .. import grafico as gr

PIP = 0.0001


def _conn():
    """SQLite em memória com as tabelas mínimas usadas pelo raio-X."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE candles (par TEXT, tf TEXT, time_utc INTEGER, open REAL, "
              "high REAL, low REAL, close REAL)")
    c.execute("CREATE TABLE decisoes (id INTEGER PRIMARY KEY AUTOINCREMENT, par TEXT, "
              "time_utc INTEGER, tf TEXT, estrategia TEXT, direcao TEXT, resultado TEXT, "
              "motivo TEXT, dados_json TEXT)")
    return c


def _candles(c, par, tf, tempos):
    for t in tempos:
        c.execute("INSERT INTO candles VALUES (?,?,?,?,?,?,?)",
                  (par, tf, t, 1.0, 1.0, 1.0, 1.0))
    c.commit()


def test_janela_tres_fatias_sem_sobreposicao():
    c = _conn()
    # candles de 60 em 60s de t=0 a t=600 (11 candles). Entrada em 300, saída em 420.
    _candles(c, "EURUSD#", "M5", list(range(0, 601, 60)))
    janela = gr._janela_trade(c, "EURUSD#", "M5", abertura_utc=300, fechamento_utc=420,
                              antes=2, depois=2)
    tempos = [r["time_utc"] for r in janela]
    # antes: 2 candles <=300 → 180,240,300 (últimos 2 antes + o de abertura); na verdade LIMIT 2
    # pega [300,240] revertidos → 240,300. durante: >300 e <=420 → 360,420. depois: >420 LIMIT 2 → 480,540.
    assert tempos == [240, 300, 360, 420, 480, 540], tempos
    # cronológico e estritamente crescente (sem duplicatas entre as fatias)
    assert tempos == sorted(set(tempos)), tempos


def test_janela_trade_aberto_puxa_mais_futuro():
    c = _conn()
    _candles(c, "EURUSD#", "M5", list(range(0, 100_000, 60)))  # muitos candles
    # trade ABERTO (fechamento None): ancora na abertura e puxa >= 500 candles à frente.
    janela = gr._janela_trade(c, "EURUSD#", "M5", abertura_utc=6000, fechamento_utc=None,
                              antes=3, depois=10)
    depois = [r for r in janela if r["time_utc"] > 6000]
    assert len(depois) >= 500, len(depois)  # aberto ignora o `depois` pequeno


def test_res_r_compra_e_venda():
    # compra: +10 pips com risco de 10 pips = +1R; venda espelhada.
    assert abs(gr._res_r("compra", 1.1000, 1.1010, 0.0010) - 1.0) < 1e-9
    assert abs(gr._res_r("venda", 1.1000, 1.0990, 0.0010) - 1.0) < 1e-9
    # perda de meio R na compra
    assert abs(gr._res_r("compra", 1.1000, 1.0995, 0.0010) - (-0.5)) < 1e-9
    # sem saída ou sem risco → None
    assert gr._res_r("compra", 1.1000, None, 0.0010) is None
    assert gr._res_r("compra", 1.1000, 1.1010, 0) is None


def test_contexto_decisao_casa_entrada():
    c = _conn()
    c.execute("INSERT INTO decisoes (par,time_utc,tf,estrategia,direcao,resultado,motivo,dados_json) "
              "VALUES (?,?,?,?,?,?,?,?)",
              ("EURUSD#", 1000, "M5", "confluencia_v1", "compra", "entrou", "score alto",
               json.dumps({"score": 5, "confluencias": ["rejeição S/R", "OB fresco"],
                           "regime": "tendencia"})))
    c.commit()
    # trade abre logo após a decisão (1000) → casa.
    ctx = gr._contexto_decisao(c, "EURUSD#", "M5", "confluencia_v1", "compra", abertura_utc=1005)
    assert ctx is not None
    assert ctx["score"] == 5
    assert "OB fresco" in ctx["confluencias"]
    assert ctx["regime"] == "tendencia"
    # direção/estratégia diferente não casa
    assert gr._contexto_decisao(c, "EURUSD#", "M5", "confluencia_v1", "venda", 1005) is None


def test_faixa_y_enquadra_candles_ignorando_niveis_distantes():
    # Candles apertados perto de 217 (caso GBPJPY do raio-X). A faixa deve abraçar os candles,
    # não os S/R distantes em 168/200 (que o Plotly, sem range fixo, usaria e esmagaria tudo).
    candles = [{"low": 216.9, "high": 217.4}, {"low": 217.0, "high": 217.3}]
    faixa = gr._faixa_y(candles, precos=(217.268, 216.965))
    assert faixa is not None
    lo, hi = faixa
    # enquadra o price action: nem perto dos níveis 168/200
    assert 216.0 < lo < 216.9, faixa
    assert 217.4 < hi < 218.4, faixa
    # margem simétrica em torno do span dos candles+preços
    assert abs((hi - 217.4) - (216.9 - lo)) < 1e-9, faixa


def test_faixa_y_inclui_precos_do_trade_fora_dos_candles():
    # Se entrada/SL/saída caírem fora das máx/mín dos candles, a faixa os inclui (ficam visíveis).
    candles = [{"low": 1.1000, "high": 1.1020}]
    faixa = gr._faixa_y(candles, precos=(1.0950, 1.1080))  # SL abaixo, alvo acima
    lo, hi = faixa
    assert lo < 1.0950 and hi > 1.1080, faixa


def test_faixa_y_degenerada_e_vazia():
    assert gr._faixa_y([]) is None  # sem candles
    # todos no mesmo preço: abre uma janela mínima em vez de span zero
    faixa = gr._faixa_y([{"low": 1.10, "high": 1.10}], precos=(1.10,))
    lo, hi = faixa
    assert hi > lo, faixa


def run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} testes do raio-X passaram.")


if __name__ == "__main__":
    run()
