#!/usr/bin/env python3
"""
🛒 E-Commerce Comprehensive Stress Test Suite
==============================================
Advanced stress testing for e-commerce platforms with realistic user journeys,
flash sale simulations, inventory race conditions, and Black Friday scenarios.

Features:
- User journey simulation (browse → search → cart → checkout)
- Mixed workload patterns (read-heavy vs write-heavy)
- Flash sale / Black Friday spike testing
- Inventory race condition detection
- Session management with tokens/cookies
- Response validation with custom assertions
- Payment gateway stress testing
- Geographic distribution simulation
- Think time for realistic user behavior
- Funnel conversion tracking

Based on best practices from:
- https://octoperf.com/use-cases/black-friday-load-testing/
- https://loadfocus.com/templates/concurrency-testing-ecommerce-cart-checkout
- https://shopify.engineering/scale-performance-testing

Requirements:
    pip install aiohttp rich faker

Usage:
    python ecommerce_stress_test.py --config config.json
    python ecommerce_stress_test.py --base-url http://localhost:8000 --scenario black-friday
"""

import asyncio
import aiohttp
import argparse
import json
import random
import time
import uuid
import hashlib
import statistics
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, Tuple
from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import re

try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

try:
    from faker import Faker
    fake = Faker()
    FAKER_AVAILABLE = True
except ImportError:
    FAKER_AVAILABLE = False
    fake = None

if RICH_AVAILABLE:
    console = Console()
else:
    class MockConsole:
        def print(self, *args, **kwargs):
            text = str(args[0]) if args else ""
            clean = re.sub(r'\[.*?\]', '', text)
            print(clean)
    console = MockConsole()


# =============================================================================
# ENUMS AND CONSTANTS
# =============================================================================

class UserAction(Enum):
    """E-commerce user actions."""
    HOMEPAGE = "homepage"
    BROWSE_CATEGORY = "browse_category"
    SEARCH = "search"
    VIEW_PRODUCT = "view_product"
    ADD_TO_CART = "add_to_cart"
    UPDATE_CART = "update_cart"
    REMOVE_FROM_CART = "remove_from_cart"
    VIEW_CART = "view_cart"
    APPLY_COUPON = "apply_coupon"
    CHECKOUT_START = "checkout_start"
    CHECKOUT_SHIPPING = "checkout_shipping"
    CHECKOUT_PAYMENT = "checkout_payment"
    PLACE_ORDER = "place_order"
    ORDER_CONFIRMATION = "order_confirmation"
    LOGIN = "login"
    LOGOUT = "logout"
    REGISTER = "register"


class UserType(Enum):
    """Types of e-commerce users."""
    BROWSER = "browser"          # Just browsing, no purchase (77%)
    SEARCHER = "searcher"        # Searches but may not buy (15%)
    BUYER = "buyer"              # Completes purchase (5%)
    POWER_BUYER = "power_buyer"  # Multiple purchases (2%)
    BOT = "bot"                  # Automated traffic (1%)


# Typical e-commerce traffic distribution
USER_TYPE_DISTRIBUTION = {
    UserType.BROWSER: 0.77,
    UserType.SEARCHER: 0.15,
    UserType.BUYER: 0.05,
    UserType.POWER_BUYER: 0.02,
    UserType.BOT: 0.01,
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class EndpointConfig:
    """Configuration for an API endpoint."""
    path: str
    method: str = "GET"
    requires_auth: bool = False
    payload_generator: Optional[Callable] = None
    response_validator: Optional[Callable] = None
    expected_status: List[int] = field(default_factory=lambda: [200, 201])
    timeout: float = 10.0
    weight: float = 1.0  # Relative frequency
    think_time_range: Tuple[float, float] = (0.5, 2.0)  # Seconds


@dataclass
class UserSession:
    """Simulated user session."""
    session_id: str
    user_type: UserType
    auth_token: Optional[str] = None
    cart_id: Optional[str] = None
    cart_items: List[str] = field(default_factory=list)
    viewed_products: List[str] = field(default_factory=list)
    search_queries: List[str] = field(default_factory=list)
    actions_performed: List[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    cookies: Dict[str, str] = field(default_factory=dict)


@dataclass
class ActionResult:
    """Result of a user action."""
    action: UserAction
    status_code: int
    latency_ms: float
    success: bool
    response_valid: bool = True
    error: Optional[str] = None
    response_data: Optional[Dict] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class EcommerceMetrics:
    """Comprehensive e-commerce metrics."""
    # Request counts
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0

    # Action-specific metrics
    action_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    action_latencies: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(list))
    action_errors: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Funnel metrics
    homepage_visits: int = 0
    product_views: int = 0
    add_to_cart_count: int = 0
    checkout_starts: int = 0
    orders_placed: int = 0
    orders_completed: int = 0

    # Session metrics
    total_sessions: int = 0
    completed_sessions: int = 0
    abandoned_carts: int = 0

    # Inventory metrics
    inventory_conflicts: int = 0
    out_of_stock_errors: int = 0

    # Payment metrics
    payment_attempts: int = 0
    payment_successes: int = 0
    payment_failures: int = 0

    # Response validation
    validation_failures: int = 0
    response_errors: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Timing
    start_time: float = 0
    end_time: float = 0

    # Latencies
    all_latencies: List[float] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time if self.end_time else time.time() - self.start_time

    @property
    def rps(self) -> float:
        return self.total_requests / self.duration if self.duration > 0 else 0

    @property
    def conversion_rate(self) -> float:
        """Cart to order conversion rate."""
        if self.add_to_cart_count == 0:
            return 0
        return (self.orders_completed / self.add_to_cart_count) * 100

    @property
    def cart_abandonment_rate(self) -> float:
        """Cart abandonment rate."""
        if self.add_to_cart_count == 0:
            return 0
        return ((self.add_to_cart_count - self.orders_completed) / self.add_to_cart_count) * 100

    @property
    def checkout_completion_rate(self) -> float:
        """Checkout completion rate."""
        if self.checkout_starts == 0:
            return 0
        return (self.orders_completed / self.checkout_starts) * 100

    @property
    def payment_success_rate(self) -> float:
        """Payment success rate."""
        if self.payment_attempts == 0:
            return 0
        return (self.payment_successes / self.payment_attempts) * 100

    @property
    def avg_latency(self) -> float:
        return statistics.mean(self.all_latencies) if self.all_latencies else 0

    @property
    def p50_latency(self) -> float:
        return statistics.median(self.all_latencies) if self.all_latencies else 0

    @property
    def p95_latency(self) -> float:
        if not self.all_latencies:
            return 0
        sorted_lat = sorted(self.all_latencies)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def p99_latency(self) -> float:
        if not self.all_latencies:
            return 0
        sorted_lat = sorted(self.all_latencies)
        idx = int(len(sorted_lat) * 0.99)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    def get_action_avg_latency(self, action: str) -> float:
        latencies = self.action_latencies.get(action, [])
        return statistics.mean(latencies) if latencies else 0

    def add_result(self, result: ActionResult):
        """Add an action result to metrics."""
        self.total_requests += 1
        self.action_counts[result.action.value] += 1

        if result.success:
            self.successful_requests += 1
            self.all_latencies.append(result.latency_ms)
            self.action_latencies[result.action.value].append(result.latency_ms)
        else:
            self.failed_requests += 1
            self.action_errors[result.action.value] += 1

        if not result.response_valid:
            self.validation_failures += 1

        # Update funnel metrics
        if result.action == UserAction.HOMEPAGE:
            self.homepage_visits += 1
        elif result.action == UserAction.VIEW_PRODUCT:
            self.product_views += 1
        elif result.action == UserAction.ADD_TO_CART:
            if result.success:
                self.add_to_cart_count += 1
        elif result.action == UserAction.CHECKOUT_START:
            if result.success:
                self.checkout_starts += 1
        elif result.action == UserAction.CHECKOUT_PAYMENT:
            self.payment_attempts += 1
            if result.success:
                self.payment_successes += 1
            else:
                self.payment_failures += 1
        elif result.action == UserAction.PLACE_ORDER:
            if result.success:
                self.orders_placed += 1
        elif result.action == UserAction.ORDER_CONFIRMATION:
            if result.success:
                self.orders_completed += 1

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON export."""
        return {
            "summary": {
                "total_requests": self.total_requests,
                "successful_requests": self.successful_requests,
                "failed_requests": self.failed_requests,
                "duration_seconds": round(self.duration, 2),
                "requests_per_second": round(self.rps, 2),
            },
            "funnel": {
                "homepage_visits": self.homepage_visits,
                "product_views": self.product_views,
                "add_to_cart": self.add_to_cart_count,
                "checkout_starts": self.checkout_starts,
                "orders_placed": self.orders_placed,
                "orders_completed": self.orders_completed,
                "conversion_rate_percent": round(self.conversion_rate, 2),
                "cart_abandonment_rate_percent": round(self.cart_abandonment_rate, 2),
                "checkout_completion_rate_percent": round(self.checkout_completion_rate, 2),
            },
            "payment": {
                "attempts": self.payment_attempts,
                "successes": self.payment_successes,
                "failures": self.payment_failures,
                "success_rate_percent": round(self.payment_success_rate, 2),
            },
            "inventory": {
                "conflicts": self.inventory_conflicts,
                "out_of_stock_errors": self.out_of_stock_errors,
            },
            "latency_ms": {
                "average": round(self.avg_latency, 2),
                "p50": round(self.p50_latency, 2),
                "p95": round(self.p95_latency, 2),
                "p99": round(self.p99_latency, 2),
            },
            "latency_by_action": {
                action: round(self.get_action_avg_latency(action), 2)
                for action in self.action_counts.keys()
            },
            "action_counts": dict(self.action_counts),
            "action_errors": dict(self.action_errors),
            "validation_failures": self.validation_failures,
        }


# =============================================================================
# E-COMMERCE STRESS TEST ENGINE
# =============================================================================

class EcommerceStressTestEngine:
    """
    Comprehensive e-commerce stress testing engine.
    """

    def __init__(
        self,
        base_url: str,
        endpoints: Optional[Dict[str, EndpointConfig]] = None,
        default_headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
        verify_ssl: bool = True,
    ):
        self.base_url = base_url.rstrip('/')
        self.endpoints = endpoints or self._default_endpoints()
        self.headers = default_headers or {
            "Content-Type": "application/json",
            "User-Agent": "EcommerceStressTest/1.0",
            "Accept": "application/json",
        }
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.verify_ssl = verify_ssl
        self.metrics = EcommerceMetrics()
        self._stop_event = asyncio.Event()

        # Sample data for testing
        self._product_ids = [f"PROD-{i:04d}" for i in range(1, 1001)]
        self._category_ids = ["electronics", "clothing", "home", "sports", "books", "toys"]
        self._coupon_codes = ["SAVE10", "BLACKFRIDAY", "CYBER20", "FLASH50", "WELCOME15"]
        self._search_terms = [
            "laptop", "phone", "headphones", "shoes", "jacket", "watch",
            "camera", "tablet", "keyboard", "mouse", "monitor", "speaker"
        ]

    def _default_endpoints(self) -> Dict[str, EndpointConfig]:
        """Default e-commerce API endpoints."""
        return {
            "homepage": EndpointConfig(
                path="/",
                method="GET",
                weight=10.0,
                think_time_range=(1.0, 3.0),
            ),
            "categories": EndpointConfig(
                path="/api/categories",
                method="GET",
                weight=5.0,
            ),
            "category_products": EndpointConfig(
                path="/api/categories/{category_id}/products",
                method="GET",
                weight=8.0,
            ),
            "search": EndpointConfig(
                path="/api/products/search",
                method="GET",
                weight=7.0,
            ),
            "product_detail": EndpointConfig(
                path="/api/products/{product_id}",
                method="GET",
                weight=15.0,
                expected_status=[200, 404],
            ),
            "add_to_cart": EndpointConfig(
                path="/api/cart/items",
                method="POST",
                weight=5.0,
                requires_auth=False,
                expected_status=[200, 201, 400, 409],  # 409 for inventory conflict
            ),
            "view_cart": EndpointConfig(
                path="/api/cart",
                method="GET",
                weight=4.0,
            ),
            "update_cart": EndpointConfig(
                path="/api/cart/items/{item_id}",
                method="PUT",
                weight=2.0,
            ),
            "remove_from_cart": EndpointConfig(
                path="/api/cart/items/{item_id}",
                method="DELETE",
                weight=1.0,
            ),
            "apply_coupon": EndpointConfig(
                path="/api/cart/coupon",
                method="POST",
                weight=1.0,
                expected_status=[200, 400, 404],
            ),
            "checkout_start": EndpointConfig(
                path="/api/checkout",
                method="POST",
                weight=2.0,
                requires_auth=True,
            ),
            "checkout_shipping": EndpointConfig(
                path="/api/checkout/shipping",
                method="POST",
                weight=1.5,
                requires_auth=True,
            ),
            "checkout_payment": EndpointConfig(
                path="/api/checkout/payment",
                method="POST",
                weight=1.0,
                requires_auth=True,
                expected_status=[200, 201, 400, 402],  # 402 for payment required/failed
            ),
            "place_order": EndpointConfig(
                path="/api/orders",
                method="POST",
                weight=0.5,
                requires_auth=True,
            ),
            "order_confirmation": EndpointConfig(
                path="/api/orders/{order_id}",
                method="GET",
                weight=0.5,
                requires_auth=True,
            ),
            "login": EndpointConfig(
                path="/api/auth/login",
                method="POST",
                weight=2.0,
            ),
            "register": EndpointConfig(
                path="/api/auth/register",
                method="POST",
                weight=0.5,
            ),
        }

    def _generate_user_data(self) -> Dict[str, Any]:
        """Generate fake user data."""
        if FAKER_AVAILABLE:
            return {
                "email": fake.email(),
                "password": fake.password(),
                "first_name": fake.first_name(),
                "last_name": fake.last_name(),
                "phone": fake.phone_number(),
                "address": {
                    "street": fake.street_address(),
                    "city": fake.city(),
                    "state": fake.state(),
                    "zip": fake.zipcode(),
                    "country": fake.country_code(),
                },
            }
        else:
            return {
                "email": f"user_{uuid.uuid4().hex[:8]}@test.com",
                "password": "TestPass123!",
                "first_name": "Test",
                "last_name": "User",
                "phone": "555-0100",
                "address": {
                    "street": "123 Test St",
                    "city": "Test City",
                    "state": "TS",
                    "zip": "12345",
                    "country": "US",
                },
            }

    def _generate_payment_data(self) -> Dict[str, Any]:
        """Generate fake payment data."""
        return {
            "card_number": "4111111111111111",  # Test card
            "expiry_month": "12",
            "expiry_year": "2025",
            "cvv": "123",
            "billing_address": self._generate_user_data()["address"],
        }

    async def _make_request(
        self,
        session: aiohttp.ClientSession,
        action: UserAction,
        endpoint_key: str,
        user_session: UserSession,
        path_params: Optional[Dict[str, str]] = None,
        query_params: Optional[Dict[str, str]] = None,
        payload: Optional[Dict] = None,
    ) -> ActionResult:
        """Make an HTTP request for a user action."""
        endpoint = self.endpoints.get(endpoint_key)
        if not endpoint:
            return ActionResult(
                action=action,
                status_code=0,
                latency_ms=0,
                success=False,
                error=f"Unknown endpoint: {endpoint_key}",
            )

        # Build URL
        path = endpoint.path
        if path_params:
            for key, value in path_params.items():
                path = path.replace(f"{{{key}}}", str(value))

        url = f"{self.base_url}{path}"
        if query_params:
            query_string = "&".join(f"{k}={v}" for k, v in query_params.items())
            url = f"{url}?{query_string}"

        # Build headers
        headers = {**self.headers}
        if user_session.auth_token:
            headers["Authorization"] = f"Bearer {user_session.auth_token}"
        if user_session.cart_id:
            headers["X-Cart-ID"] = user_session.cart_id
        headers["X-Session-ID"] = user_session.session_id
        headers["X-Request-ID"] = str(uuid.uuid4())

        start = time.perf_counter()

        try:
            async with session.request(
                endpoint.method,
                url,
                headers=headers,
                json=payload if endpoint.method in ["POST", "PUT", "PATCH"] else None,
                ssl=self.verify_ssl,
            ) as response:
                latency = (time.perf_counter() - start) * 1000
                body = await response.read()

                # Try to parse response
                response_data = None
                try:
                    response_data = json.loads(body) if body else None
                except json.JSONDecodeError:
                    pass

                # Check if status is expected
                success = response.status in endpoint.expected_status
                response_valid = True

                # Custom validation
                if endpoint.response_validator and response_data:
                    response_valid = endpoint.response_validator(response_data)

                # Check for inventory conflicts
                if response.status == 409:
                    self.metrics.inventory_conflicts += 1
                elif response.status == 410 or (response_data and response_data.get("error") == "out_of_stock"):
                    self.metrics.out_of_stock_errors += 1

                return ActionResult(
                    action=action,
                    status_code=response.status,
                    latency_ms=latency,
                    success=success and response.status in [200, 201],
                    response_valid=response_valid,
                    response_data=response_data,
                )

        except asyncio.TimeoutError:
            latency = (time.perf_counter() - start) * 1000
            return ActionResult(
                action=action,
                status_code=0,
                latency_ms=latency,
                success=False,
                error="Timeout",
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return ActionResult(
                action=action,
                status_code=0,
                latency_ms=latency,
                success=False,
                error=str(e)[:50],
            )

    async def _think_time(self, min_seconds: float = 0.5, max_seconds: float = 2.0):
        """Simulate human think time between actions."""
        await asyncio.sleep(random.uniform(min_seconds, max_seconds))

    # =========================================================================
    # USER JOURNEY SIMULATIONS
    # =========================================================================

    async def _simulate_browser_journey(
        self,
        session: aiohttp.ClientSession,
        user_session: UserSession,
    ) -> List[ActionResult]:
        """
        Simulate a browsing user (77% of traffic).
        Actions: Homepage → Browse categories → View products → Leave
        """
        results = []

        # Visit homepage
        result = await self._make_request(session, UserAction.HOMEPAGE, "homepage", user_session)
        results.append(result)
        self.metrics.add_result(result)
        await self._think_time(1.0, 3.0)

        # Browse 2-5 categories
        for _ in range(random.randint(2, 5)):
            if self._stop_event.is_set():
                break

            category = random.choice(self._category_ids)
            result = await self._make_request(
                session, UserAction.BROWSE_CATEGORY, "category_products",
                user_session, path_params={"category_id": category}
            )
            results.append(result)
            self.metrics.add_result(result)
            await self._think_time(0.5, 2.0)

        # View 3-8 products
        for _ in range(random.randint(3, 8)):
            if self._stop_event.is_set():
                break

            product_id = random.choice(self._product_ids)
            result = await self._make_request(
                session, UserAction.VIEW_PRODUCT, "product_detail",
                user_session, path_params={"product_id": product_id}
            )
            results.append(result)
            self.metrics.add_result(result)
            user_session.viewed_products.append(product_id)
            await self._think_time(2.0, 5.0)

        return results

    async def _simulate_searcher_journey(
        self,
        session: aiohttp.ClientSession,
        user_session: UserSession,
    ) -> List[ActionResult]:
        """
        Simulate a searching user (15% of traffic).
        Actions: Homepage → Search → View results → View products → Maybe add to cart
        """
        results = []

        # Visit homepage
        result = await self._make_request(session, UserAction.HOMEPAGE, "homepage", user_session)
        results.append(result)
        self.metrics.add_result(result)
        await self._think_time(0.5, 1.5)

        # Perform 2-4 searches
        for _ in range(random.randint(2, 4)):
            if self._stop_event.is_set():
                break

            search_term = random.choice(self._search_terms)
            result = await self._make_request(
                session, UserAction.SEARCH, "search",
                user_session, query_params={"q": search_term, "limit": "20"}
            )
            results.append(result)
            self.metrics.add_result(result)
            user_session.search_queries.append(search_term)
            await self._think_time(1.0, 3.0)

            # View 2-5 products from search results
            for _ in range(random.randint(2, 5)):
                if self._stop_event.is_set():
                    break

                product_id = random.choice(self._product_ids)
                result = await self._make_request(
                    session, UserAction.VIEW_PRODUCT, "product_detail",
                    user_session, path_params={"product_id": product_id}
                )
                results.append(result)
                self.metrics.add_result(result)
                await self._think_time(1.0, 4.0)

        # 30% chance to add something to cart
        if random.random() < 0.3:
            product_id = random.choice(self._product_ids)
            result = await self._make_request(
                session, UserAction.ADD_TO_CART, "add_to_cart",
                user_session, payload={"product_id": product_id, "quantity": 1}
            )
            results.append(result)
            self.metrics.add_result(result)

        return results

    async def _simulate_buyer_journey(
        self,
        session: aiohttp.ClientSession,
        user_session: UserSession,
    ) -> List[ActionResult]:
        """
        Simulate a buying user (5% of traffic).
        Full journey: Browse → Add to cart → Checkout → Payment → Order
        """
        results = []

        # Browse phase
        result = await self._make_request(session, UserAction.HOMEPAGE, "homepage", user_session)
        results.append(result)
        self.metrics.add_result(result)
        await self._think_time(0.5, 1.5)

        # View some products
        for _ in range(random.randint(2, 4)):
            if self._stop_event.is_set():
                break

            product_id = random.choice(self._product_ids)
            result = await self._make_request(
                session, UserAction.VIEW_PRODUCT, "product_detail",
                user_session, path_params={"product_id": product_id}
            )
            results.append(result)
            self.metrics.add_result(result)
            await self._think_time(1.0, 3.0)

        # Add 1-3 items to cart
        for _ in range(random.randint(1, 3)):
            if self._stop_event.is_set():
                break

            product_id = random.choice(self._product_ids)
            result = await self._make_request(
                session, UserAction.ADD_TO_CART, "add_to_cart",
                user_session, payload={
                    "product_id": product_id,
                    "quantity": random.randint(1, 3)
                }
            )
            results.append(result)
            self.metrics.add_result(result)
            if result.success:
                user_session.cart_items.append(product_id)
            await self._think_time(0.5, 1.0)

        # View cart
        result = await self._make_request(session, UserAction.VIEW_CART, "view_cart", user_session)
        results.append(result)
        self.metrics.add_result(result)
        await self._think_time(1.0, 2.0)

        # 50% chance to apply coupon
        if random.random() < 0.5:
            coupon = random.choice(self._coupon_codes)
            result = await self._make_request(
                session, UserAction.APPLY_COUPON, "apply_coupon",
                user_session, payload={"code": coupon}
            )
            results.append(result)
            self.metrics.add_result(result)
            await self._think_time(0.5, 1.0)

        # Start checkout
        result = await self._make_request(
            session, UserAction.CHECKOUT_START, "checkout_start",
            user_session, payload={"cart_id": user_session.cart_id}
        )
        results.append(result)
        self.metrics.add_result(result)
        await self._think_time(2.0, 5.0)

        # Shipping info
        user_data = self._generate_user_data()
        result = await self._make_request(
            session, UserAction.CHECKOUT_SHIPPING, "checkout_shipping",
            user_session, payload={"shipping_address": user_data["address"]}
        )
        results.append(result)
        self.metrics.add_result(result)
        await self._think_time(3.0, 8.0)

        # Payment
        payment_data = self._generate_payment_data()
        result = await self._make_request(
            session, UserAction.CHECKOUT_PAYMENT, "checkout_payment",
            user_session, payload=payment_data
        )
        results.append(result)
        self.metrics.add_result(result)
        await self._think_time(1.0, 3.0)

        # Place order (if payment succeeded)
        if result.success:
            result = await self._make_request(
                session, UserAction.PLACE_ORDER, "place_order",
                user_session, payload={"confirm": True}
            )
            results.append(result)
            self.metrics.add_result(result)

            # Order confirmation
            if result.success and result.response_data:
                order_id = result.response_data.get("order_id", "ORD-0000")
                await self._think_time(0.5, 1.0)
                result = await self._make_request(
                    session, UserAction.ORDER_CONFIRMATION, "order_confirmation",
                    user_session, path_params={"order_id": order_id}
                )
                results.append(result)
                self.metrics.add_result(result)

        return results

    async def _simulate_power_buyer_journey(
        self,
        session: aiohttp.ClientSession,
        user_session: UserSession,
    ) -> List[ActionResult]:
        """
        Simulate a power buyer (2% of traffic).
        Multiple quick purchases, often during sales.
        """
        results = []

        # Quick login
        result = await self._make_request(
            session, UserAction.LOGIN, "login",
            user_session, payload={
                "email": f"power_user_{random.randint(1, 100)}@test.com",
                "password": "PowerPass123!"
            }
        )
        results.append(result)
        self.metrics.add_result(result)

        # Multiple purchase cycles (2-4)
        for cycle in range(random.randint(2, 4)):
            if self._stop_event.is_set():
                break

            # Quick search and add
            search_term = random.choice(self._search_terms)
            result = await self._make_request(
                session, UserAction.SEARCH, "search",
                user_session, query_params={"q": search_term}
            )
            results.append(result)
            self.metrics.add_result(result)
            await self._think_time(0.2, 0.5)

            # Add multiple items quickly
            for _ in range(random.randint(2, 5)):
                product_id = random.choice(self._product_ids)
                result = await self._make_request(
                    session, UserAction.ADD_TO_CART, "add_to_cart",
                    user_session, payload={
                        "product_id": product_id,
                        "quantity": random.randint(1, 5)
                    }
                )
                results.append(result)
                self.metrics.add_result(result)
                await self._think_time(0.1, 0.3)

            # Quick checkout
            result = await self._make_request(
                session, UserAction.CHECKOUT_START, "checkout_start",
                user_session
            )
            results.append(result)
            self.metrics.add_result(result)

            # Saved payment
            result = await self._make_request(
                session, UserAction.CHECKOUT_PAYMENT, "checkout_payment",
                user_session, payload={"use_saved_payment": True}
            )
            results.append(result)
            self.metrics.add_result(result)

            # Place order
            if result.success:
                result = await self._make_request(
                    session, UserAction.PLACE_ORDER, "place_order",
                    user_session
                )
                results.append(result)
                self.metrics.add_result(result)

            await self._think_time(0.5, 1.0)

        return results

    async def _simulate_bot_traffic(
        self,
        session: aiohttp.ClientSession,
        user_session: UserSession,
    ) -> List[ActionResult]:
        """
        Simulate bot/crawler traffic (1% of traffic).
        Rapid sequential requests, no think time.
        """
        results = []

        # Rapid-fire requests
        for _ in range(random.randint(50, 100)):
            if self._stop_event.is_set():
                break

            # Random endpoint
            action_type = random.choice([
                (UserAction.HOMEPAGE, "homepage", {}),
                (UserAction.BROWSE_CATEGORY, "category_products", {"category_id": random.choice(self._category_ids)}),
                (UserAction.VIEW_PRODUCT, "product_detail", {"product_id": random.choice(self._product_ids)}),
                (UserAction.SEARCH, "search", {}),
            ])

            if action_type[0] == UserAction.SEARCH:
                result = await self._make_request(
                    session, action_type[0], action_type[1],
                    user_session, query_params={"q": random.choice(self._search_terms)}
                )
            else:
                result = await self._make_request(
                    session, action_type[0], action_type[1],
                    user_session, path_params=action_type[2] if action_type[2] else None
                )

            results.append(result)
            self.metrics.add_result(result)
            # Minimal delay
            await asyncio.sleep(0.01)

        return results

    def _select_user_type(self) -> UserType:
        """Select a user type based on distribution."""
        r = random.random()
        cumulative = 0
        for user_type, probability in USER_TYPE_DISTRIBUTION.items():
            cumulative += probability
            if r <= cumulative:
                return user_type
        return UserType.BROWSER

    async def _run_user_session(
        self,
        session: aiohttp.ClientSession,
        semaphore: asyncio.Semaphore,
    ) -> List[ActionResult]:
        """Run a complete user session."""
        async with semaphore:
            user_session = UserSession(
                session_id=str(uuid.uuid4()),
                user_type=self._select_user_type(),
                cart_id=str(uuid.uuid4()),
            )

            self.metrics.total_sessions += 1

            journey_map = {
                UserType.BROWSER: self._simulate_browser_journey,
                UserType.SEARCHER: self._simulate_searcher_journey,
                UserType.BUYER: self._simulate_buyer_journey,
                UserType.POWER_BUYER: self._simulate_power_buyer_journey,
                UserType.BOT: self._simulate_bot_traffic,
            }

            journey_func = journey_map.get(user_session.user_type, self._simulate_browser_journey)
            results = await journey_func(session, user_session)

            self.metrics.completed_sessions += 1
            return results

    # =========================================================================
    # SCENARIO IMPLEMENTATIONS
    # =========================================================================

    def _create_metrics_table(self) -> "Table":
        """Create a rich table for live metrics display."""
        if not RICH_AVAILABLE:
            return None

        table = Table(title="🛒 E-Commerce Stress Test Metrics", expand=True)
        table.add_column("Metric", style="cyan", width=25)
        table.add_column("Value", style="green", width=15)
        table.add_column("Metric", style="cyan", width=25)
        table.add_column("Value", style="green", width=15)

        m = self.metrics

        table.add_row(
            "Total Requests", f"{m.total_requests:,}",
            "RPS", f"{m.rps:,.0f}"
        )
        table.add_row(
            "Successful", f"[green]{m.successful_requests:,}[/green]",
            "Failed", f"[red]{m.failed_requests:,}[/red]"
        )
        table.add_row(
            "Homepage Visits", f"{m.homepage_visits:,}",
            "Product Views", f"{m.product_views:,}"
        )
        table.add_row(
            "Add to Cart", f"{m.add_to_cart_count:,}",
            "Checkout Starts", f"{m.checkout_starts:,}"
        )
        table.add_row(
            "Orders Placed", f"{m.orders_placed:,}",
            "Orders Completed", f"[green]{m.orders_completed:,}[/green]"
        )
        table.add_row(
            "Conversion Rate", f"{m.conversion_rate:.1f}%",
            "Cart Abandonment", f"[yellow]{m.cart_abandonment_rate:.1f}%[/yellow]"
        )
        table.add_row(
            "Payment Success", f"{m.payment_success_rate:.1f}%",
            "Inventory Conflicts", f"[red]{m.inventory_conflicts:,}[/red]"
        )
        table.add_row(
            "Avg Latency", f"{m.avg_latency:.0f}ms",
            "P99 Latency", f"{m.p99_latency:.0f}ms"
        )
        table.add_row(
            "Sessions", f"{m.total_sessions:,}",
            "Duration", f"{m.duration:.1f}s"
        )

        return table

    async def normal_traffic(
        self,
        duration_seconds: int = 300,
        users_per_second: int = 10,
        concurrency: int = 100,
    ) -> EcommerceMetrics:
        """
        Simulate normal e-commerce traffic with realistic user distribution.
        """
        console.print(Panel(
            f"[bold blue]Normal Traffic Simulation[/bold blue]\n"
            f"Duration: {duration_seconds}s | Users/sec: {users_per_second} | Concurrency: {concurrency}",
            title="🛒 Starting Test"
        ) if RICH_AVAILABLE else f"Normal Traffic - Duration: {duration_seconds}s")

        self.metrics = EcommerceMetrics()
        self.metrics.start_time = time.time()
        self._stop_event.clear()

        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        semaphore = asyncio.Semaphore(concurrency)

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            end_time = time.time() + duration_seconds
            tasks = []

            if RICH_AVAILABLE:
                with Live(self._create_metrics_table(), refresh_per_second=2) as live:
                    while time.time() < end_time and not self._stop_event.is_set():
                        # Spawn new user sessions
                        for _ in range(users_per_second):
                            task = asyncio.create_task(self._run_user_session(session, semaphore))
                            tasks.append(task)

                        live.update(self._create_metrics_table())
                        await asyncio.sleep(1.0)

                    # Wait for remaining tasks
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
            else:
                while time.time() < end_time and not self._stop_event.is_set():
                    for _ in range(users_per_second):
                        task = asyncio.create_task(self._run_user_session(session, semaphore))
                        tasks.append(task)
                    await asyncio.sleep(1.0)

                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

        self.metrics.end_time = time.time()
        return self.metrics

    async def flash_sale(
        self,
        sale_duration_seconds: int = 60,
        pre_sale_users: int = 100,
        peak_users: int = 5000,
        ramp_up_seconds: int = 5,
        concurrency: int = 1000,
    ) -> EcommerceMetrics:
        """
        Simulate a flash sale scenario.
        Massive spike at sale start, then sustained high traffic.
        """
        console.print(Panel(
            f"[bold yellow]⚡ Flash Sale Simulation[/bold yellow]\n"
            f"Pre-sale: {pre_sale_users} users | Peak: {peak_users} users\n"
            f"Ramp-up: {ramp_up_seconds}s | Duration: {sale_duration_seconds}s",
            title="🔥 Flash Sale Starting"
        ) if RICH_AVAILABLE else f"Flash Sale - Peak: {peak_users} users")

        self.metrics = EcommerceMetrics()
        self.metrics.start_time = time.time()
        self._stop_event.clear()

        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        semaphore = asyncio.Semaphore(concurrency)

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:

            if RICH_AVAILABLE:
                with Live(self._create_metrics_table(), refresh_per_second=2) as live:
                    # Pre-sale waiting room
                    console.print("[yellow]Pre-sale waiting room...[/yellow]")
                    pre_tasks = [
                        asyncio.create_task(self._run_user_session(session, semaphore))
                        for _ in range(pre_sale_users)
                    ]

                    # Wait a bit then trigger sale
                    await asyncio.sleep(3)

                    # FLASH SALE START - massive spike
                    console.print("[bold red]🔥 FLASH SALE STARTED! 🔥[/bold red]")

                    # Ramp up to peak
                    users_per_batch = peak_users // ramp_up_seconds
                    sale_tasks = []

                    for _ in range(ramp_up_seconds):
                        for _ in range(users_per_batch):
                            task = asyncio.create_task(self._simulate_flash_sale_buyer(session, semaphore))
                            sale_tasks.append(task)
                        live.update(self._create_metrics_table())
                        await asyncio.sleep(1.0)

                    # Sustained high traffic
                    end_time = time.time() + sale_duration_seconds
                    while time.time() < end_time:
                        # Add more buyers
                        for _ in range(peak_users // 10):
                            task = asyncio.create_task(self._simulate_flash_sale_buyer(session, semaphore))
                            sale_tasks.append(task)
                        live.update(self._create_metrics_table())
                        await asyncio.sleep(1.0)

                    await asyncio.gather(*pre_tasks, *sale_tasks, return_exceptions=True)
            else:
                # Similar logic without rich display
                pre_tasks = [
                    asyncio.create_task(self._run_user_session(session, semaphore))
                    for _ in range(pre_sale_users)
                ]
                await asyncio.sleep(3)

                sale_tasks = []
                users_per_batch = peak_users // ramp_up_seconds

                for _ in range(ramp_up_seconds):
                    for _ in range(users_per_batch):
                        task = asyncio.create_task(self._simulate_flash_sale_buyer(session, semaphore))
                        sale_tasks.append(task)
                    await asyncio.sleep(1.0)

                end_time = time.time() + sale_duration_seconds
                while time.time() < end_time:
                    for _ in range(peak_users // 10):
                        task = asyncio.create_task(self._simulate_flash_sale_buyer(session, semaphore))
                        sale_tasks.append(task)
                    await asyncio.sleep(1.0)

                await asyncio.gather(*pre_tasks, *sale_tasks, return_exceptions=True)

        self.metrics.end_time = time.time()
        return self.metrics

    async def _simulate_flash_sale_buyer(
        self,
        session: aiohttp.ClientSession,
        semaphore: asyncio.Semaphore,
    ) -> List[ActionResult]:
        """Flash sale buyer - fast, focused on checkout."""
        async with semaphore:
            user_session = UserSession(
                session_id=str(uuid.uuid4()),
                user_type=UserType.BUYER,
                cart_id=str(uuid.uuid4()),
            )

            results = []

            # Quick product view
            product_id = random.choice(self._product_ids[:100])  # Limited sale items
            result = await self._make_request(
                session, UserAction.VIEW_PRODUCT, "product_detail",
                user_session, path_params={"product_id": product_id}
            )
            results.append(result)
            self.metrics.add_result(result)

            # Immediate add to cart
            result = await self._make_request(
                session, UserAction.ADD_TO_CART, "add_to_cart",
                user_session, payload={"product_id": product_id, "quantity": 1}
            )
            results.append(result)
            self.metrics.add_result(result)

            if not result.success:
                return results  # Item sold out

            # Rush to checkout
            result = await self._make_request(
                session, UserAction.CHECKOUT_START, "checkout_start",
                user_session
            )
            results.append(result)
            self.metrics.add_result(result)

            # Quick payment
            result = await self._make_request(
                session, UserAction.CHECKOUT_PAYMENT, "checkout_payment",
                user_session, payload=self._generate_payment_data()
            )
            results.append(result)
            self.metrics.add_result(result)

            # Place order
            if result.success:
                result = await self._make_request(
                    session, UserAction.PLACE_ORDER, "place_order",
                    user_session
                )
                results.append(result)
                self.metrics.add_result(result)

            return results

    async def black_friday(
        self,
        duration_seconds: int = 600,
        base_users: int = 1000,
        peak_multiplier: float = 20.0,
        spike_interval_seconds: int = 60,
        concurrency: int = 2000,
    ) -> EcommerceMetrics:
        """
        Simulate Black Friday traffic patterns.
        Sustained high traffic with periodic massive spikes.
        """
        console.print(Panel(
            f"[bold red]🛍️ BLACK FRIDAY SIMULATION 🛍️[/bold red]\n"
            f"Base users: {base_users} | Peak multiplier: {peak_multiplier}x\n"
            f"Duration: {duration_seconds}s | Spike every: {spike_interval_seconds}s",
            title="🔥 Black Friday Starting"
        ) if RICH_AVAILABLE else f"Black Friday - Duration: {duration_seconds}s")

        self.metrics = EcommerceMetrics()
        self.metrics.start_time = time.time()
        self._stop_event.clear()

        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        semaphore = asyncio.Semaphore(concurrency)

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            end_time = time.time() + duration_seconds
            last_spike_time = time.time()
            all_tasks = []

            if RICH_AVAILABLE:
                with Live(self._create_metrics_table(), refresh_per_second=2) as live:
                    while time.time() < end_time and not self._stop_event.is_set():
                        current_time = time.time()

                        # Check if it's spike time
                        if current_time - last_spike_time >= spike_interval_seconds:
                            console.print("[bold yellow]⚡ TRAFFIC SPIKE! ⚡[/bold yellow]")
                            # Massive spike
                            spike_users = int(base_users * peak_multiplier)
                            for _ in range(spike_users):
                                task = asyncio.create_task(self._run_user_session(session, semaphore))
                                all_tasks.append(task)
                            last_spike_time = current_time
                        else:
                            # Normal high traffic
                            for _ in range(base_users // 10):
                                task = asyncio.create_task(self._run_user_session(session, semaphore))
                                all_tasks.append(task)

                        live.update(self._create_metrics_table())
                        await asyncio.sleep(1.0)

                    await asyncio.gather(*all_tasks, return_exceptions=True)
            else:
                while time.time() < end_time and not self._stop_event.is_set():
                    current_time = time.time()

                    if current_time - last_spike_time >= spike_interval_seconds:
                        spike_users = int(base_users * peak_multiplier)
                        for _ in range(spike_users):
                            task = asyncio.create_task(self._run_user_session(session, semaphore))
                            all_tasks.append(task)
                        last_spike_time = current_time
                    else:
                        for _ in range(base_users // 10):
                            task = asyncio.create_task(self._run_user_session(session, semaphore))
                            all_tasks.append(task)

                    await asyncio.sleep(1.0)

                await asyncio.gather(*all_tasks, return_exceptions=True)

        self.metrics.end_time = time.time()
        return self.metrics

    async def inventory_race_condition_test(
        self,
        product_id: str = "LIMITED-001",
        total_stock: int = 100,
        concurrent_buyers: int = 500,
        concurrency: int = 500,
    ) -> EcommerceMetrics:
        """
        Test inventory race conditions.
        Many users trying to buy limited stock simultaneously.
        """
        console.print(Panel(
            f"[bold magenta]🏁 Inventory Race Condition Test[/bold magenta]\n"
            f"Product: {product_id} | Stock: {total_stock} | Buyers: {concurrent_buyers}",
            title="🔒 Race Condition Test"
        ) if RICH_AVAILABLE else f"Race Condition Test - Stock: {total_stock}, Buyers: {concurrent_buyers}")

        self.metrics = EcommerceMetrics()
        self.metrics.start_time = time.time()

        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        semaphore = asyncio.Semaphore(concurrency)

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:

            async def try_purchase():
                async with semaphore:
                    user_session = UserSession(
                        session_id=str(uuid.uuid4()),
                        user_type=UserType.BUYER,
                        cart_id=str(uuid.uuid4()),
                    )

                    # Add to cart
                    result = await self._make_request(
                        session, UserAction.ADD_TO_CART, "add_to_cart",
                        user_session, payload={
                            "product_id": product_id,
                            "quantity": 1
                        }
                    )
                    self.metrics.add_result(result)

                    if not result.success:
                        return  # Sold out or conflict

                    # Try to checkout immediately
                    result = await self._make_request(
                        session, UserAction.CHECKOUT_PAYMENT, "checkout_payment",
                        user_session, payload=self._generate_payment_data()
                    )
                    self.metrics.add_result(result)

                    if result.success:
                        result = await self._make_request(
                            session, UserAction.PLACE_ORDER, "place_order",
                            user_session
                        )
                        self.metrics.add_result(result)

            # Launch all buyers simultaneously
            tasks = [try_purchase() for _ in range(concurrent_buyers)]

            if RICH_AVAILABLE:
                with Live(self._create_metrics_table(), refresh_per_second=2) as live:
                    async def updater():
                        while not all(t.done() for t in tasks):
                            live.update(self._create_metrics_table())
                            await asyncio.sleep(0.25)

                    await asyncio.gather(updater(), *tasks, return_exceptions=True)
            else:
                await asyncio.gather(*tasks, return_exceptions=True)

        self.metrics.end_time = time.time()

        # Report findings
        console.print(f"\n[bold]Race Condition Results:[/bold]")
        console.print(f"  Expected max orders: {total_stock}")
        console.print(f"  Actual orders placed: {self.metrics.orders_placed}")
        console.print(f"  Inventory conflicts detected: {self.metrics.inventory_conflicts}")
        console.print(f"  Out of stock errors: {self.metrics.out_of_stock_errors}")

        if self.metrics.orders_placed > total_stock:
            console.print(f"[bold red]⚠️ OVERSELLING DETECTED! {self.metrics.orders_placed - total_stock} extra orders![/bold red]")

        return self.metrics

    async def mixed_workload(
        self,
        duration_seconds: int = 300,
        read_write_ratio: float = 0.8,  # 80% reads, 20% writes
        target_rps: int = 500,
        concurrency: int = 200,
    ) -> EcommerceMetrics:
        """
        Mixed workload test with configurable read/write ratio.
        Simulates realistic database load patterns.
        """
        console.print(Panel(
            f"[bold cyan]📊 Mixed Workload Test[/bold cyan]\n"
            f"Read/Write Ratio: {read_write_ratio*100:.0f}%/{(1-read_write_ratio)*100:.0f}%\n"
            f"Target RPS: {target_rps} | Duration: {duration_seconds}s",
            title="⚖️ Mixed Workload"
        ) if RICH_AVAILABLE else f"Mixed Workload - R/W: {read_write_ratio}")

        self.metrics = EcommerceMetrics()
        self.metrics.start_time = time.time()
        self._stop_event.clear()

        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        semaphore = asyncio.Semaphore(concurrency)

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            end_time = time.time() + duration_seconds

            async def read_operation():
                async with semaphore:
                    user_session = UserSession(
                        session_id=str(uuid.uuid4()),
                        user_type=UserType.BROWSER,
                    )
                    # Random read operation
                    op = random.choice([
                        (UserAction.HOMEPAGE, "homepage", {}, {}),
                        (UserAction.VIEW_PRODUCT, "product_detail",
                         {"product_id": random.choice(self._product_ids)}, {}),
                        (UserAction.SEARCH, "search", {},
                         {"q": random.choice(self._search_terms)}),
                        (UserAction.BROWSE_CATEGORY, "category_products",
                         {"category_id": random.choice(self._category_ids)}, {}),
                    ])
                    result = await self._make_request(
                        session, op[0], op[1], user_session,
                        path_params=op[2] if op[2] else None,
                        query_params=op[3] if op[3] else None,
                    )
                    self.metrics.add_result(result)

            async def write_operation():
                async with semaphore:
                    user_session = UserSession(
                        session_id=str(uuid.uuid4()),
                        user_type=UserType.BUYER,
                        cart_id=str(uuid.uuid4()),
                    )
                    # Random write operation
                    op = random.choice([
                        (UserAction.ADD_TO_CART, "add_to_cart",
                         {"product_id": random.choice(self._product_ids), "quantity": 1}),
                        (UserAction.UPDATE_CART, "update_cart", {"quantity": random.randint(1, 5)}),
                        (UserAction.APPLY_COUPON, "apply_coupon",
                         {"code": random.choice(self._coupon_codes)}),
                    ])
                    result = await self._make_request(
                        session, op[0], op[1], user_session, payload=op[2]
                    )
                    self.metrics.add_result(result)

            tasks = []

            if RICH_AVAILABLE:
                with Live(self._create_metrics_table(), refresh_per_second=2) as live:
                    while time.time() < end_time and not self._stop_event.is_set():
                        for _ in range(target_rps):
                            if random.random() < read_write_ratio:
                                task = asyncio.create_task(read_operation())
                            else:
                                task = asyncio.create_task(write_operation())
                            tasks.append(task)

                        live.update(self._create_metrics_table())
                        await asyncio.sleep(1.0)

                    await asyncio.gather(*tasks, return_exceptions=True)
            else:
                while time.time() < end_time and not self._stop_event.is_set():
                    for _ in range(target_rps):
                        if random.random() < read_write_ratio:
                            task = asyncio.create_task(read_operation())
                        else:
                            task = asyncio.create_task(write_operation())
                        tasks.append(task)
                    await asyncio.sleep(1.0)

                await asyncio.gather(*tasks, return_exceptions=True)

        self.metrics.end_time = time.time()
        return self.metrics

    async def checkout_stress_test(
        self,
        concurrent_checkouts: int = 500,
        checkout_timeout_seconds: float = 30.0,
        concurrency: int = 500,
    ) -> EcommerceMetrics:
        """
        Stress test the checkout flow specifically.
        Simulates many users checking out simultaneously.
        """
        console.print(Panel(
            f"[bold green]💳 Checkout Stress Test[/bold green]\n"
            f"Concurrent checkouts: {concurrent_checkouts}",
            title="🛒 Checkout Test"
        ) if RICH_AVAILABLE else f"Checkout Stress Test - {concurrent_checkouts} concurrent")

        self.metrics = EcommerceMetrics()
        self.metrics.start_time = time.time()

        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        semaphore = asyncio.Semaphore(concurrency)

        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:

            async def full_checkout():
                async with semaphore:
                    user_session = UserSession(
                        session_id=str(uuid.uuid4()),
                        user_type=UserType.BUYER,
                        cart_id=str(uuid.uuid4()),
                    )

                    # Add items to cart
                    for _ in range(random.randint(1, 3)):
                        result = await self._make_request(
                            session, UserAction.ADD_TO_CART, "add_to_cart",
                            user_session, payload={
                                "product_id": random.choice(self._product_ids),
                                "quantity": random.randint(1, 2)
                            }
                        )
                        self.metrics.add_result(result)

                    # View cart
                    result = await self._make_request(
                        session, UserAction.VIEW_CART, "view_cart", user_session
                    )
                    self.metrics.add_result(result)

                    # Start checkout
                    result = await self._make_request(
                        session, UserAction.CHECKOUT_START, "checkout_start", user_session
                    )
                    self.metrics.add_result(result)

                    # Shipping
                    result = await self._make_request(
                        session, UserAction.CHECKOUT_SHIPPING, "checkout_shipping",
                        user_session, payload={"shipping_address": self._generate_user_data()["address"]}
                    )
                    self.metrics.add_result(result)

                    # Payment
                    result = await self._make_request(
                        session, UserAction.CHECKOUT_PAYMENT, "checkout_payment",
                        user_session, payload=self._generate_payment_data()
                    )
                    self.metrics.add_result(result)

                    # Place order
                    if result.success:
                        result = await self._make_request(
                            session, UserAction.PLACE_ORDER, "place_order", user_session
                        )
                        self.metrics.add_result(result)

            tasks = [full_checkout() for _ in range(concurrent_checkouts)]

            if RICH_AVAILABLE:
                with Live(self._create_metrics_table(), refresh_per_second=2) as live:
                    async def updater():
                        while not all(t.done() for t in tasks):
                            live.update(self._create_metrics_table())
                            await asyncio.sleep(0.25)

                    await asyncio.gather(updater(), *tasks, return_exceptions=True)
            else:
                await asyncio.gather(*tasks, return_exceptions=True)

        self.metrics.end_time = time.time()
        return self.metrics

    def generate_report(self, output_path: Optional[str] = None) -> str:
        """Generate comprehensive e-commerce test report."""
        m = self.metrics
        report = m.to_dict()
        report["timestamp"] = datetime.now(timezone.utc).isoformat()
        report["base_url"] = self.base_url

        json_str = json.dumps(report, indent=2)

        if output_path:
            Path(output_path).write_text(json_str)
            console.print(f"[green]Report saved to: {output_path}[/green]" if RICH_AVAILABLE else f"Report saved to: {output_path}")

        return json_str

    def print_summary(self):
        """Print test summary to console."""
        m = self.metrics

        if RICH_AVAILABLE:
            console.print("\n")
            console.print(Panel(
                f"""[bold]E-Commerce Test Summary[/bold]

[cyan]Total Requests:[/cyan]     {m.total_requests:,}
[green]Successful:[/green]         {m.successful_requests:,}
[red]Failed:[/red]             {m.failed_requests:,}
[cyan]Duration:[/cyan]           {m.duration:.2f}s
[cyan]RPS:[/cyan]                 {m.rps:,.0f}

[bold]Funnel Metrics:[/bold]
  Homepage Visits:    {m.homepage_visits:,}
  Product Views:      {m.product_views:,}
  Add to Cart:        {m.add_to_cart_count:,}
  Checkout Starts:    {m.checkout_starts:,}
  Orders Completed:   {m.orders_completed:,}

[bold]Conversion:[/bold]
  Conversion Rate:        {m.conversion_rate:.2f}%
  Cart Abandonment:       {m.cart_abandonment_rate:.2f}%
  Checkout Completion:    {m.checkout_completion_rate:.2f}%
  Payment Success Rate:   {m.payment_success_rate:.2f}%

[bold]Inventory:[/bold]
  Conflicts Detected:     {m.inventory_conflicts:,}
  Out of Stock Errors:    {m.out_of_stock_errors:,}

[bold]Latency:[/bold]
  Average:    {m.avg_latency:.0f}ms
  P50:        {m.p50_latency:.0f}ms
  P95:        {m.p95_latency:.0f}ms
  P99:        {m.p99_latency:.0f}ms

[bold]Latency by Action:[/bold]
{self._format_action_latencies()}
""",
                title="📊 E-Commerce Test Results",
                border_style="green"
            ))
        else:
            print(f"""
=== E-Commerce Test Summary ===
Total Requests: {m.total_requests:,}
Duration: {m.duration:.2f}s
RPS: {m.rps:,.0f}
Conversion Rate: {m.conversion_rate:.2f}%
Orders Completed: {m.orders_completed:,}
""")

    def _format_action_latencies(self) -> str:
        lines = []
        for action, latencies in sorted(self.metrics.action_latencies.items()):
            if latencies:
                avg = statistics.mean(latencies)
                lines.append(f"  {action:<20} {avg:.0f}ms")
        return "\n".join(lines[:10])  # Top 10


# =============================================================================
# MAIN
# =============================================================================

async def main():
    parser = argparse.ArgumentParser(
        description="🛒 E-Commerce Stress Test Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--base-url", "-u", required=True, help="Base API URL")
    parser.add_argument("--scenario", "-s", default="normal",
                       choices=["normal", "flash-sale", "black-friday", "race-condition",
                               "mixed-workload", "checkout-stress"])
    parser.add_argument("--duration", "-d", type=int, default=300, help="Test duration (seconds)")
    parser.add_argument("--users", type=int, default=100, help="Users per second / concurrent users")
    parser.add_argument("--concurrency", "-c", type=int, default=200, help="Max concurrent connections")
    parser.add_argument("--output", "-o", type=str, help="Output file for JSON report")

    args = parser.parse_args()

    engine = EcommerceStressTestEngine(base_url=args.base_url)

    console.print(f"\n[bold]Target:[/bold] {args.base_url}")
    console.print(f"[bold]Scenario:[/bold] {args.scenario}\n")

    if args.scenario == "normal":
        await engine.normal_traffic(
            duration_seconds=args.duration,
            users_per_second=args.users // 10,
            concurrency=args.concurrency,
        )
    elif args.scenario == "flash-sale":
        await engine.flash_sale(
            sale_duration_seconds=args.duration,
            peak_users=args.users * 10,
            concurrency=args.concurrency,
        )
    elif args.scenario == "black-friday":
        await engine.black_friday(
            duration_seconds=args.duration,
            base_users=args.users,
            concurrency=args.concurrency,
        )
    elif args.scenario == "race-condition":
        await engine.inventory_race_condition_test(
            concurrent_buyers=args.users,
            concurrency=args.concurrency,
        )
    elif args.scenario == "mixed-workload":
        await engine.mixed_workload(
            duration_seconds=args.duration,
            target_rps=args.users,
            concurrency=args.concurrency,
        )
    elif args.scenario == "checkout-stress":
        await engine.checkout_stress_test(
            concurrent_checkouts=args.users,
            concurrency=args.concurrency,
        )

    engine.print_summary()

    if args.output:
        engine.generate_report(args.output)


if __name__ == "__main__":
    asyncio.run(main())
