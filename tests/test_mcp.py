"""Offline tests exercise the actual MCP SDK protocol and stdio transport."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from video_copy_extractor import mcp_server


def sample_result(count: int = 3) -> dict:
    return {
        "job_id": "local-job-id",
        "status": "extracted",
        "source": {"platform": "youtube", "title": "Example"},
        "segments": [
            {"id": index, "start": float(index), "end": float(index + 1),
             "text": f"{index} 100% 重复重复"}
            for index in range(count)
        ],
        "accuracy_verified": False,
        "completeness_verified": False,
        "output_dir": "private-runtime-directory",
    }


class McpProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_expose_no_arbitrary_paths_or_commands(self) -> None:
        async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
            listed = await client.list_tools()
        tools = {tool.name: tool for tool in listed.tools}
        self.assertEqual(set(tools), {"doctor", "extract_video", "read_transcript"})
        self.assertEqual(set(tools["extract_video"].inputSchema["properties"]), {"url", "language"})
        self.assertEqual(set(tools["doctor"].inputSchema["properties"]), set())

    async def test_extraction_configuration_is_operator_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = {
                "VCE_MODEL_PATH": str(Path(temporary) / "model"),
                "VCE_OUTPUT_DIR": str(Path(temporary) / "output"),
                "VCE_ENABLE_BROWSER": "0",
                "VCE_BROWSER_CHANNEL": "chrome",
            }
            with patch.dict(os.environ, environment, clear=True), \
                    patch.object(mcp_server.core, "extract_video", return_value=sample_result()) as extract:
                async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
                    response = await client.call_tool("extract_video", {"url": "https://example.invalid/video", "language": "zh"})
            self.assertFalse(response.isError)
            content = response.structuredContent
            self.assertEqual(content["transcript"], "0 100% 重复重复\n1 100% 重复重复\n2 100% 重复重复")
            self.assertFalse(content["has_more"])
            self.assertFalse(content["accuracy_verified"])
            self.assertTrue(content["content_is_untrusted"])
            self.assertNotIn("output_dir", content)
            self.assertNotEqual(content["job_id"], "local-job-id")
            extract.assert_called_once_with(
                "https://example.invalid/video", model=Path(environment["VCE_MODEL_PATH"]),
                browser=False, channel="chrome", language="zh",
                output_root=Path(environment["VCE_OUTPUT_DIR"]),
            )

    async def test_pagination_preserves_all_segments(self) -> None:
        expected = sample_result(150)
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(mcp_server.core, "extract_video", return_value=expected):
            async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
                first = (await client.call_tool("extract_video", {"url": "https://example.invalid/video"})).structuredContent
                self.assertTrue(first["has_more"])
                self.assertEqual(first["next_offset"], 100)
                second = (await client.call_tool("read_transcript", {
                    "job_id": first["job_id"], "offset": first["next_offset"],
                })).structuredContent
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_offset"])
        self.assertEqual(first["segments"] + second["segments"], expected["segments"])

    async def test_read_rejects_paths_unknown_jobs_and_invalid_ranges(self) -> None:
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(mcp_server.core, "extract_video", return_value=sample_result()):
            async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
                first = (await client.call_tool("extract_video", {"url": "https://example.invalid/video"})).structuredContent
                for arguments in (
                    {"job_id": "../../private.txt"},
                    {"job_id": first["job_id"], "offset": -1},
                    {"job_id": first["job_id"], "offset": 4},
                    {"job_id": first["job_id"], "limit": 0},
                    {"job_id": first["job_id"], "limit": 101},
                ):
                    with self.subTest(arguments=arguments):
                        self.assertTrue((await client.call_tool("read_transcript", arguments)).isError)

    async def test_cache_expires_old_jobs(self) -> None:
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(mcp_server.core, "extract_video", return_value=sample_result()):
            async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
                jobs = []
                for _ in range(mcp_server.MAX_JOBS + 1):
                    response = await client.call_tool("extract_video", {"url": "https://example.invalid/video"})
                    jobs.append(response.structuredContent["job_id"])
                self.assertTrue((await client.call_tool("read_transcript", {"job_id": jobs[0]})).isError)
                self.assertFalse((await client.call_tool("read_transcript", {"job_id": jobs[-1]})).isError)

    async def test_failed_extraction_and_invalid_config_are_errors(self) -> None:
        with patch.dict(os.environ, {"VCE_ENABLE_BROWSER": "invalid"}, clear=True), \
                patch.object(mcp_server.core, "extract_video") as extract:
            async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
                self.assertTrue((await client.call_tool("extract_video", {"url": "https://example.invalid/video"})).isError)
            extract.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(mcp_server.core, "extract_video", side_effect=RuntimeError("secret-token-and-private-path")):
            async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
                response = await client.call_tool("extract_video", {"url": "https://example.invalid/video"})
                self.assertTrue(response.isError)
                self.assertNotIn("secret-token-and-private-path", response.content[0].text)
                self.assertIn("private diagnostic details were withheld", response.content[0].text)
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(mcp_server.core, "extract_video", side_effect=mcp_server.core.ExtractionError("no_audio", "No audio available.")):
            async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
                response = await client.call_tool("extract_video", {"url": "https://example.invalid/video"})
                self.assertTrue(response.isError)
                self.assertIn("No audio available", response.content[0].text)

    async def test_cancelled_request_holds_lock_until_worker_finishes(self) -> None:
        first_started = threading.Event()
        first_release = threading.Event()
        second_started = threading.Event()

        def extract(url, **options):
            if url.endswith("first"):
                first_started.set()
                if not first_release.wait(timeout=5):
                    raise RuntimeError("test worker was not released")
            else:
                second_started.set()
            return sample_result()

        with patch.dict(os.environ, {}, clear=True), \
                patch.object(mcp_server.core, "extract_video", side_effect=extract):
            async with create_connected_server_and_client_session(mcp_server.create_server()) as client:
                request_id = client._request_id
                first = asyncio.create_task(client.call_tool("extract_video", {"url": "https://example.invalid/first"}))
                try:
                    self.assertTrue(await asyncio.to_thread(first_started.wait, 2))
                    await client.send_notification(types.ClientNotification(types.CancelledNotification(
                        params=types.CancelledNotificationParams(requestId=request_id, reason="test cancellation"),
                    )))
                    second = asyncio.create_task(client.call_tool("extract_video", {"url": "https://example.invalid/second"}))
                    await asyncio.sleep(0.1)
                    self.assertFalse(second_started.is_set())
                finally:
                    first_release.set()
                await asyncio.gather(first, return_exceptions=True)
                response = await asyncio.wait_for(second, timeout=3)
                self.assertFalse(response.isError)
                self.assertTrue(second_started.is_set())

    async def test_real_stdio_initialization_and_doctor(self) -> None:
        # A child Python process runs the real server and SDK stdio transport. The
        # doctor implementation is mocked so this test needs no installed engines.
        code = (
            "from video_copy_extractor import mcp_server; "
            "mcp_server.core.doctor = lambda: (print('dependency diagnostic'), "
            "{'status': 'offline-test', 'network_used': False})[1]; "
            "mcp_server.main()"
        )
        package_root = str(Path(__file__).resolve().parents[1])
        environment = {"PYTHONPATH": package_root, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
        parameters = StdioServerParameters(command=sys.executable, args=["-c", code], env=environment)
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=timedelta(seconds=20)) as client:
                initialized = await client.initialize()
                self.assertEqual(initialized.serverInfo.name, "video-copy-extractor")
                self.assertEqual(len((await client.list_tools()).tools), 3)
                response = await client.call_tool("doctor", {})
                self.assertFalse(response.isError)
                self.assertEqual(response.structuredContent, {"status": "offline-test", "network_used": False})

    async def test_shipped_cli_entrypoint_from_another_directory(self) -> None:
        import tempfile
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            parameters = StdioServerParameters(command=sys.executable,
                args=[str(root / "scripts/vce.py"), "mcp"], cwd=directory)
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream, read_timeout_seconds=timedelta(seconds=20)) as client:
                    await client.initialize()
                    response = await client.call_tool("doctor", {})
                    self.assertFalse(response.isError)
                    self.assertEqual(response.structuredContent["version"], "0.1.0")
                    self.assertFalse(response.structuredContent["network_tested"])


if __name__ == "__main__":
    unittest.main()
