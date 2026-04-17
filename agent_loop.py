"""
Conductor Agent Loop — INF-027
================================
Main process for the Conductor AI operating system. Runs as a systemd service.
Coordinates scheduled cycles, handles incoming webhooks, and manages the
deal swarm pipeline.

Architecture:
  ConductorAgent — main orchestrator (startup, shutdown, run_cycle, handle_deal)
  aiohttp HTTP server — /health, /deal, /status endpoints
  asyncio-based scheduler — health ping, quarantine cleanup
  Signal handling — SIGTERM/SIGINT → graceful shutdown, SIGHUP → reload config
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from aiohttp import web
from pydantic import BaseModel, Field

from config.token_budgets import get_budget, check_budget
from monitoring.self_healing import (
    execute_with_healing,
    get_quarantine_status,
    clear_quarantine,
)
from utils.idempotency import IdempotencyStore, generate_key
import pii_scrubber

logger = logging.getLogger("conductor")

# ---------------------------------------------------------------------------
# Constants — read from environment, matching existing config patterns
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "7729113934")
CONDUCTOR_PORT = int(os.environ.get("CONDUCTOR_PORT", "8080"))
MAX_SCRUB_BYTES = 1_048_576  # 1 MB cap on /scrub input

# INF-105: externalized prompts served to n8n DealSwarm-v2 Stage 2 nodes
PROMPTS_DIR = Path(__file__).parent / "prompts"
_PROMPT_NAME_RE = re.compile(r"^[a-z0-9_]+$")
ALLOWED_PROMPT_NAMES = frozenset({
    "stage2_extractor",
    "stage2_risk",
    "stage2_lender",
    "stage2_compliance",
})

HEALTH_PING_INTERVAL = 300  # 5 minutes — internal self-monitoring (log only)
TELEGRAM_HEARTBEAT_INTERVAL = 21600  # 6 hours — compact status to Telegram (4x/day)
QUARANTINE_CLEANUP_INTERVAL = 3600  # 1 hour


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------

class CycleResult(BaseModel):
    """Result of a single scheduled cycle execution."""

    cycle_type: str
    success: bool
    duration_ms: int
    tokens_used: int = 0
    error: str | None = None


class DealResult(BaseModel):
    """Result of a deal processing pipeline run."""

    deal_id: str
    success: bool
    summary: str = ""
    errors: list[str] = Field(default_factory=list)
    tokens_used: int = 0
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Telegram helper
# ---------------------------------------------------------------------------

async def _send_telegram(
    message: str,
    *,
    test_mode: bool = False,
    silent: bool = False,
) -> None:
    """Send a message to the configured Telegram chat.

    Args:
        message: Text to send (HTML parse_mode).
        test_mode: If True, log instead of sending.
        silent: If True, send without notification sound.
    """
    if test_mode:
        logger.info("TELEGRAM (test mode): %s", message)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }
    if silent:
        payload["disable_notification"] = True

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception:
        logger.exception("Failed to send Telegram message")


def _format_uptime(seconds: int) -> str:
    """Format a duration in seconds as a compact human string (e.g. '14h32m')."""
    if seconds < 0:
        seconds = 0
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d{hours}h{minutes:02d}m"
    return f"{hours}h{minutes:02d}m"


def _get_process_mem_mb() -> int:
    """Return resident set size of the current process in MB (0 if unavailable)."""
    try:
        with open(f"/proc/{os.getpid()}/status", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    # Format: "VmRSS:   123456 kB"
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) // 1024
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------------------
# ConductorAgent
# ---------------------------------------------------------------------------

class ConductorAgent:
    """Main Conductor orchestrator.

    Manages subsystem initialization, scheduled cycles, deal processing,
    and system health reporting.
    """

    def __init__(self, test_mode: bool | None = None) -> None:
        if test_mode is not None:
            self.test_mode = test_mode
        else:
            self.test_mode = os.environ.get("TEST_MODE", "true").lower() == "true"

        self._start_time: float = time.monotonic()
        self._idempotency_store = IdempotencyStore(ttl_hours=24)
        self._last_cycle_results: dict[str, CycleResult] = {}
        self._scheduler_tasks: list[asyncio.Task] = []
        self._running = False
        self._write_gate = None

        # Cycle counters (reset on restart) — drive heartbeat status line
        self._total_cycles: int = 0
        self._error_cycles: int = 0
        self._last_ok_time: float | None = None
        self._last_error_time: float | None = None
        self._last_error_msg: str | None = None

    async def startup(self) -> None:
        """Initialize all subsystems, verify health, send boot alert."""
        logger.info("Conductor starting up (test_mode=%s)", self.test_mode)
        self._start_time = time.monotonic()
        self._running = True

        # Initialize write gate
        try:
            from write_gate import WriteGate
            self._write_gate = WriteGate()
        except ImportError:
            logger.warning("write_gate module not available")

        # Health checks (skip live calls in test mode)
        healthy_count = 0
        total_services = 3  # Notion, Telegram, Anthropic

        if not self.test_mode:
            checks = await self._run_health_checks()
            healthy_count = sum(1 for ok in checks.values() if ok)
        else:
            healthy_count = total_services  # assume healthy in test mode

        mode_str = "test" if self.test_mode else "production"
        await _send_telegram(
            f"<b>Conductor started.</b>\n"
            f"Mode: {mode_str}\n"
            f"Services: {healthy_count}/{total_services} healthy.",
            test_mode=self.test_mode,
        )

    async def shutdown(self) -> None:
        """Graceful shutdown: cancel scheduler, send alert, cleanup."""
        logger.info("Conductor shutting down")
        self._running = False

        # Cancel scheduler tasks
        for task in self._scheduler_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._scheduler_tasks.clear()

        await _send_telegram(
            "<b>Conductor shutting down gracefully.</b>",
            test_mode=self.test_mode,
        )

        # Cleanup idempotency store
        self._idempotency_store.cleanup()
        logger.info("Conductor shutdown complete")

    async def run_cycle(self, cycle_type: str) -> CycleResult:
        """Execute one named cycle with token budget enforcement and self-healing.

        Args:
            cycle_type: The workflow cycle type (e.g. "morning_briefing").

        Returns:
            CycleResult with execution metrics.
        """
        budget = get_budget(cycle_type)
        t0 = time.monotonic()

        try:
            result = await execute_with_healing(
                self._execute_cycle,
                cycle_type,
                budget.max_total_tokens,
                service_name=f"cycle_{cycle_type}",
                max_retries=2,
            )
            duration_ms = int((time.monotonic() - t0) * 1000)
            cycle_result = CycleResult(
                cycle_type=cycle_type,
                success=True,
                duration_ms=duration_ms,
                tokens_used=result.get("tokens_used", 0) if isinstance(result, dict) else 0,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            cycle_result = CycleResult(
                cycle_type=cycle_type,
                success=False,
                duration_ms=duration_ms,
                error=str(exc),
            )

        self._last_cycle_results[cycle_type] = cycle_result

        # Update cycle counters + emit immediate error alert on failure
        self._total_cycles += 1
        now = time.time()
        if cycle_result.success:
            self._last_ok_time = now
        else:
            self._error_cycles += 1
            self._last_error_time = now
            self._last_error_msg = cycle_result.error or "unknown error"

            if self._last_ok_time is not None:
                last_ok_str = datetime.fromtimestamp(
                    self._last_ok_time, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M UTC")
            else:
                last_ok_str = "never"

            await _send_telegram(
                "<b>🔴 Conductor error</b>\n"
                f"cycle: {cycle_type}\n"
                f"error: {cycle_result.error}\n"
                f"last ok: {last_ok_str}",
                test_mode=self.test_mode,
                silent=False,
            )

        return cycle_result

    async def _execute_cycle(self, cycle_type: str, token_budget: int) -> dict:
        """Internal cycle execution (called inside self-healing wrapper).

        Args:
            cycle_type: The workflow cycle type.
            token_budget: Maximum tokens allowed.

        Returns:
            Dict with execution details.
        """
        logger.info("Executing cycle: %s (budget=%d tokens)", cycle_type, token_budget)
        # Placeholder — actual cycle logic will be wired to n8n triggers
        # and specific cycle handlers in Phase 2+
        return {"cycle_type": cycle_type, "tokens_used": 0}

    async def handle_deal(self, deal_path: Path) -> DealResult:
        """Process a deal through the full pipeline.

        Pipeline: PDF extract → PII scrub → swarm → verify → write gate → output.

        Args:
            deal_path: Path to the deal PDF file.

        Returns:
            DealResult with processing metrics.
        """
        t0 = time.monotonic()
        deal_id = deal_path.stem
        errors: list[str] = []

        try:
            # Step 1: Extract PDF text
            from swarm.pdf_extractor import extract_text
            doc = extract_text(deal_path)
            raw_text = doc.full_text

            # Step 2: PII scrub
            try:
                from pii_scrubber import scrub_all
                scrubbed_text = scrub_all(raw_text)
            except ImportError:
                logger.warning("pii_scrubber not available, using raw text")
                scrubbed_text = raw_text

            # Step 3: Check token budget
            budget = get_budget("deal_swarm")
            budget_check = check_budget("deal_swarm", 0, 0)  # pre-check
            if not budget_check.within_budget:
                errors.append("Token budget already exceeded before deal swarm")

            # Step 4: Run deal swarm pipeline
            from swarm.runner import DealSwarmRunner
            runner = DealSwarmRunner(deal_id=deal_id, token_budget=budget.max_total_tokens)
            success, deal_output, swarm_errors = runner.run(scrubbed_text)
            errors.extend(swarm_errors)

            # Step 5: Verify output
            if success and deal_output:
                try:
                    from schemas.validators import validate_deal_complete
                    valid, issues = validate_deal_complete(deal_output)
                    if not valid:
                        errors.extend(issues)
                        success = False
                except ImportError:
                    logger.warning("validators not available, skipping verification")

            # Step 6: Write gate check
            if success and self._write_gate:
                try:
                    self._write_gate.check("notion", "create_page", {"deal_id": deal_id})
                except Exception as wg_err:
                    errors.append(f"Write gate blocked: {wg_err}")
                    success = False

            # Step 7: Idempotency key
            idem_key = generate_key("deal_process", deal_id)
            is_new = self._idempotency_store.check_and_store(idem_key)
            if not is_new:
                errors.append(f"Duplicate deal processing detected: {deal_id}")
                success = False

            duration_ms = int((time.monotonic() - t0) * 1000)
            tokens_used = runner.tokens_used if runner else 0
            summary = ""
            if deal_output:
                summary = f"Deal {deal_id}: {runner.tokens_used} tokens, {len(runner.stage_results)} stages"

            return DealResult(
                deal_id=deal_id,
                success=success,
                summary=summary,
                errors=errors,
                tokens_used=tokens_used,
                duration_ms=duration_ms,
            )

        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            errors.append(str(exc))
            return DealResult(
                deal_id=deal_id,
                success=False,
                summary="",
                errors=errors,
                duration_ms=duration_ms,
            )

    def status(self) -> dict:
        """Return system health summary.

        Returns:
            Dict with quarantine, cycle results, uptime, and budget info.
        """
        uptime_seconds = int(time.monotonic() - self._start_time)
        quarantine = [entry.model_dump() for entry in get_quarantine_status()]

        cycle_results = {
            k: v.model_dump() for k, v in self._last_cycle_results.items()
        }

        return {
            "test_mode": self.test_mode,
            "uptime_seconds": uptime_seconds,
            "running": self._running,
            "quarantine": quarantine,
            "last_cycle_results": cycle_results,
            "idempotency_store_size": len(self._idempotency_store),
        }

    # ------------------------------------------------------------------
    # Health checks
    # ------------------------------------------------------------------

    async def _run_health_checks(self) -> dict[str, bool]:
        """Ping external services and return health status."""
        results: dict[str, bool] = {}
        async with httpx.AsyncClient(timeout=10) as client:
            # Telegram
            try:
                resp = await client.get(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
                )
                results["telegram"] = resp.status_code == 200
            except Exception:
                results["telegram"] = False

            # Notion
            notion_token = os.environ.get("NOTION_TOKEN", "")
            if notion_token:
                try:
                    resp = await client.get(
                        "https://api.notion.com/v1/users/me",
                        headers={
                            "Authorization": f"Bearer {notion_token}",
                            "Notion-Version": "2022-06-28",
                        },
                    )
                    results["notion"] = resp.status_code == 200
                except Exception:
                    results["notion"] = False
            else:
                results["notion"] = False

            # Anthropic API reachability
            try:
                resp = await client.get("https://api.anthropic.com/v1/messages")
                # Any response (even 401) means the API is reachable
                results["anthropic"] = resp.status_code < 500
            except Exception:
                results["anthropic"] = False

        return results

    # ------------------------------------------------------------------
    # Scheduler loops
    # ------------------------------------------------------------------

    async def _internal_health_check_loop(self) -> None:
        """Internal self-monitoring every 5 minutes.

        Logs a heartbeat line to stdout only — never sends to Telegram.
        Detects missed cycles by comparing elapsed vs expected interval;
        if the event loop was blocked we emit an immediate audible alert.
        """
        expected = HEALTH_PING_INTERVAL
        while self._running:
            try:
                tick_start = time.monotonic()
                await asyncio.sleep(expected)
                if not self._running:
                    break

                elapsed = time.monotonic() - tick_start
                uptime = int(time.monotonic() - self._start_time)
                quarantine_count = len(get_quarantine_status())
                ok_cycles = self._total_cycles - self._error_cycles

                logger.info(
                    "heartbeat(internal) | uptime=%ds | cycles: %d ok, %d err "
                    "| quarantine=%d | mem=%dMB",
                    uptime, ok_cycles, self._error_cycles,
                    quarantine_count, _get_process_mem_mb(),
                )

                # Missed-cycle detection: asyncio.sleep should be ~accurate.
                # If elapsed is >2x expected, the event loop was blocked.
                if elapsed > expected * 2:
                    await _send_telegram(
                        "<b>🔴 Conductor missed cycle</b>\n"
                        f"expected interval: {expected}s\n"
                        f"actual: {int(elapsed)}s\n"
                        f"uptime: {_format_uptime(uptime)}",
                        test_mode=self.test_mode,
                        silent=False,
                    )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Internal health check failed")

    async def _telegram_heartbeat_loop(self) -> None:
        """Compact status line to Telegram every 6 hours (silent)."""
        while self._running:
            try:
                await asyncio.sleep(TELEGRAM_HEARTBEAT_INTERVAL)
                if not self._running:
                    break
                uptime = int(time.monotonic() - self._start_time)
                ok_cycles = self._total_cycles - self._error_cycles
                mem_mb = _get_process_mem_mb()
                msg = (
                    f"🟢 Conductor alive | uptime {_format_uptime(uptime)} | "
                    f"cycles: {ok_cycles} ok, {self._error_cycles} err | "
                    f"mem: {mem_mb}MB"
                )
                await _send_telegram(
                    msg,
                    test_mode=self.test_mode,
                    silent=True,
                )
                logger.info("heartbeat(telegram) sent | %s", msg)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Telegram heartbeat failed")

    async def _quarantine_cleanup_loop(self) -> None:
        """Clear expired quarantine entries every hour."""
        while self._running:
            try:
                await asyncio.sleep(QUARANTINE_CLEANUP_INTERVAL)
                if not self._running:
                    break
                clear_quarantine()
                logger.info("Quarantine cleanup completed")
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Quarantine cleanup failed")

    def start_scheduler(self) -> None:
        """Launch scheduler background tasks."""
        loop = asyncio.get_event_loop()
        self._scheduler_tasks.append(loop.create_task(self._internal_health_check_loop()))
        self._scheduler_tasks.append(loop.create_task(self._telegram_heartbeat_loop()))
        self._scheduler_tasks.append(loop.create_task(self._quarantine_cleanup_loop()))
        logger.info(
            "Scheduler started: internal_health=%ds, telegram_heartbeat=%ds, "
            "quarantine_cleanup=%ds",
            HEALTH_PING_INTERVAL, TELEGRAM_HEARTBEAT_INTERVAL,
            QUARANTINE_CLEANUP_INTERVAL,
        )


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# PII Scrubber endpoint (/scrub) — INF-085 single source of truth
# Called by n8n DealSwarm-v2 Prepare Deal Package node.
# ---------------------------------------------------------------------------

def _scrub_validate(body: Any) -> tuple[int, dict]:
    """Validate request body and invoke pii_scrubber. Returns (status_code, payload).

    Factored out of scrub_handler for synchronous unit testability.
    """
    if not isinstance(body, dict):
        return 400, {"error": "Request body must be a JSON object"}
    text = body.get("text")
    if text is None:
        return 400, {"error": "Missing 'text' field"}
    if not isinstance(text, str):
        return 400, {"error": "'text' must be a string"}
    if len(text.encode("utf-8")) > MAX_SCRUB_BYTES:
        return 400, {"error": f"'text' exceeds {MAX_SCRUB_BYTES} byte limit"}
    try:
        scrubbed = pii_scrubber.scrub_all(text)
        report = pii_scrubber.scrub_report(text, scrubbed)
    except Exception:
        logger.exception("PII scrubber raised — input length=%d", len(text))
        return 500, {"error": "Internal scrubber error"}
    return 200, {"scrubbed": scrubbed, "report": report}


async def scrub_handler(request: web.Request) -> web.Response:
    """POST /scrub — strip PII from mortgage document text.

    Fail-closed: any error returns non-200 so callers halt rather than
    forward unredacted PII downstream.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    status, payload = _scrub_validate(body)
    if status == 500:
        try:
            test_mode = os.environ.get("TEST_MODE", "true").lower() == "true"
            await _send_telegram(
                "🚨 PII scrubber /scrub endpoint error — DealSwarm will halt. Check conductor logs.",
                test_mode=test_mode,
            )
        except Exception:
            logger.exception("Failed to send Telegram alert for scrubber failure")
    return web.json_response(payload, status=status)


# ---------------------------------------------------------------------------
# Prompt server endpoint (GET /prompts/{name}) — INF-105 externalized prompts
# Source of truth for Stage 2 prompts that are mirrored into n8n's
# VF-DealSwarm-v2 Stage 2 Analysis node. The Python test harness and the
# contract test framework pull from here so they cannot drift from the files.
# ---------------------------------------------------------------------------

def _prompts_lookup(name: str) -> tuple[int, str, str]:
    """Return (status, body, content_type) for a prompt name. Sync-testable."""
    if not _PROMPT_NAME_RE.match(name or ""):
        return 400, '{"error": "invalid prompt name"}', "application/json"
    if name not in ALLOWED_PROMPT_NAMES:
        return 404, '{"error": "unknown prompt"}', "application/json"
    path = PROMPTS_DIR / f"{name}.md"
    try:
        resolved = path.resolve()
        resolved.relative_to(PROMPTS_DIR.resolve())
    except (ValueError, OSError):
        return 404, '{"error": "unknown prompt"}', "application/json"
    if not resolved.is_file():
        return 404, '{"error": "not found"}', "application/json"
    try:
        body = resolved.read_text(encoding="utf-8")
    except OSError:
        logger.exception("Failed to read prompt file: %s", resolved)
        return 500, '{"error": "read error"}', "application/json"
    return 200, body, "text/markdown; charset=utf-8"


async def prompts_handler(request: web.Request) -> web.Response:
    """GET /prompts/{name} — serve externalized Stage 2 prompt markdown."""
    name = request.match_info.get("name", "")
    status, body, ctype = _prompts_lookup(name)
    if ctype.startswith("application/json"):
        import json as _json
        return web.json_response(_json.loads(body), status=status)
    return web.Response(text=body, status=status, content_type="text/markdown",
                        charset="utf-8")


def create_http_app(agent: ConductorAgent) -> web.Application:
    """Create the aiohttp web application with /health, /deal, /status routes.

    Args:
        agent: The ConductorAgent instance to serve.

    Returns:
        Configured aiohttp Application.
    """
    app = web.Application()

    async def health_handler(request: web.Request) -> web.Response:
        uptime = int(time.monotonic() - agent._start_time)
        return web.json_response({
            "status": "ok",
            "uptime_seconds": uptime,
            "test_mode": agent.test_mode,
        })

    async def deal_handler(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        deal_path_str = body.get("deal_path")
        if not deal_path_str:
            return web.json_response({"error": "deal_path required"}, status=400)

        deal_path = Path(deal_path_str)
        if not deal_path.exists():
            return web.json_response({"error": f"File not found: {deal_path}"}, status=404)

        result = await agent.handle_deal(deal_path)
        return web.json_response(result.model_dump())

    async def status_handler(request: web.Request) -> web.Response:
        return web.json_response(agent.status())

    app.router.add_get("/health", health_handler)
    app.router.add_post("/deal", deal_handler)
    app.router.add_get("/status", status_handler)
    app.router.add_post("/scrub", scrub_handler)
    app.router.add_get("/prompts/{name}", prompts_handler)

    return app


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def _setup_signals(agent: ConductorAgent, runner: web.AppRunner | None = None) -> None:
    """Register signal handlers for graceful shutdown and config reload.

    Args:
        agent: The ConductorAgent instance.
        runner: Optional aiohttp AppRunner for HTTP server cleanup.
    """
    loop = asyncio.get_event_loop()

    def _handle_shutdown(sig: signal.Signals) -> None:
        logger.info("Received signal %s, initiating shutdown", sig.name)
        loop.create_task(_graceful_shutdown(agent, runner))

    def _handle_reload(sig: signal.Signals) -> None:
        logger.info("Received SIGHUP, reloading configuration")
        # Re-read TEST_MODE from environment
        agent.test_mode = os.environ.get("TEST_MODE", "true").lower() == "true"
        logger.info("Config reloaded: test_mode=%s", agent.test_mode)

    # SIGTERM and SIGINT → graceful shutdown
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_shutdown, sig)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    # SIGHUP → reload config
    if hasattr(signal, "SIGHUP"):
        try:
            loop.add_signal_handler(signal.SIGHUP, _handle_reload, signal.SIGHUP)
        except NotImplementedError:
            pass


async def _graceful_shutdown(
    agent: ConductorAgent,
    runner: web.AppRunner | None = None,
) -> None:
    """Execute graceful shutdown sequence."""
    await agent.shutdown()
    if runner:
        await runner.cleanup()
    # Stop the event loop
    asyncio.get_event_loop().stop()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _async_main(test_mode: bool | None = None) -> None:
    """Async entry point: init agent, start scheduler + HTTP server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    agent = ConductorAgent(test_mode=test_mode)
    await agent.startup()

    # HTTP server
    app = create_http_app(agent)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", CONDUCTOR_PORT)
    await site.start()
    logger.info("HTTP server listening on port %d", CONDUCTOR_PORT)

    # Signal handlers
    _setup_signals(agent, runner)

    # Scheduler
    agent.start_scheduler()

    logger.info("Conductor agent loop running")

    # Keep running until stopped
    try:
        while agent._running:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        if agent._running:
            await _graceful_shutdown(agent, runner)


def main() -> None:
    """Parse args, init agent, start scheduler + HTTP server."""
    test_mode = None
    if "--test" in sys.argv:
        test_mode = True
    elif "--production" in sys.argv:
        test_mode = False

    try:
        asyncio.run(_async_main(test_mode=test_mode))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
