"""
Test helpers: a fake agents.Runner.run that dispatches on agent.name and
returns frozen responses, so the whole Supplier Qualifier pipeline can be
exercised in tests with zero network calls and zero API cost.
"""

from dataclasses import dataclass
from typing import Any, Callable

import agents as agents_sdk


@dataclass
class FakeRunResult:
    final_output: Any


class FakeRunner:
    """
    responses: dict[agent_name, value | list[value] | callable(input_text) -> value]
      - a plain value is returned every time that agent is run
      - a list is popped from (front) each time - use this when the same
        agent name is invoked more than once per test with different inputs
      - a callable receives the rendered input text and returns a value
    """

    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    async def run(self, agent, input_text, **kwargs):
        self.calls.append((agent.name, input_text))
        if agent.name not in self.responses:
            raise AssertionError(f"No mocked response configured for agent {agent.name!r}.\nInput was:\n{input_text}")
        response = self.responses[agent.name]
        if isinstance(response, list):
            value = response.pop(0)
        elif isinstance(response, Callable):
            value = response(input_text)
        else:
            value = response
        return FakeRunResult(final_output=value)


def patch_runner(monkeypatch, responses: dict[str, Any]) -> FakeRunner:
    fake = FakeRunner(responses)
    monkeypatch.setattr(agents_sdk.Runner, "run", fake.run)
    return fake
