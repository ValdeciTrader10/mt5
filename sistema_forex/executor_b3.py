"""Executor de SOMBRA da B3 (ETAPA 8b, bloqueio (b)) — simula WIN/WDO ao vivo.

Gêmeo do `executor.py`, mas ISOLADO e DATA-ONLY por construção:
  - lê SÓ as decisões `mercado='b3'` (o `decisao_b3`); o executor do forex nunca as toca;
  - usa a ponte da Genial `mt5_bridge_b3` (que NÃO tem função de ordem) para o tick ao vivo —
    logo é IMPOSSÍVEL enviar ordem na conta real; tudo é posição VIRTUAL (simulado=1);
  - o P&L é em BRL, calculado do FATO de contrato (`config_b3.lucro_brl` = pontos × valor-por-
    ponto × contratos), porque a ponte é data-only (sem order_calc_profit);
  - a ESCALA (tick / piso / teto de SL) é DERIVADA ao vivo dos candles via `calibracao_b3`
    (lição GOLD: nunca chutar a escala — um SL menor que a vela insta-estopa tudo). Enquanto
    não houver candles suficientes para calibrar, o par simplesmente não abre (log + skip).

Reuso máximo (nada é reescrito): a matemática de saída/risco é a mesma `gestao` PURA e as
leituras agnósticas de mercado (`_atr`, `_evento_saida`, `_regime_atual`, `pode_abrir`,
`_abrir_trade`, `_fechar_trade`, …) vêm do próprio `executor` do forex.

    python -m sistema_forex.executor_b3
"""

import logging
import os
import signal
import time

from . import (calibracao_b3, config, config_b3, db, decisao, estrategias,
               executor, fuzzy_score, gestao, mt5_bridge_b3)

log = logging.getLogger("executor_b3")

_parar = False


def _tratar_sinal(signum, frame):  # pragma: no cover
    global _parar
    log.info("Sinal %s recebido — encerrando após o ciclo atual.", signum)
    _parar = True


# Offset servidor(Genial)↔UTC — carimba os trades da B3 na hora do servidor (alinha com os
# candles, igual ao forex). Medido do tick da ponte B3 (independente do offset do forex).
_OFFSET_SERVIDOR = 0


_OFFSET_DEFINIDO = False


def _atualizar_offset(hora_servidor: int) -> None:
    # Guardas contra tick VELHO (a B3 fica ~15h/dia sem tick novo — o último tick do pregão
    # faria o offset derivar e congelar _agora()): offset plausível ±12h; depois de definido,
    # só aceita variação de ±1h. Mesmo racional do executor forex.
    global _OFFSET_SERVIDOR, _OFFSET_DEFINIDO
    novo = int(round((hora_servidor - int(time.time())) / 3600.0) * 3600)
    if abs(novo) > 12 * 3600:
        return
    if _OFFSET_DEFINIDO and abs(novo - _OFFSET_SERVIDOR) > 3600:
        return
    _OFFSET_SERVIDOR = novo
    _OFFSET_DEFINIDO = True


def _agora() -> int:
    return int(time.time()) + _OFFSET_SERVIDOR


# --------------------------------------------------------------------------- #
# Leituras do banco (livro B3)
# --------------------------------------------------------------------------- #
def _decisoes_novas(conn, desde_id: int):
    return conn.execute(
        "SELECT id, par, tf, direcao, estrategia, criada_utc, variante FROM decisoes "
        "WHERE id > ? AND resultado='entrou' AND mercado='b3' ORDER BY id",
        (desde_id,),
    ).fetchall()


def _abertas_do_banco(conn):
    return conn.execute(
        "SELECT id, ticket, par, tf, estrategia, direcao, lote, preco_entrada, sl_servidor, "
        "abertura_utc, risco_inicial, mae_r, mfe_r, variante FROM trades "
        "WHERE fechamento_utc IS NULL AND mercado='b3'"
    ).fetchall()


# --------------------------------------------------------------------------- #
# Executor de sombra da B3
# --------------------------------------------------------------------------- #
class ExecutorB3:
    def __init__(self):
        self.abertas = {}          # trade_id -> estado da posição virtual
        self.simbolos = {}         # par -> símbolo real na Genial
        self._tick_cache = {}      # símbolo -> tick, reusado no MESMO ciclo
        self._ctx_cache = {}       # par -> (scores_fuzzy, vwap), reusado no MESMO ciclo
        self._calib_cache = {}     # par -> (escala, ts) — escala derivada da calibração, com TTL
        self.ultima_decisao_id = 0

    # -- infraestrutura --
    def _simbolo(self, par: str) -> str:
        if par not in self.simbolos:
            self.simbolos[par] = mt5_bridge_b3.resolver_simbolo(par)
        return self.simbolos[par]

    def _tick(self, simbolo):
        if simbolo not in self._tick_cache:
            t = mt5_bridge_b3.tick_atual(simbolo)
            self._tick_cache[simbolo] = t
            if t and t.get("time"):
                _atualizar_offset(t["time"])
        return self._tick_cache[simbolo]

    def _preco_saida(self, simbolo, direcao):
        t = self._tick(simbolo)
        if not t:
            return None
        return t["bid"] if direcao == "compra" else t["ask"]

    def _preco_entrada(self, simbolo, direcao):
        t = self._tick(simbolo)
        if not t:
            return None
        return t["ask"] if direcao == "compra" else t["bid"]

    def _escala(self, conn, par):
        """Escala do símbolo B3 (tick / piso / teto de SL, em pips=tick), com TTL.

        Prioriza override em `PARAMS_SIMBOLO_B3`; senão DERIVA da `calibracao_b3` sobre os
        candles já coletados (regra do ouro). Devolve None se ainda não há dados suficientes.
        """
        cache = self._calib_cache.get(par)
        if cache and (time.time() - cache[1]) < config_b3.CALIB_REFRESH_S:
            return cache[0]
        tick = config_b3.param_simbolo_b3(par, "tamanho_pip")
        sl_min = config_b3.param_simbolo_b3(par, "sl_min_pips")
        sl_max = config_b3.param_simbolo_b3(par, "sl_max_pips")
        if not (tick and sl_min and sl_max):
            try:
                sug = calibracao_b3.calibrar_par(conn, par).get("sugestao", {})
            except Exception:  # noqa: BLE001 - sem calibração, o par só não abre neste ciclo
                log.exception("Falha ao calibrar escala de %s", par)
                sug = {}
            if sug.get("suficiente"):
                tick = tick or sug.get("tamanho_pip")
                sl_min = sl_min or sug.get("sl_min_pips")
                sl_max = sl_max or sug.get("sl_max_pips")
        escala = None
        if tick and sl_min and sl_max:
            escala = {"tick": tick, "sl_min_pips": sl_min, "sl_max_pips": sl_max}
        self._calib_cache[par] = (escala, time.time())
        return escala

    # -- carga inicial --
    def carregar(self, conn):
        for r in _abertas_do_banco(conn):
            entrada, sl = r["preco_entrada"], r["sl_servidor"]
            be = (r["direcao"] == "compra" and sl >= entrada) or (r["direcao"] == "venda" and sl <= entrada)
            risco = r["risco_inicial"] or abs(entrada - sl)
            self.abertas[r["id"]] = {
                "trade_id": r["id"], "ticket": r["ticket"], "par": r["par"],
                "tf": r["tf"] or config.TF_OPERACAO, "simbolo": self._simbolo(r["par"]),
                "estrategia": r["estrategia"], "direcao": r["direcao"], "lote": r["lote"],
                "preco_entrada": entrada, "sl": sl, "risco": risco,
                "abertura_utc": r["abertura_utc"], "r_max": r["mfe_r"] or 0.0, "be_movido": be,
                "mae_r": r["mae_r"] or 0.0, "mfe_r": r["mfe_r"] or 0.0,
                "real": False, "variante": r["variante"] or "A_ORIGINAL",
            }
        # Alinha o relógio dos trades ao do servidor da Genial (offset de um tick no arranque).
        try:
            pares = config_b3.sombra_pares()
            if pares:
                t = mt5_bridge_b3.tick_atual(self._simbolo(pares[0]))
                if t and t.get("time"):
                    _atualizar_offset(t["time"])
        except Exception:  # noqa: BLE001 - sem tick no arranque, offset fica 0 até o 1º tick
            pass
        mx = conn.execute("SELECT MAX(id) m FROM decisoes").fetchone()["m"]
        self.ultima_decisao_id = mx or 0
        log.info("Executor B3 (sombra) iniciado. Offset servidor↔UTC: %+dh. Posições retomadas: %d",
                 _OFFSET_SERVIDOR // 3600, len(self.abertas))

    # -- contexto p/ gestão de saída por variante (fuzzy/VWAP), cacheado por ciclo --
    def _ctx_variante(self, conn, par):
        if par not in self._ctx_cache:
            scores = fuzzy_score.scores_recentes(conn, par)
            row = conn.execute("SELECT preco FROM niveis WHERE par=? AND tipo='vwap' AND ativo=1 "
                               "ORDER BY criado_em DESC LIMIT 1", (par,)).fetchone()
            self._ctx_cache[par] = (scores, row["preco"] if row else None)
        return self._ctx_cache[par]

    # -- gestão das posições abertas (tick-speed) --
    def gerir(self, conn):
        self._ctx_cache = {}
        for trade_id in list(self.abertas):
            p = self.abertas[trade_id]
            preco = self._preco_saida(p["simbolo"], p["direcao"])
            if preco is None:
                continue
            escala = self._escala(conn, p["par"])
            pip = escala["tick"] if escala else config_b3.param_simbolo_b3(p["par"], "tamanho_pip", 1.0)

            # Saída DESENHADA por variante (só B/C; a Variante A controle segue no gestor genérico).
            if (config.GESTAO_POR_VARIANTE
                    and p.get("variante") in ("B_FUZZY_PURO", "C_HIBRIDA")):
                scores, vwap = self._ctx_variante(conn, p["par"])
                idade_candles = ((_agora() - p["abertura_utc"])
                                 / (config.MINUTOS_TF.get(p["tf"], 5) * 60))
                dec = estrategias.gestao_saida_variante(
                    p["variante"], p["direcao"], preco, p["sl"],
                    fuzzy_m5=(scores.get("M5") or {}).get("score"),
                    exausto=bool((scores.get(p["tf"]) or {}).get("exaustao")),
                    vwap=vwap, m5_min=config.HIBRIDA_SAIDA_M5_MIN, aperto=config.HIBRIDA_STOP_APERTO,
                    idade_candles=idade_candles, min_candles=config.HIBRIDA_SAIDA_MIN_CANDLES)
                if dec["novo_sl"] != p["sl"]:
                    p["sl"] = dec["novo_sl"]
                    executor._atualizar_sl(conn, p["trade_id"], p["sl"])  # persiste (painel/restart)
                if dec["fechar"]:
                    self._fechar(conn, p, preco, pip, dec["motivo"])
                    continue

            # Stop de emergência EMULADO (posição virtual — não há SL de servidor na B3).
            bateu = (p["direcao"] == "compra" and preco <= p["sl"]) or \
                    (p["direcao"] == "venda" and preco >= p["sl"])
            if bateu:
                # MAE honesto até o preço do stop (mesmo fix do executor forex).
                r_stop = gestao.r_por_risco(p["direcao"], p["preco_entrada"], p["sl"], p["risco"])
                p["mae_r"] = min(p["mae_r"], r_stop)
                self._fechar(conn, p, p["sl"], pip, "stop no servidor")
                continue

            r = gestao.r_por_risco(p["direcao"], p["preco_entrada"], preco, p["risco"])
            p["r_max"] = max(p["r_max"], r)
            novo_mfe = max(p["mfe_r"], r)
            novo_mae = min(p["mae_r"], r)
            if novo_mfe != p["mfe_r"] or novo_mae != p["mae_r"]:
                p["mfe_r"], p["mae_r"] = novo_mfe, novo_mae
                executor._persistir_excursao(conn, p["trade_id"], round(p["mae_r"], 3), round(p["mfe_r"], 3))
            # P&L FLUTUANTE ao vivo (o dono acompanha no painel): R + BRL da posição aberta.
            # BRL é PURO (config_b3.lucro_brl — sem martelar a ponte). Persistido só quando o R
            # arredondado muda, para não escrever a cada segundo com centenas de posições.
            if round(r, 2) != p.get("_r_persistido"):
                p["_r_persistido"] = round(r, 2)
                lucro_atual = config_b3.lucro_brl(p["direcao"], p["preco_entrada"], preco,
                                                  p["par"], contratos=p["lote"])
                executor._persistir_ao_vivo(
                    conn, p["trade_id"], round(r, 3),
                    round(lucro_atual, 2) if lucro_atual is not None else None)
            idade_h = (_agora() - p["abertura_utc"]) / 3600
            acao, motivo = gestao.avaliar_saida(
                direcao=p["direcao"], r=r, r_max=p["r_max"], idade_h=idade_h,
                ultimo_evento=executor._evento_saida(conn, p["par"]), be_movido=p["be_movido"],
                be_trigger_r=config.BE_TRIGGER_R, giveback_r=config.GIVEBACK_R,
                tempo_max_h=config.tempo_max_h_tf(p["tf"]), estrut_min_r=config.SAIDA_ESTRUTURA_MIN_R,
            )
            if acao == "fechar":
                self._fechar(conn, p, preco, pip, motivo)
            elif acao == "mover_be":
                self._mover_be(conn, p)

    def _fechar(self, conn, p, preco, pip, motivo):
        pips = gestao.pips(p["direcao"], p["preco_entrada"], preco, pip)
        lucro = config_b3.lucro_brl(p["direcao"], p["preco_entrada"], preco, p["par"],
                                    contratos=p["lote"])
        executor._fechar_trade(conn, p["trade_id"], preco, round(pips, 1), lucro, motivo,
                               round(p["mae_r"], 3), round(p["mfe_r"], 3), fechamento_utc=_agora())
        del self.abertas[p["trade_id"]]
        brl = f"{lucro:+.2f} BRL" if lucro is not None else "—"
        log.info("🔚 [b3] %s %s %s fechada: %+.1f pts | %s | %s",
                 p["par"], p.get("tf", ""), p["direcao"], pips, brl, motivo)

    def _mover_be(self, conn, p):
        p["sl"] = p["preco_entrada"]
        p["be_movido"] = True
        executor._atualizar_sl(conn, p["trade_id"], p["preco_entrada"])
        log.info("↔ [b3] %s %s → break-even", p["par"], p["direcao"])

    # -- fechamento forçado do pregão (a corretora zera as posições no fim do dia) --
    def _preco_encerramento(self, conn, par, direcao):
        """Preço p/ valorar o fechamento forçado: tick vivo; se não houver (após o pregão), o
        último close coletado — garante P&L do encerramento mesmo sem cotação ao vivo."""
        preco = self._preco_saida(self._simbolo(par), direcao)
        if preco is not None:
            return preco
        row = decisao._ultimo(conn, par, "M1") or decisao._ultimo(conn, par, "M5")
        return row["close"] if row else None

    def _encerrar_pregao(self, conn):
        """Fechamento FORÇADO às 17:30 (relógio do servidor): a corretora zera TODAS as posições
        do dia no fim do pregão, independentemente do resultado. Reproduzimos isso e catalogamos
        o motivo (`MOTIVO_FECHAMENTO_PREGAO`). Só B3 (o forex é 24/5, não passa por aqui)."""
        if not config_b3.hora_de_fechar_pregao(config_b3.minuto_do_dia(_agora())):
            return
        for trade_id in list(self.abertas):
            p = self.abertas[trade_id]
            preco = self._preco_encerramento(conn, p["par"], p["direcao"])
            if preco is None:
                continue  # sem preço p/ valorar — tenta de novo no próximo ciclo
            escala = self._escala(conn, p["par"])
            pip = escala["tick"] if escala else config_b3.param_simbolo_b3(p["par"], "tamanho_pip", 1.0)
            self._fechar(conn, p, preco, pip, config_b3.MOTIVO_FECHAMENTO_PREGAO)

    # -- novas entradas --
    def entrar(self, conn):
        for d in _decisoes_novas(conn, self.ultima_decisao_id):
            self.ultima_decisao_id = max(self.ultima_decisao_id, d["id"])
            par, direcao = d["par"], d["direcao"]
            tf = d["tf"] or config.TF_OPERACAO
            estrategia = d["estrategia"]
            variante = d["variante"] or "A_ORIGINAL"
            # Decisão VELHA nunca abre (downtime/feed parado): o "tick atual" da Genial fora do
            # pregão é o ÚLTIMO do dia anterior — abrir nele criaria um trade fantasma.
            if d["criada_utc"] and (time.time() - d["criada_utc"]) > config.ENTRADA_MAX_ATRASO_S:
                log.warning("Decisão B3 %d (%s %s %s) descartada: %.0fs de atraso (> %ds)",
                            d["id"], par, tf, estrategia,
                            time.time() - d["criada_utc"], config.ENTRADA_MAX_ATRASO_S)
                continue
            if executor.pode_abrir(self.abertas.values(), par, tf, estrategia,
                                   livro="sombra", cap=config_b3.MAX_POS_SOMBRA_B3,
                                   variante=variante):
                try:
                    self._abrir(conn, par, tf, direcao, estrategia, variante, d)
                except Exception:  # noqa: BLE001 - uma falha de abertura não derruba o ciclo
                    log.exception("Falha ao abrir B3 %s %s %s", par, tf, direcao)

    def _abrir(self, conn, par, tf, direcao, estrategia, variante, d):
        simbolo = self._simbolo(par)
        escala = self._escala(conn, par)
        if not escala:
            log.info("B3 %s %s %s: aguardando candles p/ derivar a escala (calibração) — sem abrir",
                     par, tf, direcao)
            return
        pip = escala["tick"]
        atr = executor._atr(conn, par, tf)
        assumido = self._preco_entrada(simbolo, direcao)
        if assumido is None:
            return
        sl = gestao.calcular_sl(direcao, assumido, atr, mult=config.SL_SERVIDOR_ATR_MULT,
                                min_pips=escala["sl_min_pips"], max_pips=escala["sl_max_pips"], pip=pip)
        risco = abs(assumido - sl)
        regime = executor._regime_atual(conn, par)
        trade_id = executor._abrir_trade(
            conn, par, tf, estrategia, direcao, config_b3.CONTRATOS_B3, assumido, sl, None,
            1, risco, regime, decisao_id=d["id"], variante=variante, mercado="b3",
            abertura_utc=_agora())
        ticket = -trade_id  # id sintético (posição virtual)
        conn.execute("UPDATE trades SET ticket=? WHERE id=?", (ticket, trade_id))
        conn.commit()
        self.abertas[trade_id] = {
            "trade_id": trade_id, "ticket": ticket, "par": par, "tf": tf, "simbolo": simbolo,
            "estrategia": estrategia, "direcao": direcao, "lote": config_b3.CONTRATOS_B3,
            "preco_entrada": assumido, "sl": sl, "risco": risco, "abertura_utc": _agora(),
            "r_max": 0.0, "be_movido": False, "mae_r": 0.0, "mfe_r": 0.0, "real": False,
            "variante": variante,
        }
        log.info("🟢 [b3] %s %s %s @ %.1f | SL %.1f | %s (%s)",
                 par, tf, direcao, assumido, sl, config.nome_estrategia(estrategia),
                 config.nome_variante(variante))

    # -- ciclo --
    def ciclo(self, conn):
        self._tick_cache = {}
        self._encerrar_pregao(conn)   # fecha à força às 17:30 (antes de gerir/entrar)
        self.gerir(conn)
        self.entrar(conn)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    if not (config_b3.B3_HABILITADO and config_b3.B3_SOMBRA_HABILITADA):
        log.info("B3_HABILITADO/B3_SOMBRA_HABILITADA off — executor B3 não vai iniciar.")
        return
    signal.signal(signal.SIGINT, _tratar_sinal)
    signal.signal(signal.SIGTERM, _tratar_sinal)

    db.init_db()
    ex = ExecutorB3()
    with db.sessao() as conn:
        # `carregar` resolve símbolos na ponte — se o container mt5_b3 ainda não subiu, não
        # morrer em crash-loop: re-tentar até a ponte responder (mesma tolerância do loop).
        while not _parar:
            try:
                ex.carregar(conn)
                break
            except (mt5_bridge_b3.MT5Erro, EOFError, ConnectionError, OSError) as e:
                log.error("Ponte MT5 B3 indisponível no arranque (%s) — nova tentativa em %ss",
                          e, config_b3.GESTOR_B3_POLL_S)
                mt5_bridge_b3.reconectar()
                time.sleep(config_b3.GESTOR_B3_POLL_S)
        while not _parar:
            try:
                ex.ciclo(conn)
            except mt5_bridge_b3.MT5Erro as e:
                log.error("Ponte MT5 (B3) indisponível: %s", e)
            except (EOFError, ConnectionError, OSError) as e:
                log.error("Conexão com a ponte MT5 B3 caiu (%s) — agendando reconexão", e)
                mt5_bridge_b3.reconectar()
            except Exception:  # noqa: BLE001
                log.exception("Erro inesperado no ciclo do executor B3")
            for _ in range(config_b3.GESTOR_B3_POLL_S):
                if _parar:
                    break
                time.sleep(1)
    mt5_bridge_b3.desligar()
    log.info("Executor B3 encerrado.")


if __name__ == "__main__":
    main()
