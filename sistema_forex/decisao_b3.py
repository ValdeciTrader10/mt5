"""Estrategista de SOMBRA da B3 (ETAPA 8b) — roda as estratégias sobre WIN/WDO.

Gêmeo do `decisao.py`, mas para o mercado B3: itera `config_b3.sombra_pares()` × os TFs de
operação da B3 e, a cada candle fechado novo, chama a MESMA `decisao.avaliar_par` (as
estratégias são funções PURAS, agnósticas de mercado — reuso total, nada é reescrito),
marcando cada decisão com `mercado='b3'` + a janela de sessão/spread da B3.

Isolamento (princípio "tudo aditivo, nada alterado"):
  - as decisões `mercado='b3'` são lidas SÓ pelo `executor_b3` (ponte data-only da Genial,
    P&L em BRL); o executor do forex as ignora por WHERE `mercado='forex'`;
  - o motor já grava níveis/regime/fuzzy/VWAP de WIN/WDO (analise.um_ciclo itera os pares B3),
    então o snapshot da B3 tem o mesmo contexto que o do forex.

    python -m sistema_forex.decisao_b3            # loop
    python -m sistema_forex.decisao_b3 --uma-vez  # um ciclo (debug)
"""

import logging
import os
import signal
import sys
import time

from . import config_b3, db, decisao

log = logging.getLogger("decisao_b3")

_parar = False


def _tratar_sinal(signum, frame):  # pragma: no cover - sinal do SO
    global _parar
    log.info("Sinal %s recebido — encerrando após o ciclo atual.", signum)
    _parar = True


def _spread_max(par: str) -> float:
    """Filtro de spread da B3 (override por símbolo ou o default permissivo da sombra)."""
    return config_b3.param_simbolo_b3(par, "spread_max_pips", config_b3.SPREAD_MAX_B3)


def um_ciclo(conn, ultimo_visto: dict) -> None:
    """Avalia o candle mais recente de cada (par B3, TF de operação) ainda não visto."""
    for par in config_b3.sombra_pares():
        for tf in config_b3.TFS_OPERACAO_B3:
            candle = decisao._ultimo(conn, par, tf)
            if candle is None:
                continue
            chave = (par, tf)
            if ultimo_visto.get(chave) == candle["time_utc"]:
                continue  # nada novo neste (par, tf)
            ultimo_visto[chave] = candle["time_utc"]
            # Janela de ABERTURA da B3 (só B3): fora de 09:15–16:00 não geramos entrada — o
            # volume cai ao fim da tarde (pedido do dono). Relógio do servidor (hora do candle).
            if not config_b3.dentro_janela_abertura(
                    config_b3.minuto_do_dia(candle["time_utc"])):
                log.debug("B3 %s %s @%s fora da janela de abertura (09:15–16:00) — sem entrada",
                          par, tf, candle["time_utc"])
                continue
            try:
                for dec in decisao.avaliar_par(
                    conn, par, tf, candle,
                    mercado="b3", sessao_utc=config_b3.SESSAO_B3, spread_max=_spread_max(par),
                ):
                    log.info(
                        "Decisão B3 %s %s @%s [%s]: %s %s | score=%d | %s",
                        par, tf, candle["time_utc"], dec["estrategia"], dec["resultado"],
                        dec["direcao"] or "-", dec["score"], dec["motivo"],
                    )
            except Exception:  # noqa: BLE001 - um (par,tf) não derruba o serviço
                log.exception("Falha ao decidir B3 %s %s", par, tf)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    if not (config_b3.B3_HABILITADO and config_b3.B3_SOMBRA_HABILITADA):
        log.info("B3_HABILITADO/B3_SOMBRA_HABILITADA off — estrategista B3 não vai iniciar.")
        return
    signal.signal(signal.SIGINT, _tratar_sinal)
    signal.signal(signal.SIGTERM, _tratar_sinal)

    db.init_db()
    log.info("Estrategista B3 (sombra) iniciado. Pares: %s | TFs de operação: %s",
             ", ".join(config_b3.sombra_pares()), ", ".join(config_b3.TFS_OPERACAO_B3))

    ultimo_visto = {}
    with db.sessao() as conn:
        if "--uma-vez" in sys.argv:
            um_ciclo(conn, {})
            return
        while not _parar:
            um_ciclo(conn, ultimo_visto)
            for _ in range(config_b3.DECISAO_B3_POLL_S):
                if _parar:
                    break
                time.sleep(1)
    log.info("Estrategista B3 encerrado.")


if __name__ == "__main__":
    main()
