"""OpenAI structured-output 어댑터.

``pipeline``(큰 프롬프트 경로)에 얹혀 있던 것을 떼어냈다. 그 모듈이 사라져도
호출 통로는 남아야 한다 — 최소 프롬프트 경로와 C트랙이 모두 이 어댑터로만
모델을 부른다.

여기 있는 것은 셋뿐이다.

    1. ``StructuredOutputGateway`` — 프롬프트 둘과 Pydantic 계약 하나를 받아
       파싱된 모델을 돌려주는 Protocol
    2. ``OpenAIResponsesGateway`` — 그 Protocol의 OpenAI 구현
    3. ``RetryingGateway`` — 확률적 계약 위반에만 같은 요청을 다시 보내는 decorator

**이 모듈은 ``rd2`` 안의 무엇도 import하지 않는다.** 화살표는 위에서 아래로만
간다 — ``source_generation``이 이 어댑터를 알고, 이 어댑터는 자기를 부르는 쪽을
모른다. ``pipeline``에서 떼어낼 때 ``contracts``의 ``FailureCode``·``TokenUsage``
import가 따라왔었는데, 그건 아래가 위를 아는 모양이었다. 어휘는 여기서 스스로
정의하고(``CallFailure``·``CallUsage``), 계약으로 올리는 일은 받는 쪽이 한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Generic, Protocol, TypeVar

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    ContentFilterFinishReasonError,
    InternalServerError,
    LengthFinishReasonError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

ParsedT = TypeVar("ParsedT", bound=BaseModel)


class CallFailure(str, Enum):
    """이 어댑터가 **스스로 판정할 수 있는** 실패만 담는다.

    값은 ``source_generation.contracts.FailureCode``의 같은 이름 넷과 일부러
    같게 뒀다. 도메인 쪽에서 받을 때 ``FailureCode(exc.code.value)`` 한 줄로
    올라간다. 변환표를 미리 두지 않는 이유는 지금 그 변환을 하는 곳이 하나도
    없어서다 — 필요해지는 자리에서 그쪽이 만들면 된다.

    거꾸로 ``FailureCode``를 여기로 import하면 ``manifest_invalid``·
    ``journal_corrupt``처럼 이 어댑터가 판정할 수 없는 값 열여섯 개가 딸려 오고,
    아래층이 위층을 알게 된다.
    """

    SDK_ERROR = "sdk_error"
    STRUCTURED_OUTPUT_INVALID = "structured_output_invalid"
    MODEL_REFUSAL = "model_refusal"
    MODEL_RESPONSE_EMPTY = "model_response_empty"


@dataclass(frozen=True)
class CallUsage:
    """응답이 보고한 토큰 수. **검산하지 않는다.**

    ``TokenUsage``는 ``total == input + output``을 강제하지만 그건 영수증의
    불변식이지 어댑터의 일이 아니다. 여기서는 온 값을 그대로 옮기고, 계약으로
    올릴 때 도메인이 검산한다.
    """

    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class StructuredCall(Generic[ParsedT]):
    parsed: ParsedT
    response_id: str
    request_id: str | None = None
    token_usage: CallUsage | None = None


class StructuredCallError(RuntimeError):
    def __init__(
        self,
        code: CallFailure,
        message: str,
        *,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class StructuredOutputGateway(Protocol):
    def parse(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ParsedT],
        max_output_tokens: int,
    ) -> StructuredCall[ParsedT]: ...


class OpenAIResponsesGateway:
    """OpenAI SDK retry만 사용하는 동기 structured-output adapter."""

    def __init__(self, client: OpenAI) -> None:
        self._client = client

    def parse(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ParsedT],
        max_output_tokens: int,
    ) -> StructuredCall[ParsedT]:
        try:
            response = self._client.responses.parse(
                model=model,
                instructions=system_prompt,
                input=user_prompt,
                text_format=response_model,
                max_output_tokens=max_output_tokens,
                store=False,
                truncation="disabled",
            )
        except (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError) as exc:
            raise StructuredCallError(
                CallFailure.SDK_ERROR,
                f"{type(exc).__name__}: OpenAI SDK retries exhausted",
                retryable=True,
            ) from exc
        except APIStatusError as exc:
            raise StructuredCallError(
                CallFailure.SDK_ERROR,
                f"{type(exc).__name__}: OpenAI request rejected",
                retryable=False,
            ) from exc
        except ValidationError as exc:
            raise StructuredCallError(
                CallFailure.STRUCTURED_OUTPUT_INVALID,
                (
                    "OpenAI response did not satisfy the Pydantic contract: "
                    f"{_validation_error_summary(exc)}"
                ),
                retryable=False,
            ) from exc
        except LengthFinishReasonError as exc:
            raise StructuredCallError(
                CallFailure.STRUCTURED_OUTPUT_INVALID,
                "OpenAI structured output exceeded max_output_tokens",
                retryable=False,
            ) from exc
        except ContentFilterFinishReasonError as exc:
            raise StructuredCallError(
                CallFailure.MODEL_REFUSAL,
                "OpenAI content filter stopped the structured-output request",
                retryable=False,
            ) from exc
        except OpenAIError as exc:
            raise StructuredCallError(
                CallFailure.SDK_ERROR,
                f"{type(exc).__name__}: OpenAI SDK call failed",
                retryable=False,
            ) from exc

        # Streaming/background mode는 쓰지 않지만, SDK parsing이 terminal status보다
        # 먼저 일어나는 경계 사례를 막기 위해 completed를 별도로 확인한다.
        if response.status != "completed":
            raise StructuredCallError(
                CallFailure.STRUCTURED_OUTPUT_INVALID,
                f"OpenAI response ended with status={response.status!r}",
                retryable=False,
            )

        parsed = response.output_parsed
        if parsed is None:
            if _response_has_refusal(response):
                raise StructuredCallError(
                    CallFailure.MODEL_REFUSAL,
                    "OpenAI model refused the structured-output request",
                    retryable=False,
                )
            raise StructuredCallError(
                CallFailure.MODEL_RESPONSE_EMPTY,
                "OpenAI response contained no parsed structured output",
                retryable=False,
            )
        if not isinstance(parsed, response_model):
            raise StructuredCallError(
                CallFailure.STRUCTURED_OUTPUT_INVALID,
                (
                    f"OpenAI parsed {type(parsed).__name__}; "
                    f"expected {response_model.__name__}"
                ),
                retryable=False,
            )

        usage = None
        if response.usage is not None:
            usage = CallUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                total_tokens=response.usage.total_tokens,
            )
        return StructuredCall(
            parsed=parsed,
            response_id=response.id,
            request_id=getattr(response, "_request_id", None),
            token_usage=usage,
        )


#: 재시도가 의미 있는 실패. 모델이 확률적으로 자기모순 응답을 낼 때만 해당한다.
#:
#: 20건 fixture를 동일 조건으로 3회 돌린 결과 3회 모두 실패하는 케이스가
#: 0건이었다 — 모든 실패가 실행마다 뒤집혔다. 즉 이 실패들은 결정론적 버그가
#: 아니라 확률적 사건이고, 같은 입력을 한 번 더 보내는 것만으로 상당수가
#: 해소된다. 설정·소스·모델 구성 문제처럼 다시 보내도 같은 결과인 실패는
#: 여기 넣지 않는다.
STOCHASTIC_FAILURE_CODES: frozenset[CallFailure] = frozenset(
    {
        CallFailure.STRUCTURED_OUTPUT_INVALID,
        CallFailure.MODEL_RESPONSE_EMPTY,
        CallFailure.SDK_ERROR,
    }
)


class RetryingGateway:
    """확률적 계약 위반에만 같은 요청을 다시 보내는 gateway decorator.

    route나 target을 바꿔 조용히 우회하지 않고, **완전히 같은 요청**을 정해진
    횟수만큼만 다시 보낸다.
    """

    def __init__(
        self,
        inner: StructuredOutputGateway,
        *,
        max_attempts: int = 2,
        retry_codes: frozenset[CallFailure] = STOCHASTIC_FAILURE_CODES,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._inner = inner
        self._max_attempts = max_attempts
        self._retry_codes = retry_codes
        #: (code, 시도횟수) 기록. 재시도가 조용히 일어나지 않게 남긴다.
        self.retried: list[tuple[CallFailure, int]] = []

    def parse(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ParsedT],
        max_output_tokens: int,
    ) -> StructuredCall[ParsedT]:
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._inner.parse(
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_model=response_model,
                    max_output_tokens=max_output_tokens,
                )
            except StructuredCallError as exc:
                if exc.code not in self._retry_codes or attempt == self._max_attempts:
                    raise
                self.retried.append((exc.code, attempt))
        raise AssertionError("unreachable")  # pragma: no cover


def _validation_error_summary(exc: ValidationError, *, limit: int = 8) -> str:
    summaries: list[str] = []
    for error in exc.errors(include_input=False, include_url=False)[:limit]:
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        summaries.append(f"{location}: {error['msg']} [{error['type']}]")
    remaining = exc.error_count() - len(summaries)
    if remaining > 0:
        summaries.append(f"... and {remaining} more validation error(s)")
    return "; ".join(summaries)


def _response_has_refusal(response: object) -> bool:
    for output in getattr(response, "output", ()):
        if getattr(output, "type", None) != "message":
            continue
        for content in getattr(output, "content", ()):
            if getattr(content, "type", None) == "refusal":
                return True
    return False


def default_openai_gateway(
    *,
    api_key: str | None = None,
    max_attempts: int = 2,
) -> StructuredOutputGateway:
    """기본 gateway는 확률적 계약 위반을 한 번 재시도한다.

    ``max_attempts=1``을 주면 재시도 없이 원래 동작으로 돌아간다.
    """

    resolved_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not resolved_key:
        raise RuntimeError(
            "OPENAI_API_KEY 환경변수가 설정되지 않았습니다 — .env 또는 실행 "
            "환경에 키를 설정하세요."
        )
    gateway = OpenAIResponsesGateway(OpenAI(api_key=resolved_key))
    if max_attempts == 1:
        return gateway
    return RetryingGateway(gateway, max_attempts=max_attempts)
