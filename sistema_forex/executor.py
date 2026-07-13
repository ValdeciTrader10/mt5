"""Fase 5 — Executor + gestor de saída.

Consome as decisões "entrou" do estrategista, abre posições e as gerencia TICK A TICK
(saída por força contrária, break-even, tempo, reversão), fechando quando a gestão manda.

TRAVA DE SEGURANÇA (`config.EXECUCAO_ATIVA`):
  - false (padrão): SIMULAÇÃO sobre preço AO VIVO. Abre/gerencia/fecha posições virtuais
    usando os ticks reais (só leitura do MT5); NENHUMA ordem é enviada. Serve para ver o
    comportamento de saída em tempo real sem risco.
  - true: envia e gerencia ordens de verdade na conta demo (exige Algo Trading no terminal).

Regras inegociáveis já embutidas: stop de emergência no servidor em toda ordem, margem
verificada antes de abrir, contabilidade de pips por price_open/price_current, lock global
(dentro da ponte), teto de drawdown diário.

    python -m sistema_forex.executor
"""

import logging
import signal
import time
from datetime import datetime, timezone

from . import analise, config, db, gestao, indicadores, mt5_bridge, telegram_notif

log = logging.getLogger("executor")

_parar = False


def _tratar_sinal(signum, frame):  # pragma: no cover
    global _parar
    log.info("Sinal %s recebido — encerrando após o ciclo atual.", signum)
    _parar = True


def _agora() -> int:
    return int(time.time())


# --------------------------------------------------------------------------- #
# Leituras auxiliares do banco
# --------------------------------------------------------------------------- #
def _atr_m5(conn, par: str):
    rows = conn.execute(
        "SELECT high, low, close FROM candles WHERE par=? AND tf='M5' ORDER BY time_utc DESC LIMIT ?",
        (par, config.ATR_PERIODO * 4),
    ).fetchall()
    rows = list(reversed(rows))
    if len(rows) < config.ATR_PERIODO + 1:
        return None
    return indicadores.atr([r["high"] for r in rows], [r["low"] for r in rows],
                           [r["close"] for r in rows], config.ATR_PERIODO)


def _evento_saida(conn, par: str):
    """Último evento SMC nos TFs de ESTRUTURA de saída (M5 é ruído — fica de fora).

    A estrutura de trade é M15/H1 (config.SAIDA_ESTRUTURA_TFS); eventos de M5 não
    disparam saída, para não fechar a cada BOS de 1 candle.
    """
    tfs = config.SAIDA_ESTRUTURA_TFS or ["M15", "H1"]
    marcadores = ",".join("?" for _ in tfs)
    r = conn.execute(
        f"SELECT evento, direcao, tf FROM estrutura WHERE par=? AND tf IN ({marcadores}) "
        "ORDER BY time_utc DESC LIMIT 1",
        (par, *tfs),
    ).fetchone()
    return {"evento": r["evento"], "direcao": r["direcao"], "tf": r["tf"]} if r else None


def _espaco_r(conn, par: str, direcao: str, preco: float, risco: float):
    """Espaço (em múltiplos de R) até o nível contrário mais próximo À FRENTE do preço.

    Compra → resistência acima; venda → suporte abaixo. Sem nível à frente → None
    (campo aberto; a gestão trata como "muito espaço"). É o que permite dar ao preço a
    chance de desenvolver antes de sair por um sinal contrário fraco.
    """
    if not risco:
        return None
    alvo = None
    for nv in analise.niveis_ativos(conn, par):
        p = nv.get("preco")
        if p is None:
            continue
        if direcao == "compra" and nv["tipo"] == "resistencia" and p > preco:
            alvo = p if alvo is None else min(alvo, p)
        elif direcao == "venda" and nv["tipo"] == "suporte" and p < preco:
            alvo = p if alvo is None else max(alvo, p)
    if alvo is None:
        return None
    return abs(alvo - preco) / risco


def _decisoes_novas(conn, desde_id: int):
    return conn.execute(
        "SELECT id, par, direcao, estrategia FROM decisoes WHERE id > ? AND resultado='entrou' ORDER BY id",
        (desde_id,),
    ).fetchall()


def _abertas_do_banco(conn):
    return conn.execute(
        "SELECT id, ticket, par, estrategia, direcao, lote, preco_entrada, sl_servidor, abertura_utc, "
        "simulado, risco_inicial, mae_r, mfe_r FROM trades WHERE fechamento_utc IS NULL"
    ).fetchall()


# --------------------------------------------------------------------------- #
# Persistência de trades
# --------------------------------------------------------------------------- #
def _abrir_trade(conn, par, estrategia, direcao, lote, entrada, sl, ticket, simulado, risco) -> int:
    cur = conn.execute(
        "INSERT INTO trades (ticket, par, estrategia, direcao, lote, preco_entrada, sl_servidor, "
        "abertura_utc, simulado, risco_inicial) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ticket, par, estrategia, direcao, lote, entrada, sl, _agora(), simulado, risco),
    )
    conn.commit()
    return cur.lastrowid


def _fechar_trade(conn, trade_id, saida, pips, lucro, motivo, mae_r=None, mfe_r=None) -> None:
    conn.execute(
        "UPDATE trades SET preco_saida=?, pips=?, lucro_usd=?, motivo_saida=?, fechamento_utc=?, "
        "mae_r=?, mfe_r=? WHERE id=?",
        (saida, pips, lucro, motivo, _agora(), mae_r, mfe_r, trade_id),
    )
    conn.commit()


def _persistir_excursao(conn, trade_id, mae_r, mfe_r) -> None:
    """Grava MAE/MFE na linha aberta (só em novo extremo — barato e resiliente a restart)."""
    conn.execute("UPDATE trades SET mae_r=?, mfe_r=? WHERE id=?", (mae_r, mfe_r, trade_id))
    conn.commit()


def _atualizar_sl(conn, trade_id, sl) -> None:
    conn.execute("UPDATE trades SET sl_servidor=? WHERE id=?", (sl, trade_id))
    conn.commit()


# --------------------------------------------------------------------------- #
# Executor
# --------------------------------------------------------------------------- #
class Executor:
    def __init__(self):
        self.ativa = config.EXECUCAO_ATIVA
        self.abertas = {}          # trade_id -> estado da posição
        self.simbolos = {}         # par -> símbolo real
        self.ultima_decisao_id = 0
        self.saldo_inicial_dia = None
        self.dia = None
        self.dd_avisado = False

    # -- infraestrutura --
    def _simbolo(self, par: str) -> str:
        if par not in self.simbolos:
            self.simbolos[par] = mt5_bridge.resolver_simbolo(par)
        return self.simbolos[par]

    def _pip(self, simbolo: str) -> float:
        try:
            return mt5_bridge.tamanho_pip(simbolo)
        except Exception:  # noqa: BLE001
            return 0.0001

    def _preco_saida(self, simbolo, direcao):
        """Preço para VALORAR/fechar: bid p/ compra, ask p/ venda."""
        t = mt5_bridge.tick_atual(simbolo)
        if not t:
            return None
        return t["bid"] if direcao == "compra" else t["ask"]

    def _preco_entrada(self, simbolo, direcao):
        t = mt5_bridge.tick_atual(simbolo)
        if not t:
            return None
        return t["ask"] if direcao == "compra" else t["bid"]

    # -- carga inicial --
    def carregar(self, conn):
        for r in _abertas_do_banco(conn):
            if self.ativa and r["simulado"]:
                continue  # em modo real, ignora resíduos simulados
            entrada, sl = r["preco_entrada"], r["sl_servidor"]
            be = (r["direcao"] == "compra" and sl >= entrada) or (r["direcao"] == "venda" and sl <= entrada)
            risco = r["risco_inicial"] or abs(entrada - sl)
            self.abertas[r["id"]] = {
                "trade_id": r["id"], "ticket": r["ticket"], "par": r["par"],
                "simbolo": self._simbolo(r["par"]), "estrategia": r["estrategia"],
                "direcao": r["direcao"], "lote": r["lote"], "preco_entrada": entrada,
                "sl": sl, "risco": risco, "abertura_utc": r["abertura_utc"],
                "r_max": r["mfe_r"] or 0.0, "be_movido": be,
                "mae_r": r["mae_r"] or 0.0, "mfe_r": r["mfe_r"] or 0.0,
            }
        mx = conn.execute("SELECT MAX(id) m FROM decisoes").fetchone()["m"]
        self.ultima_decisao_id = mx or 0
        log.info("Executor iniciado (%s). Posições abertas retomadas: %d",
                 "EXECUÇÃO ATIVA" if self.ativa else "SIMULAÇÃO (preço ao vivo)", len(self.abertas))

    # -- drawdown --
    def _equity(self, conn):
        if self.ativa:
            return mt5_bridge.equity()
        # simulado: saldo base + realizado no dia + não-realizado das posições sim
        hoje0 = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        realizado = conn.execute(
            "SELECT COALESCE(SUM(lucro_usd),0) s FROM trades WHERE simulado=1 AND fechamento_utc>=?", (hoje0,)
        ).fetchone()["s"]
        nao_real = 0.0
        for p in self.abertas.values():
            preco = self._preco_saida(p["simbolo"], p["direcao"])
            if preco is not None:
                v = mt5_bridge.calc_lucro(p["direcao"], p["simbolo"], p["lote"], p["preco_entrada"], preco)
                nao_real += v or 0.0
        return config.SALDO_SIMULADO + realizado + nao_real

    def _checar_dia(self, conn):
        dia = datetime.now(timezone.utc).date().isoformat()
        if dia != self.dia:
            self.dia = dia
            self.saldo_inicial_dia = self._equity(conn) or config.SALDO_SIMULADO
            self.dd_avisado = False
            log.info("Novo dia %s — saldo inicial de referência: %.2f", dia, self.saldo_inicial_dia)

    def _dd_ok(self, conn) -> bool:
        eq = self._equity(conn)
        if eq is None:
            return True
        estourou = gestao.drawdown_estourou(self.saldo_inicial_dia, eq, config.DD_DIARIO_MAX_PCT)
        if estourou and not self.dd_avisado:
            self.dd_avisado = True
            msg = f"⛔ Drawdown diário atingido ({config.DD_DIARIO_MAX_PCT}%). Sem novas entradas hoje."
            log.warning(msg)
            telegram_notif.enviar(msg, chave_antispam="dd_dia")
        return not estourou

    def _reconciliar(self, conn):
        """Modo real: detecta posições que o BROKER fechou (SL no servidor/manual)."""
        try:
            vivos = {p["ticket"] for p in mt5_bridge.posicoes(magic=config.MAGIC)}
        except Exception:  # noqa: BLE001
            return
        for trade_id in list(self.abertas):
            p = self.abertas[trade_id]
            if p["ticket"] not in vivos:
                preco = self._preco_saida(p["simbolo"], p["direcao"]) or p["preco_entrada"]
                pip = self._pip(p["simbolo"])
                pips = gestao.pips(p["direcao"], p["preco_entrada"], preco, pip)
                lucro = mt5_bridge.calc_lucro(p["direcao"], p["simbolo"], p["lote"], p["preco_entrada"], preco)
                _fechar_trade(conn, trade_id, preco, round(pips, 1), lucro, "fechada no servidor (SL/manual)",
                              round(p["mae_r"], 3), round(p["mfe_r"], 3))
                del self.abertas[trade_id]
                log.info("↩ %s fechada no servidor (reconciliação)", p["par"])

    # -- gestão das posições abertas (tick-speed) --
    def gerir(self, conn):
        if self.ativa:
            self._reconciliar(conn)
        for trade_id in list(self.abertas):
            p = self.abertas[trade_id]
            preco = self._preco_saida(p["simbolo"], p["direcao"])
            if preco is None:
                continue
            pip = self._pip(p["simbolo"])

            # Stop de emergência: no modo real o broker cuida; na simulação, garantimos aqui.
            if not self.ativa:
                bateu = (p["direcao"] == "compra" and preco <= p["sl"]) or \
                        (p["direcao"] == "venda" and preco >= p["sl"])
                if bateu:
                    self._fechar(conn, p, p["sl"], pip, "stop no servidor")
                    continue

            r = gestao.r_por_risco(p["direcao"], p["preco_entrada"], preco, p["risco"])
            p["r_max"] = max(p["r_max"], r)
            # MAE/MFE: pior R contra e melhor R a favor durante a vida da posição.
            novo_mfe = max(p["mfe_r"], r)
            novo_mae = min(p["mae_r"], r)
            if novo_mfe != p["mfe_r"] or novo_mae != p["mae_r"]:
                p["mfe_r"], p["mae_r"] = novo_mfe, novo_mae
                _persistir_excursao(conn, p["trade_id"], round(p["mae_r"], 3), round(p["mfe_r"], 3))
            idade_h = (_agora() - p["abertura_utc"]) / 3600
            espaco_r = _espaco_r(conn, p["par"], p["direcao"], preco, p["risco"])
            acao, motivo = gestao.avaliar_saida(
                direcao=p["direcao"], r=r, r_max=p["r_max"], idade_h=idade_h,
                ultimo_evento=_evento_saida(conn, p["par"]), be_movido=p["be_movido"],
                be_trigger_r=config.BE_TRIGGER_R, giveback_r=config.GIVEBACK_R,
                tempo_max_h=config.TEMPO_MAX_POSICAO_H,
                espaco_r=espaco_r, estrut_min_r=config.SAIDA_ESTRUTURA_MIN_R,
                espaco_segurar_r=config.SAIDA_ESPACO_SEGURAR_R,
            )
            if acao == "fechar":
                self._fechar(conn, p, preco, pip, motivo)
            elif acao == "mover_be":
                self._mover_be(conn, p)

    def _fechar(self, conn, p, preco, pip, motivo):
        if self.ativa:
            try:
                mt5_bridge.fechar(p["ticket"], config.MAGIC)
            except Exception:  # noqa: BLE001
                log.exception("Falha ao fechar %s no MT5 — mantém aberta p/ retry", p["par"])
                return
        pips = gestao.pips(p["direcao"], p["preco_entrada"], preco, pip)
        lucro = mt5_bridge.calc_lucro(p["direcao"], p["simbolo"], p["lote"], p["preco_entrada"], preco)
        _fechar_trade(conn, p["trade_id"], preco, round(pips, 1), lucro, motivo,
                      round(p["mae_r"], 3), round(p["mfe_r"], 3))
        del self.abertas[p["trade_id"]]
        tag = "" if self.ativa else " [sim]"
        msg = f"🔚{tag} {p['par']} {p['direcao']} fechada: {pips:+.1f} pips | {motivo}"
        log.info(msg)
        telegram_notif.enviar(msg)

    def _mover_be(self, conn, p):
        p["sl"] = p["preco_entrada"]
        p["be_movido"] = True
        if self.ativa:
            try:
                mt5_bridge.mover_sl(p["ticket"], p["preco_entrada"])
            except Exception:  # noqa: BLE001
                log.exception("Falha ao mover SL p/ BE em %s", p["par"])
        _atualizar_sl(conn, p["trade_id"], p["preco_entrada"])
        log.info("↔ %s %s → break-even", p["par"], p["direcao"])

    # -- novas entradas --
    def entrar(self, conn):
        novas = _decisoes_novas(conn, self.ultima_decisao_id)
        for d in novas:
            self.ultima_decisao_id = max(self.ultima_decisao_id, d["id"])
            par, direcao = d["par"], d["direcao"]
            if len(self.abertas) >= config.MAX_POS_TOTAL:
                continue
            if sum(1 for p in self.abertas.values() if p["par"] == par) >= config.MAX_POS_POR_PAR:
                continue
            try:
                self._abrir(conn, par, direcao, d["estrategia"])
            except Exception:  # noqa: BLE001
                log.exception("Falha ao abrir %s %s", par, direcao)

    def _abrir(self, conn, par, direcao, estrategia):
        simbolo = self._simbolo(par)
        pip = self._pip(simbolo)
        atr = _atr_m5(conn, par)
        entrada = self._preco_entrada(simbolo, direcao)
        if entrada is None:
            return
        sl = gestao.calcular_sl(direcao, entrada, atr, mult=config.SL_SERVIDOR_ATR_MULT,
                                min_pips=config.SL_MIN_PIPS, max_pips=config.SL_MAX_PIPS, pip=pip)
        risco = abs(entrada - sl)
        if self.ativa:
            if not mt5_bridge.verificar_margem(simbolo, config.LOTE, direcao == "compra"):
                log.warning("Sem margem p/ %s %s — pulando", par, direcao)
                return
            ticket = mt5_bridge.abrir(simbolo, direcao, config.LOTE, sl, config.MAGIC, estrategia)
            simulado = 0
        else:
            ticket = None
            simulado = 1
        trade_id = _abrir_trade(conn, par, estrategia, direcao, config.LOTE, entrada, sl,
                                ticket, simulado, risco)
        if ticket is None:
            ticket = -trade_id  # id sintético para o modo simulação
            conn.execute("UPDATE trades SET ticket=? WHERE id=?", (ticket, trade_id))
            conn.commit()
        self.abertas[trade_id] = {
            "trade_id": trade_id, "ticket": ticket, "par": par, "simbolo": simbolo,
            "estrategia": estrategia, "direcao": direcao, "lote": config.LOTE,
            "preco_entrada": entrada, "sl": sl, "risco": risco, "abertura_utc": _agora(),
            "r_max": 0.0, "be_movido": False, "mae_r": 0.0, "mfe_r": 0.0,
        }
        tag = "" if self.ativa else " [sim]"
        msg = f"🟢{tag} {par} {direcao} @ {entrada:.5f} | SL {sl:.5f} | {estrategia}"
        log.info(msg)
        telegram_notif.enviar(msg)

    # -- ciclo --
    def ciclo(self, conn):
        self._checar_dia(conn)
        self.gerir(conn)                 # gestão sempre roda (fecha/protege o que está aberto)
        if self._dd_ok(conn):
            self.entrar(conn)            # só abre se o DD do dia permite


def main() -> None:
    logging.basicConfig(level=config.LOG_LEVEL,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    signal.signal(signal.SIGINT, _tratar_sinal)
    signal.signal(signal.SIGTERM, _tratar_sinal)

    db.init_db()
    ex = Executor()
    with db.sessao() as conn:
        ex.carregar(conn)
        while not _parar:
            try:
                ex.ciclo(conn)
            except mt5_bridge.MT5Erro as e:
                log.error("Ponte MT5 indisponível: %s", e)
            except Exception:  # noqa: BLE001
                log.exception("Erro inesperado no ciclo do executor")
            for _ in range(config.GESTOR_POLL_S):
                if _parar:
                    break
                time.sleep(1)
    log.info("Executor encerrado.")


if __name__ == "__main__":
    main()
