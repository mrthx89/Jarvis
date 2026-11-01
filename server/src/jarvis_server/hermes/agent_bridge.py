"""Hermes Agent Connector - Bridges audio queries to the autonomous Hermes Agent."""

import asyncio
import logging
import os
import signal

from typing import AsyncGenerator, Awaitable, Callable, Optional

from .sentence_chunker import extract_next_sentence, split_into_sentences

logger = logging.getLogger(__name__)


class HermesBridge:
    """Interfaces with Hermes Agent core via CLI with continuous session continuity."""

    def __init__(
        self,
        mode: str = "cli",
        hermes_binary: str = "hermes",
        session_name: str = "nexo",
        timeout: float = 60.0,
    ):
        self.mode = mode
        self.hermes_binary = hermes_binary
        self.session_name = session_name
        self._session_id: Optional[str] = None
        self.timeout = timeout
        self.query_active: int = 0
        self.on_reply: Optional[Callable[[str], Awaitable[None]]] = None
        self.hermes_home: Optional[str] = None

    @property
    def session_name(self) -> str:
        return "nexo"

    @session_name.setter
    def session_name(self, name: str) -> None:
        if not isinstance(name, str) or name.strip() != "nexo":
            raise ValueError("Session routing is locked to nexo")

    def get_session_name(self) -> str:
        return self.session_name

    def set_session_name(self, name: str) -> None:
        self.session_name = name

    def bind_session_id(self, session_id: str) -> None:
        """Pin the startup-resolved canonical Nexo ID for this bridge's lifetime."""
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("A nonempty nexo session ID is required")
        session_id = session_id.strip()
        if self._session_id is not None and self._session_id != session_id:
            raise ValueError("Session routing is locked to the bound nexo session ID")
        self._session_id = session_id

    def get_session_id(self) -> Optional[str]:
        return self._session_id

    async def query(self, prompt: str, session_name: Optional[str] = None) -> str:
        """Send prompt only to Nexo, preferring its pinned ID over name lookup."""
        if session_name is not None:
            target = session_name.strip() if isinstance(session_name, str) else None
            if target != "nexo" and (self._session_id is None or target != self._session_id):
                raise ValueError("Session routing is locked to nexo")
        active_session = self._session_id or "nexo"
        self.query_active += 1
        try:
            reply = await self._query(prompt, active_session)
            if self.on_reply is not None:
                try:
                    await self.on_reply(reply)
                except Exception:
                    logger.warning("Hermes reply hook failed")
            return reply
        finally:
            self.query_active -= 1

    async def query_stream(
        self, prompt: str, session_name: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """Stream sentence chunks from Hermes Agent with session continuity."""
        if session_name is not None:
            target = session_name.strip() if isinstance(session_name, str) else None
            if target != "nexo" and (self._session_id is None or target != self._session_id):
                raise ValueError("Session routing is locked to nexo")
        active_session = self._session_id or "nexo"
        self.query_active += 1
        accumulated_chunks = []
        try:
            # Check if _query has been overridden on the instance (e.g. in test mock)
            if "_query" in self.__dict__:
                reply = await self._query(prompt, active_session)
                for sentence in split_into_sentences(reply):
                    accumulated_chunks.append(sentence)
                    yield sentence
            else:
                async for sentence in self._query_stream(prompt, active_session):
                    accumulated_chunks.append(sentence)
                    yield sentence

            full_reply = " ".join(accumulated_chunks).strip()
            if self.on_reply is not None:
                try:
                    await self.on_reply(full_reply)
                except Exception:
                    logger.warning("Hermes reply hook failed")
        finally:
            self.query_active -= 1

    async def _query_stream(
        self, prompt: str, active_session: str
    ) -> AsyncGenerator[str, None]:
        if self.mode == "mock":
            await asyncio.sleep(0.3)
            reply = f"Sistem terhubung. Saya telah menerima instruksi Anda: '{prompt}'. Seluruh modul beroperasi normal."
            for s in split_into_sentences(reply):
                yield s
            return

        proc = None
        yielded_any = False
        try:
            cmd = [
                self.hermes_binary,
                "chat",
                "-c",
                active_session,
                *([] if self._session_id is not None else ["--create-if-missing"]),
                "-q",
                prompt,
                "-Q",
            ]

            env = os.environ.copy()
            if self.hermes_home:
                env["HERMES_HOME"] = str(self.hermes_home)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )

            buf = ""
            async with asyncio.timeout(self.timeout):
                while proc.stdout and not proc.stdout.at_eof():
                    chunk = await proc.stdout.read(64)
                    if not chunk:
                        break
                    buf += chunk.decode("utf-8", errors="replace")
                    while True:
                        sentence, buf = extract_next_sentence(buf, at_eof=False)
                        if sentence is None:
                            break
                        yielded_any = True
                        yield sentence

                await proc.wait()
                while True:
                    sentence, buf = extract_next_sentence(buf, at_eof=True)
                    if sentence is None:
                        break
                    yielded_any = True
                    yield sentence

            if proc.returncode != 0 and not yielded_any:
                err_msg = ""
                if proc.stderr:
                    stderr_bytes = await proc.stderr.read()
                    err_msg = stderr_bytes.decode().strip()
                logger.error(f"Hermes execution error: {err_msg}")
                yield "Maaf, terjadi kendala saat memproses instruksi melalui Hermes Agent."

        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.error("Hermes execution timed out")
            if not yielded_any:
                yield "Waktu pemrosesan melebihi batas. Mohon ulangi instruksi Anda."
        except Exception as e:
            logger.error(f"Failed to communicate with Hermes: {e}")
            if not yielded_any:
                yield "Terjadi kesalahan internal saat menghubungkan ke agen."
        finally:
            if proc is not None and proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await asyncio.shield(proc.communicate())

    async def _query(self, prompt: str, active_session: str) -> str:
        if self.mode == "mock":
            await asyncio.sleep(0.3)
            return f"Sistem terhubung. Saya telah menerima instruksi Anda: '{prompt}'. Seluruh modul beroperasi normal."

        proc = None
        try:
            # Query Hermes using continuous session thread
            cmd = [
                self.hermes_binary,
                "chat",
                "-c",
                active_session,
                *([] if self._session_id is not None else ["--create-if-missing"]),
                "-q",
                prompt,
                "-Q",
            ]

            env = os.environ.copy()
            if self.hermes_home:
                env["HERMES_HOME"] = str(self.hermes_home)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
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

        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.error("Hermes execution timed out")
            return "Waktu pemrosesan melebihi batas. Mohon ulangi instruksi Anda."
        except Exception as e:
            logger.error(f"Failed to communicate with Hermes: {e}")
            return "Terjadi kesalahan internal saat menghubungkan ke agen."
        finally:
            if proc is not None and proc.returncode is None:
                # Cancel the agent and tool subprocesses, not only communicate().
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await asyncio.shield(proc.communicate())

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


def create_hermes_bridge(
    mode: str = "cli",
    hermes_binary: str = "hermes",
    session_name: str = "nexo",
) -> HermesBridge:
    """Factory to instantiate the appropriate Hermes bridge."""
    return HermesBridge(mode=mode, hermes_binary=hermes_binary, session_name=session_name)
