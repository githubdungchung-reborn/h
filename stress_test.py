#!/usr/bin/env python3
"""
Comprehensive API Stress Test Toolkit with Parallel Execution
=============================================================
High-performance stress testing with 15+ metrics and comprehensive reporting.

Features:
- True parallel execution using asyncio.gather() and semaphores
- Connection pooling with aiohttp TCPConnector
- 15+ comprehensive metrics with percentile analysis
- HTML, JSON, and Markdown report generation
- Built-in retry logic with exponential backoff
- Rate limiting detection and analysis

Requirements:
    pip install aiohttp rich

Usage:
    python stress_test.py --url http://localhost:8000/api/products --scenario basic
    python stress_test.py --url $API_ENDPOINT --scenario burst --report json
"""

import asyncio
import aiohttp
import argparse
import time
import random
import json
import statistics
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Tuple
from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

if RICH_AVAILABLE:
    console = Console()
else:
    class MockConsole:
        def print(self, *args, **kwargs):
            # Strip rich markup for plain output
            text = str(args[0]) if args else ""
            import re
            clean = re.sub(r'\[.*?\]', '', text)
            print(clean)
    console = MockConsole()


class ReportFormat(Enum):
    JSON = "json"
    HTML = "html"
    MARKDOWN = "markdown"
    CONSOLE = "console"


@dataclass
class RequestResult:
    """Individual request result."""
    status_code: int
    latency_ms: float
    response_size: int = 0
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    ttfb_ms: float = 0  # Time to first byte


@dataclass
class ComprehensiveMetrics:
    """
    Comprehensive metrics tracking with 15+ metrics.
    """
    # Basic counts
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rate_limited_requests: int = 0  # 429 responses
    timeout_requests: int = 0
    connection_errors: int = 0

    # Latency tracking
    latencies: List[float] = field(default_factory=list)
    ttfb_times: List[float] = field(default_factory=list)  # Time to first byte

    # Status code distribution
    status_codes: Dict[int, int] = field(default_factory=lambda: defaultdict(int))

    # Error tracking
    errors: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Time tracking
    start_time: float = 0
    end_time: float = 0

    # Throughput tracking (requests per second samples)
    rps_samples: List[Tuple[float, int]] = field(default_factory=list)

    # Data transfer
    total_bytes_received: int = 0

    # Concurrent request tracking
    peak_concurrent: int = 0
    current_concurrent: int = 0

    def add_result(self, result: RequestResult):
        """Thread-safe result addition."""
        self.total_requests += 1
        self.status_codes[result.status_code] += 1
        self.total_bytes_received += result.response_size

        if result.status_code == 429:
            self.rate_limited_requests += 1
            self.failed_requests += 1
        elif result.status_code == 0:
            self.failed_requests += 1
            if result.error:
                if "Timeout" in str(result.error):
                    self.timeout_requests += 1
                elif "Connect" in str(result.error) or "Connection" in str(result.error):
                    self.connection_errors += 1
        elif 200 <= result.status_code < 300:
            self.successful_requests += 1
            self.latencies.append(result.latency_ms)
            if result.ttfb_ms > 0:
                self.ttfb_times.append(result.ttfb_ms)
        else:
            self.failed_requests += 1

        if result.error:
            error_key = str(result.error)[:50]
            self.errors[error_key] += 1

    # =========================================================================
    # METRIC 1: Success Rate
    # =========================================================================
    @property
    def success_rate(self) -> float:
        """Percentage of successful requests."""
        return (self.successful_requests / self.total_requests * 100) if self.total_requests > 0 else 0

    # =========================================================================
    # METRIC 2: Error Rate
    # =========================================================================
    @property
    def error_rate(self) -> float:
        """Percentage of failed requests."""
        return (self.failed_requests / self.total_requests * 100) if self.total_requests > 0 else 0

    # =========================================================================
    # METRIC 3: Rate Limit Hit Rate
    # =========================================================================
    @property
    def rate_limit_rate(self) -> float:
        """Percentage of requests that hit rate limits (429)."""
        return (self.rate_limited_requests / self.total_requests * 100) if self.total_requests > 0 else 0

    # =========================================================================
    # METRIC 4: Average Latency
    # =========================================================================
    @property
    def avg_latency(self) -> float:
        """Average response latency in milliseconds."""
        return statistics.mean(self.latencies) if self.latencies else 0

    # =========================================================================
    # METRIC 5: Median Latency (P50)
    # =========================================================================
    @property
    def p50_latency(self) -> float:
        """Median (50th percentile) latency."""
        return statistics.median(self.latencies) if self.latencies else 0

    # =========================================================================
    # METRIC 6: P90 Latency
    # =========================================================================
    @property
    def p90_latency(self) -> float:
        """90th percentile latency."""
        if not self.latencies:
            return 0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.90)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    # =========================================================================
    # METRIC 7: P95 Latency
    # =========================================================================
    @property
    def p95_latency(self) -> float:
        """95th percentile latency."""
        if not self.latencies:
            return 0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    # =========================================================================
    # METRIC 8: P99 Latency
    # =========================================================================
    @property
    def p99_latency(self) -> float:
        """99th percentile latency."""
        if not self.latencies:
            return 0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.99)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    # =========================================================================
    # METRIC 9: Max Latency
    # =========================================================================
    @property
    def max_latency(self) -> float:
        """Maximum latency observed."""
        return max(self.latencies) if self.latencies else 0

    # =========================================================================
    # METRIC 10: Min Latency
    # =========================================================================
    @property
    def min_latency(self) -> float:
        """Minimum latency observed."""
        return min(self.latencies) if self.latencies else 0

    # =========================================================================
    # METRIC 11: Latency Standard Deviation
    # =========================================================================
    @property
    def latency_std_dev(self) -> float:
        """Standard deviation of latency (measure of consistency)."""
        return statistics.stdev(self.latencies) if len(self.latencies) > 1 else 0

    # =========================================================================
    # METRIC 12: Requests Per Second (RPS)
    # =========================================================================
    @property
    def rps(self) -> float:
        """Overall requests per second."""
        return self.total_requests / self.duration if self.duration > 0 else 0

    # =========================================================================
    # METRIC 13: Test Duration
    # =========================================================================
    @property
    def duration(self) -> float:
        """Total test duration in seconds."""
        return self.end_time - self.start_time if self.end_time else time.time() - self.start_time

    # =========================================================================
    # METRIC 14: Throughput (MB/s)
    # =========================================================================
    @property
    def throughput_mbps(self) -> float:
        """Data throughput in MB/s."""
        return (self.total_bytes_received / (1024 * 1024)) / self.duration if self.duration > 0 else 0

    # =========================================================================
    # METRIC 15: Average Time to First Byte (TTFB)
    # =========================================================================
    @property
    def avg_ttfb(self) -> float:
        """Average time to first byte in milliseconds."""
        return statistics.mean(self.ttfb_times) if self.ttfb_times else 0

    # =========================================================================
    # METRIC 16: Apdex Score (Application Performance Index)
    # =========================================================================
    def apdex_score(self, target_ms: float = 100, tolerable_ms: float = 400) -> float:
        """
        Apdex score: Industry standard for user satisfaction.
        - Satisfied: latency <= target_ms
        - Tolerating: target_ms < latency <= tolerable_ms
        - Frustrated: latency > tolerable_ms

        Score = (Satisfied + Tolerating/2) / Total
        """
        if not self.latencies:
            return 0
        satisfied = sum(1 for l in self.latencies if l <= target_ms)
        tolerating = sum(1 for l in self.latencies if target_ms < l <= tolerable_ms)
        return (satisfied + tolerating / 2) / len(self.latencies)

    # =========================================================================
    # METRIC 17: Availability
    # =========================================================================
    @property
    def availability(self) -> float:
        """Service availability percentage (excludes rate limits from failures)."""
        non_rate_limited_failures = self.failed_requests - self.rate_limited_requests
        available = self.total_requests - non_rate_limited_failures
        return (available / self.total_requests * 100) if self.total_requests > 0 else 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary for JSON export."""
        return {
            "summary": {
                "total_requests": self.total_requests,
                "successful_requests": self.successful_requests,
                "failed_requests": self.failed_requests,
                "rate_limited_requests": self.rate_limited_requests,
                "timeout_requests": self.timeout_requests,
                "connection_errors": self.connection_errors,
                "duration_seconds": round(self.duration, 2),
            },
            "rates": {
                "success_rate_percent": round(self.success_rate, 2),
                "error_rate_percent": round(self.error_rate, 2),
                "rate_limit_rate_percent": round(self.rate_limit_rate, 2),
                "availability_percent": round(self.availability, 2),
                "requests_per_second": round(self.rps, 2),
            },
            "latency_ms": {
                "average": round(self.avg_latency, 2),
                "min": round(self.min_latency, 2),
                "max": round(self.max_latency, 2),
                "p50": round(self.p50_latency, 2),
                "p90": round(self.p90_latency, 2),
                "p95": round(self.p95_latency, 2),
                "p99": round(self.p99_latency, 2),
                "std_dev": round(self.latency_std_dev, 2),
            },
            "throughput": {
                "total_bytes": self.total_bytes_received,
                "throughput_mbps": round(self.throughput_mbps, 4),
                "avg_ttfb_ms": round(self.avg_ttfb, 2),
            },
            "quality": {
                "apdex_score": round(self.apdex_score(), 3),
                "apdex_rating": self._apdex_rating(),
            },
            "status_codes": dict(self.status_codes),
            "errors": dict(self.errors) if self.errors else {},
        }

    def _apdex_rating(self) -> str:
        """Convert Apdex score to rating."""
        score = self.apdex_score()
        if score >= 0.94:
            return "Excellent"
        elif score >= 0.85:
            return "Good"
        elif score >= 0.70:
            return "Fair"
        elif score >= 0.50:
            return "Poor"
        else:
            return "Unacceptable"


class StressTestEngine:
    """
    High-performance stress test engine with true parallel execution.
    """

    def __init__(
        self,
        base_url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        payload: Optional[Dict] = None,
        timeout: float = 30.0,
        verify_ssl: bool = True,
        retry_attempts: int = 0,
        retry_delay: float = 1.0,
    ):
        self.base_url = base_url
        self.method = method.upper()
        self.headers = headers or {"Content-Type": "application/json", "User-Agent": "StressTest/2.0"}
        self.payload = payload
        self.timeout = aiohttp.ClientTimeout(total=timeout, connect=10)
        self.verify_ssl = verify_ssl
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.metrics = ComprehensiveMetrics()
        self._stop_event = asyncio.Event()
        self._concurrent_counter = 0
        self._counter_lock = asyncio.Lock()

    async def _make_request(
        self,
        session: aiohttp.ClientSession,
        request_id: int = 0,
    ) -> RequestResult:
        """
        Make a single HTTP request with optional retry logic.
        Tracks time to first byte (TTFB) and response size.
        """
        async with self._counter_lock:
            self._concurrent_counter += 1
            if self._concurrent_counter > self.metrics.peak_concurrent:
                self.metrics.peak_concurrent = self._concurrent_counter

        attempts = 0
        max_attempts = self.retry_attempts + 1
        last_error = None

        while attempts < max_attempts:
            start = time.perf_counter()
            ttfb = 0
            response_size = 0

            try:
                async with session.request(
                    self.method,
                    self.base_url,
                    headers=self.headers,
                    json=self.payload if self.method in ["POST", "PUT", "PATCH"] else None,
                    ssl=self.verify_ssl,
                ) as response:
                    # Time to first byte
                    ttfb = (time.perf_counter() - start) * 1000

                    # Read response body
                    body = await response.read()
                    response_size = len(body)

                    latency = (time.perf_counter() - start) * 1000

                    async with self._counter_lock:
                        self._concurrent_counter -= 1

                    return RequestResult(
                        status_code=response.status,
                        latency_ms=latency,
                        response_size=response_size,
                        ttfb_ms=ttfb,
                    )

            except asyncio.TimeoutError:
                latency = (time.perf_counter() - start) * 1000
                last_error = "Timeout"
            except aiohttp.ClientConnectorError as e:
                latency = (time.perf_counter() - start) * 1000
                last_error = f"ConnectionError: {type(e).__name__}"
            except aiohttp.ClientError as e:
                latency = (time.perf_counter() - start) * 1000
                last_error = str(type(e).__name__)
            except Exception as e:
                latency = (time.perf_counter() - start) * 1000
                last_error = str(e)[:50]

            attempts += 1
            if attempts < max_attempts:
                # Exponential backoff
                await asyncio.sleep(self.retry_delay * (2 ** (attempts - 1)))

        async with self._counter_lock:
            self._concurrent_counter -= 1

        return RequestResult(
            status_code=0,
            latency_ms=latency,
            error=last_error,
        )

    def _create_metrics_table(self) -> "Table":
        """Create a rich table showing current metrics."""
        if not RICH_AVAILABLE:
            return None

        table = Table(title="📊 Live Stress Test Metrics", expand=True)
        table.add_column("Metric", style="cyan", width=22)
        table.add_column("Value", style="green", width=15)
        table.add_column("Metric", style="cyan", width=22)
        table.add_column("Value", style="green", width=15)

        m = self.metrics

        # Row 1: Request counts
        table.add_row(
            "Total Requests", f"{m.total_requests:,}",
            "Success Rate", f"{m.success_rate:.1f}%"
        )
        # Row 2: Success/Fail
        table.add_row(
            "Successful", f"[green]{m.successful_requests:,}[/green]",
            "Failed", f"[red]{m.failed_requests:,}[/red]"
        )
        # Row 3: Rate limits and RPS
        table.add_row(
            "Rate Limited (429)", f"[yellow]{m.rate_limited_requests:,}[/yellow]",
            "Current RPS", f"{m.rps:,.0f}"
        )
        # Row 4: Latency
        table.add_row(
            "Avg Latency", f"{m.avg_latency:.2f}ms",
            "P50 Latency", f"{m.p50_latency:.2f}ms"
        )
        # Row 5: More latency
        table.add_row(
            "P95 Latency", f"{m.p95_latency:.2f}ms",
            "P99 Latency", f"{m.p99_latency:.2f}ms"
        )
        # Row 6: Duration and errors
        table.add_row(
            "Duration", f"{m.duration:.1f}s",
            "Timeouts", f"{m.timeout_requests:,}"
        )
        # Row 7: Apdex and availability
        table.add_row(
            "Apdex Score", f"{m.apdex_score():.3f} ({m._apdex_rating()})",
            "Availability", f"{m.availability:.1f}%"
        )

        return table

    async def _worker_batch(
        self,
        session: aiohttp.ClientSession,
        batch_size: int,
        semaphore: asyncio.Semaphore,
    ):
        """Worker that processes a batch of requests in parallel."""
        async def single_request(req_id: int):
            async with semaphore:
                result = await self._make_request(session, req_id)
                self.metrics.add_result(result)

        # Launch all requests in the batch concurrently using gather
        await asyncio.gather(*[single_request(i) for i in range(batch_size)])

    # =========================================================================
    # SCENARIO: Basic Parallel Load Test
    # =========================================================================
    async def basic_load(
        self,
        total_requests: int = 1000,
        concurrency: int = 100,
        show_live: bool = True,
    ) -> ComprehensiveMetrics:
        """
        Basic concurrent load test with true parallel execution.
        Uses asyncio.gather() for maximum parallelism.
        """
        console.print(Panel(
            f"[bold blue]Basic Parallel Load Test[/bold blue]\n"
            f"Requests: {total_requests:,} | Concurrency: {concurrency}",
            title="🚀 Starting Test"
        ) if RICH_AVAILABLE else f"Basic Load Test - Requests: {total_requests}, Concurrency: {concurrency}")

        self.metrics = ComprehensiveMetrics()
        self.metrics.start_time = time.time()

        # Semaphore limits concurrent requests
        semaphore = asyncio.Semaphore(concurrency)

        # Configure connector with connection pooling
        connector = aiohttp.TCPConnector(
            limit=concurrency,
            limit_per_host=concurrency,
            keepalive_timeout=30,
            enable_cleanup_closed=True,
        )

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            # Create all request tasks
            async def make_single_request(req_id: int):
                async with semaphore:
                    result = await self._make_request(session, req_id)
                    self.metrics.add_result(result)

            # Launch ALL requests in parallel using gather
            tasks = [make_single_request(i) for i in range(total_requests)]

            if show_live and RICH_AVAILABLE:
                with Live(self._create_metrics_table(), refresh_per_second=4) as live:
                    async def update_display():
                        while not all(t.done() for t in [asyncio.ensure_future(asyncio.sleep(0))] + tasks[:1]):
                            live.update(self._create_metrics_table())
                            await asyncio.sleep(0.25)
                            if self.metrics.total_requests >= total_requests:
                                break

                    # Run tasks and display updater concurrently
                    gather_task = asyncio.gather(*tasks, return_exceptions=True)
                    display_task = asyncio.create_task(update_display())

                    await gather_task
                    display_task.cancel()
                    try:
                        await display_task
                    except asyncio.CancelledError:
                        pass
            else:
                await asyncio.gather(*tasks, return_exceptions=True)

        self.metrics.end_time = time.time()
        return self.metrics

    # =========================================================================
    # SCENARIO: Sustained Load
    # =========================================================================
    async def sustained_load(
        self,
        duration_seconds: int = 60,
        target_rps: int = 1000,
        concurrency: int = 100,
    ) -> ComprehensiveMetrics:
        """
        Sustained load for a fixed duration with target RPS.
        """
        console.print(Panel(
            f"[bold green]Sustained Load Test[/bold green]\n"
            f"Duration: {duration_seconds}s | Target RPS: {target_rps:,} | Concurrency: {concurrency}",
            title="⏱️ Starting Test"
        ) if RICH_AVAILABLE else f"Sustained Load - Duration: {duration_seconds}s, RPS: {target_rps}")

        self.metrics = ComprehensiveMetrics()
        self.metrics.start_time = time.time()
        self._stop_event.clear()

        connector = aiohttp.TCPConnector(
            limit=concurrency * 2,
            limit_per_host=concurrency * 2,
            keepalive_timeout=30,
        )

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            semaphore = asyncio.Semaphore(concurrency)
            end_time = time.time() + duration_seconds

            async def request_generator():
                """Generate requests at target RPS."""
                interval = 1.0 / target_rps if target_rps > 0 else 0
                request_id = 0

                while time.time() < end_time and not self._stop_event.is_set():
                    async with semaphore:
                        result = await self._make_request(session, request_id)
                        self.metrics.add_result(result)
                    request_id += 1

                    # Rate limiting sleep
                    if interval > 0:
                        await asyncio.sleep(interval)

            # Run multiple workers to achieve target RPS
            workers_needed = min(concurrency, max(1, target_rps // 100))
            workers = [request_generator() for _ in range(workers_needed)]

            if RICH_AVAILABLE:
                with Live(self._create_metrics_table(), refresh_per_second=4) as live:
                    async def updater():
                        while time.time() < end_time and not self._stop_event.is_set():
                            live.update(self._create_metrics_table())
                            await asyncio.sleep(0.25)

                    await asyncio.gather(updater(), *workers, return_exceptions=True)
            else:
                await asyncio.gather(*workers, return_exceptions=True)

        self.metrics.end_time = time.time()
        return self.metrics

    # =========================================================================
    # SCENARIO: Burst Test
    # =========================================================================
    async def burst_test(
        self,
        burst_size: int = 10000,
        burst_count: int = 5,
        cooldown_seconds: float = 2.0,
        concurrency: int = 500,
    ) -> ComprehensiveMetrics:
        """
        Send bursts of requests with cooldown periods.
        Tests rate limiting and recovery behavior.
        """
        console.print(Panel(
            f"[bold yellow]Burst Test[/bold yellow]\n"
            f"Burst Size: {burst_size:,} | Bursts: {burst_count} | "
            f"Cooldown: {cooldown_seconds}s | Concurrency: {concurrency}",
            title="💥 Starting Test"
        ) if RICH_AVAILABLE else f"Burst Test - Size: {burst_size}, Count: {burst_count}")

        self.metrics = ComprehensiveMetrics()
        self.metrics.start_time = time.time()

        connector = aiohttp.TCPConnector(
            limit=concurrency * 2,
            limit_per_host=concurrency * 2,
        )

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            semaphore = asyncio.Semaphore(concurrency)

            for burst_num in range(burst_count):
                console.print(f"[yellow]⚡ Burst {burst_num + 1}/{burst_count}[/yellow]" if RICH_AVAILABLE else f"Burst {burst_num + 1}/{burst_count}")

                # Launch all burst requests in parallel
                async def make_request(req_id):
                    async with semaphore:
                        result = await self._make_request(session, req_id)
                        self.metrics.add_result(result)

                tasks = [make_request(i) for i in range(burst_size)]

                if RICH_AVAILABLE:
                    with Live(self._create_metrics_table(), refresh_per_second=4) as live:
                        async def updater():
                            while not all(t.done() for t in tasks):
                                live.update(self._create_metrics_table())
                                await asyncio.sleep(0.25)

                        gather = asyncio.gather(*tasks, return_exceptions=True)
                        update_task = asyncio.create_task(updater())
                        await gather
                        update_task.cancel()
                else:
                    await asyncio.gather(*tasks, return_exceptions=True)

                if burst_num < burst_count - 1:
                    console.print(f"[dim]Cooling down for {cooldown_seconds}s...[/dim]" if RICH_AVAILABLE else f"Cooldown: {cooldown_seconds}s")
                    await asyncio.sleep(cooldown_seconds)

        self.metrics.end_time = time.time()
        return self.metrics

    # =========================================================================
    # SCENARIO: Ramp-Up Test
    # =========================================================================
    async def ramp_up_test(
        self,
        start_rps: int = 100,
        end_rps: int = 10000,
        ramp_duration_seconds: int = 60,
        step_duration_seconds: int = 5,
        concurrency: int = 500,
    ) -> ComprehensiveMetrics:
        """
        Gradually increase load to find breaking point.
        """
        steps = ramp_duration_seconds // step_duration_seconds
        rps_increment = (end_rps - start_rps) / steps

        console.print(Panel(
            f"[bold magenta]Ramp-Up Test[/bold magenta]\n"
            f"Start RPS: {start_rps:,} → End RPS: {end_rps:,}\n"
            f"Duration: {ramp_duration_seconds}s | Steps: {steps}",
            title="📈 Starting Test"
        ) if RICH_AVAILABLE else f"Ramp-Up - {start_rps} to {end_rps} RPS")

        self.metrics = ComprehensiveMetrics()
        self.metrics.start_time = time.time()

        connector = aiohttp.TCPConnector(limit=concurrency * 2, limit_per_host=concurrency * 2)

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            semaphore = asyncio.Semaphore(concurrency)
            current_rps = start_rps

            for step in range(steps):
                target_requests = int(current_rps * step_duration_seconds)
                console.print(f"[magenta]Step {step + 1}/{steps} - Target: {int(current_rps):,} RPS ({target_requests:,} requests)[/magenta]" if RICH_AVAILABLE else f"Step {step + 1}/{steps}: {int(current_rps)} RPS")

                async def make_request(req_id):
                    async with semaphore:
                        result = await self._make_request(session, req_id)
                        self.metrics.add_result(result)

                tasks = [make_request(i) for i in range(target_requests)]

                if RICH_AVAILABLE:
                    with Live(self._create_metrics_table(), refresh_per_second=4) as live:
                        async def updater():
                            while not all(t.done() for t in tasks):
                                live.update(self._create_metrics_table())
                                await asyncio.sleep(0.25)

                        gather = asyncio.gather(*tasks, return_exceptions=True)
                        update_task = asyncio.create_task(updater())
                        await gather
                        update_task.cancel()
                else:
                    await asyncio.gather(*tasks, return_exceptions=True)

                current_rps += rps_increment

        self.metrics.end_time = time.time()
        return self.metrics

    # =========================================================================
    # SCENARIO: Maximum Throughput
    # =========================================================================
    async def max_throughput_test(
        self,
        duration_seconds: int = 30,
        concurrency: int = 1000,
    ) -> ComprehensiveMetrics:
        """
        Push maximum requests - find the ceiling.
        """
        console.print(Panel(
            f"[bold red]⚠️ MAXIMUM THROUGHPUT TEST ⚠️[/bold red]\n"
            f"Duration: {duration_seconds}s | Concurrency: {concurrency}",
            title="🔥 Starting Test"
        ) if RICH_AVAILABLE else f"MAX THROUGHPUT - Duration: {duration_seconds}s, Concurrency: {concurrency}")

        self.metrics = ComprehensiveMetrics()
        self.metrics.start_time = time.time()
        self._stop_event.clear()

        connector = aiohttp.TCPConnector(
            limit=concurrency * 2,
            limit_per_host=concurrency * 2,
            keepalive_timeout=30,
        )

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            semaphore = asyncio.Semaphore(concurrency)
            end_time = time.time() + duration_seconds

            async def hammer():
                while time.time() < end_time and not self._stop_event.is_set():
                    async with semaphore:
                        result = await self._make_request(session)
                        self.metrics.add_result(result)

            workers = [hammer() for _ in range(concurrency)]

            if RICH_AVAILABLE:
                with Live(self._create_metrics_table(), refresh_per_second=4) as live:
                    async def updater():
                        while time.time() < end_time:
                            live.update(self._create_metrics_table())
                            await asyncio.sleep(0.25)

                    await asyncio.gather(updater(), *workers, return_exceptions=True)
            else:
                await asyncio.gather(*workers, return_exceptions=True)

        self.metrics.end_time = time.time()
        return self.metrics

    # =========================================================================
    # SCENARIO: Rate Limit Discovery
    # =========================================================================
    async def rate_limit_discovery(
        self,
        start_rps: int = 10,
        max_rps: int = 10000,
        step_multiplier: float = 1.5,
        step_duration: int = 10,
        threshold_429_percent: float = 5.0,
    ) -> Optional[int]:
        """
        Automatically discover the rate limit threshold.
        """
        console.print(Panel(
            f"[bold blue]Rate Limit Discovery[/bold blue]\n"
            f"Start: {start_rps} RPS | Max: {max_rps:,} RPS\n"
            f"Stop when 429s exceed {threshold_429_percent}%",
            title="🔍 Starting Discovery"
        ) if RICH_AVAILABLE else f"Rate Limit Discovery - Start: {start_rps}, Max: {max_rps}")

        current_rps = start_rps
        discovered_limit = None
        concurrency = 500

        connector = aiohttp.TCPConnector(limit=concurrency * 2, limit_per_host=concurrency * 2)

        while current_rps <= max_rps:
            console.print(f"[blue]Testing {int(current_rps):,} RPS...[/blue]" if RICH_AVAILABLE else f"Testing {int(current_rps)} RPS...")

            # Reset metrics for this step
            self.metrics = ComprehensiveMetrics()
            self.metrics.start_time = time.time()

            async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
                semaphore = asyncio.Semaphore(concurrency)
                target_requests = int(current_rps * step_duration)

                async def make_request(req_id):
                    async with semaphore:
                        result = await self._make_request(session, req_id)
                        self.metrics.add_result(result)

                tasks = [make_request(i) for i in range(target_requests)]
                await asyncio.gather(*tasks, return_exceptions=True)

            self.metrics.end_time = time.time()

            rate_limited_pct = self.metrics.rate_limit_rate
            console.print(
                f"  Requests: {self.metrics.total_requests:,} | "
                f"429s: {self.metrics.rate_limited_requests:,} ({rate_limited_pct:.1f}%) | "
                f"Actual RPS: {self.metrics.rps:,.0f}"
            )

            if rate_limited_pct > threshold_429_percent:
                discovered_limit = int(current_rps / step_multiplier)
                console.print(f"[green]✓ Rate limit discovered: ~{discovered_limit:,} RPS[/green]" if RICH_AVAILABLE else f"Rate limit: ~{discovered_limit} RPS")
                break

            current_rps *= step_multiplier

        if not discovered_limit:
            console.print(f"[yellow]⚠ No rate limit found up to {max_rps:,} RPS[/yellow]" if RICH_AVAILABLE else f"No rate limit found up to {max_rps} RPS")

        return discovered_limit

    # =========================================================================
    # Report Generation
    # =========================================================================
    def generate_report(
        self,
        format: ReportFormat = ReportFormat.CONSOLE,
        output_path: Optional[str] = None,
        test_name: str = "API Stress Test",
    ) -> str:
        """Generate comprehensive report in various formats."""

        if format == ReportFormat.JSON:
            return self._generate_json_report(output_path, test_name)
        elif format == ReportFormat.HTML:
            return self._generate_html_report(output_path, test_name)
        elif format == ReportFormat.MARKDOWN:
            return self._generate_markdown_report(output_path, test_name)
        else:
            return self._print_console_report()

    def _generate_json_report(self, output_path: Optional[str], test_name: str) -> str:
        """Generate JSON report."""
        report = {
            "test_name": test_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target_url": self.base_url,
            "method": self.method,
            "metrics": self.metrics.to_dict(),
            "test_passed": self._test_passed(),
            "pass_criteria": self._get_pass_criteria(),
        }

        json_str = json.dumps(report, indent=2)

        if output_path:
            Path(output_path).write_text(json_str)
            console.print(f"[green]JSON report saved to: {output_path}[/green]" if RICH_AVAILABLE else f"JSON report saved to: {output_path}")

        return json_str

    def _generate_html_report(self, output_path: Optional[str], test_name: str) -> str:
        """Generate HTML report with charts."""
        m = self.metrics
        metrics_dict = m.to_dict()

        # Determine status colors
        success_color = "#22c55e" if m.success_rate >= 95 else "#f59e0b" if m.success_rate >= 80 else "#ef4444"
        apdex_color = "#22c55e" if m.apdex_score() >= 0.85 else "#f59e0b" if m.apdex_score() >= 0.70 else "#ef4444"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{test_name} - Stress Test Report</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif; background: #0f172a; color: #e2e8f0; line-height: 1.6; padding: 2rem; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h1 {{ font-size: 2rem; margin-bottom: 0.5rem; color: #f8fafc; }}
        .subtitle {{ color: #94a3b8; margin-bottom: 2rem; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1.5rem; margin-bottom: 2rem; }}
        .card {{ background: #1e293b; border-radius: 12px; padding: 1.5rem; border: 1px solid #334155; }}
        .card-title {{ font-size: 0.875rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem; }}
        .card-value {{ font-size: 2rem; font-weight: 700; color: #f8fafc; }}
        .card-value.success {{ color: #22c55e; }}
        .card-value.warning {{ color: #f59e0b; }}
        .card-value.error {{ color: #ef4444; }}
        .metric-row {{ display: flex; justify-content: space-between; padding: 0.75rem 0; border-bottom: 1px solid #334155; }}
        .metric-row:last-child {{ border-bottom: none; }}
        .metric-label {{ color: #94a3b8; }}
        .metric-value {{ font-weight: 600; color: #f8fafc; }}
        .section {{ margin-bottom: 2rem; }}
        .section-title {{ font-size: 1.25rem; margin-bottom: 1rem; color: #f8fafc; padding-bottom: 0.5rem; border-bottom: 2px solid #3b82f6; }}
        .status-badge {{ display: inline-block; padding: 0.25rem 0.75rem; border-radius: 9999px; font-size: 0.875rem; font-weight: 600; }}
        .status-pass {{ background: #166534; color: #86efac; }}
        .status-fail {{ background: #991b1b; color: #fca5a5; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 0.75rem; text-align: left; border-bottom: 1px solid #334155; }}
        th {{ color: #94a3b8; font-weight: 600; }}
        .bar {{ height: 8px; border-radius: 4px; background: #334155; overflow: hidden; }}
        .bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.3s; }}
        .timestamp {{ color: #64748b; font-size: 0.875rem; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 {test_name}</h1>
        <p class="subtitle">
            Target: {self.base_url} | Method: {self.method} |
            Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}
        </p>

        <div class="section">
            <div class="grid">
                <div class="card">
                    <div class="card-title">Total Requests</div>
                    <div class="card-value">{m.total_requests:,}</div>
                </div>
                <div class="card">
                    <div class="card-title">Success Rate</div>
                    <div class="card-value" style="color: {success_color}">{m.success_rate:.1f}%</div>
                </div>
                <div class="card">
                    <div class="card-title">Requests/Second</div>
                    <div class="card-value">{m.rps:,.0f}</div>
                </div>
                <div class="card">
                    <div class="card-title">Apdex Score</div>
                    <div class="card-value" style="color: {apdex_color}">{m.apdex_score():.3f}</div>
                    <div class="card-title" style="margin-top: 0.25rem">{m._apdex_rating()}</div>
                </div>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">Request Summary</h2>
            <div class="card">
                <div class="metric-row">
                    <span class="metric-label">Successful Requests</span>
                    <span class="metric-value" style="color: #22c55e">{m.successful_requests:,}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">Failed Requests</span>
                    <span class="metric-value" style="color: #ef4444">{m.failed_requests:,}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">Rate Limited (429)</span>
                    <span class="metric-value" style="color: #f59e0b">{m.rate_limited_requests:,}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">Timeouts</span>
                    <span class="metric-value">{m.timeout_requests:,}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">Connection Errors</span>
                    <span class="metric-value">{m.connection_errors:,}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">Test Duration</span>
                    <span class="metric-value">{m.duration:.2f}s</span>
                </div>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">Latency Metrics (ms)</h2>
            <div class="grid">
                <div class="card">
                    <div class="metric-row">
                        <span class="metric-label">Average</span>
                        <span class="metric-value">{m.avg_latency:.2f}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Min</span>
                        <span class="metric-value">{m.min_latency:.2f}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Max</span>
                        <span class="metric-value">{m.max_latency:.2f}</span>
                    </div>
                </div>
                <div class="card">
                    <div class="metric-row">
                        <span class="metric-label">P50 (Median)</span>
                        <span class="metric-value">{m.p50_latency:.2f}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">P90</span>
                        <span class="metric-value">{m.p90_latency:.2f}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">P95</span>
                        <span class="metric-value">{m.p95_latency:.2f}</span>
                    </div>
                </div>
                <div class="card">
                    <div class="metric-row">
                        <span class="metric-label">P99</span>
                        <span class="metric-value">{m.p99_latency:.2f}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Std Deviation</span>
                        <span class="metric-value">{m.latency_std_dev:.2f}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Avg TTFB</span>
                        <span class="metric-value">{m.avg_ttfb:.2f}</span>
                    </div>
                </div>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">Throughput & Quality</h2>
            <div class="card">
                <div class="metric-row">
                    <span class="metric-label">Data Transferred</span>
                    <span class="metric-value">{m.total_bytes_received / (1024*1024):.2f} MB</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">Throughput</span>
                    <span class="metric-value">{m.throughput_mbps:.4f} MB/s</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">Availability</span>
                    <span class="metric-value">{m.availability:.2f}%</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">Error Rate</span>
                    <span class="metric-value">{m.error_rate:.2f}%</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">Rate Limit Rate</span>
                    <span class="metric-value">{m.rate_limit_rate:.2f}%</span>
                </div>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">Status Code Distribution</h2>
            <div class="card">
                <table>
                    <thead>
                        <tr>
                            <th>Status Code</th>
                            <th>Count</th>
                            <th>Percentage</th>
                            <th>Distribution</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(f'''
                        <tr>
                            <td><strong>{code}</strong></td>
                            <td>{count:,}</td>
                            <td>{count/m.total_requests*100:.1f}%</td>
                            <td>
                                <div class="bar">
                                    <div class="bar-fill" style="width: {count/m.total_requests*100}%; background: {'#22c55e' if 200 <= code < 300 else '#f59e0b' if code == 429 else '#ef4444'}"></div>
                                </div>
                            </td>
                        </tr>
                        ''' for code, count in sorted(m.status_codes.items()))}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">Test Result</h2>
            <div class="card">
                <p style="font-size: 1.25rem; margin-bottom: 1rem;">
                    Status: <span class="status-badge {'status-pass' if self._test_passed() else 'status-fail'}">
                        {'✓ PASSED' if self._test_passed() else '✗ FAILED'}
                    </span>
                </p>
                <div class="metric-row">
                    <span class="metric-label">Success Rate Threshold</span>
                    <span class="metric-value">≥ 95% (Actual: {m.success_rate:.1f}%)</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">P99 Latency Threshold</span>
                    <span class="metric-value">≤ 5000ms (Actual: {m.p99_latency:.0f}ms)</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">Error Rate Threshold</span>
                    <span class="metric-value">≤ 5% (Actual: {m.error_rate:.1f}%)</span>
                </div>
            </div>
        </div>

        <p class="timestamp">Report generated by Stress Test Toolkit v2.0</p>
    </div>
</body>
</html>"""

        if output_path:
            Path(output_path).write_text(html)
            console.print(f"[green]HTML report saved to: {output_path}[/green]" if RICH_AVAILABLE else f"HTML report saved to: {output_path}")

        return html

    def _generate_markdown_report(self, output_path: Optional[str], test_name: str) -> str:
        """Generate Markdown report."""
        m = self.metrics

        md = f"""# 📊 {test_name}

**Target:** `{self.base_url}`
**Method:** `{self.method}`
**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}

---

## Summary

| Metric | Value |
|--------|-------|
| Total Requests | {m.total_requests:,} |
| Successful | {m.successful_requests:,} |
| Failed | {m.failed_requests:,} |
| Rate Limited (429) | {m.rate_limited_requests:,} |
| Duration | {m.duration:.2f}s |
| Requests/Second | {m.rps:,.0f} |

---

## Success Metrics

| Metric | Value | Status |
|--------|-------|--------|
| Success Rate | {m.success_rate:.2f}% | {'✅' if m.success_rate >= 95 else '⚠️' if m.success_rate >= 80 else '❌'} |
| Error Rate | {m.error_rate:.2f}% | {'✅' if m.error_rate <= 5 else '⚠️' if m.error_rate <= 10 else '❌'} |
| Availability | {m.availability:.2f}% | {'✅' if m.availability >= 99 else '⚠️' if m.availability >= 95 else '❌'} |
| Apdex Score | {m.apdex_score():.3f} ({m._apdex_rating()}) | {'✅' if m.apdex_score() >= 0.85 else '⚠️' if m.apdex_score() >= 0.70 else '❌'} |

---

## Latency Metrics (ms)

| Percentile | Value |
|------------|-------|
| Min | {m.min_latency:.2f} |
| Average | {m.avg_latency:.2f} |
| P50 (Median) | {m.p50_latency:.2f} |
| P90 | {m.p90_latency:.2f} |
| P95 | {m.p95_latency:.2f} |
| P99 | {m.p99_latency:.2f} |
| Max | {m.max_latency:.2f} |
| Std Dev | {m.latency_std_dev:.2f} |

---

## Throughput

| Metric | Value |
|--------|-------|
| Total Data | {m.total_bytes_received / (1024*1024):.2f} MB |
| Throughput | {m.throughput_mbps:.4f} MB/s |
| Avg TTFB | {m.avg_ttfb:.2f} ms |

---

## Status Code Distribution

| Code | Count | Percentage |
|------|-------|------------|
{''.join(f"| {code} | {count:,} | {count/m.total_requests*100:.1f}% |" + chr(10) for code, count in sorted(m.status_codes.items()))}

---

## Test Result

**Status:** {'✅ **PASSED**' if self._test_passed() else '❌ **FAILED**'}

### Pass Criteria
- Success Rate: ≥ 95% (Actual: {m.success_rate:.1f}%)
- P99 Latency: ≤ 5000ms (Actual: {m.p99_latency:.0f}ms)
- Error Rate: ≤ 5% (Actual: {m.error_rate:.1f}%)

---

*Generated by Stress Test Toolkit v2.0*
"""

        if output_path:
            Path(output_path).write_text(md)
            console.print(f"[green]Markdown report saved to: {output_path}[/green]" if RICH_AVAILABLE else f"Markdown report saved to: {output_path}")

        return md

    def _print_console_report(self) -> str:
        """Print report to console."""
        m = self.metrics

        if RICH_AVAILABLE:
            console.print("\n")
            console.print(Panel(
                f"""[bold]Test Summary[/bold]

[cyan]Total Requests:[/cyan]     {m.total_requests:,}
[green]Successful:[/green]         {m.successful_requests:,} ({m.success_rate:.1f}%)
[red]Failed:[/red]             {m.failed_requests:,}
[yellow]Rate Limited (429):[/yellow] {m.rate_limited_requests:,}
[dim]Timeouts:[/dim]           {m.timeout_requests:,}
[dim]Connection Errors:[/dim]  {m.connection_errors:,}

[cyan]Duration:[/cyan]           {m.duration:.2f}s
[cyan]Throughput:[/cyan]         {m.rps:,.0f} req/s
[cyan]Data Transfer:[/cyan]      {m.total_bytes_received / (1024*1024):.2f} MB

[bold]Latency Statistics (ms):[/bold]
  Min:      {m.min_latency:.2f}
  Average:  {m.avg_latency:.2f}
  P50:      {m.p50_latency:.2f}
  P90:      {m.p90_latency:.2f}
  P95:      {m.p95_latency:.2f}
  P99:      {m.p99_latency:.2f}
  Max:      {m.max_latency:.2f}
  Std Dev:  {m.latency_std_dev:.2f}

[bold]Quality Metrics:[/bold]
  Apdex Score:    {m.apdex_score():.3f} ({m._apdex_rating()})
  Availability:   {m.availability:.1f}%
  Avg TTFB:       {m.avg_ttfb:.2f}ms

[bold]Status Code Distribution:[/bold]
{self._format_status_codes()}

[bold]Test Result:[/bold] {'[green]✓ PASSED[/green]' if self._test_passed() else '[red]✗ FAILED[/red]'}
""",
                title="📊 Final Results",
                border_style="green" if self._test_passed() else "red"
            ))
        else:
            print(f"""
=== Test Summary ===
Total Requests:     {m.total_requests:,}
Successful:         {m.successful_requests:,} ({m.success_rate:.1f}%)
Failed:             {m.failed_requests:,}
Rate Limited (429): {m.rate_limited_requests:,}
Duration:           {m.duration:.2f}s
Throughput:         {m.rps:,.0f} req/s

Latency (ms): Avg={m.avg_latency:.2f}, P50={m.p50_latency:.2f}, P95={m.p95_latency:.2f}, P99={m.p99_latency:.2f}
Apdex Score: {m.apdex_score():.3f} ({m._apdex_rating()})

Test Result: {'PASSED' if self._test_passed() else 'FAILED'}
""")

        return ""

    def _format_status_codes(self) -> str:
        """Format status code distribution."""
        if not self.metrics.status_codes:
            return "  No responses recorded"
        lines = []
        for code, count in sorted(self.metrics.status_codes.items()):
            pct = count / self.metrics.total_requests * 100
            color = "green" if 200 <= code < 300 else "yellow" if code == 429 else "red"
            lines.append(f"  [{color}]{code}[/{color}]: {count:,} ({pct:.1f}%)")
        return "\n".join(lines)

    def _test_passed(self) -> bool:
        """Determine if the test passed based on criteria."""
        m = self.metrics
        return (
            m.success_rate >= 95 and
            m.p99_latency <= 5000 and
            m.error_rate <= 5
        )

    def _get_pass_criteria(self) -> Dict[str, Any]:
        """Get pass criteria with actual vs expected."""
        m = self.metrics
        return {
            "success_rate": {"expected": ">=95%", "actual": f"{m.success_rate:.1f}%", "passed": m.success_rate >= 95},
            "p99_latency": {"expected": "<=5000ms", "actual": f"{m.p99_latency:.0f}ms", "passed": m.p99_latency <= 5000},
            "error_rate": {"expected": "<=5%", "actual": f"{m.error_rate:.1f}%", "passed": m.error_rate <= 5},
        }


async def run_with_retry(
    engine: StressTestEngine,
    scenario: str,
    max_attempts: int = 5,
    **kwargs,
) -> Tuple[ComprehensiveMetrics, bool, int]:
    """
    Run a stress test scenario with retry logic.
    Returns (metrics, success, attempts_used)
    """
    for attempt in range(1, max_attempts + 1):
        console.print(f"[bold]Attempt {attempt}/{max_attempts}[/bold]" if RICH_AVAILABLE else f"Attempt {attempt}/{max_attempts}")

        try:
            if scenario == "basic":
                await engine.basic_load(**kwargs)
            elif scenario == "sustained":
                await engine.sustained_load(**kwargs)
            elif scenario == "burst":
                await engine.burst_test(**kwargs)
            elif scenario == "ramp":
                await engine.ramp_up_test(**kwargs)
            elif scenario == "max":
                await engine.max_throughput_test(**kwargs)

            # Check if test passed
            if engine._test_passed():
                console.print(f"[green]✓ Test passed on attempt {attempt}[/green]" if RICH_AVAILABLE else f"Test passed on attempt {attempt}")
                return engine.metrics, True, attempt
            else:
                console.print(f"[yellow]⚠ Test failed criteria on attempt {attempt}[/yellow]" if RICH_AVAILABLE else f"Test failed on attempt {attempt}")

        except Exception as e:
            console.print(f"[red]Error on attempt {attempt}: {e}[/red]" if RICH_AVAILABLE else f"Error: {e}")

        if attempt < max_attempts:
            wait_time = 2 ** attempt  # Exponential backoff
            console.print(f"[dim]Waiting {wait_time}s before retry...[/dim]" if RICH_AVAILABLE else f"Waiting {wait_time}s...")
            await asyncio.sleep(wait_time)

    return engine.metrics, False, max_attempts


async def main():
    parser = argparse.ArgumentParser(
        description="🚀 API Stress Test Toolkit v2.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--url", "-u", required=True, help="Target API URL")
    parser.add_argument("--method", "-m", default="GET", choices=["GET", "POST", "PUT", "DELETE", "PATCH"])
    parser.add_argument("--scenario", "-s", default="basic",
                       choices=["basic", "sustained", "burst", "ramp", "max", "discover"])
    parser.add_argument("--requests", "-n", type=int, default=10000, help="Total requests (basic scenario)")
    parser.add_argument("--concurrency", "-c", type=int, default=100, help="Concurrent connections")
    parser.add_argument("--duration", "-d", type=int, default=60, help="Test duration in seconds")
    parser.add_argument("--rps", type=int, default=1000, help="Target requests per second")
    parser.add_argument("--burst-size", type=int, default=10000, help="Requests per burst")
    parser.add_argument("--burst-count", type=int, default=5, help="Number of bursts")
    parser.add_argument("--header", "-H", action="append", help="Custom header (format: Key:Value)")
    parser.add_argument("--data", type=str, help="JSON payload for POST/PUT/PATCH")
    parser.add_argument("--timeout", "-t", type=float, default=30.0, help="Request timeout in seconds")
    parser.add_argument("--no-ssl-verify", action="store_true", help="Disable SSL verification")
    parser.add_argument("--retry", type=int, default=0, help="Number of retry attempts for failed requests")
    parser.add_argument("--max-attempts", type=int, default=1, help="Max test attempts if criteria not met")
    parser.add_argument("--report", choices=["console", "json", "html", "markdown"], default="console")
    parser.add_argument("--output", "-o", type=str, help="Output file path for report")
    parser.add_argument("--test-name", type=str, default="API Stress Test", help="Name for the test report")

    args = parser.parse_args()

    # Parse headers
    headers = {"Content-Type": "application/json", "User-Agent": "StressTest/2.0"}
    if args.header:
        for h in args.header:
            key, value = h.split(":", 1)
            headers[key.strip()] = value.strip()

    # Parse payload
    payload = json.loads(args.data) if args.data else None

    # Create engine
    engine = StressTestEngine(
        base_url=args.url,
        method=args.method,
        headers=headers,
        payload=payload,
        timeout=args.timeout,
        verify_ssl=not args.no_ssl_verify,
        retry_attempts=args.retry,
    )

    console.print(f"\n[bold]Target:[/bold] {args.url}")
    console.print(f"[bold]Method:[/bold] {args.method}")
    console.print(f"[bold]Scenario:[/bold] {args.scenario}\n")

    # Prepare scenario kwargs
    kwargs = {}
    if args.scenario == "basic":
        kwargs = {"total_requests": args.requests, "concurrency": args.concurrency}
    elif args.scenario == "sustained":
        kwargs = {"duration_seconds": args.duration, "target_rps": args.rps, "concurrency": args.concurrency}
    elif args.scenario == "burst":
        kwargs = {"burst_size": args.burst_size, "burst_count": args.burst_count, "concurrency": args.concurrency}
    elif args.scenario == "ramp":
        kwargs = {"start_rps": 100, "end_rps": args.rps, "ramp_duration_seconds": args.duration, "concurrency": args.concurrency}
    elif args.scenario == "max":
        kwargs = {"duration_seconds": args.duration, "concurrency": args.concurrency}
    elif args.scenario == "discover":
        await engine.rate_limit_discovery(max_rps=args.rps * 10)
        return

    # Run with retry
    if args.max_attempts > 1:
        metrics, success, attempts = await run_with_retry(engine, args.scenario, args.max_attempts, **kwargs)
    else:
        # Run single attempt
        if args.scenario == "basic":
            await engine.basic_load(**kwargs)
        elif args.scenario == "sustained":
            await engine.sustained_load(**kwargs)
        elif args.scenario == "burst":
            await engine.burst_test(**kwargs)
        elif args.scenario == "ramp":
            await engine.ramp_up_test(**kwargs)
        elif args.scenario == "max":
            await engine.max_throughput_test(**kwargs)
        success = engine._test_passed()

    # Generate report
    report_format = ReportFormat(args.report)
    engine.generate_report(format=report_format, output_path=args.output, test_name=args.test_name)

    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
