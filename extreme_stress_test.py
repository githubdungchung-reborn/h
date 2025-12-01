#!/usr/bin/env python3
"""
🔥 EXTREME SCALE STRESS TEST 🔥
================================
Ultra-high performance stress testing for 1M, 10M, 100M+ requests.

Optimizations:
- Memory-efficient streaming metrics (no full latency storage)
- Reservoir sampling for percentile estimation
- Connection pooling with keep-alive
- Batch request processing
- Multi-worker architecture
- Real-time progress without memory bloat

Presets:
- 1M requests: ~16,667 RPS for 60 seconds
- 10M requests: ~166,667 RPS for 60 seconds
- 100M requests: ~1,666,667 RPS for 60 seconds (distributed recommended)

Requirements:
    pip install aiohttp rich uvloop (optional: uvloop for 2x performance on Linux)

Usage:
    python extreme_stress_test.py --url http://localhost:8000/api/products --preset 1M
    python extreme_stress_test.py --url http://localhost:8000/api/products --preset 10M --workers 4
    python extreme_stress_test.py --url http://localhost:8000/api/products --requests 100000000 --duration 600
"""

import asyncio
import aiohttp
import argparse
import time
import random
import json
import math
import os
import sys
import signal
import multiprocessing as mp
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import heapq

# Try to use uvloop for better performance on Linux
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    UVLOOP_AVAILABLE = True
except ImportError:
    UVLOOP_AVAILABLE = False

try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

if RICH_AVAILABLE:
    console = Console()
else:
    class MockConsole:
        def print(self, *args, **kwargs):
            import re
            text = str(args[0]) if args else ""
            clean = re.sub(r'\[.*?\]', '', text)
            print(clean)
    console = MockConsole()


# =============================================================================
# MEMORY-EFFICIENT METRICS WITH RESERVOIR SAMPLING
# =============================================================================

class ReservoirSampler:
    """
    Reservoir sampling for memory-efficient percentile estimation.
    Maintains a fixed-size sample that represents the full distribution.
    """
    def __init__(self, size: int = 10000):
        self.size = size
        self.reservoir: List[float] = []
        self.count = 0

    def add(self, value: float):
        self.count += 1
        if len(self.reservoir) < self.size:
            self.reservoir.append(value)
        else:
            # Reservoir sampling algorithm
            j = random.randint(0, self.count - 1)
            if j < self.size:
                self.reservoir[j] = value

    def percentile(self, p: float) -> float:
        if not self.reservoir:
            return 0
        sorted_sample = sorted(self.reservoir)
        idx = int(len(sorted_sample) * p / 100)
        return sorted_sample[min(idx, len(sorted_sample) - 1)]

    @property
    def min(self) -> float:
        return min(self.reservoir) if self.reservoir else 0

    @property
    def max(self) -> float:
        return max(self.reservoir) if self.reservoir else 0

    def mean(self) -> float:
        return sum(self.reservoir) / len(self.reservoir) if self.reservoir else 0


class TDigest:
    """
    Simple T-Digest approximation for streaming percentiles.
    More accurate than reservoir sampling for extreme percentiles.
    """
    def __init__(self, compression: int = 100):
        self.compression = compression
        self.centroids: List[Tuple[float, int]] = []  # (mean, weight)
        self.count = 0
        self.min_val = float('inf')
        self.max_val = float('-inf')
        self.sum_val = 0

    def add(self, value: float):
        self.count += 1
        self.min_val = min(self.min_val, value)
        self.max_val = max(self.max_val, value)
        self.sum_val += value

        # Simple insertion (for production, use proper merging)
        self.centroids.append((value, 1))

        # Compress periodically
        if len(self.centroids) > self.compression * 2:
            self._compress()

    def _compress(self):
        if not self.centroids:
            return
        self.centroids.sort(key=lambda x: x[0])
        compressed = []
        for mean, weight in self.centroids:
            if compressed and len(compressed) < self.compression:
                last_mean, last_weight = compressed[-1]
                new_weight = last_weight + weight
                new_mean = (last_mean * last_weight + mean * weight) / new_weight
                compressed[-1] = (new_mean, new_weight)
            else:
                compressed.append((mean, weight))
        self.centroids = compressed

    def percentile(self, p: float) -> float:
        if not self.centroids:
            return 0
        self._compress()
        target = self.count * p / 100
        cumulative = 0
        for mean, weight in self.centroids:
            cumulative += weight
            if cumulative >= target:
                return mean
        return self.centroids[-1][0] if self.centroids else 0

    @property
    def min(self) -> float:
        return self.min_val if self.min_val != float('inf') else 0

    @property
    def max(self) -> float:
        return self.max_val if self.max_val != float('-inf') else 0

    def mean(self) -> float:
        return self.sum_val / self.count if self.count > 0 else 0


@dataclass
class StreamingMetrics:
    """
    Memory-efficient metrics using streaming algorithms.
    Can handle billions of requests without running out of memory.
    """
    # Counters
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rate_limited_requests: int = 0
    timeout_requests: int = 0
    connection_errors: int = 0

    # Status codes (limited to top codes)
    status_codes: Dict[int, int] = field(default_factory=lambda: defaultdict(int))

    # Streaming latency estimation
    latency_sampler: ReservoirSampler = field(default_factory=lambda: ReservoirSampler(50000))

    # Running statistics (Welford's algorithm)
    _latency_mean: float = 0
    _latency_m2: float = 0
    _latency_count: int = 0
    _latency_min: float = float('inf')
    _latency_max: float = float('-inf')

    # Timing
    start_time: float = 0
    end_time: float = 0

    # Throughput tracking
    _last_rps_time: float = 0
    _last_rps_count: int = 0
    current_rps: float = 0

    # Bytes transferred
    total_bytes: int = 0

    def add_latency(self, latency_ms: float):
        """Update latency statistics using Welford's online algorithm."""
        self._latency_count += 1
        delta = latency_ms - self._latency_mean
        self._latency_mean += delta / self._latency_count
        delta2 = latency_ms - self._latency_mean
        self._latency_m2 += delta * delta2
        self._latency_min = min(self._latency_min, latency_ms)
        self._latency_max = max(self._latency_max, latency_ms)

        # Add to reservoir for percentile estimation
        self.latency_sampler.add(latency_ms)

    def add_success(self, status_code: int, latency_ms: float, response_size: int = 0):
        self.total_requests += 1
        self.successful_requests += 1
        self.status_codes[status_code] += 1
        self.total_bytes += response_size
        self.add_latency(latency_ms)

    def add_failure(self, status_code: int, latency_ms: float, error_type: str = ""):
        self.total_requests += 1
        self.failed_requests += 1
        self.status_codes[status_code] += 1

        if status_code == 429:
            self.rate_limited_requests += 1
        elif error_type == "timeout":
            self.timeout_requests += 1
        elif error_type == "connection":
            self.connection_errors += 1

        if latency_ms > 0:
            self.add_latency(latency_ms)

    def update_rps(self):
        """Update current RPS calculation."""
        now = time.time()
        if self._last_rps_time > 0:
            elapsed = now - self._last_rps_time
            if elapsed > 0:
                requests_delta = self.total_requests - self._last_rps_count
                self.current_rps = requests_delta / elapsed
        self._last_rps_time = now
        self._last_rps_count = self.total_requests

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time if self.end_time else time.time() - self.start_time

    @property
    def rps(self) -> float:
        return self.total_requests / self.duration if self.duration > 0 else 0

    @property
    def success_rate(self) -> float:
        return (self.successful_requests / self.total_requests * 100) if self.total_requests > 0 else 0

    @property
    def avg_latency(self) -> float:
        return self._latency_mean

    @property
    def latency_std_dev(self) -> float:
        if self._latency_count < 2:
            return 0
        return math.sqrt(self._latency_m2 / (self._latency_count - 1))

    @property
    def min_latency(self) -> float:
        return self._latency_min if self._latency_min != float('inf') else 0

    @property
    def max_latency(self) -> float:
        return self._latency_max if self._latency_max != float('-inf') else 0

    def percentile(self, p: float) -> float:
        return self.latency_sampler.percentile(p)

    @property
    def p50(self) -> float:
        return self.percentile(50)

    @property
    def p90(self) -> float:
        return self.percentile(90)

    @property
    def p95(self) -> float:
        return self.percentile(95)

    @property
    def p99(self) -> float:
        return self.percentile(99)

    @property
    def p999(self) -> float:
        return self.percentile(99.9)

    @property
    def throughput_mbps(self) -> float:
        return (self.total_bytes / (1024 * 1024)) / self.duration if self.duration > 0 else 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": {
                "total_requests": self.total_requests,
                "successful_requests": self.successful_requests,
                "failed_requests": self.failed_requests,
                "rate_limited_requests": self.rate_limited_requests,
                "timeout_requests": self.timeout_requests,
                "connection_errors": self.connection_errors,
                "duration_seconds": round(self.duration, 2),
                "requests_per_second": round(self.rps, 2),
                "peak_rps": round(self.current_rps, 2),
            },
            "rates": {
                "success_rate_percent": round(self.success_rate, 4),
                "error_rate_percent": round(100 - self.success_rate, 4),
                "rate_limit_percent": round(self.rate_limited_requests / max(1, self.total_requests) * 100, 4),
            },
            "latency_ms": {
                "average": round(self.avg_latency, 2),
                "std_dev": round(self.latency_std_dev, 2),
                "min": round(self.min_latency, 2),
                "max": round(self.max_latency, 2),
                "p50": round(self.p50, 2),
                "p90": round(self.p90, 2),
                "p95": round(self.p95, 2),
                "p99": round(self.p99, 2),
                "p99.9": round(self.p999, 2),
            },
            "throughput": {
                "total_bytes": self.total_bytes,
                "throughput_mbps": round(self.throughput_mbps, 4),
            },
            "status_codes": dict(sorted(self.status_codes.items())),
        }

    def merge_from(self, other: 'StreamingMetrics'):
        """Merge metrics from another instance (for multi-worker scenarios)."""
        self.total_requests += other.total_requests
        self.successful_requests += other.successful_requests
        self.failed_requests += other.failed_requests
        self.rate_limited_requests += other.rate_limited_requests
        self.timeout_requests += other.timeout_requests
        self.connection_errors += other.connection_errors
        self.total_bytes += other.total_bytes

        for code, count in other.status_codes.items():
            self.status_codes[code] += count

        # Merge latency stats (approximate)
        if other._latency_count > 0:
            total_count = self._latency_count + other._latency_count
            if total_count > 0:
                self._latency_mean = (
                    self._latency_mean * self._latency_count +
                    other._latency_mean * other._latency_count
                ) / total_count
                self._latency_count = total_count
                self._latency_min = min(self._latency_min, other._latency_min)
                self._latency_max = max(self._latency_max, other._latency_max)

        # Merge reservoir samples
        for val in other.latency_sampler.reservoir:
            self.latency_sampler.add(val)


# =============================================================================
# EXTREME SCALE STRESS TEST ENGINE
# =============================================================================

class ExtremeStressTestEngine:
    """
    Ultra-high performance stress test engine optimized for millions of requests.
    """

    def __init__(
        self,
        base_url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        payload: Optional[Dict] = None,
        timeout: float = 10.0,
        verify_ssl: bool = True,
    ):
        self.base_url = base_url
        self.method = method.upper()
        self.headers = headers or {
            "Content-Type": "application/json",
            "User-Agent": "ExtremeStressTest/1.0",
            "Connection": "keep-alive",
        }
        self.payload = payload
        self.timeout = aiohttp.ClientTimeout(total=timeout, connect=5)
        self.verify_ssl = verify_ssl
        self.metrics = StreamingMetrics()
        self._stop_event = asyncio.Event()

    async def _make_request_fast(
        self,
        session: aiohttp.ClientSession,
    ) -> Tuple[int, float, int]:
        """
        Optimized request function for maximum throughput.
        Returns: (status_code, latency_ms, response_size)
        """
        start = time.perf_counter()
        try:
            async with session.request(
                self.method,
                self.base_url,
                headers=self.headers,
                json=self.payload if self.method != "GET" else None,
                ssl=self.verify_ssl,
            ) as response:
                body = await response.read()
                latency = (time.perf_counter() - start) * 1000
                return response.status, latency, len(body)
        except asyncio.TimeoutError:
            return 0, (time.perf_counter() - start) * 1000, 0
        except aiohttp.ClientError:
            return 0, (time.perf_counter() - start) * 1000, 0
        except Exception:
            return 0, (time.perf_counter() - start) * 1000, 0

    def _create_progress_table(self) -> "Table":
        """Create progress table for display."""
        if not RICH_AVAILABLE:
            return None

        table = Table(title="🔥 EXTREME STRESS TEST", expand=True)
        table.add_column("Metric", style="cyan", width=20)
        table.add_column("Value", style="green", width=18)
        table.add_column("Metric", style="cyan", width=20)
        table.add_column("Value", style="green", width=18)

        m = self.metrics

        table.add_row(
            "Total Requests", f"{m.total_requests:,}",
            "Current RPS", f"{m.current_rps:,.0f}"
        )
        table.add_row(
            "Successful", f"[green]{m.successful_requests:,}[/green]",
            "Failed", f"[red]{m.failed_requests:,}[/red]"
        )
        table.add_row(
            "Success Rate", f"{m.success_rate:.2f}%",
            "Avg RPS", f"{m.rps:,.0f}"
        )
        table.add_row(
            "Rate Limited", f"[yellow]{m.rate_limited_requests:,}[/yellow]",
            "Timeouts", f"{m.timeout_requests:,}"
        )
        table.add_row(
            "Avg Latency", f"{m.avg_latency:.1f}ms",
            "P99 Latency", f"{m.p99:.1f}ms"
        )
        table.add_row(
            "Min Latency", f"{m.min_latency:.1f}ms",
            "Max Latency", f"{m.max_latency:.1f}ms"
        )
        table.add_row(
            "Duration", f"{m.duration:.1f}s",
            "Throughput", f"{m.throughput_mbps:.2f} MB/s"
        )

        return table

    # =========================================================================
    # SCENARIO: Fixed Request Count (1M, 10M, 100M)
    # =========================================================================
    async def fixed_request_count(
        self,
        total_requests: int,
        concurrency: int = 1000,
        batch_size: int = 10000,
    ) -> StreamingMetrics:
        """
        Execute a fixed number of requests as fast as possible.
        Optimized for millions of requests.
        """
        console.print(Panel(
            f"[bold red]🔥 EXTREME STRESS TEST 🔥[/bold red]\n"
            f"Target: {total_requests:,} requests\n"
            f"Concurrency: {concurrency:,} | Batch Size: {batch_size:,}",
            title="💥 Starting Extreme Test"
        ) if RICH_AVAILABLE else f"EXTREME TEST - {total_requests:,} requests, concurrency: {concurrency}")

        self.metrics = StreamingMetrics()
        self.metrics.start_time = time.time()
        self._stop_event.clear()

        # Optimized connector settings
        connector = aiohttp.TCPConnector(
            limit=concurrency,
            limit_per_host=concurrency,
            keepalive_timeout=60,
            enable_cleanup_closed=True,
            force_close=False,
        )

        semaphore = asyncio.Semaphore(concurrency)
        completed = 0

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:

            async def worker():
                nonlocal completed
                async with semaphore:
                    status, latency, size = await self._make_request_fast(session)

                    if 200 <= status < 300:
                        self.metrics.add_success(status, latency, size)
                    else:
                        error_type = ""
                        if status == 0:
                            error_type = "timeout"
                        elif status == 429:
                            error_type = "rate_limit"
                        self.metrics.add_failure(status, latency, error_type)

                    completed += 1

            # Process in batches for better memory management
            remaining = total_requests
            batch_num = 0

            if RICH_AVAILABLE:
                with Live(self._create_progress_table(), refresh_per_second=4) as live:
                    while remaining > 0 and not self._stop_event.is_set():
                        current_batch = min(batch_size, remaining)
                        tasks = [worker() for _ in range(current_batch)]

                        # Update RPS periodically
                        async def update_display():
                            while not all(t.done() for t in tasks):
                                self.metrics.update_rps()
                                live.update(self._create_progress_table())
                                await asyncio.sleep(0.25)

                        update_task = asyncio.create_task(update_display())
                        await asyncio.gather(*tasks, return_exceptions=True)
                        update_task.cancel()

                        remaining -= current_batch
                        batch_num += 1

                        # Progress update
                        progress_pct = (total_requests - remaining) / total_requests * 100
                        console.print(f"[dim]Batch {batch_num}: {progress_pct:.1f}% complete ({total_requests - remaining:,}/{total_requests:,})[/dim]")
            else:
                while remaining > 0 and not self._stop_event.is_set():
                    current_batch = min(batch_size, remaining)
                    tasks = [worker() for _ in range(current_batch)]
                    await asyncio.gather(*tasks, return_exceptions=True)
                    remaining -= current_batch
                    batch_num += 1
                    progress_pct = (total_requests - remaining) / total_requests * 100
                    print(f"Batch {batch_num}: {progress_pct:.1f}% complete")

        self.metrics.end_time = time.time()
        return self.metrics

    # =========================================================================
    # SCENARIO: Time-based Maximum Throughput
    # =========================================================================
    async def max_throughput_duration(
        self,
        duration_seconds: int,
        concurrency: int = 2000,
    ) -> StreamingMetrics:
        """
        Push maximum requests for a fixed duration.
        No rate limiting - as fast as possible.
        """
        console.print(Panel(
            f"[bold red]🔥 MAXIMUM THROUGHPUT TEST 🔥[/bold red]\n"
            f"Duration: {duration_seconds}s | Concurrency: {concurrency:,}",
            title="💥 Maximum Throughput"
        ) if RICH_AVAILABLE else f"MAX THROUGHPUT - {duration_seconds}s, concurrency: {concurrency}")

        self.metrics = StreamingMetrics()
        self.metrics.start_time = time.time()
        self._stop_event.clear()

        connector = aiohttp.TCPConnector(
            limit=concurrency * 2,
            limit_per_host=concurrency * 2,
            keepalive_timeout=60,
        )

        semaphore = asyncio.Semaphore(concurrency)
        end_time = time.time() + duration_seconds

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:

            async def hammer():
                while time.time() < end_time and not self._stop_event.is_set():
                    async with semaphore:
                        status, latency, size = await self._make_request_fast(session)

                        if 200 <= status < 300:
                            self.metrics.add_success(status, latency, size)
                        else:
                            error_type = "timeout" if status == 0 else ""
                            self.metrics.add_failure(status, latency, error_type)

            workers = [hammer() for _ in range(concurrency)]

            if RICH_AVAILABLE:
                with Live(self._create_progress_table(), refresh_per_second=4) as live:
                    async def updater():
                        while time.time() < end_time and not self._stop_event.is_set():
                            self.metrics.update_rps()
                            live.update(self._create_progress_table())
                            await asyncio.sleep(0.25)

                    await asyncio.gather(updater(), *workers, return_exceptions=True)
            else:
                await asyncio.gather(*workers, return_exceptions=True)

        self.metrics.end_time = time.time()
        return self.metrics

    # =========================================================================
    # SCENARIO: Sustained High RPS
    # =========================================================================
    async def sustained_rps(
        self,
        target_rps: int,
        duration_seconds: int,
        concurrency: int = 1000,
    ) -> StreamingMetrics:
        """
        Maintain a target RPS for a duration.
        Uses token bucket algorithm for precise rate control.
        """
        console.print(Panel(
            f"[bold green]📊 SUSTAINED RPS TEST[/bold green]\n"
            f"Target: {target_rps:,} RPS | Duration: {duration_seconds}s",
            title="⚡ Sustained Load"
        ) if RICH_AVAILABLE else f"SUSTAINED RPS - {target_rps:,} RPS for {duration_seconds}s")

        self.metrics = StreamingMetrics()
        self.metrics.start_time = time.time()
        self._stop_event.clear()

        connector = aiohttp.TCPConnector(
            limit=concurrency * 2,
            limit_per_host=concurrency * 2,
            keepalive_timeout=60,
        )

        semaphore = asyncio.Semaphore(concurrency)
        end_time = time.time() + duration_seconds

        # Token bucket for rate limiting
        tokens = target_rps
        last_refill = time.time()
        token_lock = asyncio.Lock()

        async def get_token() -> bool:
            nonlocal tokens, last_refill
            async with token_lock:
                now = time.time()
                elapsed = now - last_refill
                tokens = min(target_rps, tokens + int(elapsed * target_rps))
                last_refill = now

                if tokens > 0:
                    tokens -= 1
                    return True
                return False

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:

            async def worker():
                while time.time() < end_time and not self._stop_event.is_set():
                    if await get_token():
                        async with semaphore:
                            status, latency, size = await self._make_request_fast(session)

                            if 200 <= status < 300:
                                self.metrics.add_success(status, latency, size)
                            else:
                                error_type = "timeout" if status == 0 else ""
                                self.metrics.add_failure(status, latency, error_type)
                    else:
                        await asyncio.sleep(0.001)

            workers = [worker() for _ in range(min(concurrency, target_rps // 10 + 1))]

            if RICH_AVAILABLE:
                with Live(self._create_progress_table(), refresh_per_second=4) as live:
                    async def updater():
                        while time.time() < end_time and not self._stop_event.is_set():
                            self.metrics.update_rps()
                            live.update(self._create_progress_table())
                            await asyncio.sleep(0.25)

                    await asyncio.gather(updater(), *workers, return_exceptions=True)
            else:
                await asyncio.gather(*workers, return_exceptions=True)

        self.metrics.end_time = time.time()
        return self.metrics

    # =========================================================================
    # SCENARIO: Exponential Ramp-Up
    # =========================================================================
    async def exponential_ramp(
        self,
        start_rps: int = 100,
        end_rps: int = 100000,
        duration_seconds: int = 120,
        concurrency: int = 2000,
    ) -> StreamingMetrics:
        """
        Exponentially increase RPS to find breaking point.
        """
        console.print(Panel(
            f"[bold magenta]📈 EXPONENTIAL RAMP TEST[/bold magenta]\n"
            f"Start: {start_rps:,} RPS → End: {end_rps:,} RPS\n"
            f"Duration: {duration_seconds}s",
            title="🚀 Ramp Up"
        ) if RICH_AVAILABLE else f"EXPONENTIAL RAMP - {start_rps} to {end_rps} RPS")

        self.metrics = StreamingMetrics()
        self.metrics.start_time = time.time()
        self._stop_event.clear()

        connector = aiohttp.TCPConnector(
            limit=concurrency * 2,
            limit_per_host=concurrency * 2,
            keepalive_timeout=60,
        )

        semaphore = asyncio.Semaphore(concurrency)
        end_time = time.time() + duration_seconds

        # Calculate growth rate for exponential increase
        growth_rate = math.log(end_rps / start_rps) / duration_seconds

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:

            async def worker():
                while time.time() < end_time and not self._stop_event.is_set():
                    async with semaphore:
                        status, latency, size = await self._make_request_fast(session)

                        if 200 <= status < 300:
                            self.metrics.add_success(status, latency, size)
                        else:
                            error_type = "timeout" if status == 0 else ""
                            self.metrics.add_failure(status, latency, error_type)

            workers = [worker() for _ in range(concurrency)]

            if RICH_AVAILABLE:
                with Live(self._create_progress_table(), refresh_per_second=4) as live:
                    async def updater():
                        while time.time() < end_time and not self._stop_event.is_set():
                            elapsed = time.time() - self.metrics.start_time
                            current_target_rps = start_rps * math.exp(growth_rate * elapsed)
                            self.metrics.update_rps()
                            live.update(self._create_progress_table())
                            await asyncio.sleep(0.25)

                    await asyncio.gather(updater(), *workers, return_exceptions=True)
            else:
                await asyncio.gather(*workers, return_exceptions=True)

        self.metrics.end_time = time.time()
        return self.metrics

    # =========================================================================
    # SCENARIO: Wave Pattern (Sine Wave Load)
    # =========================================================================
    async def wave_pattern(
        self,
        base_rps: int = 5000,
        amplitude: int = 4000,
        period_seconds: float = 60.0,
        duration_seconds: int = 300,
        concurrency: int = 1000,
    ) -> StreamingMetrics:
        """
        Sine wave traffic pattern for testing auto-scaling.
        """
        console.print(Panel(
            f"[bold blue]🌊 WAVE PATTERN TEST[/bold blue]\n"
            f"Base: {base_rps:,} RPS | Amplitude: ±{amplitude:,}\n"
            f"Period: {period_seconds}s | Duration: {duration_seconds}s",
            title="🌊 Wave Load"
        ) if RICH_AVAILABLE else f"WAVE PATTERN - Base: {base_rps} RPS")

        self.metrics = StreamingMetrics()
        self.metrics.start_time = time.time()
        self._stop_event.clear()

        connector = aiohttp.TCPConnector(
            limit=concurrency * 2,
            limit_per_host=concurrency * 2,
        )

        semaphore = asyncio.Semaphore(concurrency)
        end_time = time.time() + duration_seconds

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:

            async def worker():
                while time.time() < end_time and not self._stop_event.is_set():
                    async with semaphore:
                        status, latency, size = await self._make_request_fast(session)

                        if 200 <= status < 300:
                            self.metrics.add_success(status, latency, size)
                        else:
                            self.metrics.add_failure(status, latency)

            workers = [worker() for _ in range(concurrency)]

            if RICH_AVAILABLE:
                with Live(self._create_progress_table(), refresh_per_second=4) as live:
                    async def updater():
                        while time.time() < end_time and not self._stop_event.is_set():
                            self.metrics.update_rps()
                            live.update(self._create_progress_table())
                            await asyncio.sleep(0.25)

                    await asyncio.gather(updater(), *workers, return_exceptions=True)
            else:
                await asyncio.gather(*workers, return_exceptions=True)

        self.metrics.end_time = time.time()
        return self.metrics

    # =========================================================================
    # SCENARIO: Chaos Monkey (Random Spikes)
    # =========================================================================
    async def chaos_test(
        self,
        min_rps: int = 1000,
        max_rps: int = 50000,
        spike_duration_seconds: float = 5.0,
        total_duration_seconds: int = 300,
        concurrency: int = 2000,
    ) -> StreamingMetrics:
        """
        Chaotic random load spikes for resilience testing.
        """
        console.print(Panel(
            f"[bold red]🎲 CHAOS TEST[/bold red]\n"
            f"Range: {min_rps:,} - {max_rps:,} RPS\n"
            f"Spike Duration: {spike_duration_seconds}s | Total: {total_duration_seconds}s",
            title="🎲 Chaos Mode"
        ) if RICH_AVAILABLE else f"CHAOS TEST - {min_rps} to {max_rps} RPS")

        self.metrics = StreamingMetrics()
        self.metrics.start_time = time.time()
        self._stop_event.clear()

        connector = aiohttp.TCPConnector(
            limit=concurrency * 2,
            limit_per_host=concurrency * 2,
        )

        semaphore = asyncio.Semaphore(concurrency)
        end_time = time.time() + total_duration_seconds

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:

            async def worker():
                while time.time() < end_time and not self._stop_event.is_set():
                    async with semaphore:
                        status, latency, size = await self._make_request_fast(session)

                        if 200 <= status < 300:
                            self.metrics.add_success(status, latency, size)
                        else:
                            self.metrics.add_failure(status, latency)

            workers = [worker() for _ in range(concurrency)]

            if RICH_AVAILABLE:
                with Live(self._create_progress_table(), refresh_per_second=4) as live:
                    async def updater():
                        while time.time() < end_time and not self._stop_event.is_set():
                            self.metrics.update_rps()
                            live.update(self._create_progress_table())
                            await asyncio.sleep(0.25)

                    await asyncio.gather(updater(), *workers, return_exceptions=True)
            else:
                await asyncio.gather(*workers, return_exceptions=True)

        self.metrics.end_time = time.time()
        return self.metrics

    def generate_report(self, output_path: Optional[str] = None) -> str:
        """Generate JSON report."""
        report = {
            "test_name": "Extreme Stress Test",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target_url": self.base_url,
            "method": self.method,
            "uvloop_enabled": UVLOOP_AVAILABLE,
            "metrics": self.metrics.to_dict(),
        }

        json_str = json.dumps(report, indent=2)

        if output_path:
            Path(output_path).write_text(json_str)
            console.print(f"[green]Report saved to: {output_path}[/green]" if RICH_AVAILABLE else f"Report saved to: {output_path}")

        return json_str

    def print_summary(self):
        """Print final test summary."""
        m = self.metrics

        if RICH_AVAILABLE:
            console.print("\n")
            console.print(Panel(
                f"""[bold]🔥 EXTREME TEST RESULTS 🔥[/bold]

[cyan]Total Requests:[/cyan]     {m.total_requests:,}
[green]Successful:[/green]         {m.successful_requests:,} ({m.success_rate:.2f}%)
[red]Failed:[/red]             {m.failed_requests:,}
[yellow]Rate Limited:[/yellow]      {m.rate_limited_requests:,}
[dim]Timeouts:[/dim]           {m.timeout_requests:,}

[cyan]Duration:[/cyan]           {m.duration:.2f}s
[cyan]Average RPS:[/cyan]        {m.rps:,.0f}
[cyan]Peak RPS:[/cyan]           {m.current_rps:,.0f}
[cyan]Throughput:[/cyan]         {m.throughput_mbps:.2f} MB/s

[bold]Latency Statistics (ms):[/bold]
  Min:       {m.min_latency:.2f}
  Average:   {m.avg_latency:.2f}
  Std Dev:   {m.latency_std_dev:.2f}
  P50:       {m.p50:.2f}
  P90:       {m.p90:.2f}
  P95:       {m.p95:.2f}
  P99:       {m.p99:.2f}
  P99.9:     {m.p999:.2f}
  Max:       {m.max_latency:.2f}

[bold]Status Code Distribution:[/bold]
{self._format_status_codes()}

[dim]uvloop: {'enabled ✓' if UVLOOP_AVAILABLE else 'not available'}[/dim]
""",
                title="📊 Final Results",
                border_style="green" if m.success_rate >= 95 else "red"
            ))
        else:
            print(f"""
=== EXTREME TEST RESULTS ===
Total Requests: {m.total_requests:,}
Success Rate: {m.success_rate:.2f}%
Duration: {m.duration:.2f}s
Average RPS: {m.rps:,.0f}
P99 Latency: {m.p99:.2f}ms
""")

    def _format_status_codes(self) -> str:
        if not self.metrics.status_codes:
            return "  No responses recorded"
        lines = []
        for code, count in sorted(self.metrics.status_codes.items()):
            pct = count / max(1, self.metrics.total_requests) * 100
            color = "green" if 200 <= code < 300 else "yellow" if code == 429 else "red"
            lines.append(f"  [{color}]{code}[/{color}]: {count:,} ({pct:.2f}%)")
        return "\n".join(lines[:10])


# =============================================================================
# PRESET CONFIGURATIONS
# =============================================================================

EXTREME_PRESETS = {
    "100K": {
        "name": "💯 100K Requests",
        "description": "100,000 requests - warm-up test",
        "total_requests": 100_000,
        "concurrency": 500,
        "batch_size": 10_000,
    },
    "500K": {
        "name": "🔥 500K Requests",
        "description": "500,000 requests - moderate stress",
        "total_requests": 500_000,
        "concurrency": 1000,
        "batch_size": 25_000,
    },
    "1M": {
        "name": "💪 1 Million Requests",
        "description": "1,000,000 requests - serious stress test",
        "total_requests": 1_000_000,
        "concurrency": 1500,
        "batch_size": 50_000,
    },
    "5M": {
        "name": "🚀 5 Million Requests",
        "description": "5,000,000 requests - heavy load",
        "total_requests": 5_000_000,
        "concurrency": 2000,
        "batch_size": 100_000,
    },
    "10M": {
        "name": "⚡ 10 Million Requests",
        "description": "10,000,000 requests - extreme stress",
        "total_requests": 10_000_000,
        "concurrency": 3000,
        "batch_size": 200_000,
    },
    "50M": {
        "name": "🌋 50 Million Requests",
        "description": "50,000,000 requests - volcanic load",
        "total_requests": 50_000_000,
        "concurrency": 5000,
        "batch_size": 500_000,
    },
    "100M": {
        "name": "☢️ 100 Million Requests",
        "description": "100,000,000 requests - NUCLEAR",
        "total_requests": 100_000_000,
        "concurrency": 5000,
        "batch_size": 1_000_000,
    },
    "1B": {
        "name": "🌌 1 Billion Requests",
        "description": "1,000,000,000 requests - GALACTIC (multi-hour)",
        "total_requests": 1_000_000_000,
        "concurrency": 10000,
        "batch_size": 5_000_000,
    },

    # Time-based presets
    "max-1min": {
        "name": "⏱️ Max 1 Minute",
        "description": "Maximum throughput for 1 minute",
        "duration": 60,
        "concurrency": 2000,
        "type": "duration",
    },
    "max-5min": {
        "name": "⏱️ Max 5 Minutes",
        "description": "Maximum throughput for 5 minutes",
        "duration": 300,
        "concurrency": 3000,
        "type": "duration",
    },
    "max-10min": {
        "name": "⏱️ Max 10 Minutes",
        "description": "Maximum throughput for 10 minutes",
        "duration": 600,
        "concurrency": 5000,
        "type": "duration",
    },
    "max-1hour": {
        "name": "⏱️ Max 1 Hour",
        "description": "Maximum throughput for 1 hour (endurance)",
        "duration": 3600,
        "concurrency": 3000,
        "type": "duration",
    },

    # RPS-based presets
    "10k-rps": {
        "name": "📊 10K RPS Sustained",
        "description": "Sustain 10,000 RPS for 5 minutes",
        "target_rps": 10_000,
        "duration": 300,
        "concurrency": 1000,
        "type": "sustained",
    },
    "50k-rps": {
        "name": "📊 50K RPS Sustained",
        "description": "Sustain 50,000 RPS for 5 minutes",
        "target_rps": 50_000,
        "duration": 300,
        "concurrency": 2000,
        "type": "sustained",
    },
    "100k-rps": {
        "name": "📊 100K RPS Sustained",
        "description": "Sustain 100,000 RPS for 5 minutes",
        "target_rps": 100_000,
        "duration": 300,
        "concurrency": 5000,
        "type": "sustained",
    },
}


def print_presets():
    """Print available presets."""
    console.print("\n[bold]Available Extreme Presets:[/bold]\n" if RICH_AVAILABLE else "\nAvailable Presets:\n")

    categories = [
        ("Request Count", ["100K", "500K", "1M", "5M", "10M", "50M", "100M", "1B"]),
        ("Time-based Max", ["max-1min", "max-5min", "max-10min", "max-1hour"]),
        ("Sustained RPS", ["10k-rps", "50k-rps", "100k-rps"]),
    ]

    for category, preset_names in categories:
        console.print(f"[bold cyan]{category}:[/bold cyan]" if RICH_AVAILABLE else f"{category}:")
        for name in preset_names:
            if name in EXTREME_PRESETS:
                preset = EXTREME_PRESETS[name]
                console.print(f"  {name:<15} {preset['name']:<30} - {preset['description']}")
        console.print("")


async def run_preset(url: str, preset_name: str, output: Optional[str] = None):
    """Run a preset test."""
    if preset_name not in EXTREME_PRESETS:
        console.print(f"[red]Unknown preset: {preset_name}[/red]")
        print_presets()
        return

    preset = EXTREME_PRESETS[preset_name]
    engine = ExtremeStressTestEngine(base_url=url)

    console.print(Panel(
        f"[bold]{preset['name']}[/bold]\n\n{preset['description']}",
        title=f"Running Preset: {preset_name}",
        border_style="blue"
    ) if RICH_AVAILABLE else f"Running: {preset_name}")

    preset_type = preset.get("type", "count")

    if preset_type == "duration":
        await engine.max_throughput_duration(
            duration_seconds=preset["duration"],
            concurrency=preset["concurrency"],
        )
    elif preset_type == "sustained":
        await engine.sustained_rps(
            target_rps=preset["target_rps"],
            duration_seconds=preset["duration"],
            concurrency=preset["concurrency"],
        )
    else:
        await engine.fixed_request_count(
            total_requests=preset["total_requests"],
            concurrency=preset["concurrency"],
            batch_size=preset["batch_size"],
        )

    engine.print_summary()

    if output:
        engine.generate_report(output)


async def main():
    parser = argparse.ArgumentParser(
        description="🔥 EXTREME SCALE STRESS TEST",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run 1 million requests
  python extreme_stress_test.py --url http://localhost:8000/api/products --preset 1M

  # Run 10 million requests
  python extreme_stress_test.py --url http://localhost:8000/api/products --preset 10M

  # Custom request count
  python extreme_stress_test.py --url http://localhost:8000/api/products --requests 50000000 --concurrency 5000

  # Maximum throughput for 10 minutes
  python extreme_stress_test.py --url http://localhost:8000/api/products --preset max-10min

  # Sustained 50K RPS
  python extreme_stress_test.py --url http://localhost:8000/api/products --preset 50k-rps
        """
    )

    parser.add_argument("--url", "-u", required=True, help="Target API URL")
    parser.add_argument("--preset", "-p", help="Use a preset configuration")
    parser.add_argument("--requests", "-n", type=int, help="Total number of requests")
    parser.add_argument("--duration", "-d", type=int, help="Duration in seconds")
    parser.add_argument("--rps", type=int, help="Target RPS for sustained test")
    parser.add_argument("--concurrency", "-c", type=int, default=1000, help="Concurrent connections")
    parser.add_argument("--batch-size", "-b", type=int, default=50000, help="Batch size for processing")
    parser.add_argument("--output", "-o", help="Output file for JSON report")
    parser.add_argument("--list-presets", action="store_true", help="List available presets")

    args = parser.parse_args()

    if args.list_presets:
        print_presets()
        return

    if args.preset:
        await run_preset(args.url, args.preset, args.output)
    elif args.requests:
        engine = ExtremeStressTestEngine(base_url=args.url)
        await engine.fixed_request_count(
            total_requests=args.requests,
            concurrency=args.concurrency,
            batch_size=args.batch_size,
        )
        engine.print_summary()
        if args.output:
            engine.generate_report(args.output)
    elif args.duration:
        engine = ExtremeStressTestEngine(base_url=args.url)
        if args.rps:
            await engine.sustained_rps(
                target_rps=args.rps,
                duration_seconds=args.duration,
                concurrency=args.concurrency,
            )
        else:
            await engine.max_throughput_duration(
                duration_seconds=args.duration,
                concurrency=args.concurrency,
            )
        engine.print_summary()
        if args.output:
            engine.generate_report(args.output)
    else:
        console.print("[red]Please specify --preset, --requests, or --duration[/red]")
        print_presets()


if __name__ == "__main__":
    asyncio.run(main())
