"""
routers/insights.py — Proactive merchant insights & Today's sales dashboard metrics.

Endpoints:
  GET /api/insights        — 3 proactive spoken-ready insights (stock, anomaly, overdue) with TTS audio
  GET /api/merchant/today  — Live today's sales tile data
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.tts import synthesize_speech
from config import get_settings
from database import get_db
from models import Merchant
from repository.postgres import PostgresTransactionRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["insights"])


class InsightAction(BaseModel):
    label: str
    action_type: str


class InsightItem(BaseModel):
    id: str
    category: str  # "stock", "sales", "dues"
    title: str
    display_text: str
    speech_text: str
    audio_base64: str
    action: Optional[InsightAction] = None


class InsightsResponse(BaseModel):
    merchant_id: int
    shop_name: str
    insights: list[InsightItem]


class TodaySalesResponse(BaseModel):
    merchant_id: int
    merchant_name: str
    shop_name: str
    today_revenue: float
    today_orders: int
    today_avg_order: float
    period_7d_revenue: float
    is_premium_active: bool = False


@router.get(
    "/insights",
    response_model=InsightsResponse,
    summary="Get proactive daily voice insights (Morning Briefing)",
)
def get_proactive_insights(
    merchant_id: Optional[int] = Query(None, description="Merchant ID"),
    language: str = Query("hi-IN", description="Language code for speech synthesis"),
    db: Session = Depends(get_db),
) -> InsightsResponse:
    settings = get_settings()
    actual_merchant_id = merchant_id or settings.merchant_id

    merchant = db.query(Merchant).filter(Merchant.id == actual_merchant_id).first()
    shop_name = merchant.shop_name if merchant else "Kirana Store"

    repo = PostgresTransactionRepository(db)

    # 1. Stock item alert
    low_items = repo.low_stock(actual_merchant_id)
    if low_items:
        first_low = low_items[0]
        stock_display = f"⚠️ {first_low.item} ka stock kam hai ({first_low.stock_qty} bacha hai, reorder level {first_low.reorder_level})।"
        stock_speech = f"{first_low.item} ka stock kam hai. Keval {first_low.stock_qty} bacha hai, turant reorder karein."
        stock_action = InsightAction(label=f"Reorder {first_low.item}", action_type="reorder_stock")
    else:
        stock_display = "✅ Sabhi mukhya items ka stock paryapt hai."
        stock_speech = "Sabhi zaroori items ka stock theek hai."
        stock_action = None

    # 2. Sales Anomaly / Stockout Impact (Tuesday rice stockout)
    stockout = repo.stockout_impact(actual_merchant_id, "rice")
    if stockout and stockout.estimated_loss > 0:
        loss_amt = int(stockout.estimated_loss)
        sales_display = f"📉 Pichle mangalwar Rice stockout hone se lagbhag ₹{loss_amt:,} ki bikri kam hui."
        sales_speech = f"Pichle mangalwar rice stockout hone se lagbhag {loss_amt} rupaye ki bikri ka nuksan hua."
        sales_action = InsightAction(label="Stockout Analysis", action_type="view_stockout_report")
    else:
        cmp = repo.compare_periods(actual_merchant_id, 7, 7)
        pct = abs(cmp.revenue_change_pct)
        direction = "badhi" if cmp.revenue_change_pct >= 0 else "kam hui"
        sales_display = f"📊 Pichle 7 dinon mein bikri {pct}% {direction} (₹{int(cmp.revenue_a):,})।"
        sales_speech = f"Pichle saat dinon mein bikri {pct} pratishat {direction}."
        sales_action = InsightAction(label="Compare Periods", action_type="view_sales_report")

    # 3. Overdue Dues Alert
    overdues = repo.overdue_customers(actual_merchant_id)
    if overdues:
        first_overdue = overdues[0]
        amt = int(first_overdue.amount_due)
        dues_display = f"⏰ {first_overdue.customer_name} ka ₹{amt:,} udhaar {first_overdue.days_overdue} din se overdue hai."
        dues_speech = f"{first_overdue.customer_name} ka {amt} rupaye udhaar {first_overdue.days_overdue} din se baaki hai. Reminder bhejein."
        dues_action = InsightAction(label=f"Remind {first_overdue.customer_name}", action_type="send_due_reminder")
    else:
        dues_summary = repo.dues_summary(actual_merchant_id)
        tot = int(dues_summary.total_pending)
        dues_display = f"💳 Kul pending udhaar ₹{tot:,} hai ({dues_summary.num_pending_customers} customers)."
        dues_speech = f"Kul pending udhaar {tot} rupaye hai."
        dues_action = InsightAction(label="View Dues", action_type="view_dues")

    insights_data = [
        InsightItem(
            id="insight-stock",
            category="stock",
            title="Inventory Alert",
            display_text=stock_display,
            speech_text=stock_speech,
            audio_base64=synthesize_speech(stock_speech, language),
            action=stock_action,
        ),
        InsightItem(
            id="insight-sales",
            category="sales",
            title="Sales Trend & Anomaly",
            display_text=sales_display,
            speech_text=sales_speech,
            audio_base64=synthesize_speech(sales_speech, language),
            action=sales_action,
        ),
        InsightItem(
            id="insight-dues",
            category="dues",
            title="Overdue Dues Reminder",
            display_text=dues_display,
            speech_text=dues_speech,
            audio_base64=synthesize_speech(dues_speech, language),
            action=dues_action,
        ),
    ]

    return InsightsResponse(
        merchant_id=actual_merchant_id,
        shop_name=shop_name,
        insights=insights_data,
    )


@router.get(
    "/merchant/today",
    response_model=TodaySalesResponse,
    summary="Get today's sales metrics tile for header",
)
def get_merchant_today_sales(
    merchant_id: Optional[int] = Query(None, description="Merchant ID"),
    db: Session = Depends(get_db),
) -> TodaySalesResponse:
    settings = get_settings()
    actual_merchant_id = merchant_id or settings.merchant_id

    merchant = db.query(Merchant).filter(Merchant.id == actual_merchant_id).first()
    merchant_name = merchant.name if merchant else "Merchant"
    shop_name = merchant.shop_name if merchant else "Ramesh Kirana Store"

    repo = PostgresTransactionRepository(db)
    summary_1d = repo.sales_summary(actual_merchant_id, 1)
    summary_7d = repo.sales_summary(actual_merchant_id, 7)

    return TodaySalesResponse(
        merchant_id=actual_merchant_id,
        merchant_name=merchant_name,
        shop_name=shop_name,
        today_revenue=float(summary_1d.total_revenue),
        today_orders=summary_1d.num_orders,
        today_avg_order=float(summary_1d.avg_order_value),
        period_7d_revenue=float(summary_7d.total_revenue),
        is_premium_active=False,
    )
