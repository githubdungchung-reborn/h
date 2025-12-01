#!/usr/bin/env python3
"""
🎯 Preset Stress Test Scenarios
================================
Pre-configured scenarios from gentle warmup to NUCLEAR mode.

Usage:
    python run_presets.py http://localhost:8000/api/products gentle
    python run_presets.py http://localhost:8000/api/products tsunami
    python run_presets.py http://localhost:8000/api/products nuclear --i-know-what-im-doing
"""

import asyncio
import sys

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    class MockConsole:
        def print(self, *args, **kwargs):
            import re
            text = str(args[0]) if args else ""
            clean = re.sub(r'\[.*?\]', '', text)
            print(clean)
    console = MockConsole()

from stress_test import StressTestEngine, ReportFormat

# =============================================================================
# PRESET CONFIGURATIONS
# =============================================================================

PRESETS = {
    # -------------------------------------------------------------------------
    # BASIC PRESETS
    # -------------------------------------------------------------------------
    "gentle": {
        "name": "🌱 Gentle Warmup",
        "description": "Light load to verify API is responding",
        "scenario": "basic",
        "params": {
            "total_requests": 100,
            "concurrency": 10,
        }
    },
    "moderate": {
        "name": "🏃 Moderate Load",
        "description": "Normal production-like traffic",
        "scenario": "basic",
        "params": {
            "total_requests": 5000,
            "concurrency": 50,
        }
    },
    "heavy": {
        "name": "🏋️ Heavy Load",
        "description": "High traffic simulation",
        "scenario": "basic",
        "params": {
            "total_requests": 50000,
            "concurrency": 200,
        }
    },

    # -------------------------------------------------------------------------
    # SUSTAINED PRESETS
    # -------------------------------------------------------------------------
    "endurance": {
        "name": "🏃‍♂️ Endurance Test",
        "description": "5 minute sustained load at 500 RPS",
        "scenario": "sustained",
        "params": {
            "duration_seconds": 300,
            "target_rps": 500,
            "concurrency": 200,
        }
    },
    "marathon": {
        "name": "🏃‍♀️ Marathon",
        "description": "30 minute endurance at 1000 RPS",
        "scenario": "sustained",
        "params": {
            "duration_seconds": 1800,
            "target_rps": 1000,
            "concurrency": 300,
        }
    },

    # -------------------------------------------------------------------------
    # SPIKE/BURST PRESETS
    # -------------------------------------------------------------------------
    "spike": {
        "name": "📈 Traffic Spike",
        "description": "Simulate sudden traffic bursts",
        "scenario": "burst",
        "params": {
            "burst_size": 5000,
            "burst_count": 3,
            "cooldown_seconds": 5.0,
            "concurrency": 300,
        }
    },
    "heartbeat": {
        "name": "💓 Heartbeat Pattern",
        "description": "Regular pulsing traffic (like cron jobs)",
        "scenario": "burst",
        "params": {
            "burst_size": 1000,
            "burst_count": 10,
            "cooldown_seconds": 3.0,
            "concurrency": 200,
        }
    },

    # -------------------------------------------------------------------------
    # RAMP-UP PRESETS
    # -------------------------------------------------------------------------
    "gradual": {
        "name": "📊 Gradual Ramp",
        "description": "Slowly increase load 100→5000 RPS",
        "scenario": "ramp",
        "params": {
            "start_rps": 100,
            "end_rps": 5000,
            "ramp_duration_seconds": 60,
            "step_duration_seconds": 10,
            "concurrency": 300,
        }
    },
    "stress-test": {
        "name": "💪 Stress Test",
        "description": "Find breaking point: 100→20000 RPS",
        "scenario": "ramp",
        "params": {
            "start_rps": 100,
            "end_rps": 20000,
            "ramp_duration_seconds": 120,
            "step_duration_seconds": 10,
            "concurrency": 500,
        }
    },

    # -------------------------------------------------------------------------
    # FUN/FANCY PRESETS
    # -------------------------------------------------------------------------
    "tsunami": {
        "name": "🌊 Tsunami Wave",
        "description": "Massive wave of requests (100K burst)",
        "scenario": "burst",
        "params": {
            "burst_size": 100000,
            "burst_count": 1,
            "cooldown_seconds": 0,
            "concurrency": 1000,
        }
    },
    "rate-limit-finder": {
        "name": "🔍 Rate Limit Finder",
        "description": "Automatically discover rate limits",
        "scenario": "discover",
        "params": {
            "start_rps": 10,
            "max_rps": 50000,
            "step_multiplier": 1.5,
            "step_duration": 10,
            "threshold_429_percent": 5.0,
        }
    },

    # -------------------------------------------------------------------------
    # EXTREME PRESETS (USE WITH CAUTION!)
    # -------------------------------------------------------------------------
    "nuclear": {
        "name": "☢️ NUCLEAR",
        "description": "Maximum destruction: 2000 concurrent for 60s",
        "scenario": "max",
        "params": {
            "duration_seconds": 60,
            "concurrency": 2000,
        },
        "dangerous": True,
    },
    "10m-per-min": {
        "name": "🚀 10M/min Target",
        "description": "Attempt 166,666 RPS for 60s (10M requests)",
        "scenario": "sustained",
        "params": {
            "duration_seconds": 60,
            "target_rps": 166666,
            "concurrency": 5000,
        },
        "dangerous": True,
    },
}


def print_presets():
    """Print all available presets."""
    console.print("\n[bold]Available Presets:[/bold]\n" if RICH_AVAILABLE else "\nAvailable Presets:\n")

    categories = [
        ("Basic", ["gentle", "moderate", "heavy"]),
        ("Sustained", ["endurance", "marathon"]),
        ("Spike/Burst", ["spike", "heartbeat"]),
        ("Ramp-Up", ["gradual", "stress-test"]),
        ("Fun/Fancy", ["tsunami", "rate-limit-finder"]),
        ("☢️ EXTREME", ["nuclear", "10m-per-min"]),
    ]

    for category, preset_names in categories:
        console.print(f"[bold cyan]{category}:[/bold cyan]" if RICH_AVAILABLE else f"{category}:")
        for name in preset_names:
            if name in PRESETS:
                preset = PRESETS[name]
                danger_flag = "[red]⚠️ DANGEROUS[/red] " if preset.get("dangerous") else ""
                if RICH_AVAILABLE:
                    console.print(f"  {preset['name']:<25} {danger_flag}- {preset['description']}")
                else:
                    console.print(f"  {name:<15} - {preset['description']}")
        console.print("")


async def run_preset(url: str, preset_name: str, dangerous_confirmed: bool = False, report_format: str = "console"):
    """Run a preset scenario."""
    if preset_name not in PRESETS:
        console.print(f"[red]Unknown preset: {preset_name}[/red]" if RICH_AVAILABLE else f"Unknown preset: {preset_name}")
        print_presets()
        return

    preset = PRESETS[preset_name]

    # Safety check for dangerous presets
    if preset.get("dangerous") and not dangerous_confirmed:
        if RICH_AVAILABLE:
            console.print(Panel(
                f"[bold red]⚠️  WARNING: {preset['name']} is DANGEROUS![/bold red]\n\n"
                f"{preset['description']}\n\n"
                f"This can overwhelm servers and potentially cause:\n"
                f"  • Service outages\n"
                f"  • Rate limiting/IP bans\n"
                f"  • Resource exhaustion\n\n"
                f"[yellow]Only use on systems you own or have permission to test![/yellow]",
                title="⚠️ Dangerous Preset",
                border_style="red"
            ))
            if not Confirm.ask("Do you want to proceed?"):
                console.print("[dim]Cancelled.[/dim]")
                return
        else:
            print(f"WARNING: {preset['name']} is DANGEROUS!")
            response = input("Do you want to proceed? (y/n): ")
            if response.lower() != 'y':
                print("Cancelled.")
                return

    if RICH_AVAILABLE:
        console.print(Panel(
            f"[bold]{preset['name']}[/bold]\n\n{preset['description']}",
            title=f"Running Preset: {preset_name}",
            border_style="blue"
        ))
    else:
        print(f"\nRunning Preset: {preset_name}")
        print(f"{preset['name']} - {preset['description']}\n")

    engine = StressTestEngine(base_url=url)
    params = preset["params"]
    scenario = preset["scenario"]

    if scenario == "basic":
        await engine.basic_load(**params)
    elif scenario == "sustained":
        await engine.sustained_load(**params)
    elif scenario == "burst":
        await engine.burst_test(**params)
    elif scenario == "ramp":
        await engine.ramp_up_test(**params)
    elif scenario == "max":
        await engine.max_throughput_test(**params)
    elif scenario == "discover":
        await engine.rate_limit_discovery(**params)
        return

    # Generate report
    engine.generate_report(
        format=ReportFormat(report_format),
        test_name=f"{preset['name']} - {preset_name}"
    )


def main():
    if len(sys.argv) < 2:
        console.print("[bold]Usage:[/bold] python run_presets.py <URL> [PRESET] [--i-know-what-im-doing] [--report json|html|markdown]")
        print_presets()
        return

    if len(sys.argv) == 2:
        if sys.argv[1] in ["--help", "-h", "help"]:
            print_presets()
            return
        console.print("[red]Please provide both URL and preset name[/red]" if RICH_AVAILABLE else "Please provide both URL and preset name")
        print_presets()
        return

    url = sys.argv[1]
    preset = sys.argv[2]
    dangerous_confirmed = "--i-know-what-im-doing" in sys.argv

    # Parse report format
    report_format = "console"
    for i, arg in enumerate(sys.argv):
        if arg == "--report" and i + 1 < len(sys.argv):
            report_format = sys.argv[i + 1]

    asyncio.run(run_preset(url, preset, dangerous_confirmed, report_format))


if __name__ == "__main__":
    main()
