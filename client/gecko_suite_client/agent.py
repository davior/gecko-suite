"""The agent loop every app's assistant shares: ask, run the tools, answer, repeat.

Extracted from GVC's `assistant/loop.py`. What stays in an app is what is the app's: its
tools and what they do. What moves here is the part that was the same everywhere and
easy to get subtly wrong: when to stop, what to do with a cut-off reply or a pause, how
many times to tell a model it is not finished, and never running a call whose arguments
did not parse.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence, Union

from gecko_suite_client.chat import Reply, SuiteConversation, ToolCall, ToolResult

logger = logging.getLogger(__name__)

# Default number of times a model that stops too soon is told to carry on. Past that it
# would only spend the user's money repeating itself. (GVC's MAX_REPAIR_NUDGES.)
MAX_NUDGES = 2
# What a handler may return: the content, a ToolResult, or (content, is_error).
HandlerResult = Union[str, ToolResult, tuple]


class Stopped(Exception):
    """The user pressed Stop."""


@dataclass
class Outcome:
    reply: str = ""
    # Set when the turn ended before the model finished on its own, with a sentence the
    # app can show.
    cut_short: Optional[str] = None
    replies: list[Reply] = field(default_factory=list)
    rounds: int = 0

    @property
    def cost(self) -> Optional[float]:
        costs = [getattr(r, "cost", None) for r in self.replies]
        return None if any(c is None for c in costs) else round(sum(costs), 6)


def _as_result(call: ToolCall, value: HandlerResult) -> ToolResult:
    if isinstance(value, ToolResult):
        return value
    if isinstance(value, tuple):
        content, is_error = value
        return ToolResult(call.id, str(content), bool(is_error))
    return ToolResult(call.id, str(value))


def default_round(handlers: Mapping[str, Callable[[dict], HandlerResult]],
                  caps: Mapping[str, int], used: dict[str, int]) -> Callable[[Sequence[ToolCall]], list[ToolResult]]:
    """Run each call through its handler, in order.

    Every call gets a result, because a call left unanswered is a 400 on the next ask: an
    unknown tool, unusable arguments, a cap reached and a handler that raised all come
    back as error results the model can read and act on.
    """

    def run(calls: Sequence[ToolCall]) -> list[ToolResult]:
        results = []
        for call in calls:
            if call.parse_error:
                results.append(ToolResult(call.id, f"Your arguments could not be used ({call.parse_error}). "
                                          "Call the tool again with a valid JSON object.", True))
                continue
            handler = handlers.get(call.name)
            if handler is None:
                results.append(ToolResult(call.id, f"There is no tool called {call.name}.", True))
                continue
            if call.name in caps and used.get(call.name, 0) >= caps[call.name]:
                results.append(ToolResult(call.id, f"{call.name} has reached its limit of {caps[call.name]} "
                                          "for this request. Carry on without it.", True))
                continue
            used[call.name] = used.get(call.name, 0) + 1
            try:
                results.append(_as_result(call, handler(call.arguments)))
            except Exception as exc:  # a tool failing is information for the model
                logger.exception("Tool %s failed", call.name)
                results.append(ToolResult(call.id, f"{call.name} failed: {exc}", True))
        return results

    return run


def run_agent(
    conversation: SuiteConversation,
    *,
    handlers: Optional[Mapping[str, Callable[[dict], HandlerResult]]] = None,
    run_round: Optional[Callable[[Sequence[ToolCall]], list[ToolResult]]] = None,
    max_rounds: int = 24,
    nudge: Optional[Callable[[Reply], Optional[str]]] = None,
    max_nudges: int = MAX_NUDGES,
    max_tokens_message: Optional[str] = None,
    caps: Optional[Mapping[str, int]] = None,
    cancelled: Callable[[], bool] = lambda: False,
    on_reply: Callable[[Reply], None] = lambda reply: None,
    on_step: Callable[[str, str], None] = lambda kind, text: None,
    on_delta: Optional[Callable[[str], None]] = None,
) -> Outcome:
    """Run one turn to completion.

    - `handlers` maps tool names to functions of the arguments; or give `run_round` to
      run a whole round's calls yourself (GVC does, to run its clip-picking jobs in
      parallel). Either way every call must get exactly one result.
    - `nudge(reply)` is asked when the model stops without calling a tool. Return text to
      tell it to carry on ("the video still has issues: ..."), or None to let it finish.
      It is the app's to report that to its user (call `on_step` from inside it): only the
      app knows what a good sentence is.
    - `max_tokens_message` replaces the sentence for a reply cut off at the model's output
      limit, which otherwise points people at Gecko Suite's settings. An app that has its
      own settings screen says so here.
    - `caps` limits how many times a tool may run in this turn (GVC's
      ASSISTANT_MAX_GENERATIONS for generate_image).
    """
    if run_round is None:
        run_round = default_round(handlers or {}, caps or {}, {})
    outcome = Outcome()
    nudges = 0
    for _ in range(max_rounds):
        if cancelled():
            raise Stopped()
        # Only passed when asked for: an app's own conversation objects (GVC's, which this
        # loop replaced, and the scripted ones its tests use) have `ask()` and nothing else.
        reply = conversation.ask(on_delta=on_delta) if on_delta is not None else conversation.ask()
        outcome.rounds += 1
        outcome.replies.append(reply)
        on_reply(reply)
        if reply.text:
            outcome.reply = reply.text

        if reply.stop == "pause":
            # A server-side tool paused the turn; sending the history back resumes it.
            continue
        if reply.stop == "refusal":
            category = getattr(reply, "refusal_category", None)
            outcome.cut_short = "The model declined this request" + (
                f" ({category})." if category else ".")
            return outcome
        if not reply.tool_calls:
            if reply.stop == "max_tokens":
                outcome.cut_short = max_tokens_message or (
                    "The reply hit the model's output limit. Raise its maximum response "
                    "length in Gecko Suite, or ask for less at once.")
                return outcome
            message = nudge(reply) if nudge else None
            if message and nudges < max_nudges:
                nudges += 1
                conversation.tell(message)
                continue
            return outcome

        if reply.text:
            on_step("think", reply.text)
        if cancelled():
            raise Stopped()
        results = run_round(reply.tool_calls)
        if len(results) != len(reply.tool_calls):
            raise RuntimeError("run_round must return one result per tool call")
        conversation.answer(results)

    outcome.cut_short = (f"Stopped after {max_rounds} rounds without finishing. What it had done so far "
                         "is below; ask again to carry on.")
    return outcome
