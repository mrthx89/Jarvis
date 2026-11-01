"""Hermes Agent Connector - Bridges audio queries to the autonomous Hermes Agent."""

import asyncio
import logging
from typing import AsyncGenerator

logger = logging.getLogger(__name__)


class HermesBridge:
    """Interfaces with Hermes Agent core via CLI with continuous session continuity."""

    def __init__(
        self,
        mode: str = "cli",
        hermes_binary: str = "hermes",
        session_name: str = "appliance",
        timeout: int = 60,
    ):
        self.mode = mode
        self.hermes_binary = hermes_binary
        self.session_name = session_name
        self.timeout = timeout

    async def query(self, prompt: str) -> str:
        """Send prompt to Hermes and receive response with session context."""
        if self.mode == "mock":
            await asyncio.sleep(0.3)
            return f"Sistem terhubung. Saya telah menerima instruksi Anda: '{prompt}'. Seluruh modul beroperasi normal."

        try:
            # Query Hermes using continuous session thread
            cmd = [
                self.hermes_binary,
                "chat",
                "-c",
                self.session_name,
                "--create-if-missing",
                "-q",
                prompt,
                "-Q",
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )

            if proc.returncode != 0:
                err_msg = stderr.decode().strip()
                logger.error(f"Hermes execution error: {err_msg}")
                return "Maaf, terjadi kendala saat memproses instruksi melalui Hermes Agent."

            raw_output = stdout.decode().strip()
            return self._clean_hermes_output(raw_output)

        except asyncio.TimeoutError:
            logger.error("Hermes execution timed out")
            return "Waktu pemrosesan melebihi batas. Mohon ulangi instruksi Anda."
        except Exception as e:
            logger.error(f"Failed to communicate with Hermes: {e}")
            return "Terjadi kesalahan internal saat menghubungkan ke agen."

    def _clean_hermes_output(self, raw_output: str) -> str:
        """Strip Hermes session banners and session_id footer to get clean text for TTS."""
        lines = raw_output.split("\n")
        content_lines = []

        for line in lines:
            trimmed = line.strip()
            if trimmed.startswith("↻ Resumed session") or trimmed.startswith("Session local-"):
                continue
            if trimmed.startswith("Model restored from session:"):
                continue
            if trimmed.startswith("session_id:"):
                continue
            content_lines.append(line)

        clean_text = "\n".join(content_lines).strip()
        return clean_text if clean_text else raw_output


def create_hermes_bridge(mode: str = "cli", hermes_binary: str = "hermes") -> HermesBridge:
    """Factory to instantiate the appropriate Hermes bridge."""
    return HermesBridge(mode=mode, hermes_binary=hermes_binary)
