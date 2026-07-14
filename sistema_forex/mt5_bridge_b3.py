"""Ponte para o 2º terminal MT5 (Genial) — SOMENTE LEITURA (feed da B3).

Espelha o padrão de `mt5_bridge.py`, mas:
  - aponta para o container `mt5_b3` (config_b3.MT5_B3_HOST/PORT), NÃO para o XM;
  - tem os PRÓPRIOS globais/lock (não compartilha estado com a ponte do forex, que segue
    intocada — princípio "tudo aditivo, nada alterado");
  - é DATA-ONLY de propósito: NÃO existe abrir/fechar/mover_sl aqui. A conta Genial é REAL
    e serve só como fonte de cotações; a ausência das funções de ordem torna impossível,
    por construção, enviar uma ordem por engano nesse terminal.

Regras herdadas (MASMC): LOCK GLOBAL (MT5 não é thread-safe); netref do numpy trazido de
uma vez com rpyc.classic.obtain(); RPyC fixado na mesma versão do servidor do Wine.
"""

import logging
import threading

from . import config_b3

log = logging.getLogger("mt5_bridge_b3")

# Lock próprio — serializa o acesso a ESTE terminal (independente do lock do forex).
_LOCK = threading.RLock()
_mt5 = None   # módulo remoto MetaTrader5 (proxy rpyc) do terminal Genial
_conn = None  # conexão rpyc.classic com o servidor da ponte (dentro do Wine da Genial)


class MT5Erro(RuntimeError):
    """Erro ao falar com a ponte MT5 da B3."""


# --------------------------------------------------------------------------- #
# Conexão
# --------------------------------------------------------------------------- #
def _cliente():
    """Módulo MetaTrader5 remoto do terminal Genial, inicializado na 1ª chamada."""
    global _mt5, _conn
    with _LOCK:
        if _mt5 is None:
            import rpyc  # local: só o cliente precisa

            log.info("Conectando à ponte MT5 (B3) em %s:%s",
                     config_b3.MT5_B3_HOST, config_b3.MT5_B3_PORT)
            try:
                _conn = rpyc.classic.connect(config_b3.MT5_B3_HOST, config_b3.MT5_B3_PORT)
            except Exception as e:  # noqa: BLE001
                _conn = None
                raise MT5Erro(f"não conectou ao servidor da ponte B3: {e}") from e
            _mt5 = _conn.modules["MetaTrader5"]
            if not _mt5.initialize():
                erro = _mt5.last_error()
                _mt5 = None
                try:
                    _conn.close()
                finally:
                    _conn = None
                raise MT5Erro(f"initialize() B3 falhou: {erro}")
            log.info("MT5 (B3/Genial) inicializado.")
        return _mt5


def timeframe(tf: str):
    """Converte 'M1'/'M5'/'M15'/'H1'/'D1'/'W1' na constante TIMEFRAME_* do MT5."""
    with _LOCK:
        mt5 = _cliente()
        mapa = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
            "W1": mt5.TIMEFRAME_W1,
        }
        if tf not in mapa:
            raise MT5Erro(f"Timeframe desconhecido: {tf}")
        return mapa[tf]


# --------------------------------------------------------------------------- #
# Símbolos
# --------------------------------------------------------------------------- #
def resolver_simbolo(par: str) -> str:
    """Confirma o nome real do símbolo B3 no broker e garante que está selecionado.

    Tenta o par + os aliases de config_b3 (WIN$N → WIN$, WINFUT, …). Levanta MT5Erro
    se nenhum candidato existir no broker (o coletor apenas PULA esse par, com aviso).
    """
    with _LOCK:
        mt5 = _cliente()
        for nome in config_b3.candidatos_simbolo(par):
            info = mt5.symbol_info(nome)
            if info is not None:
                if not info.visible:
                    mt5.symbol_select(nome, True)
                return nome
        raise MT5Erro(f"Símbolo B3 não encontrado no broker: {par}")


# --------------------------------------------------------------------------- #
# Candles (o que o coletor B3 precisa)
# --------------------------------------------------------------------------- #
def _para_dicts(rates) -> list:
    """Converte o array de rates (netref numpy do Wine) em lista de dicts."""
    import rpyc

    rates = rpyc.classic.obtain(rates)
    return [
        {
            "time": int(r["time"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "tick_volume": int(r["tick_volume"]),
            "spread": int(r["spread"]),
        }
        for r in rates
    ]


def copy_rates_from_pos(simbolo: str, tf: str, pos: int, quantidade: int):
    """Últimos `quantidade` candles a partir da posição `pos` (0 = mais recente)."""
    with _LOCK:
        mt5 = _cliente()
        rates = mt5.copy_rates_from_pos(simbolo, timeframe(tf), pos, quantidade)
        if rates is None:
            return []
        return _para_dicts(rates)


def copy_rates_range(simbolo: str, tf: str, inicio, fim):
    """copy_rates_range convertido para lista de dicts."""
    with _LOCK:
        mt5 = _cliente()
        rates = mt5.copy_rates_range(simbolo, timeframe(tf), inicio, fim)
        if rates is None:
            return []
        return _para_dicts(rates)


def tick_atual(simbolo: str):
    """Tick atual (bid/ask/time) do símbolo B3. None se indisponível."""
    with _LOCK:
        mt5 = _cliente()
        t = mt5.symbol_info_tick(simbolo)
        if t is None:
            return None
        return {"time": int(t.time), "bid": float(t.bid), "ask": float(t.ask)}


def info_simbolo(simbolo: str):
    """Especificações de contrato do símbolo B3 (SÓ LEITURA — a verdade da escala).

    Devolve tick de preço (`trade_tick_size`), valor em BRL do tick (`trade_tick_value`) e,
    derivado, o valor-por-ponto (`valor_ponto` = tick_value/tick_size). É a fonte confiável
    para o P&L em reais e para conferir o `tamanho_pip` que a calibração deriva dos candles.
    None se o símbolo não existir. Continua data-only: apenas LÊ o contrato, nunca opera.
    """
    with _LOCK:
        mt5 = _cliente()
        info = mt5.symbol_info(simbolo)
        if info is None:
            return None
        tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
        tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
        return {
            "nome": str(getattr(info, "name", simbolo)),
            "point": float(getattr(info, "point", 0.0) or 0.0),
            "digits": int(getattr(info, "digits", 0) or 0),
            "trade_tick_size": tick_size,
            "trade_tick_value": tick_value,
            "trade_contract_size": float(getattr(info, "trade_contract_size", 0.0) or 0.0),
            "valor_ponto": (tick_value / tick_size) if tick_size else None,
        }


# --------------------------------------------------------------------------- #
# Saúde
# --------------------------------------------------------------------------- #
def ping() -> dict:
    """Verifica a conexão ao terminal Genial e devolve um resumo da conta."""
    with _LOCK:
        mt5 = _cliente()
        conta = mt5.account_info()
        if conta is None:
            raise MT5Erro("account_info() (B3) retornou None — terminal logado?")
        term = mt5.terminal_info()
        return {
            "login": int(getattr(conta, "login", 0)),
            "servidor": str(getattr(conta, "server", "")),
            "saldo": float(getattr(conta, "balance", 0.0)),
            "moeda": str(getattr(conta, "currency", "")),
            "conectado": bool(getattr(term, "connected", False)) if term else False,
        }


def esta_ok() -> bool:
    """True se a ponte B3 responde. Não levanta exceção (uso em healthcheck)."""
    try:
        ping()
        return True
    except Exception as e:  # noqa: BLE001 - health check tolerante
        log.debug("esta_ok() (B3) falhou: %s", e)
        return False


def desligar() -> None:
    """Fecha a conexão (shutdown do MT5 e da conexão rpyc) do terminal B3."""
    global _mt5, _conn
    with _LOCK:
        if _mt5 is not None:
            try:
                _mt5.shutdown()
            except Exception:  # noqa: BLE001
                pass
            _mt5 = None
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
            _conn = None
