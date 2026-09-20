"""
repository/postgres.py — PostgreSQL implementation of TransactionRepository.

All queries use SQLAlchemy Core / ORM; no raw SQL strings.
Every public method returns typed dataclasses (no ORM objects escape).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import (
    Due, DueStatus, Inventory, StockoutEvent,
    Transaction, TransactionItem, TransactionStatus,
)
from repository.base import (
    DueRecord, DuesSummary, FinancialEligibility, ItemSales, PeerBenchmark,
    PeriodComparison, SalesSummary, StockItem, StockoutImpact, TransactionRepository,
)


class PostgresTransactionRepository(TransactionRepository):
    """
    Concrete implementation backed by a SQLAlchemy Session.
    Inject via FastAPI dependency: Depends(get_repo).
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _window(self, days: int) -> tuple[datetime, datetime]:
        """Returns (start_inclusive, end_exclusive=now) for the last N days."""
        now = datetime.utcnow()
        return now - timedelta(days=days), now

    def _revenue_in_window(
        self, merchant_id: int, start: datetime, end: datetime
    ) -> tuple[Decimal, int]:
        """Return (total_revenue, num_orders) for successful txns in [start, end)."""
        q = (
            self._db.query(
                func.coalesce(func.sum(Transaction.amount), 0).label("revenue"),
                func.count(Transaction.id).label("orders"),
            )
            .filter(
                Transaction.merchant_id == merchant_id,
                Transaction.status == TransactionStatus.success,
                Transaction.created_at >= start,
                Transaction.created_at < end,
            )
        )
        row = q.one()
        return Decimal(str(row.revenue)), int(row.orders)

    # ── Public API ────────────────────────────────────────────────────────────

    def sales_summary(self, merchant_id: int, period_days: int) -> SalesSummary:
        start, end = self._window(period_days)
        revenue, orders = self._revenue_in_window(merchant_id, start, end)
        avg = revenue / orders if orders else Decimal("0")
        return SalesSummary(
            period_days=period_days,
            total_revenue=revenue,
            num_orders=orders,
            avg_order_value=avg.quantize(Decimal("0.01")),
            date_from=start,
            date_to=end,
        )

    def compare_periods(
        self, merchant_id: int, period_a_days: int, period_b_days: int
    ) -> PeriodComparison:
        now = datetime.utcnow()
        end_a   = now
        start_a = now - timedelta(days=period_a_days)
        end_b   = start_a
        start_b = end_b - timedelta(days=period_b_days)

        rev_a, ord_a = self._revenue_in_window(merchant_id, start_a, end_a)
        rev_b, ord_b = self._revenue_in_window(merchant_id, start_b, end_b)

        if rev_b == 0:
            pct = 0.0
        else:
            pct = float((rev_a - rev_b) / rev_b * 100)

        return PeriodComparison(
            period_a_days=period_a_days,
            period_b_days=period_b_days,
            revenue_a=rev_a,
            revenue_b=rev_b,
            orders_a=ord_a,
            orders_b=ord_b,
            revenue_change_pct=round(pct, 2),
        )

    def top_items(
        self, merchant_id: int, period_days: int, limit: int = 5
    ) -> list[ItemSales]:
        start, end = self._window(period_days)

        rows = (
            self._db.query(
                Inventory.item,
                Inventory.category,
                func.sum(TransactionItem.quantity).label("qty_sold"),
                func.sum(TransactionItem.quantity * TransactionItem.unit_price).label("revenue"),
            )
            .join(TransactionItem, TransactionItem.inventory_id == Inventory.id)
            .join(Transaction, Transaction.id == TransactionItem.transaction_id)
            .filter(
                Transaction.merchant_id == merchant_id,
                Transaction.status == TransactionStatus.success,
                Transaction.created_at >= start,
                Transaction.created_at < end,
            )
            .group_by(Inventory.id, Inventory.item, Inventory.category)
            .order_by(func.sum(TransactionItem.quantity * TransactionItem.unit_price).desc())
            .limit(limit)
            .all()
        )

        return [
            ItemSales(
                item=r.item,
                category=r.category,
                quantity_sold=int(r.qty_sold or 0),
                revenue=Decimal(str(r.revenue or 0)),
            )
            for r in rows
        ]

    def stock_status(self, merchant_id: int) -> list[StockItem]:
        rows = (
            self._db.query(Inventory)
            .filter(Inventory.merchant_id == merchant_id)
            .order_by(Inventory.item)
            .all()
        )
        return [
            StockItem(
                item=inv.item,
                category=inv.category,
                stock_qty=inv.stock_qty,
                reorder_level=inv.reorder_level,
                unit_price=inv.unit_price,
                is_low=(inv.stock_qty <= inv.reorder_level),
            )
            for inv in rows
        ]

    def low_stock(self, merchant_id: int) -> list[StockItem]:
        rows = (
            self._db.query(Inventory)
            .filter(
                Inventory.merchant_id == merchant_id,
                Inventory.stock_qty <= Inventory.reorder_level,
            )
            .order_by(Inventory.stock_qty)
            .all()
        )
        return [
            StockItem(
                item=inv.item,
                category=inv.category,
                stock_qty=inv.stock_qty,
                reorder_level=inv.reorder_level,
                unit_price=inv.unit_price,
                is_low=True,
            )
            for inv in rows
        ]

    def stockout_impact(self, merchant_id: int, item: str) -> Optional[StockoutImpact]:
        # Find inventory row
        inv = (
            self._db.query(Inventory)
            .filter(Inventory.merchant_id == merchant_id,
                    Inventory.item.ilike(f"%{item}%"))
            .first()
        )
        if not inv:
            return None

        # Most recent stockout event
        event = (
            self._db.query(StockoutEvent)
            .filter(
                StockoutEvent.merchant_id == merchant_id,
                StockoutEvent.inventory_id == inv.id,
            )
            .order_by(StockoutEvent.start_date.desc())
            .first()
        )
        if not event:
            return None

        # Revenue on stockout day
        day_start = datetime.combine(event.start_date, datetime.min.time())
        day_end   = day_start + timedelta(days=1)
        rev_on_day, _ = self._revenue_in_window(merchant_id, day_start, day_end)

        # Average revenue over the 3 days before and 3 days after (excluding the day itself)
        comparison_days = []
        for delta in [-3, -2, -1, 1, 2, 3]:
            d = event.start_date + timedelta(days=delta)
            ds = datetime.combine(d, datetime.min.time())
            de = ds + timedelta(days=1)
            r, _ = self._revenue_in_window(merchant_id, ds, de)
            comparison_days.append(r)

        avg_comparison = (
            sum(comparison_days, Decimal("0")) / len(comparison_days)
            if comparison_days else Decimal("0")
        )
        estimated_loss = max(Decimal("0"), avg_comparison - rev_on_day)

        return StockoutImpact(
            item=inv.item,
            stockout_date=event.start_date,
            end_date=event.end_date,
            revenue_on_stockout_day=rev_on_day,
            avg_revenue_comparison_days=avg_comparison.quantize(Decimal("0.01")),
            estimated_loss=estimated_loss.quantize(Decimal("0.01")),
        )

    def dues_summary(self, merchant_id: int) -> DuesSummary:
        rows = (
            self._db.query(Due)
            .join(Due.customer)
            .filter(
                Due.merchant_id == merchant_id,
                Due.status.in_([DueStatus.pending, DueStatus.overdue]),
            )
            .all()
        )
        now = datetime.utcnow()
        records: list[DueRecord] = []
        total_pending  = Decimal("0")
        total_overdue  = Decimal("0")
        n_pending = 0
        n_overdue = 0

        for due in rows:
            days_overdue = 0
            if due.due_date and due.due_date < now:
                days_overdue = (now - due.due_date).days

            records.append(DueRecord(
                customer_name=due.customer.name,
                amount_due=due.amount_due,
                due_date=due.due_date,
                status=due.status.value,
                days_overdue=days_overdue,
            ))

            if due.status == DueStatus.overdue:
                total_overdue += due.amount_due
                n_overdue += 1
            else:
                total_pending += due.amount_due
                n_pending += 1

        return DuesSummary(
            total_pending=total_pending,
            total_overdue=total_overdue,
            num_pending_customers=n_pending,
            num_overdue_customers=n_overdue,
            records=records,
        )

    def overdue_customers(self, merchant_id: int) -> list[DueRecord]:
        rows = (
            self._db.query(Due)
            .join(Due.customer)
            .filter(
                Due.merchant_id == merchant_id,
                Due.status == DueStatus.overdue,
            )
            .all()
        )
        now = datetime.utcnow()
        return [
            DueRecord(
                customer_name=due.customer.name,
                amount_due=due.amount_due,
                due_date=due.due_date,
                status=due.status.value,
                days_overdue=(now - due.due_date).days if due.due_date else 0,
            )
            for due in rows
        ]

    def peer_benchmarking(self, merchant_id: int, metric: str = "daily_sales") -> PeerBenchmark:
        """
        Compare merchant metrics against peer / cluster averages (e.g. other seeded kirana merchants).
        """
        # Get merchant 90-day data
        m_sales = self.sales_summary(merchant_id, 90)
        m_daily_rev = float(m_sales.total_revenue) / 90.0 if m_sales.total_revenue else 0.0
        m_avg_order = float(m_sales.avg_order_value)

        # Peer average comparison (using other merchants in DB if present, or synthetic baseline cluster)
        other_merchants_revenue = (
            self._db.query(
                func.coalesce(func.sum(Transaction.amount), 0).label("revenue"),
                func.count(Transaction.id).label("orders"),
            )
            .filter(
                Transaction.merchant_id != merchant_id,
                Transaction.status == TransactionStatus.success,
            )
            .one()
        )
        peer_total_rev = float(other_merchants_revenue.revenue)
        peer_orders = int(other_merchants_revenue.orders)

        if peer_total_rev > 0:
            peer_daily_rev = peer_total_rev / 90.0
            peer_avg_order = peer_total_rev / peer_orders if peer_orders else 150.0
        else:
            # Synthetic peer cluster default (typical Mumbai kirana)
            peer_daily_rev = 3100.0
            peer_avg_order = 185.0

        metric_lower = (metric or "").lower()
        if "order" in metric_lower or "basket" in metric_lower:
            diff_pct = round(((m_avg_order - peer_avg_order) / peer_avg_order) * 100, 1)
            status = "above_average" if diff_pct > 5 else ("below_average" if diff_pct < -5 else "similar")
            insight = (
                f"Aapki average basket value Rs {round(m_avg_order)} hai, "
                f"jabki aas-paas ki kirana dukanon ka average Rs {round(peer_avg_order)} hai."
            )
            return PeerBenchmark(
                metric="avg_order_value",
                merchant_value=round(m_avg_order, 2),
                peer_avg_value=round(peer_avg_order, 2),
                difference_pct=diff_pct,
                unit="INR",
                performance_status=status,
                insight=insight,
            )
        else:
            diff_pct = round(((m_daily_rev - peer_daily_rev) / peer_daily_rev) * 100, 1)
            status = "above_average" if diff_pct > 5 else ("below_average" if diff_pct < -5 else "similar")
            insight = (
                f"Aapki daily average bikri lagbhag Rs {round(m_daily_rev)} hai, "
                f"jo ki area ke peer average Rs {round(peer_daily_rev)} se {abs(diff_pct)}% {'zyada' if diff_pct >= 0 else 'kam'} hai."
            )
            return PeerBenchmark(
                metric="daily_sales",
                merchant_value=round(m_daily_rev, 2),
                peer_avg_value=round(peer_daily_rev, 2),
                difference_pct=diff_pct,
                unit="INR",
                performance_status=status,
                insight=insight,
            )

    def financial_guidance(self, merchant_id: int, intent: str = "loan") -> FinancialEligibility:
        """
        Calculate indicative working capital loan eligibility based on 90-day sales volume.
        """
        sales_90d = self.sales_summary(merchant_id, 90)
        total_rev = sales_90d.total_revenue
        monthly_avg = (total_rev / Decimal("3")).quantize(Decimal("1.00"))
        # Working capital line typically 1.5x monthly turnover
        credit_limit = (monthly_avg * Decimal("1.5")).quantize(Decimal("1000"))
        if credit_limit < Decimal("25000"):
            credit_limit = Decimal("50000")

        return FinancialEligibility(
            is_eligible=True,
            estimated_credit_limit=credit_limit,
            monthly_sales_avg=monthly_avg,
            indicative_rate_monthly="1.2% per month",
            tenure_options=["3 months", "6 months", "12 months"],
            disclaimer="Indicative calculation based on 90-day Paytm transactions. Final approval subject to partner bank credit policy.",
        )
