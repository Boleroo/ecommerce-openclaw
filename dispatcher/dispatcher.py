import logging
import time
from dataclasses import dataclass, field
from uuid import uuid4

from pydantic import ValidationError

from .backend_client import BackendTransportError
from .observation import Observation, ToolFailure, make_error
from .registry import REGISTRY
from .schemas import Identity, ToolCall
from .validator import TRANSIENT_CODES, validate_postconditions, validate_response


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 2
    backoff_seconds: float = 0.1

    def __post_init__(self):
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 2:
            raise ValueError("max_retries must be an integer between 0 and 2")
        if not 0 <= self.backoff_seconds <= 1:
            raise ValueError("backoff_seconds must be between 0 and 1")


@dataclass
class Execution:
    trace_id: str
    identity: Identity | None = None
    requests: list = field(default_factory=list)
    retries_used: int = 0
    write_attempted: bool = False


class Dispatcher:
    def __init__(self, backend, audit, registry=REGISTRY, retry_policy=None, sleep=time.sleep):
        self.backend = backend
        self.audit = audit
        self.registry = registry
        self.retry_policy = retry_policy or RetryPolicy()
        self.sleep = sleep

    def execute(self, tool, arguments, *, token=None):
        return self.dispatch({"tool": tool, "arguments": arguments}, token=token)

    def dispatch(self, payload, *, token=None):
        state = Execution(trace_id=str(uuid4()))
        tool_name = "<invalid>"
        if isinstance(payload, dict) and isinstance(payload.get("tool"), str) and len(payload["tool"]) <= 128:
            tool_name = payload["tool"]
        audited_name = tool_name if self.registry.get(tool_name) else "<unknown>"
        try:
            self.audit.start(state.trace_id, audited_name)
        except Exception as failure:
            self._log_exception(state, "audit_start", failure)
            return self._failure(tool_name, state, "AUDIT_UNAVAILABLE")
        try:
            call = ToolCall.model_validate(payload)
            tool = self.registry.get(call.tool)
            if tool is None:
                raise ToolFailure("UNKNOWN_TOOL")
            arguments = tool.input_model.model_validate(call.arguments)
            if not isinstance(token, str) or not token or any(character.isspace() for character in token) or not token.isascii():
                raise ToolFailure("UNAUTHENTICATED")
            state.identity = self._request(
                state, "GET", "/v1/auth/context", Identity, token=token,
            )
            if not tool.required_permissions <= set(state.identity.permissions):
                raise ToolFailure("PERMISSION_DENIED")
            before = self._preflight(tool, arguments, token, state)
            result = self._invoke(tool, arguments, token, state)
            try:
                validate_postconditions(tool, arguments, result, before)
            except ToolFailure:
                if state.write_attempted:
                    raise ToolFailure("WRITE_OUTCOME_UNKNOWN") from None
                raise
            observation = Observation(
                success=True, tool=call.tool, error=None,
                data=result.model_dump(mode="json"), trace_id=state.trace_id,
            )
        except ValidationError as failure:
            code = "WRITE_OUTCOME_UNKNOWN" if state.write_attempted else "INVALID_ARGUMENT"
            if state.write_attempted:
                self._log_exception(state, "write_validation", failure)
            observation = self._failure(tool_name, state, code)
        except ToolFailure as failure:
            observation = Observation(success=False, tool=tool_name, error=failure.error, data=None, trace_id=state.trace_id)
        except Exception as failure:
            self._log_exception(state, "dispatch", failure)
            observation = self._failure(tool_name, state, "WRITE_OUTCOME_UNKNOWN" if state.write_attempted else "INTERNAL_ERROR")
        try:
            self.audit.finish(observation, state.identity, state.requests)
        except Exception as failure:
            self._log_exception(state, "audit_finish", failure)
            return self._failure(tool_name, state, "WRITE_OUTCOME_UNKNOWN" if state.write_attempted else "AUDIT_UNAVAILABLE")
        return observation

    @staticmethod
    def _failure(tool_name, state, code):
        return Observation(success=False, tool=tool_name, error=make_error(code), data=None, trace_id=state.trace_id)

    def _preflight(self, tool, arguments, token, state):
        if getattr(arguments, "idempotency_key", None) is not None:
            return None
        if tool.name == "refund_order":
            read_tool = self.registry.get("get_order")
            read_arguments = read_tool.input_model(order_id=arguments.order_id)
            order = self._invoke(read_tool, read_arguments, token, state)
            validate_postconditions(read_tool, read_arguments, order)
            if order.status not in ("paid", "shipped", "delivered"):
                raise ToolFailure("INVALID_BUSINESS_STATE")
            if arguments.amount > order.remaining_refundable_amount:
                raise ToolFailure("REFUND_AMOUNT_EXCEEDED")
            return order
        if tool.name == "change_price":
            read_tool = self.registry.get("product_stats")
            read_arguments = read_tool.input_model(sku=arguments.sku)
            product = self._invoke(read_tool, read_arguments, token, state)
            validate_postconditions(read_tool, read_arguments, product)
            return product
        return None

    def _invoke(self, tool, arguments, token, state):
        values = arguments.model_dump(mode="json", exclude_none=True)
        path_values = {name: values.pop(name) for name in tool.path_fields}
        endpoint = tool.endpoint.format(**path_values)
        try:
            result = self._request(
                state, tool.method, endpoint, tool.output_model, token=token,
                params=values if tool.method == "GET" else None,
                json_body=values if tool.method != "GET" else None,
                success_status=tool.success_status,
            )
        except ToolFailure as failure:
            approval = failure.error.approval
            if approval is not None:
                expected = arguments.model_dump(exclude={"idempotency_key", *tool.path_fields})
                actual = approval.parameters.model_dump(exclude={"expected_price"})
                if approval.operation != tool.name or approval.tenant_id != state.identity.tenant_id or approval.initiator_id != state.identity.user_id or approval.resource_id not in path_values.values() or actual != expected:
                    raise ToolFailure("WRITE_OUTCOME_UNKNOWN") from None
            raise
        if hasattr(result, "approval_level") and result.tenant_id != state.identity.tenant_id:
            raise ToolFailure("WRITE_OUTCOME_UNKNOWN" if tool.method != "GET" else "INVALID_BACKEND_RESPONSE")
        return result

    def _request(self, state, method, endpoint, output_model, *, token, params=None, json_body=None, success_status=200):
        while True:
            attempt = {"method": method, "endpoint": endpoint}
            state.requests.append(attempt)
            if method != "GET":
                attempt["outcome"] = "dispatch_pending"
                try:
                    self.audit.checkpoint(state.trace_id, state.identity, state.requests)
                except Exception as failure:
                    attempt["outcome"] = "AUDIT_UNAVAILABLE"
                    self._log_exception(state, "audit_checkpoint", failure)
                    raise ToolFailure("AUDIT_UNAVAILABLE") from None
                state.write_attempted = True
            try:
                response = self.backend.request(
                    method, endpoint, token=token, trace_id=state.trace_id,
                    params=params, json_body=json_body,
                )
                attempt["status"] = response.status_code
                result = validate_response(
                    response, output_model, success_status, is_write=method != "GET",
                )
                attempt["outcome"] = "validated"
                return result
            except BackendTransportError as failure:
                error = ToolFailure(failure.code)
            except ToolFailure as failure:
                error = failure
            except Exception as failure:
                code = "WRITE_OUTCOME_UNKNOWN" if method != "GET" else "INTERNAL_ERROR"
                attempt["outcome"] = code
                self._log_exception(state, "backend_request", failure)
                raise
            attempt["outcome"] = error.error.code
            if method != "GET" and error.error.category in ("INFRASTRUCTURE", "RESPONSE"):
                raise ToolFailure("WRITE_OUTCOME_UNKNOWN") from None
            if method == "GET" and error.error.recoverable and error.error.code in TRANSIENT_CODES and state.retries_used < self.retry_policy.max_retries:
                delay = self.retry_policy.backoff_seconds * (2 ** state.retries_used)
                state.retries_used += 1
                self.sleep(delay)
                continue
            raise error

    @staticmethod
    def _log_exception(state, stage, failure):
        logger.error(
            "tool execution exception trace_id=%s stage=%s exception_type=%s",
            state.trace_id, stage, type(failure).__name__,
        )
