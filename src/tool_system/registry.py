from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol

from ..context_system.context_analyzer import DEFAULT_TOOL_RESULT_TRUNCATION_ENABLED
from .context import ToolContext
from .permission_handler import PermissionResult
from .protocol import ToolCall, ToolResult
from .schema_validation import validate_json_schema


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    aliases: tuple[str, ...] = ()
    is_read_only: bool = False
    is_destructive: bool = False
    strict: bool = False
    max_result_size_chars: int = 20_000


class Tool(Protocol):
    def spec(self) -> ToolSpec: ...

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolResult: ...

    def check_permissions(
        self, tool_input: dict[str, Any], context: ToolContext
    ) -> PermissionResult:
        """Check if this tool has permission to run.

        Args:
            tool_input: The input arguments for the tool.
            context: The tool execution context.

        Returns:
            PermissionResult indicating allow, deny, or ask.
        """
        return PermissionResult.allow()


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False))
    except Exception:
        return len(str(value))


def _truncate_middle(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    marker = f"[truncated {len(text) - max_chars} chars]"
    if max_chars <= len(marker) + 2:
        return text[:max_chars]
    side_budget = max_chars - len(marker)
    omitted = len(text) - side_budget
    marker = f"[truncated {omitted} chars]"
    side_budget = max_chars - len(marker)
    head = side_budget // 2
    tail = side_budget - head
    return f"{text[:head]}{marker}{text[-tail:]}"


def _preview_value(value: Any, max_chars: int) -> dict[str, Any]:
    preview = _truncate_middle(str(value), max(100, max_chars - 80))
    return {
        "truncated": True,
        "original_type": type(value).__name__,
        "preview": preview,
    }


def _truncate_structured_strings(value: Any, max_string_chars: int) -> Any:
    if isinstance(value, str):
        return _truncate_middle(value, max_string_chars)
    if isinstance(value, list):
        return [_truncate_structured_strings(item, max_string_chars) for item in value]
    if isinstance(value, tuple):
        return [_truncate_structured_strings(item, max_string_chars) for item in value]
    if isinstance(value, dict):
        return {
            key: _truncate_structured_strings(val, max_string_chars)
            for key, val in value.items()
        }
    return value


def truncate_tool_output(output: Any, max_chars: int) -> Any:
    """Limit tool output size while keeping normal success/error semantics."""
    if max_chars <= 0:
        return output
    if isinstance(output, str):
        return _truncate_middle(output, max_chars)
    if not isinstance(output, (dict, list, tuple)):
        return output
    if _json_size(output) <= max_chars:
        return output

    max_string_chars = max(80, max_chars // 4)
    truncated = _truncate_structured_strings(output, max_string_chars)
    if _json_size(truncated) <= max_chars:
        return truncated
    return _preview_value(output, max_chars)


def _tool_result_truncation_enabled(context: ToolContext) -> bool:
    config = getattr(context, "config", {}) or {}
    if "session" in config and isinstance(config["session"], dict):
        config = config["session"]
    return bool(
        config.get(
            "tool_result_truncation_enabled",
            DEFAULT_TOOL_RESULT_TRUNCATION_ENABLED,
        )
    )


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        self._tools: list[Tool] = []
        self._by_name: dict[str, Tool] = {}
        if tools:
            for tool in tools:
                self.register(tool)

    def register(self, tool: Tool) -> None:
        spec = tool.spec()
        key = spec.name.lower()
        if key in self._by_name:
            raise ValueError(f"duplicate tool name: {spec.name}")
        self._tools.append(tool)
        self._by_name[key] = tool
        for alias in spec.aliases:
            alias_key = alias.lower()
            if alias_key in self._by_name:
                raise ValueError(f"duplicate tool alias: {alias}")
            self._by_name[alias_key] = tool

    def list_specs(self) -> list[ToolSpec]:
        return [tool.spec() for tool in self._tools]

    def get(self, name: str) -> Tool | None:
        return self._by_name.get(name.lower())

    def dispatch(self, call: ToolCall, context: ToolContext) -> ToolResult:
        def _result(
            *,
            name: str,
            output: Any,
            is_error: bool = False,
            tool_use_id: str | None = None,
            content_type: str = "json",
            max_chars: int = 20_000,
        ) -> ToolResult:
            if _tool_result_truncation_enabled(context):
                output = truncate_tool_output(output, max_chars)
            return ToolResult(
                name=name,
                output=output,
                is_error=is_error,
                tool_use_id=tool_use_id,
                content_type=content_type,
            )

        tool = self.get(call.name)
        if tool is None:
            return _result(
                name=call.name,
                output={"error": f"unknown tool: {call.name}"},
                is_error=True,
                tool_use_id=call.tool_use_id,
            )
        spec = tool.spec()
        context.ensure_tool_allowed(spec.name)
        validate_json_schema(call.input, spec.input_schema, root_name=spec.name)

        # Check permissions before running
        permission_result = tool.check_permissions(call.input, context) if hasattr(tool, 'check_permissions') else PermissionResult.allow()
        if permission_result.behavior.value == "deny":
            return _result(
                name=spec.name,
                output={"error": permission_result.message or "permission denied"},
                is_error=True,
                tool_use_id=call.tool_use_id,
                max_chars=spec.max_result_size_chars,
            )
        if permission_result.behavior.value == "ask":
            # Need user interaction
            if context.permission_handler is None:
                # No handler available, deny by default
                return _result(
                    name=spec.name,
                    output={"error": permission_result.message or "permission required but no handler available"},
                    is_error=True,
                    tool_use_id=call.tool_use_id,
                    max_chars=spec.max_result_size_chars,
                )
            # Call the permission handler
            allowed, _ = context.permission_handler(
                spec.name,
                permission_result.message or f"Tool '{spec.name}' requires permission",
                permission_result.suggestion,
            )
            if not allowed:
                return _result(
                    name=spec.name,
                    output={"error": "permission denied by user"},
                    is_error=True,
                    tool_use_id=call.tool_use_id,
                    max_chars=spec.max_result_size_chars,
                )
            # User allowed - proceed with potentially updated input
            if permission_result.updated_input:
                call = ToolCall(
                    name=call.name,
                    input=permission_result.updated_input,
                    tool_use_id=call.tool_use_id,
                )

        result = tool.run(call.input, context)
        output = result.output
        if _tool_result_truncation_enabled(context):
            output = truncate_tool_output(output, spec.max_result_size_chars)
        if result.tool_use_id is None and call.tool_use_id is not None:
            return ToolResult(
                name=result.name,
                output=output,
                is_error=result.is_error,
                tool_use_id=call.tool_use_id,
                content_type=result.content_type,
            )
        return ToolResult(
            name=result.name,
            output=output,
            is_error=result.is_error,
            tool_use_id=result.tool_use_id,
            content_type=result.content_type,
        )
