"""Orquestrador: um agente com ferramentas + um revisor independente.

Fluxo:
  pedido -> loop (LLM decide -> ferramentas executam -> resultado volta ao LLM) -> resposta
         -> revisor (outro contexto, saída estruturada) -> se reprovar, o agente corrige (até N rodadas)

Cada passo é registrado no trace (tempo, tokens, ferramenta, argumentos, resumo do resultado).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from .documents import Workspace
from .llm import LLM, Message, Usage
from .prompts import load_prompt
from .tools import TOOLS, Session

ApprovalFn = Callable[[str, dict], bool]

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "aprovado": {"type": "boolean"},
        "problemas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["aprovado", "problemas"],
}


def deny_all(tool: str, args: dict) -> bool:
    return False


@dataclass
class RunResult:
    answer: str
    outputs: list[Path]
    trace: list[dict]
    usage: Usage
    seconds: float
    review: dict | None
    steps: int
    session: Session = field(repr=False)

    def to_json(self) -> dict:
        return {"resposta": self.answer, "arquivos": [p.name for p in self.outputs], "passos": self.steps,
                "segundos": round(self.seconds, 2), "chamadas_llm": self.usage.calls,
                "tokens_entrada": self.usage.input_tokens, "tokens_saida": self.usage.output_tokens,
                "revisao": self.review, "trace": self.trace}


def _short(obj, limit: int = 300) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…"


class Agent:
    def __init__(self, llm: LLM, approve: ApprovalFn = deny_all, max_steps: int = 20, review_rounds: int = 1,
                 on_event: Callable[[dict], None] | None = None):
        self.llm = llm
        self.approve = approve
        self.max_steps = max_steps
        self.review_rounds = review_rounds
        self.on_event = on_event or (lambda e: None)

    def run(self, request: str, input_dir: Path, output_dir: Path, reference_date: date | None = None) -> RunResult:
        t0 = time.perf_counter()
        session = Session(workspace=Workspace(input_dir, output_dir), llm=self.llm, reference_date=reference_date)
        trace: list[dict] = []

        def log(kind: str, **kw):
            ev = {"t": round(time.perf_counter() - t0, 2), "tipo": kind, **kw}
            trace.append(ev)
            self.on_event(ev)

        ref = (reference_date or date.today()).isoformat()
        messages = [Message(role="user", text=f"Data de hoje: {ref}.\n\nPedido: {request}")]
        specs = [t.spec for t in TOOLS.values()]
        system = load_prompt("system")
        steps, answer, review = 0, "", None

        for round_ in range(self.review_rounds + 1):
            answer = self._loop(system, messages, specs, session, log, budget=self.max_steps - steps)
            steps = sum(1 for e in trace if e["tipo"] == "llm")
            if self.review_rounds == 0:
                break
            review = self._review(request, session, answer, log)
            if review["aprovado"] or round_ == self.review_rounds or steps >= self.max_steps:
                break
            messages.append(Message(role="user", text="Um revisor independente encontrou estes problemas. "
                                    "Corrija os entregáveis e responda de novo:\n- " + "\n- ".join(review["problemas"])))

        return RunResult(answer=answer, outputs=session.outputs, trace=trace, usage=session.usage,
                         seconds=time.perf_counter() - t0, review=review, steps=steps, session=session)

    def _loop(self, system, messages, specs, session: Session, log, budget: int) -> str:
        for _ in range(max(budget, 0)):
            resp = self.llm.chat(system, messages, specs)
            session.usage.add(resp.usage)
            log("llm", segundos=round(resp.usage.seconds, 2), tokens_in=resp.usage.input_tokens,
                tokens_out=resp.usage.output_tokens, ferramentas=[c.name for c in resp.tool_calls])
            messages.append(Message(role="assistant", text=resp.text, tool_calls=resp.tool_calls, raw=resp.raw))
            if not resp.tool_calls:
                return resp.text or ""
            results = []
            for call in resp.tool_calls:
                results.append((call.name, self._execute(call.name, call.args, session, log)))
            messages.append(Message(role="tool", tool_results=results))
        log("limite", mensagem="Limite de passos atingido")
        return "Interrompido: limite de passos atingido antes de concluir o pedido."

    def _execute(self, name: str, args: dict, session: Session, log) -> dict:
        tool = TOOLS.get(name)
        if tool is None:
            res = {"erro": f"Ferramenta desconhecida: {name}"}
            log("ferramenta", nome=name, args=_short(args), ok=False, resultado=_short(res))
            return res
        if tool.sensitive:
            ok = self.approve(name, args)
            log("aprovacao", nome=name, args=_short(args), aprovado=ok)
            if not ok:
                return {"erro": "Ação negada pelo usuário. Não tente novamente; mencione na resposta final."}
        t = time.perf_counter()
        try:
            res = tool.fn(session, args)
            ok = "erro" not in res
        except Exception as e:  # erros viram resultado para o modelo se recuperar
            res, ok = {"erro": f"{type(e).__name__}: {e}"}, False
        log("ferramenta", nome=name, args=_short(args), ok=ok, segundos=round(time.perf_counter() - t, 2),
            resultado=_short(res))
        return res

    def _review(self, request: str, session: Session, answer: str, log) -> dict:
        payload = {
            "pedido": request,
            "arquivos_gerados": [p.name for p in session.outputs],
            "analise": session.analysis,
            "relatorio": session.report_text,
            "resposta_final_do_agente": answer,
        }
        t = time.perf_counter()
        try:
            review, usage = self.llm.json(load_prompt("reviewer"),
                                          json.dumps(payload, ensure_ascii=False, default=str), REVIEW_SCHEMA)
            session.usage.add(usage)
        except Exception as e:
            review = {"aprovado": True, "problemas": [], "erro_revisor": f"{type(e).__name__}: {e}"}
        log("revisao", segundos=round(time.perf_counter() - t, 2), aprovado=review["aprovado"],
            problemas=review.get("problemas", []))
        return review
