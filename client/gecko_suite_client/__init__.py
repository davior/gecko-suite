"""Calling Gecko Suite from Notes, GAM and GVC."""

from gecko_suite_client.agent import Outcome, Stopped, run_agent
from gecko_suite_client.chat import Image, Reply, SuiteChat, SuiteConversation, ToolCall, ToolResult, ToolSpec
from gecko_suite_client.client import SuiteClient, SuiteError, SuiteNotConfigured, SuiteUnavailable
from gecko_suite_client.embed import SuiteEmbedder
from gecko_suite_client.media import MediaJob, SuiteMedia
from gecko_suite_client.speech import SuiteSpeech
from gecko_suite_client.search import WEB_SEARCH_TOOL, format_results, web_search

__all__ = [
    "Image", "MediaJob", "Outcome", "Reply", "Stopped", "SuiteChat", "SuiteClient", "SuiteConversation",
    "SuiteEmbedder", "SuiteError", "SuiteMedia", "SuiteSpeech", "SuiteNotConfigured", "SuiteUnavailable", "ToolCall", "ToolResult",
    "ToolSpec", "WEB_SEARCH_TOOL", "format_results", "run_agent", "web_search",
]
