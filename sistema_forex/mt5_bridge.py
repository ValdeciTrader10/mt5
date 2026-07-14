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
_mt5 = None   # módulo remoto MetaTrader5 (proxy rpyc), singleton criado sob demanda
_conn = None  # conexão rpyc.classic com o servidor da ponte (dentro do Wine)


class MT5Erro(RuntimeError):
    """Erro ao falar com a ponte MT5."""


# --------------------------------------------------------------------------- #
# Conexão
# --------------------------------------------------------------------------- #
def _cliente():
    """Retorna o módulo MetaTrader5 remoto (proxy rpyc), inicializado na 1ª chamada.

    Falamos direto com o servidor RPyC clássico que roda dentro do container "mt5"
    (Python do Wine, com o pacote MetaTrader5). Usamos rpyc.classic para não depender
    de mt5linux/pymt5linux no cliente e poder fixar a MESMA versão de RPyC do servidor.
    """
    global _mt5, _conn
    with _LOCK:
        if _mt5 is None:
            import rpyc  # local: só o cliente precisa

            log.info("Conectando à ponte MT5 em %s:%s", config.MT5_HOST, config.MT5_PORT)
            try:
                _conn = rpyc.classic.connect(config.MT5_HOST, config.MT5_PORT)
            except Exception as e:  # noqa: BLE001
                _conn = None
                raise MT5Erro(f"não conectou ao servidor da ponte: {e}") from e
            _mt5 = _conn.modules["MetaTrader5"]
            if not _mt5.initialize():
                erro = _mt5.last_error()
                _mt5 = None
                try:
                    _conn.close()
                finally:
                    _conn = None
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
            "W1": mt5.TIMEFRAME_W1,
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
        bases = [par] + config.ALIASES_SIMBOLO.get(par, [])
        candidatos = []
        for b in bases:                        # cada base, com e sem o sufixo "#"
            candidatos += [b, b.rstrip("#"), b + config.SUFIXO_PADRAO]
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
    import rpyc

    with _LOCK:
        mt5 = _cliente()
        rates = mt5.copy_rates_range(simbolo, timeframe(tf), inicio, fim)
        if rates is None:
            return []
        # rates vem como netref (numpy no lado do Wine). obtain() traz o array inteiro
        # de uma vez para o cliente (rápido); iterar o netref direto seria lento.
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
    import rpyc

    with _LOCK:
        mt5 = _cliente()
        rates = mt5.copy_rates_from_pos(simbolo, timeframe(tf), pos, quantidade)
        if rates is None:
            return []
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


# --------------------------------------------------------------------------- #
# Ordens (Fase 5) — encapsulam as constantes remotas do MT5 aqui, num só lugar
# --------------------------------------------------------------------------- #
def posicoes(simbolo: str = None, magic: int = None) -> list:
    """Posições abertas como lista de dicts (ticket, simbolo, tipo, volume, preço, sl, lucro)."""
    with _LOCK:
        mt5 = _cliente()
        pos = mt5.positions_get(symbol=simbolo) if simbolo else mt5.positions_get()
        if pos is None:
            return []
        out = []
        for p in pos:
            if magic is not None and int(getattr(p, "magic", 0)) != magic:
                continue
            out.append({
                "ticket": int(p.ticket),
                "simbolo": str(p.symbol),
                "direcao": "compra" if int(p.type) == int(mt5.POSITION_TYPE_BUY) else "venda",
                "volume": float(p.volume),
                "preco_entrada": float(p.price_open),
                "preco_atual": float(p.price_current),
                "sl": float(p.sl),
                "lucro": float(p.profit),
                "abertura_utc": int(p.time),
            })
        return out


def preco_fill(simbolo: str, ticket: int, magic: int = None):
    """Preço de execução REAL (price_open do deal) da posição recém-aberta, para medir a
    derrapagem contra o preço que a sombra assumiu. None se a posição não for encontrada."""
    for p in posicoes(simbolo=simbolo, magic=magic):
        if p["ticket"] == int(ticket):
            return p["preco_entrada"]
    return None


def _filling(mt5, simbolo):
    """Modo de preenchimento suportado pelo símbolo (varia por broker)."""
    try:
        info = mt5.symbol_info(simbolo)
        modo = int(getattr(info, "filling_mode", 0))
        # bit 1 = FOK, bit 2 = IOC (SYMBOL_FILLING_*)
        if modo & 2:
            return mt5.ORDER_FILLING_IOC
        if modo & 1:
            return mt5.ORDER_FILLING_FOK
    except Exception:  # noqa: BLE001
        pass
    return mt5.ORDER_FILLING_IOC


def abrir(simbolo: str, direcao: str, lote: float, sl: float, magic: int, comentario: str = "") -> int:
    """Envia ordem a mercado com stop no servidor. Retorna o ticket. Levanta MT5Erro em falha."""
    with _LOCK:
        mt5 = _cliente()
        tick = mt5.symbol_info_tick(simbolo)
        if tick is None:
            raise MT5Erro(f"symbol_info_tick({simbolo}) None ao abrir")
        eh_compra = direcao == "compra"
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": simbolo,
            "volume": float(lote),
            "type": mt5.ORDER_TYPE_BUY if eh_compra else mt5.ORDER_TYPE_SELL,
            "price": float(tick.ask if eh_compra else tick.bid),
            "sl": float(sl),
            "deviation": 20,
            "magic": int(magic),
            "comment": comentario[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _filling(mt5, simbolo),
        }
        res = mt5.order_send(req)
        if res is None:
            raise MT5Erro(f"order_send retornou None: {mt5.last_error()}")
        if int(res.retcode) != int(mt5.TRADE_RETCODE_DONE):
            raise MT5Erro(f"order_send retcode {res.retcode} ({getattr(res, 'comment', '')})")
        return int(res.order)


def fechar(ticket: int, magic: int) -> None:
    """Fecha a posição do ticket com uma ordem a mercado contrária."""
    with _LOCK:
        mt5 = _cliente()
        pos = mt5.positions_get(ticket=ticket)
        if not pos:
            return  # já fechada
        p = pos[0]
        simbolo = str(p.symbol)
        eh_compra = int(p.type) == int(mt5.POSITION_TYPE_BUY)
        tick = mt5.symbol_info_tick(simbolo)
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": simbolo,
            "volume": float(p.volume),
            "type": mt5.ORDER_TYPE_SELL if eh_compra else mt5.ORDER_TYPE_BUY,
            "position": int(ticket),
            "price": float(tick.bid if eh_compra else tick.ask),
            "deviation": 20,
            "magic": int(magic),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _filling(mt5, simbolo),
        }
        res = mt5.order_send(req)
        if res is None or int(res.retcode) != int(mt5.TRADE_RETCODE_DONE):
            raise MT5Erro(f"fechar({ticket}) falhou: {getattr(res, 'retcode', 'None')}")


def mover_sl(ticket: int, novo_sl: float) -> None:
    """Modifica o stop da posição (usado no break-even)."""
    with _LOCK:
        mt5 = _cliente()
        pos = mt5.positions_get(ticket=ticket)
        if not pos:
            return
        p = pos[0]
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": str(p.symbol),
            "position": int(ticket),
            "sl": float(novo_sl),
            "tp": float(p.tp),
        }
        res = mt5.order_send(req)
        if res is None or int(res.retcode) != int(mt5.TRADE_RETCODE_DONE):
            raise MT5Erro(f"mover_sl({ticket}) falhou: {getattr(res, 'retcode', 'None')}")


def calc_lucro(direcao: str, simbolo: str, lote: float, entrada: float, saida: float):
    """Lucro em USD via order_calc_profit (read-only, funciona sem Algo Trading). None se indisponível."""
    with _LOCK:
        mt5 = _cliente()
        tipo = mt5.ORDER_TYPE_BUY if direcao == "compra" else mt5.ORDER_TYPE_SELL
        try:
            v = mt5.order_calc_profit(tipo, simbolo, float(lote), float(entrada), float(saida))
            return float(v) if v is not None else None
        except Exception:  # noqa: BLE001
            return None


def equity():
    """Equity atual da conta (para o teto de drawdown diário). None se indisponível."""
    with _LOCK:
        mt5 = _cliente()
        conta = mt5.account_info()
        return float(conta.equity) if conta else None


def tick_atual(simbolo: str):
    """Retorna dict com o tick atual (bid, ask, time). None se indisponível OU inválido.

    Rejeita cotação-fantasma (bid/ask ≤ 0 ou cruzada) — no forex é raro (24/5), mas o GOLD
    fora do pregão/fim de semana pode devolver 0, e um 0 como preço de saída de uma VENDA
    registraria lucro absurdo na gestão de sombra. Cotação inválida = ausência de preço.
    """
    with _LOCK:
        mt5 = _cliente()
        t = mt5.symbol_info_tick(simbolo)
        if t is None:
            return None
        bid, ask = float(t.bid), float(t.ask)
        if not (bid > 0 and ask > 0 and ask >= bid):
            log.debug("Tick inválido de %s (bid=%s ask=%s) — ignorado.", simbolo, bid, ask)
            return None
        return {"time": int(t.time), "bid": bid, "ask": ask}


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
    """Fecha a conexão (shutdown do MT5 e da conexão rpyc)."""
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
