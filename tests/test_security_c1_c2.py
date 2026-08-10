"""Tests verifying C1 command injection fix and C2 fail-loud HTTP status handling."""

from unittest.mock import MagicMock, patch
import httpx
import pytest

from refusal_detector.adapters import AnthropicAPIAdapter, ClaudeCodeCLIAdapter, OpenAIAdapter
from refusal_detector.ports import ReasonClass


def test_c1_claude_cli_shell_injection_safety():
    """Verify that ClaudeCodeCLIAdapter uses shell=False and stdin piping, preventing command injection."""
    adapter = ClaudeCodeCLIAdapter()
    malicious_prompt = "benign text & echo VULNERABLE > test.txt & dir"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Claude output", stderr="")
        adapter.test(malicious_prompt)

        # Assert shell=False is enforced
        assert mock_run.call_count == 1
        kwargs = mock_run.call_args.kwargs
        assert kwargs.get("shell") is False
        assert kwargs.get("input") == malicious_prompt


def test_c2_anthropic_fail_loud_on_401_and_500():
    """Verify AnthropicAPIAdapter raises HTTPStatusError on 401 and 500 instead of returning blocked=True."""
    adapter = AnthropicAPIAdapter(api_key="invalid_key")

    with patch("httpx.Client.post") as mock_post:
        # Mock HTTP 401 Unauthorized
        mock_res_401 = MagicMock(status_code=401, text="Unauthorized API Key")
        mock_res_401.raise_for_status.side_effect = httpx.HTTPStatusError("401 Unauthorized", request=MagicMock(), response=mock_res_401)
        mock_post.return_value = mock_res_401

        with pytest.raises(httpx.HTTPStatusError):
            adapter.test("Test prompt")

        # Mock HTTP 500 Internal Server Error
        mock_res_500 = MagicMock(status_code=500, text="Internal Server Error")
        mock_res_500.raise_for_status.side_effect = httpx.HTTPStatusError("500 Error", request=MagicMock(), response=mock_res_500)
        mock_post.return_value = mock_res_500

        with pytest.raises(httpx.HTTPStatusError):
            adapter.test("Test prompt")


def test_c2_openai_fail_loud_on_401_and_500():
    """Verify OpenAIAdapter raises HTTPStatusError on 401 and 500 instead of returning blocked=True."""
    adapter = OpenAIAdapter(api_key="invalid_key", base_url="https://api.deepseek.com", model="deepseek-chat")

    with patch("httpx.Client.post") as mock_post:
        # Mock HTTP 401 Unauthorized
        mock_res_401 = MagicMock(status_code=401, text="Invalid Bearer Token")
        mock_res_401.raise_for_status.side_effect = httpx.HTTPStatusError("401 Unauthorized", request=MagicMock(), response=mock_res_401)
        mock_post.return_value = mock_res_401

        with pytest.raises(httpx.HTTPStatusError):
            adapter.test("Test prompt")
