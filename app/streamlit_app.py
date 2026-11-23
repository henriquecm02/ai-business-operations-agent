"""Dashboard: execute o agente ou reabra uma execução salva (sem chave de API).

  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
import tema

from opsagent.agent import Agent
from opsagent.cli import DEFAULT_REQUEST
from opsagent.config import Settings, make_llm
from opsagent.llm import QuotaExceeded
from opsagent.outputs import brl

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo" / "data" / "inbox"
SAVED_ROOTS = [ROOT / "examples", ROOT / "runs"]
PROBLEMS = {"duplicada": "Cobrança duplicada", "total_divergente": "Total ≠ soma dos itens",
            "cnpj_formato_invalido": "CNPJ inválido", "vencimento_antes_da_emissao": "Vencimento antes da emissão"}

st.set_page_config(page_title="Agente de Operações", page_icon="🗂️", layout="wide")
# Os valores em R$ eram cortados ("R$ 5.67…") com a barra lateral aberta em telas de notebook.
st.markdown(
    "<style>[data-testid='stMetricValue']{font-size:1.65rem}"
    "[data-testid='stMetricValue'] div{overflow:visible;text-overflow:clip}"
    "[data-testid='stMetricLabel'] p{white-space:normal}"
    "[data-testid='stMetricDelta'],[data-testid='stMetricDelta'] div"
    "{white-space:normal;overflow:visible;text-overflow:clip}</style>",
    unsafe_allow_html=True)


def saved_runs() -> list[Path]:
    runs = [p.parent for root in SAVED_ROOTS if root.exists() for p in root.rglob("execucao.json")]
    return sorted(runs, key=lambda p: p.stat().st_mtime, reverse=True)


def render(data: dict, folder: Path) -> None:
    a = data.get("analise") or {}
    rev = data.get("revisao") or {}

    st.markdown(f"**Pedido:** {data.get('pedido', '')}")
    st.caption(f"Modelo `{data.get('modelo')}` · referência {data.get('data_referencia')} · "
               f"{data.get('segundos', 0):.0f}s · {data.get('chamadas_llm')} chamadas ao LLM · "
               f"{(data.get('tokens_entrada', 0) + data.get('tokens_saida', 0)):,} tokens · "
               f"revisor: {'✅ aprovado' if rev.get('aprovado') else '⚠️ ' + '; '.join(rev.get('problemas', []))}"
               .replace(",", "."))

    if a:
        alerts = a.get("alertas", [])
        k = st.columns(5)
        prox = a["vencimentos_proximos"]
        evitado = sum((x.get("valor_evitado") or 0) + (x.get("diferenca") or 0) for x in alerts)
        for col, (label, value, sub, warn) in zip(k, [
            ("Total em faturas", brl(a["total_geral"]), f"{a['faturas_validas']} faturas válidas", False),
            ("Vencido", brl(a["vencidos"]["total"]), f"{len(a['vencidos']['itens'])} faturas",
             bool(a["vencidos"]["total"])),
            (f"Vence em {prox['horizonte_dias']} dias", brl(prox["total"]), f"{len(prox['itens'])} faturas", False),
            ("Pagamento indevido evitado", brl(evitado), "duplicata + divergência", False),
            ("Alertas", str(len(alerts)), "exigem atenção humana", bool(alerts)),
        ]):
            col.metric(label, value, sub, delta_color="inverse" if warn else "off", delta_arrow="off")

        c1, c2 = st.columns(2)
        for col, title, series in [(c1, "Gasto por fornecedor", a["por_fornecedor"]),
                                   (c2, "Gasto por categoria", a["por_categoria"])]:
            with col:
                st.subheader(title)
                df = pd.DataFrame({"nome": list(series), "valor": list(series.values())})
                df["rotulo"] = df["valor"].map(brl)
                bars = alt.Chart(df).mark_bar(color=tema.cor(0), cornerRadiusEnd=4).encode(
                    x=alt.X("valor:Q", title=None, scale=alt.Scale(domain=[0, df["valor"].max() * 1.25]),
                            axis=alt.Axis(format=",.0f", labelExpr="'R$ ' + replace(datum.label, ',', '.')")),
                    y=alt.Y("nome:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260)),
                    tooltip=[alt.Tooltip("nome:N", title=""), alt.Tooltip("rotulo:N", title="Total")])
                text = bars.mark_text(align="left", dx=6, color=tema.texto_secundario(), fontWeight=600).encode(text="rotulo:N")
                st.altair_chart((bars + text).properties(height=34 * len(df) + 20), use_container_width=True)

        if alerts:
            st.subheader("🚨 Alertas")
            st.dataframe(pd.DataFrame([{
                "Problema": PROBLEMS.get(x["problema"], x["problema"]), "Documento": x["arquivo"],
                "Fornecedor": x.get("fornecedor"),
                "Detalhe": (f"cópia de {x['duplicada_de']}" if x.get("duplicada_de") else
                            f"impresso {brl(x['total_impresso'])} · itens somam {brl(x['soma_itens'])}"
                            if x.get("soma_itens") is not None else ""),
            } for x in alerts]), hide_index=True, use_container_width=True)

    tab_resp, tab_rel, tab_venc, tab_arq, tab_trace = st.tabs(
        ["💬 Resposta", "📄 Relatório", "📅 Vencimentos", "⬇️ Arquivos", "🔍 Trace"])
    with tab_resp:
        st.markdown(data.get("resposta") or "")
    with tab_rel:
        st.markdown(data.get("relatorio") or "_Nenhum relatório gerado._")
    with tab_venc:
        if a:
            rows = [{**x, "situação": "vencida"} for x in a["vencidos"]["itens"]] + \
                   [{**x, "situação": "a vencer"} for x in a["vencimentos_proximos"]["itens"]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    with tab_arq:
        for name in data.get("arquivos", []):
            p = folder / name
            if p.exists():
                st.download_button(f"⬇️ {name}", p.read_bytes(), file_name=name, key=f"{folder}/{name}")
    with tab_trace:
        steps = [e for e in data.get("trace", []) if e["tipo"] in ("ferramenta", "aprovacao", "revisao")]
        st.dataframe(pd.DataFrame([{"t (s)": e["t"], "etapa": e.get("nome", e["tipo"]),
                                    "ok": e.get("ok", e.get("aprovado")), "detalhe": e.get("args", "")}
                                   for e in steps]), hide_index=True, use_container_width=True)


tema.aplicar_tema(
    "Agente de operações com IA",
    "Uma pasta de notas, faturas e contratos vira planilha, gráficos e relatório executivo, com validação em código, "
    "revisor independente e aprovação humana.",
    kicker="Agente de IA · operações",
    chips=["Gemini", "Function calling", "MCP", "OCR"])

settings = Settings.from_env()
runs = saved_runs()

with st.sidebar:
    modo = st.radio("Modo", ["Ver execução salva", "Nova execução"], index=0 if runs else 1)

if modo == "Ver execução salva":
    if not runs:
        st.info("Nenhuma execução salva ainda. Rode o agente (CLI ou 'Nova execução').")
        st.stop()
    with st.sidebar:
        escolha = st.selectbox("Execução", runs, format_func=lambda p: str(p.relative_to(ROOT)))
    render(json.loads((escolha / "execucao.json").read_text(encoding="utf-8")), escolha)
    st.stop()

if not settings.api_key:
    st.error("Defina GEMINI_API_KEY (veja .env.example) para executar o agente. "
             "Sem chave, use o modo 'Ver execução salva'.")
    st.stop()

with st.sidebar:
    fonte = st.radio("Documentos", ["Pasta de demonstração (17 arquivos sintéticos)", "Enviar meus arquivos"])
    uploads = None
    if fonte.startswith("Enviar"):
        uploads = st.file_uploader("PDF, DOCX ou TXT", type=["pdf", "docx", "txt", "md"], accept_multiple_files=True)
    ref = st.date_input("Data de referência (vencimentos)", value=date(2026, 10, 20) if fonte.startswith("Pasta")
                        else date.today())
    permitir = st.checkbox("Permitir ações sensíveis (gerar e-mail)", value=False,
                           help="Sem isso, o agente pede e a ação é negada: aprovação humana obrigatória.")
    revisor = st.checkbox("Revisor independente", value=True)
    st.caption(f"Modelo: `{settings.model}`")

pedido = st.text_area("Pedido", DEFAULT_REQUEST, height=90)

if st.button("Executar", type="primary"):
    if fonte.startswith("Enviar") and not uploads:
        st.warning("Envie pelo menos um arquivo.")
        st.stop()
    work = Path(tempfile.mkdtemp(prefix="opsagent_"))
    entrada = DEMO
    if uploads:
        entrada = work / "entrada"
        entrada.mkdir()
        for f in uploads:
            (entrada / Path(f.name).name).write_bytes(f.getvalue())
    saida = ROOT / "runs" / work.name

    with st.status("Executando…", expanded=True) as status:
        def on_event(ev: dict) -> None:
            if ev["tipo"] == "ferramenta":
                st.write(f"{'✅' if ev['ok'] else '❌'} `{ev['nome']}` · {ev.get('segundos', 0)}s")
            elif ev["tipo"] == "aprovacao":
                st.write(f"{'🔓 aprovada' if ev['aprovado'] else '🔒 negada'}: ação sensível `{ev['nome']}`")
            elif ev["tipo"] == "revisao":
                st.write(f"🔎 Revisor: {'aprovado' if ev['aprovado'] else 'pediu correções'}")

        agent = Agent(make_llm(settings), approve=lambda *_: permitir, review_rounds=1 if revisor else 0,
                      on_event=on_event)
        try:
            result = agent.run(pedido, entrada, saida, reference_date=ref)
        except QuotaExceeded as e:
            status.update(label="Cota do modelo esgotada", state="error")
            st.error(str(e))
            st.stop()
        result.save(saida)
        status.update(label=f"Concluído em {result.seconds:.0f}s · salvo em runs/{saida.name}", state="complete")

    render(result.to_json(), saida)
