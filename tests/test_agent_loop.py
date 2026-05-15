"""Test agent loop with mocked provider to verify tool invocation."""

import os
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile

from src.agent.conversation import Conversation
from src.providers.base import ChatResponse
from src.skills.create import create_skill
from src.tool_system.defaults import build_default_registry
from src.tool_system.context import ToolContext
from src.tool_system.agent_loop import run_agent_loop, AgentLoopResult


class TestAgentLoop(unittest.TestCase):
    """Test agent loop logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        """Clean up test fixtures."""
        self.temp_dir.cleanup()

    def test_agent_loop_calls_tool(self):
        """Test agent loop correctly dispatches a tool call from mocked LLM."""
        conversation = Conversation()
        conversation.add_user_message("Create a file hello.py with content print('hello world')")

        # Mock provider
        mock_provider = MagicMock()
        mock_provider.chat_stream_response.side_effect = NotImplementedError()

        # First response: tool use Write
        mock_tool_use = {
            "id": "toolu_123",
            "name": "Write",
            "input": {
                "file_path": str(self.workspace / "hello.py"),
                "content": "print('hello world')"
            }
        }
        mock_response1 = ChatResponse(
            content="I will create the file.",
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[mock_tool_use],
        )

        # Second response: final text after tool result
        mock_response2 = ChatResponse(
            content="File created successfully!",
            model="test-model",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="stop",
            tool_uses=None,
        )

        mock_provider.chat.side_effect = [mock_response1, mock_response2]

        result = run_agent_loop(
            conversation=conversation,
            provider=mock_provider,
            tool_registry=self.registry,
            tool_context=self.context,
            verbose=False,
        )

        # Verify final response
        self.assertIsInstance(result, AgentLoopResult)
        self.assertEqual(result.response_text, "File created successfully!")

        # Verify provider was called twice
        self.assertEqual(mock_provider.chat.call_count, 2)

        # Verify file was created
        hello_py = self.workspace / "hello.py"
        self.assertTrue(hello_py.exists())
        self.assertEqual(hello_py.read_text(), "print('hello world')")

    def test_agent_loop_injects_skill_listing_when_skill_tool_available(self):
        """Model-invocable skills should be visible in the provider system prompt."""
        skills_dir = self.workspace / "skills"
        create_skill(
            directory=skills_dir,
            name="review-pr",
            description="Review pull requests",
            when_to_use="use when asked to inspect a PR",
            body="Review it",
        )
        conversation = Conversation()
        conversation.add_user_message("Please review this PR")

        mock_provider = MagicMock()
        mock_provider.chat_stream_response.side_effect = NotImplementedError()
        mock_provider.chat.return_value = ChatResponse(
            content="Done",
            model="test-model",
            usage=None,
            finish_reason="stop",
            tool_uses=None,
        )

        with patch.dict(os.environ, {"CLAWD_SKILLS_DIR": str(skills_dir)}):
            run_agent_loop(
                conversation=conversation,
                provider=mock_provider,
                tool_registry=self.registry,
                tool_context=self.context,
                verbose=False,
            )

        api_messages = mock_provider.chat.call_args.args[0]
        system_prompt = api_messages[0]["content"]
        self.assertIn("The following skills are available for use with the Skill tool", system_prompt)
        self.assertIn("review-pr: Review pull requests - use when asked to inspect a PR", system_prompt)
        skill_schema = next(
            schema
            for schema in mock_provider.chat.call_args.kwargs["tools"]
            if schema["name"] == "Skill"
        )
        self.assertIn("Available skills are listed in the system prompt", skill_schema["description"])
        self.assertNotIn("review-pr: Review pull requests - use when asked to inspect a PR", skill_schema["description"])

    def test_agent_loop_creates_hello_world(self):
        """Test agent loop creates hello.py and writes print('hello world')."""
        conversation = Conversation()
        conversation.add_user_message("Create a file hello.py with content print('hello world')")

        mock_provider = MagicMock()
        mock_provider.chat_stream_response.side_effect = NotImplementedError()

        # First response: tool use Write
        hello_path = self.workspace / "hello.py"
        mock_tool_write = {
            "id": "toolu_123",
            "name": "Write",
            "input": {
                "file_path": str(hello_path),
                "content": "print('hello world')"
            }
        }
        mock_response1 = ChatResponse(
            content="I will create the file.",
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[mock_tool_write],
        )

        # Second response: final
        mock_response2 = ChatResponse(
            content="File created successfully!",
            model="test-model",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="stop",
            tool_uses=None,
        )

        mock_provider.chat.side_effect = [mock_response1, mock_response2]

        result = run_agent_loop(
            conversation=conversation,
            provider=mock_provider,
            tool_registry=self.registry,
            tool_context=self.context,
            verbose=False,
        )

        self.assertIsInstance(result, AgentLoopResult)
        self.assertEqual(result.response_text, "File created successfully!")
        self.assertTrue(hello_path.exists())
        self.assertEqual(hello_path.read_text(), "print('hello world')")

    def test_agent_loop_stream_emits_final_text_chunks(self):
        """Streaming mode emits final response chunks without changing the result."""
        conversation = Conversation()
        conversation.add_user_message("Say hello")

        mock_provider = MagicMock()
        mock_provider.chat_stream_response.side_effect = NotImplementedError()
        mock_provider.chat.return_value = ChatResponse(
            content="Hello from Clawd!",
            model="test-model",
            usage={"input_tokens": 3, "output_tokens": 4},
            finish_reason="stop",
            tool_uses=None,
        )

        chunks: list[str] = []
        result = run_agent_loop(
            conversation=conversation,
            provider=mock_provider,
            tool_registry=self.registry,
            tool_context=self.context,
            stream=True,
            verbose=False,
            on_text_chunk=chunks.append,
        )

        self.assertEqual("".join(chunks), "Hello from Clawd!")
        self.assertEqual(result.response_text, "Hello from Clawd!")
        self.assertEqual(mock_provider.chat.call_count, 1)
        self.assertEqual(len(conversation.messages), 2)
        self.assertEqual(conversation.messages[-1].role, "assistant")
        self.assertEqual(conversation.messages[-1].content, "Hello from Clawd!")

    def test_agent_loop_stream_only_emits_final_turn_text(self):
        """Streaming mode skips interim tool-planning text and emits the final answer only."""
        conversation = Conversation()
        conversation.add_user_message("Create a file hello.py with content print('hello world')")

        mock_provider = MagicMock()
        mock_provider.chat_stream_response.side_effect = NotImplementedError()
        hello_path = self.workspace / "hello.py"
        mock_response1 = ChatResponse(
            content="I will create the file.",
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "toolu_123",
                "name": "Write",
                "input": {
                    "file_path": str(hello_path),
                    "content": "print('hello world')",
                },
            }],
        )
        mock_response2 = ChatResponse(
            content="File created successfully!",
            model="test-model",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="stop",
            tool_uses=None,
        )
        mock_provider.chat.side_effect = [mock_response1, mock_response2]

        chunks: list[str] = []
        result = run_agent_loop(
            conversation=conversation,
            provider=mock_provider,
            tool_registry=self.registry,
            tool_context=self.context,
            stream=True,
            verbose=False,
            on_text_chunk=chunks.append,
        )

        self.assertEqual("".join(chunks), "File created successfully!")
        self.assertEqual(result.response_text, "File created successfully!")
        self.assertTrue(hello_path.exists())

    def test_agent_loop_stream_uses_structured_provider_streaming_for_tool_turns(self):
        """Structured provider streaming can emit pre-tool text and final text across turns."""
        conversation = Conversation()
        conversation.add_user_message("Create hello.py")

        provider = MagicMock()
        hello_path = self.workspace / "hello.py"

        stream_responses = [
            ChatResponse(
                content="I will create the file.",
                model="test-model",
                usage={"input_tokens": 10, "output_tokens": 20},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "toolu_123",
                    "name": "Write",
                    "input": {
                        "file_path": str(hello_path),
                        "content": "print('hello world')",
                    },
                }],
            ),
            ChatResponse(
                content="File created successfully!",
                model="test-model",
                usage={"input_tokens": 30, "output_tokens": 10},
                finish_reason="stop",
                tool_uses=None,
            ),
        ]

        def stream_side_effect(messages, tools=None, on_text_chunk=None, **kwargs):
            response = stream_responses.pop(0)
            if on_text_chunk is not None and response.content:
                on_text_chunk(response.content)
            return response

        provider.chat_stream_response.side_effect = stream_side_effect
        provider.chat.side_effect = AssertionError("chat() should not be used when structured streaming is available")

        chunks: list[str] = []
        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            stream=True,
            verbose=False,
            on_text_chunk=chunks.append,
        )

        self.assertEqual("".join(chunks), "I will create the file.File created successfully!")
        self.assertEqual(result.response_text, "File created successfully!")
        self.assertEqual(provider.chat_stream_response.call_count, 2)
        self.assertTrue(hello_path.exists())

    def test_agent_loop_stream_falls_back_when_structured_streaming_is_unavailable(self):
        """If the provider lacks structured streaming, the stable synchronous path still works."""
        conversation = Conversation()
        conversation.add_user_message("Say hello")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Hello from fallback!",
            model="test-model",
            usage={"input_tokens": 2, "output_tokens": 3},
            finish_reason="stop",
            tool_uses=None,
        )

        chunks: list[str] = []
        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            stream=True,
            verbose=False,
            on_text_chunk=chunks.append,
        )

        self.assertEqual("".join(chunks), "Hello from fallback!")
        self.assertEqual(result.response_text, "Hello from fallback!")
        provider.chat.assert_called_once()

    def test_auto_compact_runs_before_provider_call_and_rebuilds_openai_messages(self):
        """Oversized context triggers auto compact and provider sees compacted messages."""
        conversation = Conversation()
        conversation.add_user_message("old user " + ("x" * 2000))
        conversation.add_assistant_message("old assistant " + ("y" * 2000))

        provider = MagicMock()
        provider.model = "test-1k"
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="done",
            model="test-1k",
            usage={},
            finish_reason="stop",
            tool_uses=None,
        )

        async def fake_compact(conv, _provider, _model, trigger="manual", **_kwargs):
            self.assertEqual(trigger, "auto")
            conv.clear()
            conv.add_user_message("summary after compact")

        with patch("src.tool_system.agent_loop.estimate_context_tokens", return_value=1000), \
             patch("src.tool_system.agent_loop.get_auto_compact_threshold", return_value=10), \
             patch("src.tool_system.agent_loop.compact_conversation", side_effect=fake_compact) as compact_mock:
            result = run_agent_loop(
                conversation=conversation,
                provider=provider,
                tool_registry=self.registry,
                tool_context=self.context,
                verbose=False,
            )

        self.assertEqual(result.response_text, "done")
        compact_mock.assert_called_once()
        sent_messages = provider.chat.call_args.args[0]
        sent_text = "\n".join(str(m.get("content", "")) for m in sent_messages)
        self.assertIn("summary after compact", sent_text)
        self.assertNotIn("old user", sent_text)

    def test_auto_compact_failure_continues_original_request_once(self):
        """A compact failure does not block the provider call or loop forever."""
        conversation = Conversation()
        conversation.add_user_message("old user")
        conversation.add_assistant_message("old assistant")

        provider = MagicMock()
        provider.model = "test-1k"
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="done",
            model="test-1k",
            usage={},
            finish_reason="stop",
            tool_uses=None,
        )

        async def fail_compact(*_args, **_kwargs):
            raise RuntimeError("compact failed")

        with patch("src.tool_system.agent_loop.estimate_context_tokens", return_value=1000), \
             patch("src.tool_system.agent_loop.get_auto_compact_threshold", return_value=10), \
             patch("src.tool_system.agent_loop.compact_conversation", side_effect=fail_compact) as compact_mock:
            result = run_agent_loop(
                conversation=conversation,
                provider=provider,
                tool_registry=self.registry,
                tool_context=self.context,
                verbose=False,
            )

        self.assertEqual(result.response_text, "done")
        compact_mock.assert_called_once()
        provider.chat.assert_called_once()
        sent_messages = provider.chat.call_args.args[0]
        sent_text = "\n".join(str(m.get("content", "")) for m in sent_messages)
        self.assertIn("old user", sent_text)


if __name__ == "__main__":
    unittest.main()
