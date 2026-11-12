"""Prompts as versioned artifacts (L2).

Files live in prompts/<name>.<version>.yaml. The active version per prompt is chosen by the
environment variable PROMPT_VERSION_<NAME> (default v1), so switching variants is a config
change, and the version used is recorded with every response (RunResult.prompt_versions and
each trace event).
"""
from __future__ import annotations

import os
import pathlib
import string
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Optional

import yaml

PROMPTS_DIR = pathlib.Path(os.environ.get("PROMPTS_DIR", pathlib.Path(__file__).resolve().parents[3] / "prompts"))


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    description: str
    system: str
    user_template: str

    def render_system(self, **kw) -> str:
        return string.Template(self.system).safe_substitute(**kw)

    def render_user(self, **kw) -> str:
        return string.Template(self.user_template).safe_substitute(**kw)


def active_version(name: str) -> str:
    return os.environ.get(f"PROMPT_VERSION_{name.upper()}", "v1")


@lru_cache(maxsize=64)
def _load(name: str, version: str) -> Prompt:
    path = PROMPTS_DIR / f"{name}.{version}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Prompt(
        name=name,
        version=str(data.get("version", version)),
        description=data.get("description", ""),
        system=data.get("system", ""),
        user_template=data.get("user_template", ""),
    )


def get_prompt(name: str, version: Optional[str] = None) -> Prompt:
    return _load(name, version or active_version(name))


def active_versions(names: Iterable[str]) -> dict[str, str]:
    return {n: active_version(n) for n in names}


def list_versions(name: str) -> list[str]:
    return sorted(p.name.split(".")[1] for p in PROMPTS_DIR.glob(f"{name}.*.yaml"))
