#!/usr/bin/env python3
"""
Test script to verify multi-turn conversation performance with auto-compact.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile

from src.agent.conversation import Conversation
from src.providers.base import ChatResponse
from src.tool_system.defaults import build_default_registry
from src.tool_system.context import ToolContext
from src.tool_system.agent_loop import run_agent_loop, AgentLoopResult
from src.context_system.context_analyzer import analyze_context, get_context_window_for_model

def test_multi_turn_with_auto_compact():
    """Test multi-turn conversation with auto-compact triggered."""
    print("🧪 Testing multi-turn conversation with auto-compact...")
    
    # Create temporary workspace
    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir)
        registry = build_default_registry()
        context = ToolContext(workspace_root=workspace)
        
        # Create conversation with large initial messages to trigger auto-compact
        conversation = Conversation()
        
        # Add large messages that exceed threshold
        large_user_msg = "old user message " + ("x" * 3000)  # ~3k chars
        large_assistant_msg = "old assistant response " + ("y" * 3000)  # ~3k chars
        
        conversation.add_user_message(large_user_msg)
        conversation.add_assistant_message(large_assistant_msg)
        
        print(f"📊 Initial context size: {len(conversation.messages)} messages")
        
        # Mock provider
        mock_provider = MagicMock()
        mock_provider.model = "claude-sonnet-4-6"
        mock_provider.chat_stream_response.side_effect = NotImplementedError()
        
        # Provider responses
        mock_response1 = ChatResponse(
            content="I will help you with that.",
            model="claude-sonnet-4-6",
            usage={"input_tokens": 100, "output_tokens": 50},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "toolu_123",
                "name": "Write",
                "input": {
                    "file_path": str(workspace / "test.txt"),
                    "content": "Hello World"
                }
            }],
        )
        
        mock_response2 = ChatResponse(
            content="Task completed successfully!",
            model="claude-sonnet-4-6",
            usage={"input_tokens": 150, "output_tokens": 75},
            finish_reason="stop",
            tool_uses=None,
        )
        
        mock_provider.chat.side_effect = [mock_response1, mock_response2]
        
        # Track auto-compact calls
        compact_calls = []
        
        async def mock_compact(conv, provider, model, trigger="manual", **kwargs):
            compact_calls.append(trigger)
            print(f"🗜️  Auto-compact triggered with: {trigger}")
            # Simulate compact by keeping only summary
            conv.clear()
            conv.add_user_message("Conversation compacted due to size")
            return conv
        
        # Patch the auto-compact functions
        with patch("src.tool_system.agent_loop.estimate_context_tokens", return_value=1000), \
             patch("src.tool_system.agent_loop.get_auto_compact_threshold", return_value=500), \
             patch("src.tool_system.agent_loop.compact_conversation", side_effect=mock_compact):
            
            print("🚀 Starting agent loop...")
            result = run_agent_loop(
                conversation=conversation,
                provider=mock_provider,
                tool_registry=registry,
                tool_context=context,
                verbose=False,
            )
        
        print(f"✅ Agent loop completed")
        print(f"📈 Number of auto-compact calls: {len(compact_calls)}")
        print(f"🔄 Compact triggers: {compact_calls}")
        print(f"📝 Final response: {result.response_text}")
        
        # Verify results
        assert len(compact_calls) > 0, "Auto-compact should have been triggered"
        assert "auto" in compact_calls, "Auto-compact should have been triggered automatically"
        assert result.response_text == "Task completed successfully!", "Final response should match"
        
        print("✅ Test passed!")

def test_context_analysis():
    """Test context analysis functionality."""
    print("\n📊 Testing context analysis...")
    
    # Create test conversation
    conversation = Conversation()
    conversation.add_user_message("Hello, how are you?")
    conversation.add_assistant_message("I'm doing well, thank you!")
    
    # Analyze context
    result = analyze_context(
        conversation_api_messages=[
            {"role": "user", "content": "Hello, how are you?"},
            {"role": "assistant", "content": "I'm doing well, thank you!"},
        ],
        model="claude-sonnet-4-6",
        system_prompt="You are a helpful assistant.",
        tool_schemas=[
            {"name": "Read", "description": "Read a file", "input_schema": {"type": "object"}},
            {"name": "Write", "description": "Write a file", "input_schema": {"type": "object"}},
        ],
        claude_md_content="# Project Instructions\n\nThis is a test project.",
    )
    
    print(f"📊 Model: {result.model}")
    print(f"📊 Max tokens: {result.max_tokens:,}")
    print(f"📊 Total tokens: {result.total_tokens:,}")
    
    # Calculate free tokens
    free_tokens = result.max_tokens - result.total_tokens
    print(f"📊 Free tokens: {free_tokens:,}")
    
    # Show categories
    print("\n📋 Context categories:")
    for category in result.categories:
        print(f"  - {category.name}: {category.tokens:,} tokens")
    
    # Verify context window
    expected_window = get_context_window_for_model("claude-sonnet-4-6")
    assert result.max_tokens == expected_window, f"Expected {expected_window:,} tokens, got {result.max_tokens:,}"
    
    print("✅ Context analysis test passed!")

def test_token_estimation():
    """Test token estimation accuracy."""
    print("\n🔢 Testing token estimation...")
    
    from src.context_system.context_analyzer import estimate_context_tokens
    
    # Test with simple messages
    test_messages = [
        {"role": "user", "content": "Hello world"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    
    estimated_tokens = estimate_context_tokens(test_messages)
    print(f"📊 Estimated tokens: {estimated_tokens}")
    
    # Should be greater than 0
    assert estimated_tokens > 0, "Token estimation should be positive"
    
    print("✅ Token estimation test passed!")

def main():
    """Run all tests."""
    print("🚀 Starting multi-turn conversation tests...")
    
    try:
        test_context_analysis()
        test_token_estimation()
        test_multi_turn_with_auto_compact()
        
        print("\n🎉 All tests passed!")
        print("\n📋 Summary:")
        print("  ✅ Context analysis works correctly")
        print("  ✅ Token estimation is functional")
        print("  ✅ Auto-compact triggers properly in multi-turn conversations")
        print("  ✅ Agent loop handles compressed context correctly")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()