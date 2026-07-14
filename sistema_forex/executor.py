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
from datetime import datetime

from . import analise, config, db, estrategias, fuzzy_score, gestao, indicadores, mt5_bridge, telegram_notif

log = logging.getLogger("executor")

_parar = False


def _tratar_sinal(signum, frame):  # pragma: no cover
    global _parar
    log.info("Sinal %s recebido — encerrando após o ciclo atual.", signum)
    _parar = True


# Offset (segundos) entre a hora do SERVIDOR do MT5 e o UTC real. Os candles são gravados na
# hora do servidor (r["time"] do MT5, XM = UTC+3); para os trades ALINHAREM com os candles (e o
# raio-X/janelas baterem), carimbamos abertura/fechamento na MESMA hora — a do MetaTrader.
_OFFSET_SERVIDOR = 0


def _atualizar_offset(hora_servidor: int) -> None:
    """Recalcula o offset servidor↔UTC a partir de um tick (arredondado à HORA cheia, que é o
    formato de um fuso de broker — evita ruído de latência do tick)."""
    global _OFFSET_SERVIDOR
    _OFFSET_SERVIDOR = int(round((hora_servidor - int(time.time())) / 3600.0) * 3600)


def _agora() -> int:
    """Epoch na hora do SERVIDOR do broker (a mesma dos candles / do MetaTrader)."""
    return int(time.time()) + _OFFSET_SERVIDOR


# --------------------------------------------------------------------------- #
# Leituras auxiliares do banco
# --------------------------------------------------------------------------- #
def _atr(conn, par: str, tf: str):
    """ATR do TF DE OPERAÇÃO do trade — o stop/tolerância usa a volatilidade do próprio TF
    (o M1 tem ATR bem menor que o M15), para o SL ser coerente com o livro que operou."""
    rows = conn.execute(
        "SELECT high, low, close FROM candles WHERE par=? AND tf=? ORDER BY time_utc DESC LIMIT ?",
        (par, tf, config.ATR_PERIODO * 4),
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


def pode_abrir(abertas_vals, par: str, tf: str, estrategia: str, *, livro: str, cap: int,
               variante: str = "A_ORIGINAL") -> bool:
    """Decide (função PURA, testável) se cabe abrir mais uma posição p/ (par, tf, estrategia,
    VARIANTE) no LIVRO indicado.

    Há DOIS livros independentes que podem coexistir para a MESMA combinação (gêmeos):
      - `livro="sombra"` (virtual): catálogo — cada (par, tf, ESTRATÉGIA, VARIANTE) roda a sua; o
        único limite é o teto amplo `cap` (MAX_POS_SOMBRA).
      - `livro="real"` (demo): idem, mas conta só as posições reais e usa `cap`=MAX_POS_REAL
        (protege a margem do demo). A correlação é checada à parte (só real e se ligada).

    Dedup: uma posição viva por (par, tf, ESTRATÉGIA, VARIANTE) DENTRO do mesmo livro. A variante
    entra na chave (ETAPA 6) para que A_ORIGINAL e C_HIBRIDA da mesma (par, tf, estratégia) sejam
    LIVROS SEPARADOS (o gêmeo fuzzy-filtrado convive com o original — é o par A↔C a comparar), assim
    como uma virtual e uma real da mesma combinação coexistem (par sombra↔real).
    """
    quer_real = livro == "real"
    mesmas = [p for p in abertas_vals if bool(p.get("real")) == quer_real]
    if any(p["par"] == par and p["tf"] == tf and p["estrategia"] == estrategia
           and p.get("variante", "A_ORIGINAL") == variante for p in mesmas):
        return False
    return len(mesmas) < cap


def _decisoes_novas(conn, desde_id: int):
    # SÓ o mercado forex: as decisões `mercado='b3'` são do executor de sombra da B3 (ponte
    # data-only, P&L em BRL) — o executor do forex nunca as toca (símbolo WIN/WDO não existe no XM).
    return conn.execute(
        "SELECT id, par, tf, direcao, estrategia, criada_utc, variante FROM decisoes "
        "WHERE id > ? AND resultado='entrou' AND (mercado='forex' OR mercado IS NULL) ORDER BY id",
        (desde_id,),
    ).fetchall()


def _abertas_do_banco(conn):
    # Só posições do livro forex (mercado='b3' pertence ao executor_b3, que resolve o símbolo
    # na ponte da Genial — resolver WIN/WDO na ponte do XM levantaria erro no arranque).
    return conn.execute(
        "SELECT id, ticket, par, tf, estrategia, direcao, lote, preco_entrada, sl_servidor, abertura_utc, "
        "simulado, risco_inicial, mae_r, mfe_r, variante FROM trades "
        "WHERE fechamento_utc IS NULL AND (mercado='forex' OR mercado IS NULL)"
    ).fetchall()


# --------------------------------------------------------------------------- #
# Persistência de trades
# --------------------------------------------------------------------------- #
def _regime_atual(conn, par: str):
    r = conn.execute(
        "SELECT regime FROM regime_log WHERE par=? ORDER BY time_utc DESC LIMIT 1", (par,)
    ).fetchone()
    return r["regime"] if r else None


def _abrir_trade(conn, par, tf, estrategia, direcao, lote, entrada, sl, ticket, simulado, risco,
                 regime, fill=None, decisao_id=None, variante="A_ORIGINAL", mercado="forex",
                 abertura_utc=None) -> int:
    fill = fill or {}
    cur = conn.execute(
        "INSERT INTO trades (ticket, par, tf, estrategia, direcao, lote, preco_entrada, sl_servidor, "
        "abertura_utc, simulado, risco_inicial, regime_entrada, decisao_id, "
        "preco_sinal, spread_entrada, derrapagem_pips, delay_s, variante, mercado) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ticket, par, tf, estrategia, direcao, lote, entrada, sl,
         abertura_utc if abertura_utc is not None else _agora(), simulado, risco, regime,
         decisao_id, fill.get("preco_sinal"), fill.get("spread_entrada"),
         fill.get("derrapagem_pips"), fill.get("delay_s"), variante, mercado),
    )
    conn.commit()
    return cur.lastrowid


def _fechar_trade(conn, trade_id, saida, pips, lucro, motivo, mae_r=None, mfe_r=None,
                  fechamento_utc=None) -> None:
    # `fechamento_utc` explícito permite ao executor_b3 carimbar na hora do servidor da Genial
    # (o offset do forex fica 0 no container da B3) — mantém abertura/fechamento no mesmo relógio.
    conn.execute(
        "UPDATE trades SET preco_saida=?, pips=?, lucro_usd=?, motivo_saida=?, fechamento_utc=?, "
        "mae_r=?, mfe_r=? WHERE id=?",
        (saida, pips, lucro, motivo, _agora() if fechamento_utc is None else fechamento_utc,
         mae_r, mfe_r, trade_id),
    )
    conn.commit()


def _persistir_excursao(conn, trade_id, mae_r, mfe_r) -> None:
    """Grava MAE/MFE na linha aberta (só em novo extremo — barato e resiliente a restart)."""
    conn.execute("UPDATE trades SET mae_r=?, mfe_r=? WHERE id=?", (mae_r, mfe_r, trade_id))
    conn.commit()


def _persistir_ao_vivo(conn, trade_id, r_atual, lucro_atual) -> None:
    """P&L FLUTUANTE (não realizado) da posição aberta — o dono acompanha ao vivo no painel.
    `lucro_atual` em USD (forex) ou BRL (B3). Chamado só quando o R arredondado muda (barato)."""
    conn.execute("UPDATE trades SET r_atual=?, lucro_atual=? WHERE id=?",
                 (r_atual, lucro_atual, trade_id))
    conn.commit()


def _atualizar_sl(conn, trade_id, sl) -> None:
    conn.execute("UPDATE trades SET sl_servidor=? WHERE id=?", (sl, trade_id))
    conn.commit()


# --------------------------------------------------------------------------- #
# Executor
# --------------------------------------------------------------------------- #
class Executor:
    def __init__(self):
        self.ativa = config.EXECUCAO_ATIVA          # full-real: TODAS as ordens reais
        # Paralelo curado: sombra cataloga tudo + livro real (demo) só das positivas.
        # Ignorado quando full-real já está ligado (aí é tudo real de qualquer forma).
        self.real_curada = config.EXECUCAO_REAL_CURADA and not self.ativa
        self.tem_real = self.ativa or self.real_curada   # envia ordens reais em algum livro?
        self.abertas = {}          # trade_id -> estado da posição
        self.simbolos = {}         # par -> símbolo real
        self._tick_cache = {}      # símbolo -> tick, reusado dentro do MESMO ciclo (fan-out)
        self._ctx_cache = {}       # par -> (scores_fuzzy, vwap), reusado no MESMO ciclo (gestão por variante)
        self.ultima_decisao_id = 0
        self.saldo_inicial_dia = None
        self.dia = None
        self.dd_avisado = False
        self._dd_real_ok = True    # atualizado por ciclo; trava só o livro REAL

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

    def _tick(self, simbolo):
        """Tick do símbolo, cacheado por CICLO: com dezenas de posições simuladas de vários
        (par,tf,estrategia) sobre os mesmos 3 símbolos, evita N chamadas RPyC/lock por segundo
        (3 leituras/ciclo em vez de uma por posição). O cache é limpo no topo de `ciclo`."""
        if simbolo not in self._tick_cache:
            t = mt5_bridge.tick_atual(simbolo)
            self._tick_cache[simbolo] = t
            if t and t.get("time"):        # mantém o offset servidor↔UTC fresco (alinha trades↔candles)
                _atualizar_offset(t["time"])
        return self._tick_cache[simbolo]

    def _preco_saida(self, simbolo, direcao):
        """Preço para VALORAR/fechar: bid p/ compra, ask p/ venda."""
        t = self._tick(simbolo)
        if not t:
            return None
        return t["bid"] if direcao == "compra" else t["ask"]

    def _preco_entrada(self, simbolo, direcao):
        t = self._tick(simbolo)
        if not t:
            return None
        return t["ask"] if direcao == "compra" else t["bid"]

    # -- carga inicial --
    def carregar(self, conn):
        for r in _abertas_do_banco(conn):
            if self.ativa and r["simulado"]:
                continue  # full-real: ignora resíduos simulados (o livro é só real)
            entrada, sl = r["preco_entrada"], r["sl_servidor"]
            be = (r["direcao"] == "compra" and sl >= entrada) or (r["direcao"] == "venda" and sl <= entrada)
            risco = r["risco_inicial"] or abs(entrada - sl)
            self.abertas[r["id"]] = {
                "trade_id": r["id"], "ticket": r["ticket"], "par": r["par"],
                "tf": r["tf"] or config.TF_OPERACAO,
                "simbolo": self._simbolo(r["par"]), "estrategia": r["estrategia"],
                "direcao": r["direcao"], "lote": r["lote"], "preco_entrada": entrada,
                "sl": sl, "risco": risco, "abertura_utc": r["abertura_utc"],
                "r_max": r["mfe_r"] or 0.0, "be_movido": be,
                "mae_r": r["mae_r"] or 0.0, "mfe_r": r["mfe_r"] or 0.0,
                "real": not r["simulado"],   # livro da posição (real=demo, else sombra)
                "variante": r["variante"] or "A_ORIGINAL",   # dimensão do laboratório (dedup por variante)
            }
        # Alinha o relógio dos trades ao do SERVIDOR (candles/MetaTrader): mede o offset uma vez
        # no arranque a partir de um tick; depois `_tick` o mantém fresco.
        try:
            if config.PARES:
                t = mt5_bridge.tick_atual(self._simbolo(config.PARES[0]))
                if t and t.get("time"):
                    _atualizar_offset(t["time"])
        except Exception:  # noqa: BLE001 - sem tick no arranque, offset fica 0 até o 1º tick
            pass
        log.info("Offset servidor↔UTC: %+dh (trades carimbados na hora do MetaTrader)",
                 _OFFSET_SERVIDOR // 3600)
        mx = conn.execute("SELECT MAX(id) m FROM decisoes").fetchone()["m"]
        self.ultima_decisao_id = mx or 0
        if self.ativa:
            modo = "EXECUÇÃO REAL TOTAL (todas as ordens no broker)"
        elif self.real_curada:
            modo = (f"PARALELO CURADO — sombra cataloga tudo + real (demo) p/ "
                    f"{','.join(config.EXEC_REAL_ESTRATEGIAS)} em {','.join(config.EXEC_REAL_TFS)}")
        else:
            modo = "SIMULAÇÃO (sombra sobre preço ao vivo)"
        log.info("Executor iniciado (%s). Posições abertas retomadas: %d", modo, len(self.abertas))

    # -- drawdown --
    def _equity(self, conn):
        if self.tem_real:
            return mt5_bridge.equity()   # equity REAL do demo (o livro real é o que protegemos)
        # simulado: saldo base + realizado no dia + não-realizado das posições sim.
        # Meia-noite do SERVIDOR (fechamento_utc é hora de servidor — mesmo relógio do MetaTrader).
        hoje0 = _agora() - (_agora() % 86400)
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
        # Dia do SERVIDOR (MetaTrader), consistente com abertura/fechamento_utc e os candles.
        dia = datetime.utcfromtimestamp(_agora()).date().isoformat()
        if dia != self.dia:
            self.dia = dia
            self.saldo_inicial_dia = self._equity(conn) or config.SALDO_SIMULADO
            self.dd_avisado = False
            log.info("Novo dia %s (servidor) — saldo inicial de referência: %.2f",
                     dia, self.saldo_inicial_dia)

    def _dd_ok(self, conn) -> bool:
        """DD diário do livro REAL (equity do demo). Só trava o livro real; a sombra nunca
        trunca (senão perdemos amostra do catálogo)."""
        if not self.tem_real:
            return True   # pura sombra: DD é virtual, catálogo segue
        eq = self._equity(conn)
        if eq is None:
            return True
        estourou = gestao.drawdown_estourou(self.saldo_inicial_dia, eq, config.DD_DIARIO_MAX_PCT)
        if estourou and not self.dd_avisado:
            self.dd_avisado = True
            msg = (f"⛔ Drawdown diário atingido ({config.DD_DIARIO_MAX_PCT}%). Sem novas ordens "
                   "REAIS hoje (a sombra continua catalogando).")
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
            if not p.get("real"):
                continue  # posições virtuais têm ticket sintético — não reconciliar
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
    def _ctx_variante(self, conn, par):
        """Contexto (fuzzy por TF + VWAP) do par para a gestão de saída por variante, CACHEADO por
        ciclo (uma leitura por par mesmo com dezenas de posições — não martela o banco)."""
        if par not in self._ctx_cache:
            scores = fuzzy_score.scores_recentes(conn, par)
            row = conn.execute("SELECT preco FROM niveis WHERE par=? AND tipo='vwap' AND ativo=1 "
                               "ORDER BY criado_em DESC LIMIT 1", (par,)).fetchone()
            self._ctx_cache[par] = (scores, row["preco"] if row else None)
        return self._ctx_cache[par]

    def gerir(self, conn):
        if self.tem_real:
            self._reconciliar(conn)   # fecha no banco o que o broker fechou (só posições reais)
        self._ctx_cache = {}          # zera o cache de contexto fuzzy/VWAP a cada ciclo
        for trade_id in list(self.abertas):
            p = self.abertas[trade_id]
            preco = self._preco_saida(p["simbolo"], p["direcao"])
            if preco is None:
                continue
            pip = self._pip(p["simbolo"])

            # Gestão de saída POR VARIANTE (só sombra B/C; a Variante A controle nunca passa aqui).
            # Liga as saídas desenhadas de cada variante (roadmap Etapas 5/6, antes só catalogadas
            # pela saída genérica) — motivado pela auditoria: perdedoras saíam 100% no stop cheio.
            if (not p.get("real") and config.GESTAO_POR_VARIANTE
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
                    p["sl"] = dec["novo_sl"]       # exaustão apertou o stop virtual (só aproxima)
                if dec["fechar"]:
                    self._fechar(conn, p, preco, pip, dec["motivo"])
                    continue

            # Stop de emergência: posição REAL o broker cuida (SL de servidor); a VIRTUAL
            # (sombra) é emulada aqui tick a tick.
            if not p.get("real"):
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
            # P&L FLUTUANTE ao vivo (R + USD) — o dono acompanha as posições abertas no painel.
            # `usd_por_pip` (USD de +1 pip a favor, neste lote) é calculado UMA vez por posição via
            # order_calc_profit e cacheado no dict → não martela a ponte a cada ciclo. Persistido só
            # quando o R arredondado muda. Aproximação: a conversão é a do preço de entrada (drift
            # pequeno em pares USD-base); suficiente para exibir o flutuante da sombra.
            if round(r, 2) != p.get("_r_persistido"):
                p["_r_persistido"] = round(r, 2)
                if "usd_por_pip" not in p:
                    alvo = (p["preco_entrada"] + pip) if p["direcao"] == "compra" \
                        else (p["preco_entrada"] - pip)
                    try:
                        p["usd_por_pip"] = mt5_bridge.calc_lucro(
                            p["direcao"], p["simbolo"], p["lote"], p["preco_entrada"], alvo)
                    except Exception:  # noqa: BLE001 - sem valor-por-pip, mostra só o R
                        p["usd_por_pip"] = None
                pips_atual = gestao.pips(p["direcao"], p["preco_entrada"], preco, pip)
                lucro_atual = (p["usd_por_pip"] * pips_atual) if p.get("usd_por_pip") else None
                _persistir_ao_vivo(conn, p["trade_id"], round(r, 3),
                                   round(lucro_atual, 2) if lucro_atual is not None else None)
            idade_h = (_agora() - p["abertura_utc"]) / 3600
            acao, motivo = gestao.avaliar_saida(
                direcao=p["direcao"], r=r, r_max=p["r_max"], idade_h=idade_h,
                ultimo_evento=_evento_saida(conn, p["par"]), be_movido=p["be_movido"],
                be_trigger_r=config.BE_TRIGGER_R, giveback_r=config.GIVEBACK_R,
                tempo_max_h=config.TEMPO_MAX_POSICAO_H,
                estrut_min_r=config.SAIDA_ESTRUTURA_MIN_R,
            )
            if acao == "fechar":
                self._fechar(conn, p, preco, pip, motivo)
            elif acao == "mover_be":
                self._mover_be(conn, p)

    def _fechar(self, conn, p, preco, pip, motivo):
        if p.get("real"):
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
        tag = " [real]" if p.get("real") else " [sim]"
        msg = f"🔚{tag} {p['par']} {p.get('tf', '')} {p['direcao']} fechada: {pips:+.1f} pips | {motivo}"
        log.info(msg)
        telegram_notif.enviar(msg)

    def _mover_be(self, conn, p):
        p["sl"] = p["preco_entrada"]
        p["be_movido"] = True
        if p.get("real"):
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
            tf = d["tf"] or config.TF_OPERACAO
            estrategia = d["estrategia"]
            variante = d["variante"] or "A_ORIGINAL"

            # LIVRO SOMBRA (virtual): cataloga TODAS as combinações (incl. cada variante como livro
            # próprio) — salvo no full-real, em que só existe o livro real.
            if not self.ativa and pode_abrir(self.abertas.values(), par, tf, estrategia,
                                             livro="sombra", cap=config.MAX_POS_SOMBRA,
                                             variante=variante):
                self._abrir_seguro(conn, par, tf, direcao, estrategia, real=False, d=d)

            # LIVRO REAL (demo): full-real abre tudo; curado abre só as combinações positivas.
            quer_real = self.ativa or (self.real_curada and config.combo_real(estrategia, tf))
            if quer_real and self._pode_abrir_real(par, tf, direcao, estrategia, variante):
                self._abrir_seguro(conn, par, tf, direcao, estrategia, real=True, d=d)

    def _pode_abrir_real(self, par, tf, direcao, estrategia, variante="A_ORIGINAL") -> bool:
        if not self._dd_real_ok:            # teto de DD diário trava só o livro real
            return False
        # Guarda de correlação: só se ligada (GUARDA_CORRELACAO) — avalia só as posições reais.
        if config.GUARDA_CORRELACAO:
            reais = [p for p in self.abertas.values() if p.get("real") and p["tf"] == tf]
            posic = [{"par": p["par"], "direcao": p["direcao"]} for p in reais]
            if gestao.viola_correlacao(posic, par, direcao, config.MAX_EXPOSICAO_MOEDA):
                log.info("Real bloqueado por correlação [%s]: %s %s", tf, par, direcao)
                return False
        return pode_abrir(self.abertas.values(), par, tf, estrategia,
                          livro="real", cap=config.MAX_POS_REAL, variante=variante)

    def _abrir_seguro(self, conn, par, tf, direcao, estrategia, *, real, d):
        try:
            self._abrir(conn, par, tf, direcao, estrategia, real=real, d=d)
        except Exception:  # noqa: BLE001 - uma falha de abertura não derruba o ciclo
            log.exception("Falha ao abrir %s %s %s [%s]", par, tf, direcao,
                          "real" if real else "sombra")

    def _abrir(self, conn, par, tf, direcao, estrategia, *, real, d):
        simbolo = self._simbolo(par)
        pip = self._pip(simbolo)
        atr = _atr(conn, par, tf)
        assumido = self._preco_entrada(simbolo, direcao)   # preço-sinal (o que a sombra assume)
        if assumido is None:
            return
        # Limites de SL POR SÍMBOLO (o ouro precisa de stops muito mais largos que o forex).
        sl_min = config.param_simbolo(par, "sl_min_pips", config.SL_MIN_PIPS)
        sl_max = config.param_simbolo(par, "sl_max_pips", config.SL_MAX_PIPS)
        sl = gestao.calcular_sl(direcao, assumido, atr, mult=config.SL_SERVIDOR_ATR_MULT,
                                min_pips=sl_min, max_pips=sl_max, pip=pip)
        entrada, ticket, fill = assumido, None, {}
        if real:
            if not mt5_bridge.verificar_margem(simbolo, config.LOTE, direcao == "compra"):
                log.warning("Sem margem p/ %s %s [real] — pulando", par, direcao)
                return
            ticket = mt5_bridge.abrir(simbolo, direcao, config.LOTE, sl, config.MAGIC, estrategia)
            # Fill REAL vs preço-sinal → derrapagem/spread/delay (comparação com a sombra).
            preenchido = mt5_bridge.preco_fill(simbolo, ticket, magic=config.MAGIC)
            entrada = preenchido if preenchido is not None else assumido
            derr = ((entrada - assumido) if direcao == "compra" else (assumido - entrada)) / pip
            t = self._tick(simbolo)
            fill = {
                "preco_sinal": round(assumido, 5),
                "spread_entrada": round((t["ask"] - t["bid"]) / pip, 2) if t and pip else None,
                "derrapagem_pips": round(derr, 2),
                # delay em segundos REAIS: decisoes.criada_utc é UTC (time.time()); medimos
                # contra o UTC de agora (NÃO _agora(), que é hora de servidor).
                "delay_s": (int(time.time()) - d["criada_utc"]) if d and d["criada_utc"] else None,
            }
        simulado = 0 if real else 1
        risco = abs(entrada - sl)
        regime = _regime_atual(conn, par)
        variante = (d["variante"] if d and d["variante"] else "A_ORIGINAL")
        trade_id = _abrir_trade(conn, par, tf, estrategia, direcao, config.LOTE, entrada, sl,
                                ticket, simulado, risco, regime, fill=fill,
                                decisao_id=(d["id"] if d else None), variante=variante)
        if ticket is None:
            ticket = -trade_id  # id sintético para o modo simulação
            conn.execute("UPDATE trades SET ticket=? WHERE id=?", (ticket, trade_id))
            conn.commit()
        self.abertas[trade_id] = {
            "trade_id": trade_id, "ticket": ticket, "par": par, "tf": tf, "simbolo": simbolo,
            "estrategia": estrategia, "direcao": direcao, "lote": config.LOTE,
            "preco_entrada": entrada, "sl": sl, "risco": risco, "abertura_utc": _agora(),
            "r_max": 0.0, "be_movido": False, "mae_r": 0.0, "mfe_r": 0.0, "real": real,
            "variante": variante,
        }
        tag = " [real]" if real else " [sim]"
        extra = ""
        if real and fill.get("derrapagem_pips") is not None:
            extra = f" | derrap {fill['derrapagem_pips']:+.1f}p · spread {fill.get('spread_entrada')}p"
        msg = (f"🟢{tag} {par} {tf} {direcao} @ {entrada:.5f} | SL {sl:.5f} | "
               f"{config.nome_estrategia(estrategia)}{extra}")
        log.info(msg)
        telegram_notif.enviar(msg)

    # -- ciclo --
    def ciclo(self, conn):
        self._tick_cache = {}            # tick fresco por ciclo (reusado entre posições/símbolo)
        self._checar_dia(conn)
        self.gerir(conn)                 # gestão sempre roda (fecha/protege o que está aberto)
        # DD diário trava só o livro REAL (dentro de entrar); a sombra sempre cataloga.
        self._dd_real_ok = self._dd_ok(conn)
        self.entrar(conn)


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
