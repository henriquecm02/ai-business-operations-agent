"""Extração estruturada com LLM + validação determinística.

O LLM só transforma texto em JSON. Todas as checagens (somas, datas, CNPJ, duplicatas)
são feitas em código, porque são regras exatas que um modelo pode errar.
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

from .documents import read_text_with_method
from .llm import LLM, QuotaExceeded, Usage
from .prompts import load_prompt

NULLABLE_STR = {"type": ["string", "null"]}
NULLABLE_NUM = {"type": ["number", "null"]}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tipo": {"type": "string", "enum": ["fatura", "contrato", "outro"]},
        "numero": NULLABLE_STR,
        "fornecedor": NULLABLE_STR,
        "cnpj": NULLABLE_STR,
        "data_emissao": {**NULLABLE_STR, "description": "AAAA-MM-DD"},
        "vencimento": {**NULLABLE_STR, "description": "AAAA-MM-DD"},
        "valor_total": NULLABLE_NUM,
        "categoria": {"type": ["string", "null"],
                      "enum": ["insumos", "embalagens", "servicos", "software", "utilidades", "outros", None]},
        "itens": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "descricao": {"type": "string"},
                    "quantidade": {"type": "number"},
                    "preco_unitario": {"type": ["number", "null"]},
                    "subtotal": {"type": "number"},
                },
                "required": ["descricao", "quantidade", "subtotal"],
            },
        },
        "contrato": {
            "type": ["object", "null"],
            "properties": {
                "contratada": NULLABLE_STR,
                "valor": NULLABLE_NUM,
                "periodicidade_valor": NULLABLE_STR,
                "vigencia_inicio": NULLABLE_STR,
                "vigencia_fim": NULLABLE_STR,
                "renovacao_automatica": {"type": ["boolean", "null"]},
                "pontos_de_atencao": {"type": "array", "items": {"type": "string"}},
            },
        },
        "resumo": {"type": "string", "description": "Uma frase descrevendo o documento."},
    },
    "required": ["tipo", "itens", "resumo"],
}

CNPJ_RE = re.compile(r"^\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}$")
MAX_CHARS = 20_000


def _parse_date(v: str | None) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def validate(record: dict) -> list[str]:
    """Regras de negócio sobre um documento. Retorna códigos de problema."""
    issues: list[str] = []
    if record.get("tipo") != "fatura":
        return issues
    for f in ("numero", "fornecedor", "data_emissao", "vencimento", "valor_total"):
        if record.get(f) in (None, ""):
            issues.append(f"campo_ausente:{f}")
    if record.get("cnpj") and not CNPJ_RE.match(record["cnpj"]):
        issues.append("cnpj_formato_invalido")
    itens = record.get("itens") or []
    for it in itens:
        if it.get("preco_unitario") is not None and \
                abs(it["quantidade"] * it["preco_unitario"] - it["subtotal"]) > 0.011:
            issues.append(f"subtotal_incorreto:{it['descricao']}")
    if itens and record.get("valor_total") is not None:
        soma = round(sum(it["subtotal"] for it in itens), 2)
        if abs(soma - record["valor_total"]) > 0.011:
            issues.append("total_divergente")
            record["soma_itens"] = soma
    em, ve = _parse_date(record.get("data_emissao")), _parse_date(record.get("vencimento"))
    if record.get("data_emissao") and not em:
        issues.append("data_emissao_invalida")
    if record.get("vencimento") and not ve:
        issues.append("vencimento_invalido")
    if em and ve and ve < em:
        issues.append("vencimento_antes_da_emissao")
    return issues


def mark_duplicates(records: list[dict]) -> None:
    """Marca como duplicada a segunda ocorrência de (CNPJ, número) ou (CNPJ, data, valor)."""
    seen: dict[tuple, str] = {}
    for r in records:
        if r.get("tipo") != "fatura":
            continue
        keys = [("n", r.get("cnpj"), r.get("numero")),
                ("v", r.get("cnpj"), r.get("data_emissao"), r.get("valor_total"))]
        hit = next((seen[k] for k in keys if all(k[1:]) and k in seen), None)
        if hit:
            r["problemas"].append("duplicada")
            r["duplicada_de"] = hit
        else:
            for k in keys:
                if all(k[1:]):
                    seen[k] = r["arquivo"]


BATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"documentos": {"type": "array", "items": {
        **SCHEMA, "properties": {"arquivo": {"type": "string"}, **SCHEMA["properties"]},
        "required": ["arquivo", *SCHEMA["required"]]}}},
    "required": ["documentos"],
}


QTY_PREFIX = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*[xX×]\s+")


def _finish(data: dict, name: str, truncated: bool, metodo: str = "texto") -> dict:
    data.setdefault("itens", [])
    for it in data["itens"]:  # "10x Alface" -> "Alface" (a quantidade já está no campo próprio)
        it["descricao"] = QTY_PREFIX.sub("", it.get("descricao") or "").strip()
    data["arquivo"] = name
    data["problemas"] = validate(data) + (["texto_truncado"] if truncated else []) + (["lido_por_ocr"] if metodo == "ocr" else [])
    data["leitura"] = metodo
    return data


def extract_one(llm: LLM, path: Path) -> tuple[dict, Usage]:
    text, metodo = read_text_with_method(path)
    prompt = f"Arquivo: {path.name}\n\n<documento>\n{text[:MAX_CHARS]}\n</documento>"
    data, usage = llm.json(load_prompt("extract"), prompt, SCHEMA)
    return _finish(data, path.name, len(text) > MAX_CHARS, metodo), usage


def extract_batch(llm: LLM, paths: list[Path]) -> tuple[list[dict], Usage, list[dict]]:
    """Vários documentos em uma única chamada; devolve um registro por arquivo enviado."""
    lidos = {p.name: read_text_with_method(p) for p in paths}
    texts = {n: t for n, (t, _) in lidos.items()}
    blocks = "\n\n".join(f'<documento arquivo="{n}">\n{t[:MAX_CHARS]}\n</documento>' for n, t in texts.items())
    prompt = (f"Extraia os {len(texts)} documentos abaixo. Devolva exatamente um item em `documentos` por "
              f"arquivo, com o campo `arquivo` igual ao atributo do documento.\n\n{blocks}")
    data, usage = llm.json(load_prompt("extract"), prompt, BATCH_SCHEMA)
    got = {d.get("arquivo"): d for d in data.get("documentos", [])}
    records, errors = [], []
    for name, text in texts.items():
        if name in got:
            records.append(_finish(got[name], name, len(text) > MAX_CHARS, lidos[name][1]))
        else:
            errors.append({"arquivo": name, "erro": "documento ausente na resposta do modelo"})
    return records, usage, errors


def extract_many(llm: LLM, paths: list[Path], batch_size: int | None = None, workers: int = 3
                 ) -> tuple[list[dict], Usage, list[dict]]:
    """Extrai em lotes paralelos. Lotes reduzem o número de requisições (importante em planos com
    limite diário); documentos que falharem no lote são refeitos individualmente."""
    batch_size = batch_size or int(os.getenv("OPSAGENT_BATCH_SIZE", "6"))
    total = Usage()
    records: list[dict] = []
    errors: list[dict] = []
    batches = [paths[i:i + batch_size] for i in range(0, len(paths), batch_size)]

    def job(batch: list[Path]):
        try:
            return extract_batch(llm, batch) if len(batch) > 1 else _single(batch[0])
        except QuotaExceeded:
            raise
        except Exception as e:
            return [], Usage(), [{"arquivo": p.name, "erro": f"{type(e).__name__}: {e}"} for p in batch]

    def _single(p: Path):
        rec, usage = extract_one(llm, p)
        return [rec], usage, []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for recs, usage, errs in ex.map(job, batches):
            total.add(usage)
            records.extend(recs)
            errors.extend(errs)

    by_name = {p.name: p for p in paths}
    retry, errors = [e for e in errors if e["arquivo"] in by_name], []
    for e in retry:  # segunda chance, um por um
        try:
            rec, usage = extract_one(llm, by_name[e["arquivo"]])
            total.add(usage)
            records.append(rec)
        except QuotaExceeded:
            raise
        except Exception as ex2:
            errors.append({"arquivo": e["arquivo"], "erro": f"{type(ex2).__name__}: {ex2}"})

    records.sort(key=lambda r: r["arquivo"])
    mark_duplicates(records)
    return records, total, errors
