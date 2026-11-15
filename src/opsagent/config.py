"""Configuração via variáveis de ambiente.

Ordem de busca da chave: variável de ambiente > ./.env > ~/.config/portfolio/.env.
A chave nunca é gravada em arquivos do repositório.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "gemini-3.8-flash"


def load_env() -> None:
    load_dotenv(Path.cwd() / ".env", override=False)
    load_dotenv(Path.home() / ".config" / "portfolio" / ".env", override=False)


@dataclass
class Settings:
    api_key: str | None
    model: str
    # Preço por 1M tokens (USD). Zero por padrão: o tier gratuito do Gemini não cobra.
    price_in: float
    price_out: float

    @classmethod
    def from_env(cls) -> "Settings":
        load_env()
        return cls(
            api_key=os.getenv("GEMINI_API_KEY"),
            model=os.getenv("GEMINI_MODEL", DEFAULT_MODEL),
            price_in=float(os.getenv("PRICE_IN_PER_MTOK", "0")),
            price_out=float(os.getenv("PRICE_OUT_PER_MTOK", "0")),
        )

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return input_tokens / 1e6 * self.price_in + output_tokens / 1e6 * self.price_out


def make_llm(settings: Settings | None = None):
    from .llm import GeminiLLM

    s = settings or Settings.from_env()
    if not s.api_key:
        raise SystemExit(
            "GEMINI_API_KEY não encontrada. Crie uma chave em https://aistudio.google.com e "
            "defina a variável de ambiente ou um arquivo .env (veja .env.example)."
        )
    return GeminiLLM(api_key=s.api_key, model=s.model)
