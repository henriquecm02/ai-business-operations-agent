"""Prompts versionados. Cada prompt é um arquivo `<nome>_v<N>.md`; a versão ativa vem de
PROMPT_VERSIONS (ou da variável de ambiente OPSAGENT_PROMPT_<NOME>=vN), o que permite
comparar versões no benchmark sem mudar código."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).parent
PROMPT_VERSIONS = {"system": "v1", "extract": "v2", "reviewer": "v1"}


def active_version(name: str) -> str:
    return os.getenv(f"OPSAGENT_PROMPT_{name.upper()}", PROMPT_VERSIONS[name])


@lru_cache(maxsize=None)
def _read(name: str, version: str) -> str:
    return (DIR / f"{name}_{version}.md").read_text(encoding="utf-8").strip()


def load_prompt(name: str) -> str:
    return _read(name, active_version(name))
