"""Análise determinística (pandas) dos documentos extraídos. É a única fonte de números do relatório."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd


def invoices_frame(records: list[dict], include_duplicates: bool = False) -> pd.DataFrame:
    rows = [r for r in records if r.get("tipo") == "fatura"
            and (include_duplicates or "duplicada" not in r.get("problemas", []))]
    cols = ["arquivo", "numero", "fornecedor", "cnpj", "categoria", "data_emissao", "vencimento",
            "valor_total", "problemas"]
    df = pd.DataFrame([{c: r.get(c) for c in cols} for r in rows], columns=cols)
    df["valor_total"] = pd.to_numeric(df["valor_total"], errors="coerce").fillna(0.0)
    df["data_emissao"] = pd.to_datetime(df["data_emissao"], errors="coerce")
    df["vencimento"] = pd.to_datetime(df["vencimento"], errors="coerce")
    df["categoria"] = df["categoria"].fillna("outros")
    # O nome do fornecedor varia entre documentos (abreviações, sufixos); o CNPJ não.
    # Usa o nome mais frequente (ou mais longo, em empate) de cada CNPJ como nome canônico.
    if len(df):
        canon = (df.dropna(subset=["cnpj"]).groupby("cnpj")["fornecedor"]
                 .agg(lambda s: max(s.value_counts().items(), key=lambda kv: (kv[1], len(str(kv[0]))))[0]))
        df["fornecedor"] = df["cnpj"].map(canon).fillna(df["fornecedor"])
    return df


def _money(series: pd.Series) -> dict[str, float]:
    return {str(k): round(float(v), 2) for k, v in series.sort_values(ascending=False).items()}


def analyze(records: list[dict], reference: date | None = None, horizon_days: int = 14) -> dict:
    ref = pd.Timestamp(reference or date.today())
    df = invoices_frame(records)
    contracts = [r for r in records if r.get("tipo") == "contrato"]
    out: dict = {
        "data_referencia": ref.date().isoformat(),
        "documentos_processados": len(records),
        "faturas_validas": int(len(df)),
        "contratos": len(contracts),
        "total_geral": round(float(df["valor_total"].sum()), 2),
        "ticket_medio": round(float(df["valor_total"].mean()), 2) if len(df) else 0.0,
        "por_fornecedor": _money(df.groupby("fornecedor")["valor_total"].sum()) if len(df) else {},
        "por_categoria": _money(df.groupby("categoria")["valor_total"].sum()) if len(df) else {},
        "por_mes_emissao": {k.strftime("%Y-%m"): round(float(v), 2)
                    for k, v in df.groupby(df["data_emissao"].dt.to_period("M"))["valor_total"].sum()
                    .to_timestamp().items()} if len(df) else {},
    }
    if len(df):
        top = df.groupby("fornecedor")["valor_total"].sum()
        out["maior_fornecedor"] = {"nome": top.idxmax(), "valor": round(float(top.max()), 2),
                                   "participacao_pct": round(float(top.max() / top.sum() * 100), 1)}
    soon = df[(df["vencimento"] >= ref) & (df["vencimento"] <= ref + timedelta(days=horizon_days))]
    late = df[df["vencimento"] < ref]
    fmt = lambda d: [{"numero": r.numero, "fornecedor": r.fornecedor, "vencimento": r.vencimento.date().isoformat(),
                      "valor": round(float(r.valor_total), 2)} for r in d.sort_values("vencimento").itertuples()]
    out["vencimentos_proximos"] = {"horizonte_dias": horizon_days, "itens": fmt(soon),
                                   "total": round(float(soon["valor_total"].sum()), 2)}
    out["vencidos"] = {"itens": fmt(late), "total": round(float(late["valor_total"].sum()), 2)}

    names = dict(zip(df["arquivo"], df["fornecedor"]))
    alerts = []
    for r in records:
        for p in r.get("problemas", []):
            a = {"arquivo": r["arquivo"], "problema": p, "numero": r.get("numero"),
                 "fornecedor": names.get(r["arquivo"], r.get("fornecedor"))}
            if p == "duplicada":
                a["duplicada_de"] = r.get("duplicada_de")
                a["valor_evitado"] = r.get("valor_total")
            if p == "total_divergente":
                a["total_impresso"] = r.get("valor_total")
                a["soma_itens"] = r.get("soma_itens")
                a["diferenca"] = round((r.get("valor_total") or 0) - (r.get("soma_itens") or 0), 2)
            alerts.append(a)
    out["alertas"] = alerts
    out["contratos_detalhe"] = [{"arquivo": c["arquivo"], **(c.get("contrato") or {})} for c in contracts]
    return out


NON_MONEY_KEYS = {"documentos_processados", "faturas_validas", "contratos", "horizonte_dias",
                  "participacao_pct", "quantidade"}


def numbers_in(obj) -> set[float]:
    """Valores monetários presentes na análise (para checar se o relatório cita valores reais).
    Contagens e percentuais ficam de fora: "1 contrato" não pode validar um "R$ 1,00" inventado."""
    found: set[float] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in NON_MONEY_KEYS:
                found |= numbers_in(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= numbers_in(v)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        found.add(round(float(obj), 2))
    return found
