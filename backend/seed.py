"""
seed.py — Deterministic seed for Polaris demo data.

Merchant 1: Ramesh Kirana Store, Mumbai  (PRIMARY demo merchant)
Merchant 2: Sunita General Store, Pune   (benchmarking / comparison)

PLANTED PATTERNS (Merchant 1)
──────────────────────────────
1. Last 7 days revenue ≈ ₹18,400  (~12 % below the prior 7 days ≈ ₹20,910)
2. Rice (chawal) was out of stock last Tuesday; that day's revenue dropped ~40 %
3. 6 customers have pending dues; 1 is overdue 30+ days
4. Weekend sales are ~35 % higher than weekdays
5. Top items by revenue: Rice, Atta, Oil, Sugar, Tea
6. UPI ≈ 60 %, Wallet ≈ 10 %, Cash ≈ 20 %, Card ≈ 10 %

Run:  python seed.py
Safe: idempotent — skips if merchant_id=1 already exists.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from decimal import Decimal

import database as _db
from models import (
    Base, Customer, Due, DueStatus, Inventory, Merchant,
    PaymentMode, StockoutEvent, Transaction, TransactionItem, TransactionStatus,
)

# ── Reproducible randomness ───────────────────────────────────────────────────
RNG = random.Random(42)

# ── Reference point: today 00:00 UTC ─────────────────────────────────────────
TODAY = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
# "Last Tuesday" = most recent Tuesday before today
def _last_tuesday() -> date:
    d = TODAY.date()
    # weekday(): Mon=0 … Sun=6  → Tue=1
    days_since_tue = (d.weekday() - 1) % 7 or 7   # at least 1 day back
    return d - timedelta(days=days_since_tue)

LAST_TUESDAY = _last_tuesday()

# ── Catalogue ─────────────────────────────────────────────────────────────────
# (item, category, unit, unit_price, reorder_level, current_stock)
M1_CATALOGUE = [
    ("Basmati Rice (5 kg)",   "Grains",       "bag",    Decimal("320.00"), 10,  8),   # low stock
    ("Whole Wheat Atta (5 kg)","Grains",       "bag",    Decimal("250.00"), 10, 25),
    ("Refined Oil (1 L)",     "Oils & Fats",  "bottle", Decimal("155.00"), 12, 18),
    ("Sugar (1 kg)",          "Staples",      "packet", Decimal("48.00"),  20, 30),
    ("Tea Leaves (250 g)",    "Beverages",    "packet", Decimal("95.00"),  15, 22),
    ("Toor Dal (1 kg)",       "Pulses",       "packet", Decimal("140.00"), 10, 14),
    ("Masoor Dal (1 kg)",     "Pulses",       "packet", Decimal("120.00"), 10,  6),   # low stock
    ("Salt (1 kg)",           "Staples",      "packet", Decimal("20.00"),  20, 50),
    ("Biscuits (pack of 12)", "Snacks",       "pack",   Decimal("60.00"),  20, 35),
    ("Detergent (1 kg)",      "Household",    "packet", Decimal("185.00"),  8, 12),
    ("Soap (pack of 4)",      "Personal Care","pack",   Decimal("80.00"),  10,  4),   # low stock
    ("Instant Noodles (12×)", "Snacks",       "pack",   Decimal("120.00"), 15, 28),
]

M2_CATALOGUE = [
    ("Basmati Rice (5 kg)",   "Grains",       "bag",    Decimal("315.00"), 10, 20),
    ("Whole Wheat Atta (5 kg)","Grains",       "bag",    Decimal("245.00"), 10, 18),
    ("Refined Oil (1 L)",     "Oils & Fats",  "bottle", Decimal("150.00"), 12, 30),
    ("Sugar (1 kg)",          "Staples",      "packet", Decimal("46.00"),  20, 40),
    ("Tea Leaves (250 g)",    "Beverages",    "packet", Decimal("90.00"),  15, 25),
    ("Toor Dal (1 kg)",       "Pulses",       "packet", Decimal("135.00"), 10, 10),
    ("Poha (500 g)",          "Grains",       "packet", Decimal("35.00"),  10, 20),
    ("Groundnut Oil (1 L)",   "Oils & Fats",  "bottle", Decimal("165.00"), 8,  15),
]

# ── Customer tables ───────────────────────────────────────────────────────────
M1_CUSTOMERS = [
    ("Suresh Kumar",   "9876543210"),
    ("Anita Sharma",   "9123456780"),
    ("Ravi Patel",     "9988776655"),
    ("Meena Iyer",     "9765432109"),
    ("Arjun Singh",    "9871234560"),
    ("Kavita Desai",   "9654321098"),
    ("Mohan Tiwari",   "9812345670"),
]

# ── Due amounts (planted) ─────────────────────────────────────────────────────
# 6 pending; Suresh Kumar's due is 32 days old → overdue
M1_DUES = [
    # (customer_idx, amount, days_ago_created, status)
    (0, Decimal("1850.00"), 45, DueStatus.overdue),   # Suresh — due_date = 45-14=31 days ago → overdue
    (1, Decimal("620.00"),  10, DueStatus.pending),
    (2, Decimal("1200.00"),  5, DueStatus.pending),
    (3, Decimal("450.00"),   8, DueStatus.pending),
    (4, Decimal("980.00"),  15, DueStatus.pending),
    (5, Decimal("330.00"),   3, DueStatus.pending),
]

# Item selection weights — makes Rice, Atta, Oil, Sugar, Tea appear most often
ITEM_WEIGHTS = [
    0.18,  # Rice
    0.16,  # Atta
    0.14,  # Oil
    0.13,  # Sugar  ← boosted
    0.12,  # Tea    ← boosted
    0.08,  # Toor Dal
    0.05,  # Masoor Dal
    0.04,  # Salt
    0.04,  # Biscuits
    0.03,  # Detergent
    0.02,  # Soap
    0.01,  # Noodles
]
PAYMENT_MODES  = [PaymentMode.upi, PaymentMode.wallet, PaymentMode.cash, PaymentMode.card]
PAYMENT_WEIGHTS = [0.60, 0.10, 0.20, 0.10]

def _pick_payment() -> PaymentMode:
    return RNG.choices(PAYMENT_MODES, weights=PAYMENT_WEIGHTS, k=1)[0]

def _pick_status() -> TransactionStatus:
    return (TransactionStatus.refunded if RNG.random() < 0.025
            else TransactionStatus.success)

# ── Revenue targets ──────────────────────────────────────────────────────────
# We want:
#   days -7..-1  (last 7)  ≈ 18,400  → target ~18,400
#   days -14..-8 (prior 7) ≈ 20,910  → 18400 / 0.88 ≈ 20,909
# We'll drive this by scaling daily order counts in those windows.

TARGET_LAST7    = 18_400.0     # ₹
TARGET_PRIOR7   = 20_910.0     # ₹  (≈ 12 % higher)
TARGET_DAILY_BASE = 2_100.0    # ₹ for a normal weekday (days -90..-15)

WEEKDAY_MULT = {0: 1.0, 1: 1.0, 2: 0.95, 3: 1.0, 4: 1.05, 5: 1.35, 6: 1.30}

# Items that appear in rice-related transactions (for stockout impact)
RICE_IDX = 0  # index in M1_CATALOGUE

def _build_transactions_m1(merchant: Merchant, inventory: list[Inventory],
                            customers: list[Customer]) -> list[Transaction]:
    """
    Build 90 days of transactions with the planted patterns baked in.
    Returns a list of Transaction objects (not yet flushed).
    """
    all_txns: list[Transaction] = []
    item_prices = {inv.id: inv.unit_price for inv in inventory}
    rice_inv_id = inventory[RICE_IDX].id

    for day_offset in range(90, -1, -1):   # 90 days ago → today (inclusive)
        tx_date = TODAY - timedelta(days=day_offset)
        day_date = tx_date.date()
        wday = tx_date.weekday()           # 0=Mon … 6=Sun
        is_tuesday = (wday == 1)
        is_weekend = (wday >= 5)

        # ── Determine target revenue for this day ─────────────────────────────
        if 1 <= day_offset <= 7:           # "last 7 days"
            daily_target = TARGET_LAST7 / 7
        elif 8 <= day_offset <= 14:        # "prior 7 days"
            daily_target = TARGET_PRIOR7 / 7
        else:
            daily_target = TARGET_DAILY_BASE * WEEKDAY_MULT[wday]

        # Rice stockout: last Tuesday → 40 % revenue drop
        is_stockout_day = (day_date == LAST_TUESDAY)
        if is_stockout_day:
            daily_target *= 0.60

        # ── Generate transactions for the day ─────────────────────────────────
        avg_basket = 520.0   # weighted avg basket: ~2 items × 1.5 qty × ₹175 avg price
        n_orders = max(3, int(daily_target / avg_basket))
        actual_revenue = 0.0

        for _ in range(n_orders):
            tx_hour = RNG.randint(8, 21)
            tx_time = tx_date.replace(
                hour=tx_hour,
                minute=RNG.randint(0, 59),
                second=RNG.randint(0, 59),
            )
            # Pick 1–3 items using weighted selection (favours top items)
            available = [inv for inv in inventory
                         if not (is_stockout_day and inv.id == rice_inv_id)]
            avail_weights = [ITEM_WEIGHTS[i] for i, inv in enumerate(inventory)
                             if not (is_stockout_day and inv.id == rice_inv_id)]
            n_items = RNG.randint(1, 3)
            chosen  = RNG.choices(available, weights=avail_weights, k=n_items)
            chosen  = list(dict.fromkeys(chosen))  # deduplicate preserving order

            items_data: list[tuple[Inventory, int]] = []
            basket_total = Decimal("0")
            for inv_item in chosen:
                qty = RNG.randint(1, 3)
                basket_total += inv_item.unit_price * qty
                items_data.append((inv_item, qty))

            # random customer name (30 % known, 70 % walk-in)
            if RNG.random() < 0.30 and customers:
                cname = RNG.choice(customers).name
            else:
                cname = "Walk-in"

            payment  = _pick_payment()
            status   = _pick_status()

            txn = Transaction(
                merchant_id=merchant.id,
                customer_name=cname,
                amount=basket_total,
                payment_mode=payment,
                status=status,
                created_at=tx_time,
            )
            txn._items_data = items_data  # type: ignore[attr-defined]
            all_txns.append(txn)
            actual_revenue += float(basket_total)

        # Small correction: if we drifted far from target, add/remove one txn
        diff = daily_target - actual_revenue
        if abs(diff) > 200 and diff > 0:
            # add a small correction txn
            inv_item = RNG.choice(inventory)
            correction = max(Decimal("50"), Decimal(str(round(diff, 2))))
            tx_time = tx_date.replace(hour=12, minute=0)
            txn = Transaction(
                merchant_id=merchant.id,
                customer_name="Walk-in",
                amount=correction,
                payment_mode=PaymentMode.upi,
                status=TransactionStatus.success,
                created_at=tx_time,
            )
            txn._items_data = [(inv_item, 1)]  # type: ignore[attr-defined]
            all_txns.append(txn)

    return all_txns


# ── Main seed ─────────────────────────────────────────────────────────────────

def seed() -> None:
    if _db.engine is None:
        _db.init_engine()
    Base.metadata.create_all(bind=_db.engine)
    db = _db.SessionLocal()
    try:
        if db.query(Merchant).filter(Merchant.id == 1).first():
            print("[OK] Seed already present -- skipping.")
            return

        # -- Merchant 1: Ramesh Kirana Store ----------------------------------
        print("[1] Creating Merchant 1: Ramesh Kirana Store ...")
        m1 = Merchant(id=1, name="Ramesh Gupta", shop_name="Ramesh Kirana Store",
                      city="Mumbai", phone="9000000001")
        db.add(m1)
        db.flush()

        # Inventory
        m1_inv: list[Inventory] = []
        for item, cat, unit, price, reorder, stock in M1_CATALOGUE:
            inv = Inventory(merchant_id=m1.id, item=item, category=cat,
                            unit=unit, unit_price=price,
                            reorder_level=reorder, stock_qty=stock)
            db.add(inv)
            m1_inv.append(inv)
        db.flush()

        # Customers
        m1_custs: list[Customer] = []
        for name, phone in M1_CUSTOMERS:
            c = Customer(merchant_id=m1.id, name=name, phone=phone,
                         first_seen=TODAY - timedelta(days=RNG.randint(30, 90)),
                         last_seen=TODAY - timedelta(days=RNG.randint(0, 7)))
            db.add(c)
            m1_custs.append(c)
        db.flush()

        # Transactions
        print("  Generating 90+ days of transactions ...")
        txns = _build_transactions_m1(m1, m1_inv, m1_custs)
        for txn in txns:
            db.add(txn)
        db.flush()

        # Transaction items
        for txn in txns:
            items_data = getattr(txn, "_items_data", [])
            for inv_item, qty in items_data:
                db.add(TransactionItem(
                    transaction_id=txn.id,
                    inventory_id=inv_item.id,
                    item_category=inv_item.category,
                    quantity=qty,
                    unit_price=inv_item.unit_price,
                ))

        # Dues (6 customers)
        print("  Seeding dues ...")
        for cust_idx, amount, days_ago, status in M1_DUES:
            created = TODAY - timedelta(days=days_ago)
            due_date = created + timedelta(days=14)
            db.add(Due(
                merchant_id=m1.id,
                customer_id=m1_custs[cust_idx].id,
                amount_due=amount,
                due_date=due_date,
                status=status,
                note=f"Grocery credit — {m1_custs[cust_idx].name}",
            ))

        # Stockout event: Rice was out last Tuesday
        rice_inv = m1_inv[RICE_IDX]
        db.add(StockoutEvent(
            merchant_id=m1.id,
            inventory_id=rice_inv.id,
            start_date=LAST_TUESDAY,
            end_date=LAST_TUESDAY + timedelta(days=1),
            note="Supplier delivery delayed — restocked next day",
        ))

        # -- Merchant 2: Sunita General Store --------------------------------
        print("[2] Creating Merchant 2: Sunita General Store ...")
        m2 = Merchant(id=2, name="Sunita Rao", shop_name="Sunita General Store",
                      city="Pune", phone="9000000002")
        db.add(m2)
        db.flush()

        m2_inv: list[Inventory] = []
        for item, cat, unit, price, reorder, stock in M2_CATALOGUE:
            inv = Inventory(merchant_id=m2.id, item=item, category=cat,
                            unit=unit, unit_price=price,
                            reorder_level=reorder, stock_qty=stock)
            db.add(inv)
            m2_inv.append(inv)
        db.flush()

        # M2: steady growth — last 7 days ~5 % higher than prior 7
        m2_rng = random.Random(99)
        for day_offset in range(90, 0, -1):
            tx_date = TODAY - timedelta(days=day_offset)
            wday = tx_date.weekday()
            if 1 <= day_offset <= 7:
                daily_target = 15_000 / 7
            elif 8 <= day_offset <= 14:
                daily_target = 14_300 / 7
            else:
                daily_target = 1_800 * {0:1,1:1,2:.95,3:1,4:1.05,5:1.25,6:1.2}[wday]

            n_orders = max(4, int(daily_target / 280))
            for _ in range(n_orders):
                chosen = m2_rng.sample(m2_inv, k=m2_rng.randint(1, 3))
                basket = sum(i.unit_price * m2_rng.randint(1, 2) for i in chosen)
                db.add(Transaction(
                    merchant_id=m2.id,
                    customer_name="Walk-in",
                    amount=basket,
                    payment_mode=m2_rng.choices(PAYMENT_MODES, weights=PAYMENT_WEIGHTS, k=1)[0],
                    status=TransactionStatus.success,
                    created_at=tx_date.replace(
                        hour=m2_rng.randint(9, 20),
                        minute=m2_rng.randint(0, 59),
                    ),
                ))

        db.commit()
        print("\n[DONE] Seed complete!")
        print(f"   Merchant 1: {m1.shop_name} ({m1.city})")
        print(f"   Merchant 2: {m2.shop_name} ({m2.city})")
        print(f"   Transactions: ~{len(txns)} (M1) + M2 auto-generated")
        print(f"   Rice stockout planted: {LAST_TUESDAY}")
        print("   Last-Tuesday sales gap: ~40% drop expected")

    except Exception as exc:
        db.rollback()
        raise exc
    finally:
        db.close()


if __name__ == "__main__":
    seed()
