"""
repository/base.py — Abstract interface for merchant data access.

Any real Paytm API adapter just needs to implement this ABC.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Optional


# ── Return types (plain dataclasses — no ORM leakage) ────────────────────────

@dataclass
class SalesSummary:
    period_days: int
    total_revenue: Decimal
    num_orders: int
    avg_order_value: Decimal
    date_from: datetime
    date_to: datetime


@dataclass
class PeriodComparison:
    period_a_days: int
    period_b_days: int
    revenue_a: Decimal
    revenue_b: Decimal
    orders_a: int
    orders_b: int
    revenue_change_pct: float   # positive = A is higher than B


@dataclass
class ItemSales:
    item: str
    category: str
    quantity_sold: int
    revenue: Decimal


@dataclass
class StockItem:
    item: str
    category: str
    stock_qty: int
    reorder_level: int
    unit_price: Decimal
    is_low: bool        # stock_qty <= reorder_level


@dataclass
class StockoutImpact:
    item: str
    stockout_date: date
    end_date: Optional[date]
    revenue_on_stockout_day: Decimal
    avg_revenue_comparison_days: Decimal   # average of surrounding days
    estimated_loss: Decimal


@dataclass
class DueRecord:
    customer_name: str
    amount_due: Decimal
    due_date: Optional[datetime]
    status: str
    days_overdue: int   # 0 if not overdue


@dataclass
class DuesSummary:
    total_pending: Decimal
    total_overdue: Decimal
    num_pending_customers: int
    num_overdue_customers: int
    records: list[DueRecord]


@dataclass
class PeerBenchmark:
    metric: str
    merchant_value: float
    peer_avg_value: float
    difference_pct: float
    unit: str
    performance_status: str   # "above_average", "below_average", "similar"
    insight: str


@dataclass
class FinancialEligibility:
    is_eligible: bool
    estimated_credit_limit: Decimal
    monthly_sales_avg: Decimal
    indicative_rate_monthly: str
    tenure_options: list[str]
    disclaimer: str


# ── Abstract Repository ───────────────────────────────────────────────────────

class TransactionRepository(ABC):
    """
    Interface for all merchant data queries.
    Implementations: PostgresTransactionRepository (this project), future PaytmAPIRepository.
    """

    @abstractmethod
    def sales_summary(self, merchant_id: int, period_days: int) -> SalesSummary:
        """Revenue, order count, and avg basket for the last N days."""

    @abstractmethod
    def compare_periods(
        self,
        merchant_id: int,
        period_a_days: int,
        period_b_days: int,
    ) -> PeriodComparison:
        """
        Compare period A (most recent N days) vs period B (prior M days).
        E.g. compare_periods(id, 7, 7) → last 7 vs prior 7.
        """

    @abstractmethod
    def top_items(
        self,
        merchant_id: int,
        period_days: int,
        limit: int = 5,
    ) -> list[ItemSales]:
        """Top-selling items by revenue over the last N days."""

    @abstractmethod
    def stock_status(self, merchant_id: int) -> list[StockItem]:
        """Current stock levels for all items."""

    @abstractmethod
    def low_stock(self, merchant_id: int) -> list[StockItem]:
        """Items at or below reorder_level."""

    @abstractmethod
    def stockout_impact(self, merchant_id: int, item: str) -> Optional[StockoutImpact]:
        """
        Revenue impact of the most recent stockout event for the given item.
        Returns None if no stockout event is recorded.
        """

    @abstractmethod
    def dues_summary(self, merchant_id: int) -> DuesSummary:
        """Aggregate dues info: totals + per-customer records."""

    @abstractmethod
    def overdue_customers(self, merchant_id: int) -> list[DueRecord]:
        """Customers whose dues are past due_date (status=overdue)."""

    @abstractmethod
    def peer_benchmarking(self, merchant_id: int, metric: str = "daily_sales") -> PeerBenchmark:
        """Compare merchant metrics against peer/cluster averages."""

    @abstractmethod
    def financial_guidance(self, merchant_id: int, intent: str = "loan") -> FinancialEligibility:
        """Calculate indicative working capital loan eligibility based on 90-day sales."""
