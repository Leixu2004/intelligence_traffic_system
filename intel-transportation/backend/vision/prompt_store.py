"""Prompt 模板读取（prompts/ 目录，$占位符语法）。

用 string.Template 而不是 str.format：事故分析模板里要输出 JSON 示例，
含大量花括号，format 会误解析。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from string import Template

PROMPT_SUFFIX = ".txt"


class PromptTemplateError(RuntimeError):
    pass


@lru_cache(maxsize=64)
def _read_template(directory: str, name: str) -> Template:
    path = Path(directory) / f"{name}{PROMPT_SUFFIX}"
    if not path.is_file():
        raise PromptTemplateError(f"Prompt 模板缺失: {path}")
    return Template(path.read_text(encoding="utf-8").strip())


class PromptStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def names(self, expected: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            name
            for name in expected
            if (self.directory / f"{name}{PROMPT_SUFFIX}").is_file()
        )

    def missing(self, expected: tuple[str, ...]) -> tuple[str, ...]:
        present = set(self.names(expected))
        return tuple(name for name in expected if name not in present)

    def render(self, template: str, **variables: object) -> str:
        """``render("alert", analysis=...)``；形参名不能叫 name，否则与 $name 占位符冲突。"""
        try:
            loaded = _read_template(str(self.directory), template)
        except PromptTemplateError as exc:
            raise PromptTemplateError(f"{exc}；请检查 prompts/ 目录是否完整") from exc
        return loaded.safe_substitute({key: str(value) for key, value in variables.items()})
