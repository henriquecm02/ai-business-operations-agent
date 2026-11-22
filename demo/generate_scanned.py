"""Gera notas fiscais escaneadas (PDF só com imagem) para testar o OCR.

Cada nota é desenhada como PDF, convertida em imagem e "estragada" como um scan real: tons de
cinza, leve rotação, ruído e resolução menor. O PDF final não tem texto embutido.

Uso: python demo/generate_scanned.py   (precisa de poppler-utils; determinístico pela semente)
Saída: demo/data/escaneados/*.pdf e demo/data/ground_truth_escaneados.json
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from pdf2image import convert_from_path
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).parent))
from generate_data import make_invoice, pdf_invoice  # noqa: E402

ROOT = Path(__file__).parent / "data"
OUT = ROOT / "escaneados"

# (layout, rotação em graus, desvio do ruído, dpi do "scanner")
QUALIDADES = [(0, 0.4, 6, 200), (1, -0.9, 10, 200), (0, 1.6, 14, 150), (1, 0.0, 22, 150), (0, 3.0, 30, 100), (1, -2.5, 40, 100)]


def escanear(pdf: Path, destino: Path, rotacao: float, ruido: float, dpi: int, rng: np.random.Generator) -> None:
    img = convert_from_path(str(pdf), dpi=dpi)[0].convert("L")
    img = img.rotate(rotacao, expand=False, fillcolor=255, resample=Image.BICUBIC)
    arr = np.asarray(img, dtype=np.float32) + rng.normal(0, ruido, (img.height, img.width))
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.4))
    img.save(destino, "PDF", resolution=dpi)


def main() -> None:
    rng_py, rng_np = random.Random(2026), np.random.default_rng(2026)
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.iterdir():
        f.unlink()
    truth = {}
    inicio = date(2026, 10, 1)
    with tempfile.TemporaryDirectory() as tmp:
        for i, (layout, rot, ruido, dpi) in enumerate(QUALIDADES):
            inv = make_invoice(rng_py, 50 + i, inicio + timedelta(days=rng_py.randint(0, 20)))
            limpo = Path(tmp) / "limpo.pdf"
            pdf_invoice(inv, limpo, layout=layout)
            nome = f"escaneada_{inv['numero']}.pdf"
            escanear(limpo, OUT / nome, rot, ruido, dpi, rng_np)
            truth[nome] = {"tipo": "fatura", **inv, "qualidade": {"rotacao": rot, "ruido": ruido, "dpi": dpi},
                           "problemas_esperados": ["lido_por_ocr"]}
    (ROOT / "ground_truth_escaneados.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(truth)} notas escaneadas em {OUT}")


if __name__ == "__main__":
    main()
