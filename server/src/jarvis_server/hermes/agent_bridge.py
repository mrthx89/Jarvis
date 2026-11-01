"""Hermes Agent Connector - Bridges audio queries to the autonomous Hermes Agent."""

import asyncio
import logging
from typing import AsyncGenerator

logger = logging.getLogger(__name__)


class HermesBridge:
    """Interfaces with Hermes Agent core via CLI or daemon."""

    def __init__(self, mode: str = "cli", hermes_binary: str = "hermes", timeout: int = 60):
        self.mode = mode
        self.hermes_binary = hermes_binary
        self.timeout = timeout

    async def query(self, prompt: str) -> str:
        """Send prompt to Hermes and receive response."""
        if self.mode == "mock":
            await asyncio.sleep(0.3)
            return f"Sistem terhubung. Saya telah menerima instruksi Anda: '{prompt}'. Seluruh modul beroperasi normal."

        try:
            # Execute one-shot query via hermes chat -q
            proc = await asyncio.create_subprocess_exec(
                self.hermes_binary,
                "chat",
                "-q",
                prompt,
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

            return stdout.decode().strip()

        except asyncio.TimeoutError:
            logger.error("Hermes execution timed out")
            return "Waktu pemrosesan melebihi batas. Mohon ulangi instruksi Anda."
        except Exception as e:
            logger.error(f"Failed to communicate with Hermes: {e}")
            return "Terjadi kesalahan internal saat menghubungkan ke agen."


def create_hermes_bridge(mode: str = "cli", hermes_binary: str = "hermes") -> HermesBridge:
    """Factory to instantiate the appropriate Hermes bridge."""
    return HermesBridge(mode=mode, hermes_binary=hermes_binary)
