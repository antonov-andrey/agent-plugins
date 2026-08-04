"""Secret-safe bounded GraphQL transport for proven Linear MCP gaps."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import Message
import json
import math
import random
import time
import urllib.error
import urllib.request

from json_contract import JsonContractError, json_load_strict


class LinearTransportError(RuntimeError):
    """Report a typed Linear transport failure without external payload data."""


class LinearAuthenticationError(LinearTransportError):
    """Report an invalid or insufficient Linear credential."""


class LinearRateLimitError(LinearTransportError):
    """Report exhausted bounded Linear rate-limit retries."""


class LinearResponseError(LinearTransportError):
    """Report a malformed or rejected Linear GraphQL response."""


@dataclass(frozen=True, slots=True)
class LinearRetryPolicy:
    """Bound one safe repeat policy for an idempotent GraphQL operation."""

    attempt_count: int = 4
    initial_delay_seconds: float = 1.0
    maximum_delay_seconds: float = 20.0

    def __post_init__(self) -> None:
        """Validate bounded retry values."""

        if isinstance(self.attempt_count, bool) or not 1 <= self.attempt_count <= 8:
            raise ValueError("attempt_count must be within 1..8")
        delay_by_name_map = {
            "initial_delay_seconds": self.initial_delay_seconds,
            "maximum_delay_seconds": self.maximum_delay_seconds,
        }
        for name, value in delay_by_name_map.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be one finite non-negative number")
        if self.maximum_delay_seconds < self.initial_delay_seconds:
            raise ValueError("maximum_delay_seconds must not be less than initial_delay_seconds")


@dataclass(frozen=True, slots=True)
class LinearHttpResponse:
    """Carry one decoded GraphQL HTTP response."""

    payload: object
    headers: Message


class LinearGraphQLTransport:
    """Execute exact typed operations against the official Linear endpoint."""

    __slots__ = ("_clock", "_credential", "_opener", "_random", "_retry", "_sleep")

    ENDPOINT = "https://api.linear.app/graphql"

    def __init__(
        self,
        credential: str,
        *,
        retry: LinearRetryPolicy | None = None,
        opener: Callable[..., object] = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
        random_source: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Initialize a one-process secret-bearing transport.

        Args:
            credential: One in-memory API key or OAuth bearer token.
            retry: Safe bounded repeat policy.
            opener: HTTP opener dependency.
            sleeper: Delay dependency.
            random_source: Jitter dependency.
            clock: Current Unix time dependency used for provider reset guidance.
        """

        if (
            not isinstance(credential, str)
            or not credential
            or any(character in credential for character in ("\x00", "\n", "\r"))
        ):
            raise LinearAuthenticationError("Linear credential is absent or malformed")
        self._credential = credential
        self._retry = retry or LinearRetryPolicy()
        self._opener = opener
        self._sleep = sleeper
        self._random = random_source
        self._clock = clock

    def __repr__(self) -> str:
        """Return a representation that never includes credentials.

        Returns:
            Redacted transport representation.
        """

        return "LinearGraphQLTransport(endpoint='https://api.linear.app/graphql', credential=<redacted>)"

    def execute(
        self,
        *,
        operation_name: str,
        document: str,
        variables: Mapping[str, object],
        repeat_safe: bool,
    ) -> dict[str, object]:
        """Execute one complete GraphQL operation with typed failures.

        Args:
            operation_name: Exact GraphQL operation name.
            document: Static operation document.
            variables: Validated operation variables.
            repeat_safe: Whether the complete operation is proven safe to repeat.

        Returns:
            GraphQL data object.
        """

        if not operation_name or not document or not isinstance(variables, Mapping):
            raise LinearResponseError("GraphQL operation contract is incomplete")
        encoded = json.dumps(
            {
                "operationName": operation_name,
                "query": document,
                "variables": dict(variables),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        attempt_limit = self._retry.attempt_count if repeat_safe else 1
        is_rate_limited = False
        for attempt_index in range(attempt_limit):
            try:
                response = self._request(encoded)
                return self._data_extract(
                    response.payload,
                    headers=response.headers,
                    repeat_safe=repeat_safe,
                    attempt_index=attempt_index,
                )
            except LinearAuthenticationError:
                raise
            except TransientLinearFailure as error:
                is_rate_limited = error.rate_limited
                if attempt_index + 1 >= attempt_limit:
                    break
                self._sleep(self._delay_get(attempt_index, headers=error.headers))
        if is_rate_limited:
            raise LinearRateLimitError("Linear rate limit remained unavailable after bounded retries")
        raise LinearTransportError("Linear operation failed after bounded safe retries")

    def _request(self, encoded: bytes) -> LinearHttpResponse:
        """Perform one HTTP attempt and decode its JSON body.

        Args:
            encoded: Complete request body.

        Returns:
            Decoded payload and response headers.
        """

        request = urllib.request.Request(
            self.ENDPOINT,
            data=encoded,
            headers={
                "Authorization": self._credential,
                "Content-Type": "application/json",
                "User-Agent": "linear-agent-tools/0.1",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=30) as response:
                headers = response.headers
                raw = response.read()
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise LinearAuthenticationError("Linear rejected the supplied credential or scope") from None
            if error.code == 400:
                try:
                    return LinearHttpResponse(payload=json_load_strict(error.read()), headers=error.headers)
                except JsonContractError:
                    raise LinearResponseError("Linear GraphQL returned malformed JSON") from None
            if error.code == 429 or error.code in {408, 500, 502, 503, 504}:
                raise TransientLinearFailure(rate_limited=error.code == 429, headers=error.headers) from None
            raise LinearResponseError(f"Linear GraphQL returned unexpected HTTP status {error.code}") from None
        except TimeoutError, urllib.error.URLError, ConnectionError:
            raise TransientLinearFailure(rate_limited=False, headers=Message()) from None
        try:
            return LinearHttpResponse(payload=json_load_strict(raw), headers=headers)
        except JsonContractError:
            raise LinearResponseError("Linear GraphQL returned malformed JSON") from None

    def _data_extract(
        self,
        payload: object,
        *,
        headers: Message,
        repeat_safe: bool,
        attempt_index: int,
    ) -> dict[str, object]:
        """Extract a complete GraphQL data object or classify its errors.

        Args:
            payload: Decoded GraphQL response.
            headers: Response headers containing optional provider retry guidance.
            repeat_safe: Whether the operation may repeat.
            attempt_index: Zero-based attempt index.

        Returns:
            The GraphQL data object.
        """

        if not isinstance(payload, dict):
            raise LinearResponseError("Linear GraphQL response root must be an object")
        error_list = payload.get("errors", [])
        if error_list:
            if not isinstance(error_list, list) or any(not isinstance(item, dict) for item in error_list):
                raise LinearResponseError("Linear GraphQL errors have another shape")
            code_set = {
                item.get("extensions", {}).get("code")
                for item in error_list
                if isinstance(item.get("extensions"), dict)
            }
            if code_set & {"AUTHENTICATION_ERROR", "FORBIDDEN", "UNAUTHENTICATED"}:
                raise LinearAuthenticationError("Linear rejected the supplied credential or scope")
            if code_set & {"RATELIMITED", "RATE_LIMITED"} and repeat_safe:
                raise TransientLinearFailure(rate_limited=True, headers=headers)
            raise LinearResponseError(
                f"Linear GraphQL rejected operation at attempt {attempt_index + 1} with typed provider errors"
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise LinearResponseError("Linear GraphQL response has no data object")
        return data

    def _delay_get(self, attempt_index: int, *, headers: Message) -> float:
        """Return one bounded retry delay using provider guidance when valid.

        Args:
            attempt_index: Zero-based attempt index.
            headers: Response headers.

        Returns:
            Delay in seconds.
        """

        retry_after = headers.get("Retry-After")
        if retry_after is not None:
            try:
                return min(max(float(retry_after), 0.0), self._retry.maximum_delay_seconds)
            except ValueError:
                pass
        reset = headers.get("X-RateLimit-Endpoint-Requests-Reset") or headers.get("X-RateLimit-Requests-Reset")
        if reset is not None:
            try:
                until_reset = max(float(reset) / 1000.0 - self._clock(), 0.0)
                return min(until_reset, self._retry.maximum_delay_seconds)
            except ValueError:
                pass
        exponential = min(
            self._retry.initial_delay_seconds * (2**attempt_index),
            self._retry.maximum_delay_seconds,
        )
        return exponential * (0.75 + 0.5 * self._random())


class TransientLinearFailure(Exception):
    """Carry one internal retry classification without external error text."""

    def __init__(self, *, rate_limited: bool, headers: Message) -> None:
        """Initialize the retry classification.

        Args:
            rate_limited: Whether provider quota caused the failure.
            headers: Safe response headers used only for retry guidance.
        """

        super().__init__()
        self.rate_limited = rate_limited
        self.headers = headers
