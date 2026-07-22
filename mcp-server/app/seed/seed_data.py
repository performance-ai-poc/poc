"""Deterministic seed data.

Fixed primary keys everywhere so results (row counts, joins, retrieval IDs) are
stable across seed runs and safe to assert on in tests.

One intentional exception: ``orders.created_at`` is expressed as an *age in
days* rather than an absolute timestamp, and ``build_seed.py`` inserts it as
``now() - make_interval(days => age_days)``. That keeps "failed orders from last
week" meaningful relative to whenever the demo runs, while the *set* of failed
orders (their IDs, customers, totals) stays fixed. Three orders are failed
*within* the last 7 days (1001/1002/1003) and one is failed but older than a
week (1009) — so a ``created_at >= now() - interval '7 days'`` filter returns
exactly three, deterministically.

No secrets or personal data: customers are organizations, tracking numbers are
synthetic, documents are generic policy/FAQ text.
"""

from __future__ import annotations

# (id, name, region, age_days)
CUSTOMERS = [
    (1, "Acme Robotics", "us-west", 400),
    (2, "Globex Logistics", "us-east", 380),
    (3, "Initech Retail", "eu-central", 350),
    (4, "Umbrella Foods", "us-central", 300),
    (5, "Soylent Grocers", "ap-south", 260),
]

# (id, customer_id, status, total_cents, age_days)
ORDERS = [
    (1001, 1, "failed", 12999, 2),      # failed, last week
    (1002, 2, "failed", 4500, 4),       # failed, last week
    (1003, 3, "failed", 78900, 6),      # failed, last week
    (1004, 1, "delivered", 3200, 12),
    (1005, 2, "shipped", 15000, 9),
    (1006, 4, "processing", 8800, 3),
    (1007, 5, "delivered", 2500, 20),
    (1008, 3, "shipped", 61000, 15),
    (1009, 4, "failed", 9900, 30),      # failed, but OLDER than a week
    (1010, 5, "processing", 1800, 1),
    (1011, 1, "shipped", 45000, 8),
    (1012, 2, "delivered", 7300, 25),
]

# (id, order_id, carrier, tracking_number, status, age_days)
SHIPMENTS = [
    (2001, 1001, "UPS", "1Z-ACME-0001", "exception", 1),
    (2002, 1002, "FedEx", "FX-GLOBEX-0002", "lost", 3),
    (2003, 1003, "DHL", "DHL-INITECH-0003", "delayed", 5),
    (2004, 1004, "UPS", "1Z-ACME-0004", "delivered", 11),
    (2005, 1005, "FedEx", "FX-GLOBEX-0005", "in_transit", 7),
    (2006, 1008, "DHL", "DHL-INITECH-0008", "in_transit", 13),
    (2007, 1009, "UPS", "1Z-UMBRELLA-0009", "exception", 28),
    (2008, 1011, "FedEx", "FX-ACME-0011", "in_transit", 6),
]


def _docs() -> list[tuple[str, str, list[str]]]:
    """Return (doc_id, title, [chunk_text, ...]) tuples.

    doc_007 is the escalation policy the demo scenario searches for; its first
    chunk is written to rank first for "escalation policy for failed orders".
    """
    docs: list[tuple[str, str, list[str]]] = [
        ("doc_001", "Shipping Policy", [
            "Standard orders ship within two business days. Expedited orders ship same day when placed before noon.",
            "Carriers are selected automatically by destination region. Tracking numbers are issued at handoff to the carrier.",
        ]),
        ("doc_002", "Returns Policy", [
            "Customers may return unopened goods within 30 days for a full refund. Opened goods are assessed case by case.",
        ]),
        ("doc_003", "Refund Policy", [
            "Approved refunds are issued to the original payment method within five to seven business days.",
        ]),
        ("doc_004", "SLA Overview", [
            "Support acknowledges new tickets within four business hours. Priority tickets are acknowledged within one hour.",
            "An incident is considered resolved once the customer confirms the issue no longer occurs.",
        ]),
        ("doc_005", "Data Retention", [
            "Operational logs are retained for 90 days. Order records are retained for seven years for accounting purposes.",
        ]),
        ("doc_006", "Carrier Exceptions", [
            "A carrier exception means the shipment could not proceed as planned, for example a failed delivery attempt or a lost parcel.",
            "Exceptions should be reconciled daily against the shipments table and open exceptions flagged to the fulfillment team.",
        ]),
        ("doc_007", "Escalation Policy", [
            "Escalation policy for failed orders: when an order is marked failed, the on-call operator must escalate to the "
            "fulfillment lead within 24 hours. Escalation is mandatory for any failed order whose shipment is in an exception, "
            "lost, or delayed state. Record the escalation, notify the carrier, and open a follow-up ticket.",
            "Escalations are reviewed weekly. Repeated failed orders for the same customer trigger a second-tier escalation to "
            "the account manager, who decides whether to expedite a replacement or issue a refund.",
            "If a failed order is not escalated within the 24 hour window, the incident is auto-escalated and the on-call "
            "operator's manager is notified.",
        ]),
        ("doc_008", "Order Lifecycle", [
            "An order moves through processing, shipped, and delivered. An order that cannot be fulfilled is marked failed.",
            "A failed order retains its history so it can be audited, reprocessed, or refunded.",
        ]),
        ("doc_009", "Customer Communication", [
            "Notify customers proactively when a shipment is delayed. Use the customer's preferred channel where known.",
        ]),
        ("doc_010", "Fulfillment Runbook", [
            "The fulfillment team reviews failed orders each morning, checks the associated shipment status, and follows the "
            "escalation policy where required.",
        ]),
        ("doc_011", "Warehouse Safety", [
            "Aisles must remain clear. Report spills immediately. Forklift operation requires current certification.",
        ]),
        ("doc_012", "Payment Processing", [
            "Payments are authorized at checkout and captured at shipment. A capture failure marks the order failed.",
        ]),
        ("doc_013", "Inventory Counts", [
            "Cycle counts run weekly. Discrepancies over one percent are investigated before the next replenishment.",
        ]),
        ("doc_014", "Tax Guidance", [
            "Tax is calculated by destination. Exempt customers must have a valid certificate on file before checkout.",
        ]),
        ("doc_015", "Onboarding Guide", [
            "New operators complete the fulfillment runbook, the escalation policy, and a shadowing shift before going live.",
        ]),
    ]
    # Pad to 30 documents with generic single-chunk FAQ entries so full-text
    # search has realistic breadth to rank against. Deterministic content.
    faq_topics = [
        "Address Changes", "Gift Wrapping", "Bulk Orders", "Loyalty Program", "Promo Codes",
        "Backorders", "International Shipping", "Packaging Standards", "Damaged Goods", "Invoice Requests",
        "Account Security", "Newsletter Preferences", "Store Hours", "Contact Channels", "Accessibility",
    ]
    for i, topic in enumerate(faq_topics, start=16):
        doc_id = f"doc_{i:03d}"
        text = (
            f"{topic}: this FAQ entry explains how {topic.lower()} are handled. "
            f"Contact support for anything this document does not cover."
        )
        docs.append((doc_id, topic, [text]))
    return docs


def documents() -> list[tuple[str, str, str, str]]:
    """Flatten docs into (chunk_id, doc_id, title, text) rows with stable chunk IDs."""
    rows: list[tuple[str, str, str, str]] = []
    for doc_id, title, chunks in _docs():
        for n, text in enumerate(chunks, start=1):
            rows.append((f"{doc_id}#chunk_{n}", doc_id, title, text))
    return rows
