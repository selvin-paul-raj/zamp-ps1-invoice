"""Generates the synthetic seed set (vendors, POs, GRNs, paid history, labelled invoices).

All data is synthetic. Run `python seed/make_seed.py` to regenerate the JSON files.
Each invoice carries an `expected` label (decision + primary reason code) used by evals and tests.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).parent

VENDORS = [
    {"id": "V001", "name": "Acme Industrial Supplies Pvt Ltd", "tax_id": "29AABCA1234F1Z5", "currency": "INR", "bank_account": "50100012345678", "status": "active", "aliases": ["ACME IND"]},
    {"id": "V002", "name": "Brightline Logistics LLP", "tax_id": "33AAGFB5521K1ZN", "currency": "INR", "bank_account": "39871200004411", "status": "active", "aliases": []},
    {"id": "V003", "name": "Nordic Sensors AB", "tax_id": "SE556677889901", "currency": "EUR", "bank_account": "SE4550000000058398257466", "status": "active", "aliases": []},
    {"id": "V004", "name": "Pacific Packaging Co", "tax_id": "US-94-1234567", "currency": "USD", "bank_account": "021000021-778812", "status": "active", "aliases": []},
    {"id": "V005", "name": "Shadow Traders", "tax_id": "27AAQCS0001R1Z0", "currency": "INR", "bank_account": "11223344556677", "status": "blocked", "aliases": []},
    {"id": "V006", "name": "Kaveri Office Solutions Pvt Ltd", "tax_id": "33AAKCK7788M1ZN", "currency": "INR", "bank_account": "60221100998877", "status": "active", "aliases": []},
    {"id": "V007", "name": "Deccan Electricals Pvt Ltd", "tax_id": "29AADCD4455E1ZP", "currency": "INR", "bank_account": "77450011223344", "status": "active", "aliases": []},
]
# GSTINs above carry valid check characters (see app.domain.gstin_check_char). Buyer: 33AABCZ4321P1Z4 (Tamil Nadu).


def L(no, sku, desc, qty, price, tax):
    return {"line_no": no, "sku": sku, "description": desc, "quantity": qty, "unit_price": price, "tax_rate": tax}


POS = [
    {"po_number": "PO-1001", "vendor_id": "V001", "currency": "INR", "status": "open", "po_date": "2026-09-01",
     "lines": [L(1, "STL-ROD-12", "Steel rod 12mm", 500, "120.00", "0.18"), L(2, "BOLT-M10", "Hex bolt M10", 2000, "8.50", "0.18")]},
    {"po_number": "PO-1002", "vendor_id": "V002", "currency": "INR", "status": "open", "po_date": "2026-08-20",
     "lines": [L(1, "FRT-BLR-CHN", "Freight trip Bengaluru-Chennai", 20, "18000.00", "0.12")]},
    {"po_number": "PO-1003", "vendor_id": "V003", "currency": "EUR", "status": "open", "po_date": "2026-09-05",
     "lines": [L(1, "TMP-SNS-200", "Temperature sensor TS-200", 100, "42.00", "0")]},
    {"po_number": "PO-1004", "vendor_id": "V004", "currency": "USD", "status": "open", "po_date": "2026-09-10",
     "lines": [L(1, "BOX-CORR-L", "Corrugated box, large", 5000, "0.80", "0")]},
    {"po_number": "PO-1005", "vendor_id": "V001", "currency": "INR", "status": "open", "po_date": "2026-08-01",
     "lines": [L(1, "WLD-ROD-3", "Welding rod 3.15mm (kg)", 1000, "45.00", "0.18")]},
    {"po_number": "PO-1006", "vendor_id": "V006", "currency": "INR", "status": "open", "po_date": "2026-09-12",
     "lines": [L(1, "CHR-ERG", "Ergonomic chair", 20, "9500.00", "0.18"), L(2, "DSK-STD", "Standing desk", 10, "14000.00", "0.18")]},
    {"po_number": "PO-1007", "vendor_id": "V002", "currency": "INR", "status": "closed", "po_date": "2026-06-01",
     "lines": [L(1, "FRT-BLR-HYD", "Freight trip Bengaluru-Hyderabad", 5, "21000.00", "0.12")]},
    {"po_number": "PO-1008", "vendor_id": "V006", "currency": "INR", "status": "open", "po_date": "2026-09-20",
     "lines": [L(1, "TNR-HP26", "Toner cartridge HP 26A", 40, "3200.00", "0.18")]},
    {"po_number": "PO-1009", "vendor_id": "V001", "currency": "INR", "status": "open", "po_date": "2026-09-22",
     "lines": [L(1, "STL-ROD-12", "Steel rod 12mm", 100, "120.00", "0.18")]},
    {"po_number": "PO-1010", "vendor_id": "V004", "currency": "USD", "status": "open", "po_date": "2026-09-25",
     "lines": [L(1, "TAPE-48", "Packing tape 48mm", 600, "1.20", "0")]},
    # Used by the PDF samples (seed/pdfs) and the India-specific cases.
    {"po_number": "PO-1011", "vendor_id": "V006", "currency": "INR", "status": "open", "po_date": "2026-09-28",
     "lines": [L(1, "PPR-A4-500", "A4 copier paper, ream of 500", 200, "260.00", "0.18"), L(2, "PEN-BLU-50", "Ball pens, box of 50", 50, "180.00", "0.18")]},
    {"po_number": "PO-1012", "vendor_id": "V002", "currency": "INR", "status": "open", "po_date": "2026-09-26",
     "lines": [L(1, "FRT-CHN-CBE", "Freight trip Chennai-Coimbatore", 10, "15000.00", "0.12")]},
    {"po_number": "PO-1013", "vendor_id": "V007", "currency": "INR", "status": "open", "po_date": "2026-09-15",
     "lines": [L(1, "TRF-500KVA", "500 kVA distribution transformer", 1, "640000.00", "0.18"), L(2, "HT-CBL-240", "HT cable 240 sq mm (metre)", 800, "720.00", "0.18")]},
    {"po_number": "PO-1014", "vendor_id": "V006", "currency": "INR", "status": "open", "po_date": "2026-09-29",
     "lines": [L(1, "TNR-HP88", "Toner cartridge HP 88A", 30, "2900.00", "0.18")]},
]

GRNS = [
    {"grn_id": "GRN-5001", "po_number": "PO-1001", "received_date": "2026-09-20", "lines": [{"line_no": 1, "quantity": 500}, {"line_no": 2, "quantity": 2000}]},
    {"grn_id": "GRN-5002", "po_number": "PO-1002", "received_date": "2026-09-30", "lines": [{"line_no": 1, "quantity": 12}]},
    {"grn_id": "GRN-5003", "po_number": "PO-1003", "received_date": "2026-09-28", "lines": [{"line_no": 1, "quantity": 100}]},
    {"grn_id": "GRN-5004", "po_number": "PO-1004", "received_date": "2026-10-01", "lines": [{"line_no": 1, "quantity": 3000}]},
    {"grn_id": "GRN-5005", "po_number": "PO-1005", "received_date": "2026-09-15", "lines": [{"line_no": 1, "quantity": 1000}]},
    {"grn_id": "GRN-5006", "po_number": "PO-1006", "received_date": "2026-10-02", "lines": [{"line_no": 1, "quantity": 20}, {"line_no": 2, "quantity": 6}]},
    {"grn_id": "GRN-5008", "po_number": "PO-1008", "received_date": "2026-10-03", "lines": [{"line_no": 1, "quantity": 40}]},
    {"grn_id": "GRN-5009", "po_number": "PO-1009", "received_date": "2026-10-04", "lines": [{"line_no": 1, "quantity": 100}]},
    {"grn_id": "GRN-5010", "po_number": "PO-1010", "received_date": "2026-10-05", "lines": [{"line_no": 1, "quantity": 600}]},
    {"grn_id": "GRN-5011", "po_number": "PO-1011", "received_date": "2026-10-06", "lines": [{"line_no": 1, "quantity": 200}, {"line_no": 2, "quantity": 50}]},
    {"grn_id": "GRN-5012", "po_number": "PO-1012", "received_date": "2026-10-07", "lines": [{"line_no": 1, "quantity": 10}]},
    {"grn_id": "GRN-5013", "po_number": "PO-1013", "received_date": "2026-10-05", "lines": [{"line_no": 1, "quantity": 1}, {"line_no": 2, "quantity": 800}]},
    {"grn_id": "GRN-5014", "po_number": "PO-1014", "received_date": "2026-10-07", "lines": [{"line_no": 1, "quantity": 30}]},
]


def priced(lines):
    """Fill amount / tax per line and return (lines, subtotal, tax, total)."""
    sub = tax = Decimal("0")
    out = []
    for ln in lines:
        amt = Decimal(str(ln["quantity"])) * Decimal(ln["unit_price"])
        t = (amt * Decimal(ln["tax_rate"])).quantize(Decimal("0.01"))
        sub += amt
        tax += t
        out.append({**ln, "amount": f"{amt:.2f}", "tax_amount": f"{t:.2f}"})
    return out, f"{sub:.2f}", f"{tax:.2f}", f"{sub + tax:.2f}"


# Paid history (already in AP / ERP).
HISTORY = []
for ref, vid, num, po, date, lines in [
    ("HIST-0001", "V002", "BL-5521", "PO-1002", "2026-09-12", [L(1, "FRT-BLR-CHN", "Freight trip Bengaluru-Chennai", 4, "18000.00", "0.12")]),
    ("HIST-0002", "V001", "ACM-2201", "PO-1005", "2026-08-25", [L(1, "WLD-ROD-3", "Welding rod 3.15mm (kg)", 400, "45.00", "0.18")]),
    ("HIST-0003", "V001", "ACM-2219", "PO-1005", "2026-09-10", [L(1, "WLD-ROD-3", "Welding rod 3.15mm (kg)", 450, "45.00", "0.18")]),
]:
    pl, sub, tax, tot = priced(lines)
    HISTORY.append({"ref": ref, "vendor_id": vid, "invoice_number": num, "po_number": po, "invoice_date": date, "currency": "INR", "total": tot, "status": "paid", "lines": pl})


def inv(id_, received, vendor, number, date, currency, po, lines, expected, reason, *, tax_id=None, bank=None, notes="", source="email", override_totals=None, story="", **extra):
    pl, sub, tax, tot = priced(lines)
    if override_totals:
        sub, tax, tot = override_totals.get("subtotal", sub), override_totals.get("tax_total", tax), override_totals.get("total", tot)
    return {
        "id": id_, "received_at": received, "source": source, "vendor_name": vendor, "vendor_tax_id": tax_id,
        "invoice_number": number, "invoice_date": date, "currency": currency, "po_number": po,
        "bank_account": bank, "notes": notes, "lines": pl, "subtotal": sub, "tax_total": tax, "total": tot,
        "expected": {"decision": expected, "reason": reason, "story": story}, **extra,
    }


A, H, R = "APPROVE", "HOLD_FOR_REVIEW", "REJECT"
INVOICES = [
    inv("INV-01", "2026-10-05T09:10:00+05:30", "Acme Industrial Supplies Pvt Ltd", "ACM-2301", "2026-10-03", "INR", "PO-1001",
        [L(1, "STL-ROD-12", "Steel rod 12mm", 500, "120.00", "0.18"), L(2, "BOLT-M10", "Hex bolt M10", 2000, "8.50", "0.18")],
        A, None, tax_id="29AABCA1234F1Z5", bank="50100012345678", source="portal", story="Happy path: clean 3-way match on two lines."),
    inv("INV-02", "2026-10-05T10:00:00+05:30", "Brightline Logistics LLP", "BL-5602", "2026-10-02", "INR", "PO-1002",
        [L(1, "FRT-BLR-CHN", "Freight trip Bengaluru-Chennai", 3, "18000.00", "0.12")],
        A, None, bank="39871200004411", story="Second invoice on a PO already partly paid; cumulative 7 of 12 received."),
    inv("INV-03", "2026-10-05T11:30:00+05:30", "Nordic Sensors A.B.", "NS-2026-114", "2026-10-01", "EUR", "PO-1003",
        [L(1, "TMP-SNS-200", "Temperature sensor TS-200", 90, "42.50", "0")],
        A, None, story="Fuzzy vendor name; +1.19% price variance inside the 2% tolerance -> approve with lower confidence."),
    inv("INV-04", "2026-10-06T09:00:00+05:30", "Pacific Packaging Co", "PP-7781", "2026-10-04", "USD", "PO-1004",
        [L(1, "BOX-CORR-L", "Corrugated box, large", 3000, "0.80", "0")],
        A, None, story="Partial delivery billed correctly: 3,000 of 5,000 received and billed."),
    inv("INV-05", "2026-10-06T09:30:00+05:30", "Pacific Packaging Co", "PP-7790", "2026-10-05", "USD", "PO-1004",
        [L(1, "BOX-CORR-L", "Corrugated box, large", 2000, "0.80", "0")],
        H, "QTY_EXCEEDS_RECEIPT", story="Partial delivery: vendor bills the remaining 2,000 boxes that have not been received yet."),
    inv("INV-06", "2026-10-06T12:00:00+05:30", "Kaveri Office Solutions Pvt Ltd", "KOS-0144", "2026-10-05", "INR", "PO-1006",
        [L(1, "CHR-ERG", "Ergonomic chair", 20, "10450.00", "0.18")],
        H, "PRICE_VARIANCE", story="Price variance beyond tolerance: chairs billed 10% over PO price."),
    inv("INV-07", "2026-10-07T08:45:00+05:30", "ACME IND", "ACM/2301", "2026-10-03", "INR", "PO-1001",
        [L(1, "STL-ROD-12", "Steel rod 12mm", 500, "120.00", "0.18"), L(2, "BOLT-M10", "Hex bolt M10", 2000, "8.50", "0.18")],
        R, "DUPLICATE_INVOICE", tax_id="29AABCA1234F1Z5", notes="Reminder: payment pending", source="email",
        story="Duplicate invoice: INV-01 resent by email with a reformatted number."),
    inv("INV-08", "2026-10-07T10:15:00+05:30", "Brightline Logistics LLP", "5521-OPS", "2026-10-06", "INR", "PO-1002",
        [L(1, "FRT-BLR-CHN", "Freight trip Bengaluru-Chennai", 4, "18000.00", "0.12")],
        H, "POSSIBLE_DUPLICATE", story="Near-duplicate with reference drift: same amount as paid BL-5521 under a new number."),
    inv("INV-09", "2026-10-07T11:00:00+05:30", "Nordic Sensors AB", "NS-2026-120", "2026-10-06", "USD", "PO-1003",
        [L(1, "TMP-SNS-200", "Temperature sensor TS-200", 10, "42.00", "0")],
        H, "CURRENCY_MISMATCH", story="Currency mismatch: EUR PO billed in USD."),
    inv("INV-10", "2026-10-07T15:20:00+05:30", "Kaveri Office Solutions", "KOS-0150", "2026-10-06", "INR", None,
        [L(None, "TNR-HP26", "Toner cartridge HP 26A", 10, "3200.00", "0.18")],
        H, "PO_INFERRED", story="Missing PO reference: implied PO-1008 inferred from the vendor's open POs, needs confirmation."),
    inv("INV-11", "2026-10-08T09:05:00+05:30", "Brightline Logistics LLP", "BL-5630", "2026-10-07", "INR", "PO-9999",
        [L(1, "FRT-BLR-CHN", "Freight trip Bengaluru-Chennai", 1, "18000.00", "0.12")],
        H, "PO_NOT_FOUND", story="Missing PO: the referenced PO does not exist in the register."),
    inv("INV-12", "2026-10-08T10:40:00+05:30", "Kaveri Office Solutions Pvt Ltd", "KOS-0152", "2026-10-07", "INR", "PO-1006",
        [L(2, "DSK-STD", "Standing desk", 6, "14000.00", "0.28")],
        H, "TAX_RATE_MISMATCH", story="Tax mismatch: 28% GST charged where the PO says 18%."),
    inv("INV-13", "2026-10-08T12:00:00+05:30", "Acme Industrial Supplies Pvt Ltd", "ACM-2330", "2026-10-07", "INR", "PO-1005",
        [L(1, "WLD-ROD-3", "Welding rod 3.15mm (kg)", 200, "45.00", "0.18")],
        H, "PO_QTY_OVERRUN", tax_id="29AABCA1234F1Z5", story="Split-PO overrun: third invoice pushes cumulative 1,050 kg past the 1,000 kg PO."),
    inv("INV-14", "2026-10-08T16:30:00+05:30", "Acme Industrial Supplies Pvt Ltd", "ACM-2340", "2026-10-08", "INR", "PO-1009",
        [L(1, "STL-ROD-12", "Steel rod 12mm", 100, "120.00", "0.18")],
        H, "BANK_DETAILS_CHANGED", tax_id="29AABCA1234F1Z5", bank="91827364550011",
        notes="URGENT: we have changed our bank. Please remit to our new account immediately.",
        story="BEC fraud signal: everything matches, but remit-to account changed with urgency language."),
    inv("INV-15", "2026-10-09T09:00:00+05:30", "Shadow Traders", "ST-0042", "2026-10-08", "INR", None,
        [L(1, "MISC", "Consulting services", 1, "95000.00", "0.18")],
        R, "VENDOR_BLOCKED", story="Blocked vendor in the vendor master."),
    inv("INV-16", "2026-10-09T10:00:00+05:30", "Brightline Logistics LLP", "BL-5641", "2026-10-08", "INR", "PO-1007",
        [L(1, "FRT-BLR-HYD", "Freight trip Bengaluru-Hyderabad", 1, "21000.00", "0.12")],
        R, "PO_CLOSED", story="Invoice against a closed PO."),
    inv("INV-17", "2026-10-09T11:00:00+05:30", "Pacific Packaging Co", "PP-7802", "2026-10-08", "USD", "PO-1010",
        [L(1, "TAPE-48", "Packing tape 48mm", 600, "1.20", "0")],
        H, "ARITHMETIC_MISMATCH", override_totals={"total": "792.00"}, story="Totals don't add up: 600 x 1.20 = 720 but total says 792."),
    # ---- India / AP-policy edge cases (rules-v2)
    inv("INV-18", "Fri 2026-10-09 2:00 PM IST (UTC+05:30)", "Brightline Logistics LLP", "BL-5650", "2026-10-09", "INR", "PO-1012",
        [L(1, "FRT-CHN-CBE", "Freight trip Chennai-Coimbatore", 10, "15000.00", "0.12")],
        H, "GST_TYPE_MISMATCH", tax_id="33AAGFB5521K1ZN", bank="39871200004411", gst_type="IGST",
        story="GST head wrong: supplier and buyer are both in Tamil Nadu (state 33), so CGST+SGST applies, but the invoice charges IGST."),
    inv("INV-19", "Fri 2026-10-09 3:00 PM IST (UTC+05:30)", "Kaveri Office Solutions Pvt Ltd", "KOS-0158", "2026-10-09", "INR", "PO-1011",
        [L(2, "PEN-BLU-50", "Ball pens, box of 50", 20, "180.00", "0.18")],
        H, "INVALID_GSTIN", tax_id="33AAKCK7788M1ZM", bank="60221100998877", gst_type="CGST_SGST",
        story="GSTIN typo: last character wrong, so the GST checksum fails. Input tax credit at risk until the registration is verified."),
    inv("INV-20", "Fri 2026-10-09 4:00 PM IST (UTC+05:30)", "Deccan Electricals Pvt Ltd", "DE/26-27/0412", "2026-10-08", "INR", "PO-1013",
        [L(1, "TRF-500KVA", "500 kVA distribution transformer", 1, "640000.00", "0.18")],
        H, "APPROVAL_LIMIT", tax_id="29AADCD4455E1ZP", bank="77450011223344", gst_type="IGST",
        story="Approval matrix: every check passes (inter-state IGST correct), but INR 7.55 lakh is above the INR 5 lakh auto-approval limit, so a Finance Controller signs off."),
    inv("INV-21", "Sat 2026-10-10 10:00 AM IST (UTC+05:30)", "Acme Industrial Supplies Pvt Ltd", "ACM-CN-018", "2026-10-09", "INR", "PO-1005",
        [L(1, "WLD-ROD-3", "Welding rod 3.15mm (kg) - returned, damaged", 20, "45.00", "0.18")],
        H, "CREDIT_NOTE_TO_APPLY", tax_id="29AABCA1234F1Z5", doc_type="credit_note", reference_invoice="ACM-2201",
        story="Credit note for 20 kg returned against paid invoice ACM-2201: matched and queued to be applied, never paid out."),
    inv("INV-22", "Sat 2026-10-10 11:00 AM IST (UTC+05:30)", "Kaveri Office Solutions Pvt Ltd", "KOS-PF-031", "2026-10-09", "INR", "PO-1014",
        [L(1, "TNR-HP88", "Toner cartridge HP 88A", 30, "2900.00", "0.18")],
        H, "UNSUPPORTED_DOC_TYPE", tax_id="33AAKCK7788M1ZN", doc_type="proforma",
        story="A proforma (advance quote) sent to the AP inbox: everything matches, but it is not a tax invoice, so nothing is paid on it."),
]


def main():
    for name, data in [("vendors", VENDORS), ("purchase_orders", POS), ("grns", GRNS), ("invoice_history", HISTORY), ("invoices", INVOICES)]:
        (HERE / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {len(INVOICES)} invoices, {len(POS)} POs, {len(GRNS)} GRNs, {len(VENDORS)} vendors, {len(HISTORY)} history")


if __name__ == "__main__":
    main()
