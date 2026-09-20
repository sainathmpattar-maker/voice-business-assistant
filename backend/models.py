"""
models.py — SQLAlchemy ORM models for Polaris merchant schema.

Tables
------
merchants         — merchant profile
transactions      — each sale (payment recorded, Paytm-style)
transaction_items — line items per transaction (links to inventory)
inventory         — product catalogue with stock levels
customers         — known customer profiles
dues              — credit / udhaar ledger
stockout_events   — periods when an item had zero stock
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger, Column, Date, DateTime, Enum,
    ForeignKey, Integer, Numeric, String, Text,
)
from sqlalchemy.orm import relationship

from database import Base


# ── Enumerations ──────────────────────────────────────────────────────────────

class PaymentMode(str, enum.Enum):
    upi      = "upi"
    wallet   = "wallet"
    cash     = "cash"
    card     = "card"
    netbanking = "netbanking"
    bnpl     = "bnpl"


class TransactionStatus(str, enum.Enum):
    success  = "success"
    refunded = "refunded"
    failed   = "failed"


class DueStatus(str, enum.Enum):
    pending  = "pending"
    paid     = "paid"
    overdue  = "overdue"


# ── Merchant ──────────────────────────────────────────────────────────────────

class Merchant(Base):
    __tablename__ = "merchants"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(120), nullable=False)
    shop_name  = Column(String(120), nullable=False)
    city       = Column(String(80))
    phone      = Column(String(15), unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    transactions    = relationship("Transaction",    back_populates="merchant")
    inventory_items = relationship("Inventory",      back_populates="merchant")
    customers       = relationship("Customer",       back_populates="merchant")
    dues            = relationship("Due",            back_populates="merchant")
    stockout_events = relationship("StockoutEvent",  back_populates="merchant")


# ── Inventory (product catalogue + live stock) ────────────────────────────────

class Inventory(Base):
    """
    One row per SKU per merchant.
    unit_price is the current selling price.
    reorder_level triggers a low-stock alert.
    """
    __tablename__ = "inventory"

    id             = Column(Integer, primary_key=True, index=True)
    merchant_id    = Column(Integer, ForeignKey("merchants.id"), nullable=False, index=True)
    item           = Column(String(120), nullable=False)   # e.g. "Basmati Rice (5kg)"
    category       = Column(String(80))                    # e.g. "Grains"
    stock_qty      = Column(Integer, default=0)
    reorder_level  = Column(Integer, default=10)
    unit           = Column(String(20), default="kg")
    unit_price     = Column(Numeric(10, 2), nullable=False)

    merchant         = relationship("Merchant", back_populates="inventory_items")
    transaction_items = relationship("TransactionItem", back_populates="inventory_item")
    stockout_events  = relationship("StockoutEvent", back_populates="inventory_item")


# ── Customer ──────────────────────────────────────────────────────────────────

class Customer(Base):
    __tablename__ = "customers"

    id          = Column(Integer, primary_key=True, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False, index=True)
    phone       = Column(String(15))
    name        = Column(String(120), nullable=False)
    first_seen  = Column(DateTime, default=datetime.utcnow)
    last_seen   = Column(DateTime, default=datetime.utcnow)

    merchant = relationship("Merchant", back_populates="customers")
    dues     = relationship("Due",      back_populates="customer")


# ── Transaction (one sale / payment event) ────────────────────────────────────

class Transaction(Base):
    """
    Represents a single payment event — Paytm UPI, wallet, cash, card, etc.
    customer_name is denormalised for fast display (customer may be anonymous).
    """
    __tablename__ = "transactions"

    id            = Column(Integer, primary_key=True, index=True, autoincrement=True)
    merchant_id   = Column(Integer, ForeignKey("merchants.id"), nullable=False, index=True)
    customer_name = Column(String(120), default="Walk-in")   # denormalised
    amount        = Column(Numeric(12, 2), nullable=False)
    payment_mode  = Column(Enum(PaymentMode), nullable=False)
    status        = Column(Enum(TransactionStatus), default=TransactionStatus.success)
    created_at    = Column(DateTime, nullable=False, index=True)

    merchant = relationship("Merchant",         back_populates="transactions")
    items    = relationship("TransactionItem",  back_populates="transaction")


# ── Transaction Line Items ────────────────────────────────────────────────────

class TransactionItem(Base):
    __tablename__ = "transaction_items"

    id              = Column(Integer, primary_key=True, index=True, autoincrement=True)
    transaction_id  = Column(Integer,    ForeignKey("transactions.id"), nullable=False, index=True)
    inventory_id    = Column(Integer,    ForeignKey("inventory.id"),    nullable=False, index=True)
    item_category   = Column(String(80))        # denormalised from Inventory.category
    quantity        = Column(Integer, default=1)
    unit_price      = Column(Numeric(10, 2), nullable=False)

    transaction    = relationship("Transaction", back_populates="items")
    inventory_item = relationship("Inventory",   back_populates="transaction_items")


# ── Dues (Udhaar / credit ledger) ─────────────────────────────────────────────

class Due(Base):
    __tablename__ = "dues"

    id          = Column(Integer, primary_key=True, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    amount_due  = Column(Numeric(10, 2), nullable=False)
    due_date    = Column(DateTime)
    status      = Column(Enum(DueStatus), default=DueStatus.pending)
    note        = Column(Text)

    merchant = relationship("Merchant", back_populates="dues")
    customer = relationship("Customer", back_populates="dues")


# ── Stockout Events ───────────────────────────────────────────────────────────

class StockoutEvent(Base):
    """
    Records a window where stock_qty hit zero for an item.
    end_date=None means still out of stock.
    """
    __tablename__ = "stockout_events"

    id           = Column(Integer, primary_key=True, index=True)
    merchant_id  = Column(Integer, ForeignKey("merchants.id"), nullable=False, index=True)
    inventory_id = Column(Integer, ForeignKey("inventory.id"), nullable=False, index=True)
    start_date   = Column(Date, nullable=False)
    end_date     = Column(Date, nullable=True)   # NULL = currently out of stock
    note         = Column(String(200))

    merchant       = relationship("Merchant",  back_populates="stockout_events")
    inventory_item = relationship("Inventory", back_populates="stockout_events")
