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
        "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, par TEXT, tf TEXT, "
        "estrategia TEXT, direcao TEXT, pips REAL, lucro_usd REAL, motivo_saida TEXT, "
        "preco_entrada REAL, preco_saida REAL, risco_inicial REAL, mae_r REAL, mfe_r REAL, "
        "regime_entrada TEXT, abertura_utc INTEGER, fechamento_utc INTEGER)"
    )
    c.execute(
        "CREATE TABLE decisoes (id INTEGER PRIMARY KEY AUTOINCREMENT, par TEXT, time_utc INTEGER, "
        "tf TEXT, estrategia TEXT, direcao TEXT, resultado TEXT, motivo TEXT, dados_json TEXT)"
    )
    return c


def _trade(c, **kw):
    campos = {
        "par": "EURUSD#", "tf": "M5", "estrategia": "confluencia_v1", "direcao": "compra",
        "pips": -10, "lucro_usd": -10.0, "motivo_saida": "stop", "preco_entrada": 1.1000,
        "preco_saida": 1.0990, "risco_inicial": 0.0010, "mae_r": -1.0, "mfe_r": 0.0,
        "regime_entrada": "tendencia", "abertura_utc": 1000, "fechamento_utc": 2000,
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


def run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} testes da auditoria passaram.")


if __name__ == "__main__":
    run()
