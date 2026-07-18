"""Gráfico de candles offline + níveis do motor (Fase 2/3).

Gera HTML com Plotly EMBUTIDO (sem CDN — padrão dos sistemas Empenho), lendo os
candles do banco e desenhando por cima os níveis calculados pelo motor: suportes,
resistências e FVGs (zonas). Mostra os níveis daquele TF de origem.

Uso:
    python -m sistema_forex.grafico EURUSD# M5
    # ou importado: html = grafico_html("EURUSD#", "M5", limite=500)
"""

import logging
import sys

from . import analise, config, db

log = logging.getLogger("grafico")


def _buscar_candles(conn, par: str, tf: str, limite: int):
    rows = conn.execute(
        """
        SELECT time_utc, open, high, low, close, tick_volume, real_volume
        FROM candles
        WHERE par = ? AND tf = ?
        ORDER BY time_utc DESC
        LIMIT ?
        """,
        (par, tf, limite),
    ).fetchall()
    return list(reversed(rows))  # cronológico


def _desenhar_niveis(fig, niveis, x0, x1, tf: str) -> tuple:
    """Desenha S/R (linhas) e FVG (zonas) do motor por cima do gráfico. Retorna (n_sr, n_fvg).

    Compartilhado pelo gráfico do par e pelo raio-x do trade, para os dois mostrarem o
    MESMO contexto de níveis. Só FVGs do próprio TF entram (as de outro TF são ruído aqui)."""
    cores = {"suporte": "#3fb950", "resistencia": "#f85149"}
    sr_n = fvg_n = 0
    for nv in niveis:
        tipo = nv["tipo"]
        if tipo in cores:
            m = nv.get("meta") or {}
            lw = 1 + min(2, int((nv.get("forca") or 1) / 4))
            det = f"·{m.get('toques', 0)}t·{int((m.get('respeito') or 0) * 100)}%rej" if m else ""
            fig.add_hline(
                y=nv["preco"], line_color=cores[tipo], line_width=lw,
                line_dash="dot", opacity=0.6,
                annotation_text=f"{tipo[:3].upper()} {m.get('tf', '')}·f{nv['forca']}{det}",
                annotation_position="right",
                annotation_font_size=9, annotation_font_color=cores[tipo],
            )
            sr_n += 1
        elif tipo.startswith("fvg") and nv.get("preco2") is not None and nv.get("tf_origem") == tf:
            alta = tipo.endswith("bull")
            fig.add_shape(
                type="rect", x0=x0, x1=x1, y0=nv["preco"], y1=nv["preco2"],
                line_width=0, layer="below",
                fillcolor="rgba(63,185,80,0.10)" if alta else "rgba(248,81,73,0.10)",
            )
            fvg_n += 1
    return sr_n, fvg_n


def grafico_html(par: str, tf: str, limite: int = 500) -> str:
    """Retorna o HTML completo (offline) do gráfico de candles par/tf."""
    import plotly.graph_objects as go
    from datetime import datetime

    with db.sessao() as conn:
        candles = _buscar_candles(conn, par, tf, limite)
        # S/R agora vem só de TFs fortes (H1/D1/W1). Trazemos TODOS os níveis do par
        # (não só os do TF do gráfico) para os S/R fortes aparecerem também no M5.
        niveis = analise.niveis_ativos(conn, par)

    if not candles:
        return (
            "<html><body style='font-family:sans-serif;padding:2rem'>"
            f"<h3>Sem candles para {par} {tf}</h3>"
            "<p>O coletor ainda não gravou dados para este par/timeframe.</p>"
            "</body></html>"
        )

    tempos = [datetime.utcfromtimestamp(c["time_utc"]) for c in candles]
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=tempos,
                open=[c["open"] for c in candles],
                high=[c["high"] for c in candles],
                low=[c["low"] for c in candles],
                close=[c["close"] for c in candles],
                name=par,
            )
        ]
    )
    # --- Níveis do motor desenhados por cima ---
    x0, x1 = tempos[0], tempos[-1]
    sr_n, fvg_n = _desenhar_niveis(fig, niveis, x0, x1, tf)
    fig.update_layout(
        title=f"{par} — {tf} · {len(candles)} candles · {sr_n} níveis S/R · {fvg_n} FVGs",
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
        margin=dict(l=40, r=20, t=50, b=40),
        height=640,
    )
    # include_plotlyjs=True embute a lib inteira → funciona offline, sem CDN.
    return fig.to_html(include_plotlyjs=True, full_html=True)


# --------------------------------------------------------------------------- #
# Raio-X do trade — reconstrói o contexto (passado + futuro) de um trade fechado
# para auditar POR QUE perdeu (ou ganhou): entrada adiantada? stop no ruído? alvo
# curto? Como os candles ficam no banco, o "futuro" se preenche sozinho com o tempo.
# --------------------------------------------------------------------------- #
def _janela_trade(conn, par, tf, abertura_utc, fechamento_utc, antes, depois):
    """Candles do TF do trade em [abertura − `antes` … fechamento + `depois`], cronológico.

    Três fatias sem sobreposição: ANTES (contexto que gerou a entrada), DURANTE (vida do
    trade) e DEPOIS (o que o preço fez em seguida). Trade ainda aberto (fechamento None):
    ancora no `abertura` e puxa mais candles à frente para mostrar até o presente."""
    col = "time_utc, open, high, low, close"
    aberto = fechamento_utc is None
    fim = fechamento_utc if not aberto else abertura_utc
    pre = conn.execute(
        f"SELECT {col} FROM candles WHERE par=? AND tf=? AND time_utc<=? ORDER BY time_utc DESC LIMIT ?",
        (par, tf, abertura_utc, antes),
    ).fetchall()
    meio = conn.execute(
        f"SELECT {col} FROM candles WHERE par=? AND tf=? AND time_utc>? AND time_utc<=? "
        "ORDER BY time_utc ASC LIMIT 5000",
        (par, tf, abertura_utc, fim),
    ).fetchall()
    pos = conn.execute(
        f"SELECT {col} FROM candles WHERE par=? AND tf=? AND time_utc>? ORDER BY time_utc ASC LIMIT ?",
        (par, tf, fim, max(depois, 500) if aberto else depois),
    ).fetchall()
    return list(reversed(pre)) + list(meio) + list(pos)


def _res_r(direcao, entrada, saida, risco):
    """Resultado do trade em múltiplos de R (None se faltar preço/risco)."""
    if saida is None or not risco:
        return None
    from . import gestao
    return gestao.r_por_risco(direcao, entrada, saida, risco)


def _contexto_decisao(conn, par, tf, estrategia, direcao, abertura_utc, decisao_id=None,
                      variante=None):
    """Recupera a decisão de ENTRADA que originou o trade (o 'porquê entrou'): score,
    confluências e regime gravados pelo estrategista.

    Preferência: se o trade tem `decisao_id` (FK gravada na abertura), casa DIRETO — exato,
    sem heurística. Fallback (trades antigos sem FK): casa por (par, tf, estratégia, direção,
    VARIANTE) na decisão 'entrou' mais recente até pouco antes da abertura. Sem o filtro de
    variante, um trade da A pegava o 'porquê' da C_HIBRIDA (mesma estratégia/candle) — o motivo
    aparecia com prefixo 'C|', misturando os livros."""
    import json
    r = None
    if decisao_id:
        r = conn.execute(
            "SELECT time_utc, motivo, dados_json FROM decisoes WHERE id=?", (decisao_id,)
        ).fetchone()
    if r is None:
        cond = ["par=?", "tf=?", "estrategia=?", "direcao=?", "resultado='entrou'", "time_utc<=?"]
        args = [par, tf, estrategia, direcao, (abertura_utc or 0) + 120]
        if variante is not None:
            cond.append("COALESCE(variante,'A_ORIGINAL')=?"); args.append(variante)
        r = conn.execute(
            f"SELECT time_utc, motivo, dados_json FROM decisoes WHERE {' AND '.join(cond)} "
            "ORDER BY time_utc DESC LIMIT 1", args,
        ).fetchone()
    if not r:
        return None
    dados = {}
    try:
        dados = json.loads(r["dados_json"] or "{}")
    except Exception:  # noqa: BLE001
        pass
    return {"motivo": r["motivo"], "score": dados.get("score"),
            "confluencias": dados.get("confluencias") or [], "regime": dados.get("regime")}


def _faixa_y(candles, precos=(), margem: float = 0.08):
    """Faixa [min, max] do eixo Y para ENQUADRAR o price action do trade.

    Sem isto, o Plotly auto-ajusta o eixo para caber TODOS os S/R desenhados por cima —
    inclusive níveis fortes distantes (ex.: um nível em 168 num par cotado a ~217) — e os
    candles ficam ESMAGADOS numa tira ilegível no topo (bug do raio-X das perdedoras). Ancora
    a faixa nas máximas/mínimas dos candles + os preços do trade (entrada/SL/saída) com uma
    margem; os níveis fora disso simplesmente saem da tela em vez de comprimir o gráfico
    inteiro. Retorna None se não houver candles."""
    lows = [c["low"] for c in candles if c["low"] is not None]
    highs = [c["high"] for c in candles if c["high"] is not None]
    if not lows or not highs:
        return None
    extras = [p for p in precos if p]
    lo = min([min(lows)] + extras)
    hi = max([max(highs)] + extras)
    span = hi - lo
    if span <= 0:  # tudo no mesmo preço (dados sintéticos/degenerados) — abre uma janela mínima
        span = abs(hi) * 0.001 or 1.0
    pad = span * margem
    return [lo - pad, hi + pad]


def grafico_trade_html(trade_id: int, antes: int = None, depois: int = None,
                       plotly_cdn: bool = False, incluir_raiox: bool = False) -> str:
    """HTML (offline) do raio-x de UM trade: candles antes/durante/depois com entrada, SL,
    saída, MAE/MFE, níveis do motor e o contexto da decisão que abriu a posição.

    `plotly_cdn=True` carrega o Plotly da CDN em vez de embutir ~4,5 MB — usado na EXPORTAÇÃO EM
    LOTE (dezenas de arquivos num zip; o gráfico renderiza com internet). `incluir_raiox=True`
    embute o raio-X TEXTUAL em pips (a leitura de price action que a IA usa p/ auditar a entrada)."""
    import plotly.graph_objects as go
    from datetime import datetime

    antes = antes if antes is not None else config.GRAFICO_TRADE_BARRAS_ANTES
    depois = depois if depois is not None else config.GRAFICO_TRADE_BARRAS_DEPOIS

    raiox_txt = None
    with db.sessao() as conn:
        t = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        if not t:
            return _pagina_erro(f"Trade #{trade_id} não encontrado.")
        t = dict(t)
        par, tf = t["par"], t["tf"] or config.TF_OPERACAO
        candles = _janela_trade(conn, par, tf, t["abertura_utc"], t["fechamento_utc"], antes, depois)
        niveis = analise.niveis_ativos(conn, par)
        ctx = _contexto_decisao(conn, par, tf, t["estrategia"], t["direcao"], t["abertura_utc"],
                                decisao_id=t.get("decisao_id"),
                                variante=t.get("variante") or "A_ORIGINAL")
        if incluir_raiox:
            try:
                from . import auditoria   # lazy: auditoria importa grafico (evita import circular)
                raiox_txt = auditoria.raiox_texto(auditoria.raiox_dados(conn, t))
            except Exception:  # noqa: BLE001 - o raio-X textual é um extra; não derruba a página
                raiox_txt = None

    if not candles:
        return _pagina_erro(f"Sem candles {par} {tf} para o trade #{trade_id}.")

    tempos = [datetime.utcfromtimestamp(c["time_utc"]) for c in candles]
    x0, x1 = tempos[0], tempos[-1]
    fig = go.Figure(data=[go.Candlestick(
        x=tempos, open=[c["open"] for c in candles], high=[c["high"] for c in candles],
        low=[c["low"] for c in candles], close=[c["close"] for c in candles], name=par,
    )])
    _desenhar_niveis(fig, niveis, x0, x1, tf)

    direcao = t["direcao"]
    entrada, sl, saida = t["preco_entrada"], t["sl_servidor"], t["preco_saida"]
    t_ent = datetime.utcfromtimestamp(t["abertura_utc"]) if t["abertura_utc"] else x0
    t_sai = datetime.utcfromtimestamp(t["fechamento_utc"]) if t["fechamento_utc"] else x1
    lucro = t.get("lucro_usd") or 0
    venceu = lucro > 0
    cor_res = "#3fb950" if venceu else "#f85149"

    # Zona sombreada da vida do trade + linhas de entrada/SL/saída.
    fig.add_vrect(x0=t_ent, x1=t_sai, line_width=0, fillcolor="rgba(88,166,255,0.07)", layer="below")
    fig.add_hline(y=entrada, line_color="#58a6ff", line_width=1.2,
                  annotation_text=f"Entrada {entrada:.5f}", annotation_position="left",
                  annotation_font_size=10, annotation_font_color="#58a6ff")
    if sl:
        fig.add_hline(y=sl, line_color="#f85149", line_width=1, line_dash="dash",
                      annotation_text=f"SL {sl:.5f}", annotation_position="left",
                      annotation_font_size=10, annotation_font_color="#f85149")
    seta = "triangle-up" if direcao == "compra" else "triangle-down"
    fig.add_trace(go.Scatter(x=[t_ent], y=[entrada], mode="markers", name="entrada",
                             marker=dict(symbol=seta, size=14, color="#58a6ff",
                                         line=dict(width=1, color="#c9d1d9"))))
    if saida is not None:
        fig.add_hline(y=saida, line_color=cor_res, line_width=1, line_dash="dot",
                      annotation_text=f"Saída {saida:.5f}", annotation_position="right",
                      annotation_font_size=10, annotation_font_color=cor_res)
        fig.add_trace(go.Scatter(x=[t_sai], y=[saida], mode="markers", name="saída",
                                 marker=dict(symbol="x", size=13, color=cor_res,
                                             line=dict(width=1, color="#c9d1d9"))))

    # Fixa o eixo Y ao redor dos candles + preços do trade — do contrário os S/R fortes
    # distantes esticam o auto-range e achatam os candles numa tira ilegível.
    faixa = _faixa_y(candles, (entrada, sl, saida))
    fig.update_layout(
        title=f"Raio-X #{trade_id} · {par} {tf} · {config.nome_estrategia(t['estrategia'])} · {direcao}",
        xaxis_rangeslider_visible=False, template="plotly_dark",
        margin=dict(l=40, r=20, t=50, b=40), height=560, showlegend=False,
        yaxis=dict(range=faixa) if faixa else {},
    )
    plot = fig.to_html(include_plotlyjs=("cdn" if plotly_cdn else True), full_html=False)
    return _pagina_trade(t, ctx, plot, tf, antes, depois, raiox_texto=raiox_txt)


def _fmt_ts(ep) -> str:
    from datetime import datetime
    return datetime.utcfromtimestamp(ep).strftime("%Y-%m-%d %H:%M") if ep else "—"


def _pagina_trade(t: dict, ctx, plot_html: str, tf: str, antes: int, depois: int,
                  raiox_texto: str = None) -> str:
    """Monta a página do raio-x: cabeçalho com os fatos do trade + o 'porquê entrou'."""
    import html as _h
    from . import gestao

    entrada, saida = t["preco_entrada"], t["preco_saida"]
    risco = t.get("risco_inicial")
    res_r = _res_r(t["direcao"], entrada, saida, risco)
    lucro = t.get("lucro_usd") or 0
    venceu = lucro > 0
    classe = "win" if venceu else "loss"
    veredito = "GANHADORA" if venceu else "PERDEDORA" if lucro < 0 else "ZERADA"

    def _r(v, d=2):
        return "—" if v is None else f"{v:.{d}f}"

    fatos = [
        ("Resultado", f'<b class="{classe}">{veredito}</b>'),
        ("Pips", _r(t.get("pips"), 1)),
        ("USD", _r(lucro, 2)),
        ("R", _r(res_r, 2)),
        ("MAE (pior R contra)", _r(t.get("mae_r"), 2)),
        ("MFE (melhor R a favor)", _r(t.get("mfe_r"), 2)),
        ("Entrada", _r(entrada, 5)),
        ("SL inicial", _r(t.get("sl_servidor"), 5)),
        ("Saída", _r(saida, 5)),
        ("Aberto em", _fmt_ts(t.get("abertura_utc"))),
        ("Fechado em", _fmt_ts(t.get("fechamento_utc"))),
        ("Regime na entrada", _h.escape(str(t.get("regime_entrada") or "—"))),
        ("Motivo da saída", _h.escape(str(t.get("motivo_saida") or "—"))),
    ]
    cards = "".join(
        f'<div class="fato"><span class="k">{k}</span><span class="v">{v}</span></div>'
        for k, v in fatos
    )

    # Bloco "por que entrou" (contexto da decisão do estrategista).
    if ctx:
        confl = ctx.get("confluencias") or []
        lis = "".join(f"<li>{_h.escape(str(c))}</li>" for c in confl) or "<li class='muted'>—</li>"
        porque = (
            f'<div class="porque"><h2>Por que entrou</h2>'
            f'<p><b>Score:</b> {ctx.get("score") if ctx.get("score") is not None else "—"} · '
            f'<b>Regime:</b> {_h.escape(str(ctx.get("regime") or "—"))}</p>'
            f'<p class="muted">{_h.escape(str(ctx.get("motivo") or ""))}</p>'
            f'<ul>{lis}</ul></div>'
        )
    else:
        porque = ('<div class="porque"><h2>Por que entrou</h2>'
                  '<p class="muted">Decisão de origem não encontrada em <code>decisoes</code> '
                  '(trade anterior ao registro atual, ou dados removidos).</p></div>')

    # Leitura para o auditor: MAE/MFE dizem se foi ruído no stop ou alvo curto.
    dicas = []
    mae, mfe = t.get("mae_r"), t.get("mfe_r")
    if not venceu and mfe is not None and mfe >= 1.0:
        dicas.append(f"O preço andou <b>+{mfe:.1f}R</b> a favor antes de virar — alvo/parcial "
                     "possivelmente deixado na mesa (calibração de saída).")
    if not venceu and mae is not None and mae <= -0.9 and (mfe or 0) < 0.3:
        dicas.append("Foi contra quase de imediato (MFE baixo, MAE fundo) — possível entrada "
                     "adiantada ou contra o contexto (revisar ponto/gatilho).")
    if venceu and mae is not None and mae <= -1.0:
        dicas.append(f"Aguentou <b>{mae:.1f}R</b> de calor antes de vingar — o stop precisa "
                     "desse espaço; cuidado ao apertá-lo.")
    dica_html = ("<div class='dicas'><h2>Leitura</h2><ul>"
                 + "".join(f"<li>{d}</li>" for d in dicas) + "</ul></div>") if dicas else ""

    # Raio-X TEXTUAL (price action em pips) — a leitura que a IA usa p/ auditar o ponto de entrada.
    raiox_html = (f"<div class='porque'><h2>Raio-X textual (para a IA)</h2>"
                  f"<pre style='white-space:pre-wrap;font-size:.8rem;color:#c9d1d9;margin:0'>"
                  f"{_h.escape(raiox_texto)}</pre></div>") if raiox_texto else ""

    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Raio-X trade #{t['id']}</title>
<style>
  body{{font-family:system-ui,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:1rem 1.2rem}}
  a{{color:#58a6ff;text-decoration:none}} h1{{font-size:1.15rem;margin:.2rem 0 1rem}}
  h2{{font-size:1rem;margin:.2rem 0 .5rem;color:#e6edf3}}
  .win{{color:#3fb950}} .loss{{color:#f85149}} .muted{{color:#8b949e}}
  .fatos{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.5rem;margin-bottom:1rem}}
  .fato{{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:.45rem .6rem;display:flex;flex-direction:column}}
  .fato .k{{font-size:.7rem;color:#8b949e;text-transform:uppercase;letter-spacing:.02em}}
  .fato .v{{font-size:1rem;margin-top:.1rem}}
  .porque,.dicas{{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:.7rem .9rem;margin-top:1rem}}
  .porque ul,.dicas ul{{margin:.3rem 0 0;padding-left:1.1rem}} li{{margin:.15rem 0}}
  .topo{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem}}
  code{{background:#21262d;padding:.05rem .3rem;border-radius:4px}}
</style></head><body>
  <div class="topo">
    <h1>🔍 Raio-X do trade #{t['id']} — {_h.escape(t['par'])} {tf}
      <span class="{classe}">· {veredito}</span></h1>
    <a href="/analitico">← voltar à Análise</a>
  </div>
  <div class="fatos">{cards}</div>
  {plot_html}
  <p class="muted" style="margin-top:.6rem">Janela: {antes} candles antes da entrada e {depois}
    depois do fechamento (TF {tf}). O trecho após a saída revela se o stop pegou ruído, se o
    alvo foi curto ou se a entrada foi adiantada. O "futuro" cresce sozinho conforme o coletor
    grava novos candles — reabra dias depois para ver o desfecho completo.</p>
  {porque}
  {dica_html}
  {raiox_html}
</body></html>"""


def _pagina_erro(msg: str) -> str:
    return ("<html><body style='font-family:sans-serif;background:#0d1117;color:#c9d1d9;"
            f"padding:2rem'><h3>{msg}</h3><p><a href='/analitico' style='color:#58a6ff'>"
            "← voltar</a></p></body></html>")


def main() -> None:
    logging.basicConfig(level=config.LOG_LEVEL)
    par = sys.argv[1] if len(sys.argv) > 1 else config.PARES[0]
    tf = sys.argv[2] if len(sys.argv) > 2 else config.TF_OPERACAO
    html = grafico_html(par, tf)
    saida = config.DADOS_DIR / f"grafico_{par.replace('#','')}_{tf}.html"
    config.DADOS_DIR.mkdir(parents=True, exist_ok=True)
    saida.write_text(html, encoding="utf-8")
    print(f"Gráfico salvo em {saida}")


if __name__ == "__main__":
    main()
