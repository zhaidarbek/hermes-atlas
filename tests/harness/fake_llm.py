"""The fake Llm: stands in for Hermes's ctx.llm, with scripted structured outputs per auxiliary
task. A task's script is a list of outputs, given in order, or a function of the call. A call
with nothing scripted is a failure, so no scene can lean on a model by accident."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

Respond = Callable[[dict], dict]


class UnscriptedCall(AssertionError):
    pass


@dataclass(frozen=True)
class Result:
    """The fields of Hermes's PluginLlmStructuredResult that Atlas reads."""
    parsed: Any
    content_type: str = "json"


class FakeLlm:
    def __init__(self, script: Mapping[str, list[dict] | Respond] | None = None) -> None:
        self._script = {task: out if callable(out) else list(out) for task, out in (script or {}).items()}
        self.calls: list[dict] = []

    def complete_structured(self, *, instructions: str, input: list, json_schema: dict | None = None,
                            task: str | None = None, **kw) -> Result:
        call = {"task": task, "instructions": instructions, "input": "\n".join(b["text"] for b in input),
                "schema": json_schema}
        self.calls.append(call)
        script = self._script.get(task)
        if callable(script):
            return Result(script(call))
        if not script:
            raise UnscriptedCall(f"no scripted output for {task}")
        return Result(script.pop(0))
