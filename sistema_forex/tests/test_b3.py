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

from .. import config_b3, coletor_b3, db
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


def main() -> int:
    testes = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in testes:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(testes)} testes passaram ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
