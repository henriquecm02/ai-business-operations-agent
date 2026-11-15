"""Interface web: envie documentos (ou use a pasta de demonstração), faça o pedido e acompanhe o agente.

  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

import streamlit as st

from opsagent.agent import Agent
from opsagent.cli import DEFAULT_REQUEST
from opsagent.config import Settings, make_llm
from opsagent.llm import QuotaExceeded

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo" / "data" / "inbox"

st.set_page_config(page_title="Agente de Operações", page_icon="🗂️", layout="wide")
st.title("🗂️ Agente de Operações com IA")
st.caption("Uma pasta de documentos financeiros vira planilha, gráficos e relatório executivo, com validação e revisão.")

settings = Settings.from_env()
if not settings.api_key:
    st.error("Defina GEMINI_API_KEY (veja .env.example) para executar.")
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
    work = Path(tempfile.mkdtemp(prefix="opsagent_"))
    if uploads:
        entrada = work / "entrada"
        entrada.mkdir()
        for f in uploads:
            (entrada / Path(f.name).name).write_bytes(f.getvalue())
    elif fonte.startswith("Enviar"):
        st.warning("Envie pelo menos um arquivo.")
        st.stop()
    else:
        entrada = DEMO
    saida = work / "saida"

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
        status.update(label=f"Concluído em {result.seconds:.0f}s", state="complete")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tempo", f"{result.seconds:.0f}s")
    c2.metric("Chamadas ao LLM", result.usage.calls)
    c3.metric("Tokens", f"{result.usage.input_tokens + result.usage.output_tokens:,}".replace(",", "."))
    c4.metric("Arquivos gerados", len(result.outputs))

    st.subheader("Resposta do agente")
    st.markdown(result.answer)

    a = result.session.analysis
    if a and a.get("alertas"):
        st.subheader("Alertas")
        st.dataframe(a["alertas"], use_container_width=True)

    tab_rel, tab_graf, tab_arq, tab_trace = st.tabs(["Relatório", "Gráficos", "Arquivos", "Trace"])
    with tab_rel:
        st.markdown(result.session.report_text or "_Nenhum relatório gerado._")
    with tab_graf:
        for p in result.session.charts.values():
            st.image(str(p))
    with tab_arq:
        for p in result.outputs:
            st.download_button(f"⬇️ {p.name}", p.read_bytes(), file_name=p.name, key=str(p))
    with tab_trace:
        st.json(json.loads(json.dumps(result.to_json(), default=str)), expanded=False)
