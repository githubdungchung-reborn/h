#!/usr/bin/env python3
"""
🎨 Fancy Traffic Patterns
=========================
Creative and realistic traffic patterns for stress testing.

Usage:
    python fancy_patterns.py http://localhost:8000/api/products sine
    python fancy_patterns.py http://localhost:8000/api/products sawtooth
    python fancy_patterns.py http://localhost:8000/api/products random-walk
"""

import asyncio
import aiohttp
import math
import random
import sys
import time
from dataclasses import dataclass, field
from collections import defaultdict
from typing import Generator
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn

console = Console()


@dataclass
class Metrics:
    total: int = 0
    success: int = 0
    rate_limited: int = 0
    errors: int = 0
    latencies: list = field(default_factory=list)
    start_time: float = 0

    @property
    def rps(self) -> float:
        elapsed = time.time() - self.start_time if self.start_time else 1
        return self.total / elapsed


class TrafficPatternEngine:
    def __init__(self, url: str, timeout: float = 30.0):
        self.url = url
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.metrics = Metrics()

    def _create_display(self, pattern_name: str, current_rps: int, phase: str = "") -> Table:
        table = Table(title=f"🎨 {pattern_name}", expand=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        m = self.metrics
        success_rate = (m.success / m.total * 100) if m.total > 0 else 0
        avg_latency = sum(m.latencies[-100:]) / len(m.latencies[-100:]) if m.latencies else 0

        table.add_row("Current Target RPS", f"{current_rps:,}")
        table.add_row("Actual RPS", f"{m.rps:,.0f}")
        table.add_row("Total Requests", f"{m.total:,}")
        table.add_row("Success Rate", f"{success_rate:.1f}%")
        table.add_row("Rate Limited (429)", f"[{'red' if m.rate_limited > 0 else 'green'}]{m.rate_limited:,}[/]")
        table.add_row("Avg Latency (last 100)", f"{avg_latency:.1f}ms")
        if phase:
            table.add_row("Phase", f"[yellow]{phase}[/yellow]")

        return table

    async def _send_batch(self, session: aiohttp.ClientSession, count: int, semaphore: asyncio.Semaphore):
        """Send a batch of requests."""
        async def single_request():
            async with semaphore:
                start = time.perf_counter()
                try:
                    async with session.get(self.url) as resp:
                        await resp.read()
                        latency = (time.perf_counter() - start) * 1000
                        self.metrics.total += 1
                        self.metrics.latencies.append(latency)
                        if resp.status == 429:
                            self.metrics.rate_limited += 1
                        elif 200 <= resp.status < 300:
                            self.metrics.success += 1
                        else:
                            self.metrics.errors += 1
                except Exception:
                    self.metrics.total += 1
                    self.metrics.errors += 1

        await asyncio.gather(*[single_request() for _ in range(count)])

    # =========================================================================
    # PATTERN: Sine Wave 🌊
    # =========================================================================
    async def sine_wave(
        self,
        base_rps: int = 500,
        amplitude: int = 400,
        period_seconds: float = 30.0,
        duration_seconds: int = 120,
        concurrency: int = 300,
    ):
        """
        Sine wave traffic pattern - smooth oscillation.
        Good for: Testing auto-scaling responsiveness.
        """
        console.print(Panel(
            f"[bold blue]🌊 Sine Wave Pattern[/bold blue]\n"
            f"Base: {base_rps} RPS | Amplitude: ±{amplitude} | Period: {period_seconds}s",
            title="Starting Pattern"
        ))

        self.metrics = Metrics()
        self.metrics.start_time = time.time()
        end_time = time.time() + duration_seconds

        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            semaphore = asyncio.Semaphore(concurrency)

            with Live(self._create_display("Sine Wave", base_rps), refresh_per_second=4) as live:
                while time.time() < end_time:
                    elapsed = time.time() - self.metrics.start_time
                    # Sine function: RPS oscillates between (base - amplitude) and (base + amplitude)
                    current_rps = int(base_rps + amplitude * math.sin(2 * math.pi * elapsed / period_seconds))
                    current_rps = max(10, current_rps)  # Minimum 10 RPS

                    # Send requests for this second
                    await self._send_batch(session, current_rps, semaphore)
                    live.update(self._create_display("Sine Wave 🌊", current_rps))

        self._print_summary("Sine Wave")

    # =========================================================================
    # PATTERN: Sawtooth ⚡
    # =========================================================================
    async def sawtooth(
        self,
        min_rps: int = 100,
        max_rps: int = 2000,
        ramp_up_seconds: float = 20.0,
        drop_seconds: float = 2.0,
        cycles: int = 5,
        concurrency: int = 300,
    ):
        """
        Sawtooth pattern - gradual increase then sudden drop.
        Good for: Testing recovery after load drops.
        """
        console.print(Panel(
            f"[bold yellow]⚡ Sawtooth Pattern[/bold yellow]\n"
            f"Range: {min_rps} → {max_rps} RPS | Cycles: {cycles}",
            title="Starting Pattern"
        ))

        self.metrics = Metrics()
        self.metrics.start_time = time.time()

        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            semaphore = asyncio.Semaphore(concurrency)

            with Live(self._create_display("Sawtooth", min_rps), refresh_per_second=4) as live:
                for cycle in range(cycles):
                    # Ramp up phase
                    ramp_start = time.time()
                    while time.time() - ramp_start < ramp_up_seconds:
                        progress = (time.time() - ramp_start) / ramp_up_seconds
                        current_rps = int(min_rps + (max_rps - min_rps) * progress)
                        await self._send_batch(session, current_rps, semaphore)
                        live.update(self._create_display("Sawtooth ⚡", current_rps, f"Cycle {cycle+1}/{cycles} - Ramping"))

                    # Sudden drop
                    console.print(f"[yellow]💥 DROP! Cycle {cycle+1}[/yellow]")
                    drop_start = time.time()
                    while time.time() - drop_start < drop_seconds:
                        await self._send_batch(session, min_rps, semaphore)
                        live.update(self._create_display("Sawtooth ⚡", min_rps, f"Cycle {cycle+1}/{cycles} - Recovery"))

        self._print_summary("Sawtooth")

    # =========================================================================
    # PATTERN: Square Wave ⬛
    # =========================================================================
    async def square_wave(
        self,
        low_rps: int = 100,
        high_rps: int = 2000,
        period_seconds: float = 20.0,
        duration_seconds: int = 120,
        concurrency: int = 300,
    ):
        """
        Square wave - alternating between high and low.
        Good for: Testing sudden load changes.
        """
        console.print(Panel(
            f"[bold magenta]⬛ Square Wave Pattern[/bold magenta]\n"
            f"Low: {low_rps} RPS | High: {high_rps} RPS | Period: {period_seconds}s",
            title="Starting Pattern"
        ))

        self.metrics = Metrics()
        self.metrics.start_time = time.time()
        end_time = time.time() + duration_seconds
        half_period = period_seconds / 2

        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            semaphore = asyncio.Semaphore(concurrency)

            with Live(self._create_display("Square Wave", low_rps), refresh_per_second=4) as live:
                phase_start = time.time()
                is_high = False

                while time.time() < end_time:
                    if time.time() - phase_start >= half_period:
                        is_high = not is_high
                        phase_start = time.time()
                        console.print(f"[magenta]{'📈 HIGH' if is_high else '📉 LOW'}[/magenta]")

                    current_rps = high_rps if is_high else low_rps
                    await self._send_batch(session, current_rps, semaphore)
                    live.update(self._create_display("Square Wave ⬛", current_rps, "HIGH" if is_high else "LOW"))

        self._print_summary("Square Wave")

    # =========================================================================
    # PATTERN: Random Walk 🎲
    # =========================================================================
    async def random_walk(
        self,
        start_rps: int = 500,
        min_rps: int = 50,
        max_rps: int = 5000,
        max_step: int = 200,
        duration_seconds: int = 120,
        concurrency: int = 300,
    ):
        """
        Random walk - RPS changes randomly each second.
        Good for: Chaos testing, unpredictable load simulation.
        """
        console.print(Panel(
            f"[bold red]🎲 Random Walk Pattern[/bold red]\n"
            f"Range: {min_rps} - {max_rps} RPS | Max Step: ±{max_step}",
            title="Starting Pattern"
        ))

        self.metrics = Metrics()
        self.metrics.start_time = time.time()
        end_time = time.time() + duration_seconds
        current_rps = start_rps

        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            semaphore = asyncio.Semaphore(concurrency)

            with Live(self._create_display("Random Walk", current_rps), refresh_per_second=4) as live:
                while time.time() < end_time:
                    # Random step
                    step = random.randint(-max_step, max_step)
                    current_rps = max(min_rps, min(max_rps, current_rps + step))

                    await self._send_batch(session, current_rps, semaphore)
                    live.update(self._create_display("Random Walk 🎲", current_rps))

        self._print_summary("Random Walk")

    # =========================================================================
    # PATTERN: Pulse 💓
    # =========================================================================
    async def pulse(
        self,
        base_rps: int = 100,
        pulse_rps: int = 3000,
        pulse_duration: float = 2.0,
        rest_duration: float = 8.0,
        pulses: int = 10,
        concurrency: int = 300,
    ):
        """
        Pulse pattern - periodic short bursts.
        Good for: Simulating batch jobs, scheduled tasks.
        """
        console.print(Panel(
            f"[bold green]💓 Pulse Pattern[/bold green]\n"
            f"Base: {base_rps} RPS | Pulse: {pulse_rps} RPS for {pulse_duration}s | Rest: {rest_duration}s",
            title="Starting Pattern"
        ))

        self.metrics = Metrics()
        self.metrics.start_time = time.time()

        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            semaphore = asyncio.Semaphore(concurrency)

            with Live(self._create_display("Pulse", base_rps), refresh_per_second=4) as live:
                for pulse_num in range(pulses):
                    # Rest phase
                    console.print(f"[dim]💤 Resting... ({pulse_num+1}/{pulses})[/dim]")
                    rest_start = time.time()
                    while time.time() - rest_start < rest_duration:
                        await self._send_batch(session, base_rps, semaphore)
                        live.update(self._create_display("Pulse 💓", base_rps, f"Rest {pulse_num+1}/{pulses}"))

                    # Pulse!
                    console.print(f"[green]💓 PULSE![/green]")
                    pulse_start = time.time()
                    while time.time() - pulse_start < pulse_duration:
                        await self._send_batch(session, pulse_rps, semaphore)
                        live.update(self._create_display("Pulse 💓", pulse_rps, f"PULSE {pulse_num+1}/{pulses}"))

        self._print_summary("Pulse")

    # =========================================================================
    # PATTERN: Exponential Growth 📈
    # =========================================================================
    async def exponential_growth(
        self,
        start_rps: int = 10,
        max_rps: int = 50000,
        growth_rate: float = 1.5,
        step_seconds: float = 5.0,
        concurrency: int = 500,
    ):
        """
        Exponential growth - RPS multiplies each step.
        Good for: Finding breaking points quickly.
        """
        console.print(Panel(
            f"[bold cyan]📈 Exponential Growth[/bold cyan]\n"
            f"Start: {start_rps} RPS | Max: {max_rps} RPS | Growth: {growth_rate}x every {step_seconds}s",
            title="Starting Pattern"
        ))

        self.metrics = Metrics()
        self.metrics.start_time = time.time()
        current_rps = start_rps

        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            semaphore = asyncio.Semaphore(concurrency)

            with Live(self._create_display("Exponential Growth", current_rps), refresh_per_second=4) as live:
                while current_rps <= max_rps:
                    console.print(f"[cyan]📊 Level: {int(current_rps):,} RPS[/cyan]")

                    step_start = time.time()
                    while time.time() - step_start < step_seconds:
                        await self._send_batch(session, int(current_rps), semaphore)
                        live.update(self._create_display("Exponential Growth 📈", int(current_rps)))

                    # Check if we're hitting rate limits
                    if self.metrics.rate_limited > self.metrics.total * 0.1:
                        console.print(f"[yellow]⚠️ Hit rate limit threshold at {int(current_rps):,} RPS[/yellow]")
                        break

                    current_rps *= growth_rate

        self._print_summary("Exponential Growth")

    # =========================================================================
    # PATTERN: Concert Traffic 🎸
    # =========================================================================
    async def concert_traffic(
        self,
        pre_show_rps: int = 100,
        doors_open_rps: int = 2000,
        show_start_rps: int = 5000,
        encore_rps: int = 8000,
        post_show_rps: int = 500,
        phase_duration: float = 15.0,
        concurrency: int = 500,
    ):
        """
        Simulate concert ticket sales pattern.
        Good for: E-commerce flash sale simulation.
        """
        phases = [
            ("🎫 Pre-show (normal browsing)", pre_show_rps),
            ("🚪 Doors Open! (initial rush)", doors_open_rps),
            ("🎸 Show Starts! (peak load)", show_start_rps),
            ("🔥 ENCORE! (maximum hype)", encore_rps),
            ("👋 Post-show (wind down)", post_show_rps),
        ]

        console.print(Panel(
            f"[bold yellow]🎸 Concert Traffic Pattern[/bold yellow]\n"
            f"Simulating ticket sale/event pattern\n"
            f"Peak: {encore_rps:,} RPS",
            title="Starting Pattern"
        ))

        self.metrics = Metrics()
        self.metrics.start_time = time.time()

        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            semaphore = asyncio.Semaphore(concurrency)

            with Live(self._create_display("Concert Traffic", pre_show_rps), refresh_per_second=4) as live:
                for phase_name, target_rps in phases:
                    console.print(f"\n[yellow]{phase_name}[/yellow]")
                    phase_start = time.time()

                    while time.time() - phase_start < phase_duration:
                        await self._send_batch(session, target_rps, semaphore)
                        live.update(self._create_display("Concert Traffic 🎸", target_rps, phase_name))

        self._print_summary("Concert Traffic")

    def _print_summary(self, pattern_name: str):
        """Print test summary."""
        m = self.metrics
        duration = time.time() - m.start_time
        success_rate = (m.success / m.total * 100) if m.total > 0 else 0
        avg_latency = sum(m.latencies) / len(m.latencies) if m.latencies else 0

        console.print(Panel(
            f"""[bold]{pattern_name} Complete[/bold]

Total Requests:     {m.total:,}
Duration:           {duration:.1f}s
Average RPS:        {m.total / duration:,.0f}
Success Rate:       {success_rate:.1f}%
Rate Limited (429): {m.rate_limited:,}
Errors:             {m.errors:,}
Avg Latency:        {avg_latency:.1f}ms
""",
            title="📊 Results",
            border_style="green"
        ))


PATTERNS = {
    "sine": ("sine_wave", {}),
    "sawtooth": ("sawtooth", {}),
    "square": ("square_wave", {}),
    "random-walk": ("random_walk", {}),
    "pulse": ("pulse", {}),
    "exponential": ("exponential_growth", {}),
    "concert": ("concert_traffic", {}),
}


def print_patterns():
    console.print("\n[bold]Available Patterns:[/bold]\n")
    patterns_info = [
        ("sine", "🌊 Sine Wave", "Smooth oscillating traffic"),
        ("sawtooth", "⚡ Sawtooth", "Gradual increase, sudden drop"),
        ("square", "⬛ Square Wave", "Alternating high/low"),
        ("random-walk", "🎲 Random Walk", "Unpredictable changes"),
        ("pulse", "💓 Pulse", "Periodic short bursts"),
        ("exponential", "📈 Exponential", "Multiplying growth"),
        ("concert", "🎸 Concert", "Event traffic simulation"),
    ]
    for name, title, desc in patterns_info:
        console.print(f"  {title:<25} ({name}) - {desc}")


async def main():
    if len(sys.argv) < 3:
        console.print("[bold]Usage:[/bold] python fancy_patterns.py <URL> <PATTERN>")
        print_patterns()
        return

    url = sys.argv[1]
    pattern = sys.argv[2].lower()

    if pattern not in PATTERNS:
        console.print(f"[red]Unknown pattern: {pattern}[/red]")
        print_patterns()
        return

    engine = TrafficPatternEngine(url)
    method_name, kwargs = PATTERNS[pattern]
    await getattr(engine, method_name)(**kwargs)


if __name__ == "__main__":
    asyncio.run(main())
