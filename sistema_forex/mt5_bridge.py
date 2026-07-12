"""Ponte para o MetaTrader 5.

O terminal MT5 roda sob Wine dentro do container "mt5" (imagem gmag11/MetaTrader5-Docker),
que expõe a API Python via RPyC na porta 8001. Aqui usamos o cliente `mt5linux`, cuja
interface é idêntica à do pacote oficial `MetaTrader5`.

Regras inegociáveis (lições do MASMC) implementadas já na fundação:
- LOCK GLOBAL: MT5 não é thread-safe. TODA chamada passa pelo lock.
- verificar_margem() antes de qualquer order_send (retcode 10019 destruiu o teste anterior).
- preco_pip() usa price_open/price_current do deal — nunca diferença entre posições.
"""

import logging
import threading

from . import config

log = logging.getLogger("mt5_bridge")

# Lock global — serializa todo acesso ao MT5 (thread-safe).
_LOCK = threading.RLock()
_mt5 = None  # cliente mt5linux (singleton, criado sob demanda)


class MT5Erro(RuntimeError):
    """Erro ao falar com a ponte MT5."""


# --------------------------------------------------------------------------- #
# Conexão
# --------------------------------------------------------------------------- #
def _cliente():
    """Retorna o cliente mt5linux conectado (cria e inicializa na 1ª chamada)."""
    global _mt5
    with _LOCK:
        if _mt5 is None:
            # pymt5linux é o fork mantido; mt5linux fica como fallback. Mesma API.
            try:
                from pymt5linux import MetaTrader5
            except ImportError:
                try:
                    from mt5linux import MetaTrader5
                except ImportError as e:  # pragma: no cover - só falta em dev sem deps
                    raise MT5Erro(
                        "pymt5linux/mt5linux não instalado. Rode dentro do container "
                        "ou instale requirements.txt."
                    ) from e
            log.info("Conectando à ponte MT5 em %s:%s", config.MT5_HOST, config.MT5_PORT)
            _mt5 = MetaTrader5(host=config.MT5_HOST, port=config.MT5_PORT)
            if not _mt5.initialize():
                erro = _mt5.last_error()
                _mt5 = None
                raise MT5Erro(f"initialize() falhou: {erro}")
            log.info("MT5 inicializado.")
        return _mt5


def chamar(nome, *args, **kwargs):
    """Executa uma função arbitrária do MT5 sob o lock global.

    Ex.: chamar('symbol_info_tick', 'EURUSD#').
    Centraliza o lock para que qualquer código do sistema jamais fale com o MT5
    fora dele.
    """
    with _LOCK:
        mt5 = _cliente()
        fn = getattr(mt5, nome)
        return fn(*args, **kwargs)


def timeframe(tf: str):
    """Converte 'M5'/'M15'/'H1'/'D1' na constante TIMEFRAME_* do MT5."""
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
        }
        if tf not in mapa:
            raise MT5Erro(f"Timeframe desconhecido: {tf}")
        return mapa[tf]


# --------------------------------------------------------------------------- #
# Símbolos
# --------------------------------------------------------------------------- #
def resolver_simbolo(par: str) -> str:
    """Confirma o nome real do símbolo no broker e garante que está selecionado.

    XM usa sufixo '#' na maioria, mas nem todos. Se o par exato não existir,
    tenta sem/com sufixo antes de desistir (lição do doc: confirmar via symbols_get).
    """
    with _LOCK:
        mt5 = _cliente()
        candidatos = [par, par.rstrip("#"), par + config.SUFIXO_PADRAO]
        vistos = set()
        for nome in candidatos:
            if nome in vistos:
                continue
            vistos.add(nome)
            info = mt5.symbol_info(nome)
            if info is not None:
                if not info.visible:
                    mt5.symbol_select(nome, True)
                return nome
        raise MT5Erro(f"Símbolo não encontrado no broker: {par}")


# --------------------------------------------------------------------------- #
# Contabilidade de pips (lição MASMC: cálculo quebrado invalidou o diagnóstico)
# --------------------------------------------------------------------------- #
def tamanho_pip(simbolo: str) -> float:
    """Valor de 1 pip em preço para o símbolo.

    Pip = 10 * point para pares de 5/3 casas (padrão), = point para 4/2 casas.
    """
    with _LOCK:
        mt5 = _cliente()
        info = mt5.symbol_info(simbolo)
        if info is None:
            raise MT5Erro(f"symbol_info({simbolo}) retornou None")
        point = info.point
        digits = info.digits
        return point * (10 if digits in (3, 5) else 1)


def preco_pip(simbolo: str, preco_entrada: float, preco_atual: float, eh_compra: bool) -> float:
    """Pips entre entrada e preço atual, com sinal (positivo = a favor).

    Usa preços do deal (price_open / price_current), NUNCA diferença entre posições.
    """
    pip = tamanho_pip(simbolo)
    delta = (preco_atual - preco_entrada) if eh_compra else (preco_entrada - preco_atual)
    return delta / pip


# --------------------------------------------------------------------------- #
# Margem (lição MASMC: 96% das ordens falharam por margem — retcode 10019)
# --------------------------------------------------------------------------- #
def verificar_margem(simbolo: str, lote: float, eh_compra: bool) -> bool:
    """True se há margem livre suficiente para abrir a ordem.

    Usa order_calc_margin + account_info.margin_free. Deve ser chamado SEMPRE
    antes de order_send.
    """
    with _LOCK:
        mt5 = _cliente()
        tipo = mt5.ORDER_TYPE_BUY if eh_compra else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(simbolo)
        if tick is None:
            raise MT5Erro(f"symbol_info_tick({simbolo}) retornou None")
        preco = tick.ask if eh_compra else tick.bid
        margem = mt5.order_calc_margin(tipo, simbolo, lote, preco)
        if margem is None:
            log.warning("order_calc_margin retornou None para %s", simbolo)
            return False
        conta = mt5.account_info()
        if conta is None:
            raise MT5Erro("account_info() retornou None")
        livre = conta.margin_free
        ok = livre >= margem
        if not ok:
            log.warning(
                "Margem insuficiente %s: necessária %.2f, livre %.2f", simbolo, margem, livre
            )
        return ok


# --------------------------------------------------------------------------- #
# Candles
# --------------------------------------------------------------------------- #
def copy_rates_range(simbolo: str, tf: str, inicio, fim):
    """copy_rates_range convertido para lista de dicts (evita netref numpy do RPyC).

    Cada item: {time, open, high, low, close, tick_volume, spread, real_volume}.
    """
    with _LOCK:
        mt5 = _cliente()
        rates = mt5.copy_rates_range(simbolo, timeframe(tf), inicio, fim)
        if rates is None:
            return []
        # rates é um numpy structured array (possivelmente via netref). Materializa.
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


def tick_atual(simbolo: str):
    """Retorna dict com o tick atual (bid, ask, last, time, spread aproximado)."""
    with _LOCK:
        mt5 = _cliente()
        t = mt5.symbol_info_tick(simbolo)
        if t is None:
            return None
        return {"time": int(t.time), "bid": float(t.bid), "ask": float(t.ask)}


# --------------------------------------------------------------------------- #
# Saúde
# --------------------------------------------------------------------------- #
def ping() -> dict:
    """Verifica a conexão e devolve um resumo da conta (para health/painel)."""
    with _LOCK:
        mt5 = _cliente()
        conta = mt5.account_info()
        if conta is None:
            raise MT5Erro("account_info() retornou None — terminal logado?")
        term = mt5.terminal_info()
        return {
            "login": int(getattr(conta, "login", 0)),
            "servidor": str(getattr(conta, "server", "")),
            "saldo": float(getattr(conta, "balance", 0.0)),
            "moeda": str(getattr(conta, "currency", "")),
            "margem_livre": float(getattr(conta, "margin_free", 0.0)),
            "conectado": bool(getattr(term, "connected", False)) if term else False,
            "trade_allowed": bool(getattr(term, "trade_allowed", False)) if term else False,
        }


def esta_ok() -> bool:
    """True se a ponte responde. Não levanta exceção (uso em healthcheck)."""
    try:
        ping()
        return True
    except Exception as e:  # noqa: BLE001 - health check tolerante
        log.debug("esta_ok() falhou: %s", e)
        return False


def desligar() -> None:
    """Fecha a conexão (shutdown do MT5)."""
    global _mt5
    with _LOCK:
        if _mt5 is not None:
            try:
                _mt5.shutdown()
            except Exception:  # noqa: BLE001
                pass
            _mt5 = None
