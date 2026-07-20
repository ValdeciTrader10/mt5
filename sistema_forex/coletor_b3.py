"""Coletor B3 (ETAPA 8) — candles de WIN/WDO do MT5 da Genial → SQLite.

Gêmeo do `coletor_mt5.py`, mas para o SEGUNDO terminal (Genial, container `mt5_b3`):
  - usa a ponte DATA-ONLY `mt5_bridge_b3` (sem risco de ordem na conta real);
  - lê os símbolos/TFs/backfill de `config_b3`;
  - grava na MESMA tabela `candles`, com `par` = símbolo B3 (WIN$N, WDO$N…) — não colide
    com o forex (pares distintos) e o motor do forex (que itera `config.PARES`) não os toca.

Reaproveita as funções PURAS de persistência do coletor forex (`gravar_candles`, `contar`)
— são agnósticas de broker (recebem conn + lista de candles).

Aceite (ETAPA 8, meta desta etapa): WIN logando — contagem de candles crescendo no banco,
sem buracos, visível no log DEBUG a cada candle fechado.
"""

import logging
import os
import signal
import time
from datetime import datetime

from . import config_b3, db, indicadores, mt5_bridge_b3
from .coletor_mt5 import _MIN_POR_TF, contar, gravar_candles

log = logging.getLogger("coletor_b3")

# Quantos candles buscar por TF a cada verificação incremental (folga p/ não perder nada).
_JANELA_INCREMENTAL = 10

_parar = False


def _tratar_sinal(signum, frame):  # pragma: no cover - sinal do SO
    global _parar
    log.info("Sinal %s recebido — encerrando após a iteração atual.", signum)
    _parar = True


# --------------------------------------------------------------------------- #
# Backfill
# --------------------------------------------------------------------------- #
def _alvo_barras(tf: str) -> int:
    """Nº de candles a pedir para cobrir BACKFILL_MESES_B3 (com folga)."""
    minutos = _MIN_POR_TF.get(tf, 5)
    dias = config_b3.BACKFILL_MESES_B3 * 31
    alvo = max(1, (dias * 24 * 60) // minutos, config_b3.BACKFILL_MIN_BARRAS_B3)
    if tf == "M1":  # teto do M1 (6 meses seriam dezenas de milhares de velas)
        alvo = min(alvo, config_b3.BACKFILL_M1_BARRAS_B3)
    return alvo


def _backfill_tf(simbolo: str, tf: str, alvo: int) -> list:
    """Busca ~`alvo` candles com retry enquanto o histórico do MT5 vai baixando."""
    anterior = -1
    candles: list = []
    for _ in range(config_b3.BACKFILL_TENTATIVAS_B3):
        candles = mt5_bridge_b3.copy_rates_from_pos(simbolo, tf, 0, alvo)
        n = len(candles)
        if n <= anterior:      # parou de crescer → histórico estabilizou
            break
        anterior = n
        if n >= alvo:          # já temos tudo o que pedimos
            break
        time.sleep(config_b3.BACKFILL_ESPERA_S_B3)
    return candles


def backfill(conn, simbolos: dict) -> None:
    """Baixa ~BACKFILL_MESES_B3 de histórico por símbolo/tf. Idempotente (INSERT OR IGNORE)."""
    for par, simbolo in simbolos.items():
        for tf in config_b3.TFS_COLETA_B3:
            alvo = _alvo_barras(tf)
            candles = _backfill_tf(simbolo, tf, alvo)
            # Última barra em formação NUNCA persiste; REPLACE saneia parciais congelados.
            novos = gravar_candles(conn, par, tf, candles[:-1], substituir=True)
            conn.commit()
            log.info(
                "Backfill B3 %s %s: %d recebidos (alvo %d), %d gravados/saneados, total no banco %d",
                par, tf, len(candles), alvo, novos, contar(conn, par, tf),
            )


# --------------------------------------------------------------------------- #
# Loop incremental
# --------------------------------------------------------------------------- #
def _tf_gatilho() -> str:
    """TF mais fino coletado — dispara a coleta assim que o candle dele fecha."""
    return min(config_b3.TFS_COLETA_B3, key=lambda tf: _MIN_POR_TF.get(tf, 5)) \
        if config_b3.TFS_COLETA_B3 else "M5"


def _ultimo_fechado(simbolo: str, tf: str):
    """Retorna o candle `tf` fechado mais recente (índice -2), ou None."""
    recentes = mt5_bridge_b3.copy_rates_from_pos(simbolo, tf, 0, 2)
    if len(recentes) < 2:
        return None
    return recentes[-2]  # [-2] é o último fechado; [-1] está em formação


def _delta_do_candle(simbolo: str, tf: str, candle: dict):
    """DELTA de fluxo (agressão compra−venda) do candle, dos TRADE ticks da sua janela de tempo.

    None se o feed não devolveu ticks (leilão/fora do pregão) — o consumidor cai no volume só.
    A janela é [time, time + duração do TF) no MESMO relógio (server) dos candles."""
    minutos = _MIN_POR_TF.get(tf, 5)
    inicio = datetime.utcfromtimestamp(candle["time"])
    fim = datetime.utcfromtimestamp(candle["time"] + minutos * 60)
    try:
        ticks = mt5_bridge_b3.copy_ticks_range(simbolo, inicio, fim)
    except mt5_bridge_b3.MT5Erro as e:
        log.debug("Sem ticks p/ delta %s %s: %s", simbolo, tf, e)
        return None
    return indicadores.delta_de_ticks(ticks)


def _atualizar_deltas(conn, par: str, simbolo: str, tf: str, fechados: list) -> None:
    """Calcula e grava o DELTA dos candles recém-fechados que ainda não o têm (evita re-buscar
    ticks). Só nos TFs de OPERAÇÃO da B3 (onde a VSA/Delta roda) — bound de custo dos ticks."""
    if not fechados or tf not in config_b3.TFS_OPERACAO_B3:
        return
    tempos = [c["time"] for c in fechados]
    ph = ",".join("?" * len(tempos))
    com_delta = {
        r[0] for r in conn.execute(
            f"SELECT time_utc FROM candles WHERE par=? AND tf=? AND time_utc IN ({ph}) "
            "AND delta IS NOT NULL",
            (par, tf, *tempos),
        ).fetchall()
    }
    for c in fechados:
        if c["time"] in com_delta:
            continue
        d = _delta_do_candle(simbolo, tf, c)
        if d is not None:
            conn.execute("UPDATE candles SET delta=? WHERE par=? AND tf=? AND time_utc=?",
                         (d, par, tf, c["time"]))


def backfill_deltas(conn, simbolos: dict) -> None:
    """Preenche o DELTA dos candles RECENTES que ainda não o têm — UMA VEZ, no arranque. Dá dado
    imediato ao gráfico/estratégia (não espera o pregão) e valida o pipeline tick→delta contra o
    feed real. Bounded por `DELTA_BACKFILL_CANDLES` (os candles mais novos primeiro). Candles sem
    ticks (fim de semana/pré-abertura) ficam NULL — legítimo, não têm agressão a medir."""
    limite = config_b3.DELTA_BACKFILL_CANDLES
    if limite <= 0:
        return
    for par, simbolo in simbolos.items():
        for tf in config_b3.TFS_OPERACAO_B3:
            faltantes = conn.execute(
                "SELECT time_utc FROM candles WHERE par=? AND tf=? AND delta IS NULL "
                "ORDER BY time_utc DESC LIMIT ?",
                (par, tf, limite),
            ).fetchall()
            preenchidos = 0
            for r in faltantes:
                d = _delta_do_candle(simbolo, tf, {"time": r["time_utc"]})
                if d is not None:
                    conn.execute("UPDATE candles SET delta=? WHERE par=? AND tf=? AND time_utc=?",
                                 (d, par, tf, r["time_utc"]))
                    preenchidos += 1
                if _parar:
                    break
            conn.commit()
            log.info("Backfill delta B3 %s %s: %d de %d candles preenchidos",
                     par, tf, preenchidos, len(faltantes))


def coletar_incremental(conn, par: str, simbolo: str) -> int:
    """Insere candles novos de todos os TFs para um símbolo. Retorna total de novos.

    Nos TFs de operação, calcula também o DELTA de fluxo (agressão) do candle a partir dos
    trade ticks — a matéria-prima da VSA/Delta na B3 (no forex não há delta = só tick_volume).
    """
    total = 0
    for tf in config_b3.TFS_COLETA_B3:
        candles = mt5_bridge_b3.copy_rates_from_pos(simbolo, tf, 0, _JANELA_INCREMENTAL)
        fechados = candles[:-1] if candles else []  # descarta o candle em formação
        total += gravar_candles(conn, par, tf, fechados)
        _atualizar_deltas(conn, par, simbolo, tf, fechados)
    return total


def loop(conn, simbolos: dict) -> None:
    """Loop: a cada candle de gatilho (TF fino) fechado novo, coleta incremental.

    Fora do pregão da B3 não há candle novo — o loop apenas espera (log DEBUG), resiliente.
    """
    ultimo_visto = {par: None for par in simbolos}
    tf_gatilho = _tf_gatilho()
    log.info("Coletor B3 em loop. Símbolos: %s | gatilho=%s", ", ".join(simbolos), tf_gatilho)
    while not _parar:
        t0 = time.monotonic()
        try:
            houve_novo = False
            for par, simbolo in simbolos.items():
                fechado = _ultimo_fechado(simbolo, tf_gatilho)
                if fechado is None:
                    log.debug("%s: sem candles suficientes ainda (pregão fechado?).", par)
                    continue
                t_fechado = fechado["time"]
                if ultimo_visto[par] == t_fechado:
                    continue  # nada novo neste símbolo
                houve_novo = True
                novos = coletar_incremental(conn, par, simbolo)
                conn.commit()
                ultimo_visto[par] = t_fechado
                log.debug(
                    "%s: candle %s fechado %s | %d candles novos | spread=%s",
                    par, tf_gatilho,
                    datetime.utcfromtimestamp(t_fechado).strftime("%Y-%m-%d %H:%M"),
                    novos, fechado["spread"],
                )
            if not houve_novo:
                log.debug("B3: nenhum candle novo. Latência do ciclo: %.2fs", time.monotonic() - t0)
        except mt5_bridge_b3.MT5Erro as e:
            log.error("Erro de ponte MT5 (B3): %s — tentando de novo em %ss", e, config_b3.COLETOR_B3_POLL_S)
        except (EOFError, ConnectionError, OSError) as e:
            log.error("Conexão com a ponte MT5 B3 caiu (%s) — agendando reconexão", e)
            mt5_bridge_b3.reconectar()
        except Exception:  # noqa: BLE001 - loop resiliente
            log.exception("Erro inesperado no loop do coletor B3")
        for _ in range(config_b3.COLETOR_B3_POLL_S):  # espera interrompível
            if _parar:
                break
            time.sleep(1)
    log.info("Loop B3 encerrado.")


# --------------------------------------------------------------------------- #
# Entrada
# --------------------------------------------------------------------------- #
def resolver_simbolos() -> dict:
    """Mapeia cada símbolo lógico B3 (config_b3.PARES_B3) para o nome real no broker.

    Um símbolo ausente no broker é apenas PULADO com aviso (não derruba o coletor).
    """
    simbolos = {}
    for par in config_b3.PARES_B3:
        try:
            real = mt5_bridge_b3.resolver_simbolo(par)
        except mt5_bridge_b3.MT5Erro as e:
            log.warning("Símbolo B3 %s ignorado (não resolvido no broker): %s", par, e)
            continue
        simbolos[par] = real
        if real != par:
            log.info("Símbolo B3 %s resolvido para '%s'", par, real)
    return simbolos


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    if not config_b3.B3_HABILITADO:
        log.info("B3_HABILITADO=false — coletor B3 não vai iniciar.")
        return
    signal.signal(signal.SIGINT, _tratar_sinal)
    signal.signal(signal.SIGTERM, _tratar_sinal)

    db.init_db()
    resumo = mt5_bridge_b3.ping()
    log.info("Conectado ao MT5 B3 (Genial): login=%s servidor=%s saldo=%.2f %s",
             resumo["login"], resumo["servidor"], resumo["saldo"], resumo["moeda"])

    simbolos = resolver_simbolos()
    if not simbolos:
        log.error("Nenhum símbolo B3 resolvido — confira PARES_B3/ALIASES e a Market Watch da Genial.")
    with db.sessao() as conn:
        backfill(conn, simbolos)
        backfill_deltas(conn, simbolos)   # preenche o delta dos candles recentes (1×, valida o pipeline)
        loop(conn, simbolos)
    mt5_bridge_b3.desligar()


if __name__ == "__main__":
    main()
