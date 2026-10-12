"""Generates realistic synthetic vendor invoice PDFs for the demo and the evals.

Three different vendor layouts, Indian GST style, plus one *scanned* invoice (an image-only PDF with
a slight tilt and noise, no text layer) so the OCR path is exercised. All data is synthetic.

    python seed/make_pdfs.py      # writes seed/pdfs/*.pdf and seed/pdfs/labels.json

Needs `reportlab` and `Pillow` (dev only; the app itself only needs pypdf + the tesseract binary).
"""
from __future__ import annotations

import io
import json
import random
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT = Path(__file__).parent / "pdfs"
BUYER = ("Zenith Retail Pvt Ltd (demo buyer)", "Plot 12, Guindy Industrial Estate, Chennai 600032, Tamil Nadu", "33AABCZ4321P1Z4")


def money(x: Decimal) -> str:
    return f"{x:,.2f}"


def totals(lines, rate):
    sub = sum((Decimal(q) * Decimal(p) for _, _, _, q, p in lines), Decimal("0"))
    tax = (sub * Decimal(rate)).quantize(Decimal("0.01"))
    return sub, tax, sub + tax


def layout_classic(c: canvas.Canvas, d: dict) -> None:
    """Layout A: boxed 'TAX INVOICE', DD-MM-YYYY dates, HSN column, CGST/SGST or IGST summary."""
    W, H = A4
    c.setFont("Helvetica-Bold", 15)
    c.drawString(20 * mm, H - 22 * mm, d["vendor"])
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, H - 28 * mm, d["vendor_addr"])
    c.drawString(20 * mm, H - 33 * mm, f"GSTIN: {d['gstin']}    Phone: +91 44 4000 1234")
    c.setStrokeColor(colors.black)
    c.rect(140 * mm, H - 36 * mm, 50 * mm, 12 * mm)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(144 * mm, H - 32 * mm, d.get("title", "TAX INVOICE"))
    c.setFont("Helvetica", 9.5)
    y = H - 48 * mm
    for k, v in [("Invoice No", d["number"]), ("Invoice Date", d["date_dmy"]), ("PO Ref", d["po"]), ("Place of Supply", d["pos"])]:
        c.drawString(130 * mm, y, f"{k}: {v}")
        y -= 5 * mm
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(20 * mm, H - 48 * mm, "Bill To:")
    c.setFont("Helvetica", 9.5)
    c.drawString(20 * mm, H - 53 * mm, BUYER[0])
    c.drawString(20 * mm, H - 58 * mm, BUYER[1])
    c.drawString(20 * mm, H - 63 * mm, f"Buyer GSTIN: {BUYER[2]}")
    # table
    y = H - 78 * mm
    c.setFillColor(colors.HexColor("#eeeeee"))
    c.rect(20 * mm, y - 2 * mm, 170 * mm, 7 * mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9)
    cols = [20, 28, 55, 118, 133, 152, 170]
    for x, h in zip(cols, ["#", "Item Code", "Description", "HSN", "Qty", "Rate", "Amount"]):
        c.drawString(x * mm + 1, y, h)
    c.setFont("Helvetica", 9)
    for i, (sku, desc, hsn, q, p) in enumerate(d["lines"], start=1):
        y -= 7 * mm
        amt = Decimal(q) * Decimal(p)
        for x, v in zip(cols, [str(i), sku, desc, hsn, str(q), money(Decimal(p)), money(amt)]):
            c.drawString(x * mm + 1, y, v)
    sub, tax, tot = totals(d["lines"], d["rate"])
    y -= 12 * mm
    c.line(120 * mm, y + 8 * mm, 190 * mm, y + 8 * mm)
    rows = [("Taxable Value", money(sub))]
    pct = Decimal(d["rate"]) * 100
    if d["gst"] == "IGST":
        rows.append((f"IGST @ {pct:.0f}%", money(tax)))
    else:
        half = (tax / 2).quantize(Decimal("0.01"))
        rows += [(f"CGST @ {float(pct) / 2:g}%", money(half)), (f"SGST @ {float(pct) / 2:g}%", money(tax - half))]
    rows.append(("Grand Total INR", money(tot)))
    for k, v in rows:
        c.setFont("Helvetica-Bold" if k.startswith("Grand") else "Helvetica", 9.5)
        c.drawString(122 * mm, y, f"{k}:")
        c.drawRightString(189 * mm, y, v)
        y -= 6 * mm
    y -= 6 * mm
    c.setFont("Helvetica", 8.5)
    c.drawString(20 * mm, y, f"Bank: {d['bank_name']}   A/c No: {d['bank']}   IFSC: {d['ifsc']}")
    c.drawString(20 * mm, y - 5 * mm, "Payment terms: 30 days from invoice date. Subject to Chennai jurisdiction.")
    c.drawString(20 * mm, 20 * mm, "This is a computer generated invoice. Synthetic data for a case study.")


def layout_modern(c: canvas.Canvas, d: dict) -> None:
    """Layout B: logo bar, 'Bill #' / 'Dated' (ISO date), GST % per line, 'Purchase Order' wording."""
    W, H = A4
    c.setFillColor(colors.HexColor("#1f3a5f"))
    c.rect(0, H - 30 * mm, W, 30 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(18 * mm, H - 17 * mm, d["vendor"])
    c.setFont("Helvetica", 9)
    c.drawString(18 * mm, H - 24 * mm, f"{d['vendor_addr']}  |  GSTIN {d['gstin']}")
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(18 * mm, H - 45 * mm, "INVOICE")
    c.setFont("Helvetica", 10)
    c.drawString(120 * mm, H - 40 * mm, f"Bill # {d['number']}")
    c.drawString(120 * mm, H - 46 * mm, f"Dated: {d['date_iso']}")
    c.drawString(120 * mm, H - 52 * mm, f"Purchase Order: {d['po']}")
    c.drawString(18 * mm, H - 55 * mm, f"Customer: {BUYER[0]}, GSTIN {BUYER[2]}")
    y = H - 70 * mm
    c.setFont("Helvetica-Bold", 9.5)
    cols = [18, 26, 55, 125, 140, 160, 178]
    for x, h in zip(cols, ["Sr", "SKU", "Description", "Qty", "Unit Price", "GST%", "Amount"]):
        c.drawString(x * mm, y, h)
    c.line(18 * mm, y - 2 * mm, 195 * mm, y - 2 * mm)
    c.setFont("Helvetica", 9.5)
    pct = Decimal(d["rate"]) * 100
    for i, (sku, desc, _hsn, q, p) in enumerate(d["lines"], start=1):
        y -= 7 * mm
        for x, v in zip(cols, [str(i), sku, desc, str(q), money(Decimal(p)), f"{pct:.0f}%", money(Decimal(q) * Decimal(p))]):
            c.drawString(x * mm, y, v)
    sub, tax, tot = totals(d["lines"], d["rate"])
    y -= 14 * mm
    for k, v in [("Sub Total", money(sub)), (f"{'IGST' if d['gst'] == 'IGST' else 'CGST+SGST'} {pct:.0f}%", money(tax)), ("Amount Due (INR)", money(tot))]:
        c.setFont("Helvetica-Bold" if k.startswith("Amount") else "Helvetica", 10)
        c.drawString(125 * mm, y, f"{k}:")
        c.drawRightString(195 * mm, y, v)
        y -= 6 * mm
    c.setFont("Helvetica", 9)
    c.drawString(18 * mm, y - 10 * mm, f"Remit to: {d['bank_name']}, Account Number {d['bank']}, IFSC {d['ifsc']}")
    c.drawString(18 * mm, 18 * mm, "Thank you for your business. Synthetic data for a case study.")


SAMPLES = [
    {   # Happy path from a real PDF: clean text layer, intra-state CGST+SGST, full 3-way match.
        "file": "KOS-0160_kaveri_clean.pdf", "layout": layout_classic, "vendor": "Kaveri Office Solutions Pvt Ltd",
        "vendor_addr": "41 Anna Salai, Chennai 600002, Tamil Nadu", "gstin": "33AAKCK7788M1ZN", "number": "KOS-0160",
        "date_dmy": "10-10-2026", "date_iso": "2026-10-10", "po": "PO-1011", "pos": "Tamil Nadu (33)", "gst": "CGST_SGST", "rate": "0.18",
        "lines": [("PPR-A4-500", "A4 copier paper, ream of 500", "4802", 200, "260.00"), ("PEN-BLU-50", "Ball pens, box of 50", "9608", 50, "180.00")],
        "bank_name": "Canara Bank", "bank": "60221100998877", "ifsc": "CNRB0001234",
        "expected": {"decision": "APPROVE", "reason": None, "story": "Happy path from a real PDF: text extracted, matched to PO-1011 and GRN-5011, CGST+SGST correct for a Tamil Nadu supplier."},
    },
    {   # Same vendor family, scanned: image-only PDF -> OCR -> fields read but held for a human check.
        "file": "KOS-0161_kaveri_scanned.pdf", "layout": layout_classic, "scan": True, "vendor": "Kaveri Office Solutions Pvt Ltd",
        "vendor_addr": "41 Anna Salai, Chennai 600002, Tamil Nadu", "gstin": "33AAKCK7788M1ZN", "number": "KOS-0161",
        "date_dmy": "11-10-2026", "date_iso": "2026-10-11", "po": "PO-1014", "pos": "Tamil Nadu (33)", "gst": "CGST_SGST", "rate": "0.18",
        "lines": [("TNR-HP88", "Toner cartridge HP 88A", "8443", 30, "2900.00")],
        "bank_name": "Canara Bank", "bank": "60221100998877", "ifsc": "CNRB0001234",
        "expected": {"decision": "HOLD_FOR_REVIEW", "reason": "LOW_EXTRACTION_CONFIDENCE", "story": "Scanned paper invoice (no text layer): OCR reads every field and the match is clean, but OCR reads are never auto-approved in v1, so a person confirms the fields."},
    },
    {   # Different layout; GST head wrong (IGST on an intra-state supply).
        "file": "BL-5651_brightline_igst.pdf", "layout": layout_modern, "vendor": "Brightline Logistics LLP",
        "vendor_addr": "7 Harbour Road, Chennai 600001", "gstin": "33AAGFB5521K1ZN", "number": "BL-5651",
        "date_dmy": "10-10-2026", "date_iso": "2026-10-10", "po": "PO-1012", "pos": "Tamil Nadu (33)", "gst": "IGST", "rate": "0.12",
        "lines": [("FRT-CHN-CBE", "Freight trip Chennai-Coimbatore", "9965", 4, "15000.00")],
        "bank_name": "ICICI Bank", "bank": "39871200004411", "ifsc": "ICIC0000456",
        "expected": {"decision": "HOLD_FOR_REVIEW", "reason": "GST_TYPE_MISMATCH", "story": "Different vendor layout. Supplier and buyer are both in Tamil Nadu, so CGST+SGST applies, but the PDF charges IGST."},
    },
    {   # Inter-state, above the approval limit: everything matches, still needs a Finance Controller.
        "file": "DE-0413_deccan_high_value.pdf", "layout": layout_classic, "vendor": "Deccan Electricals Pvt Ltd",
        "vendor_addr": "88 Peenya Industrial Area, Bengaluru 560058, Karnataka", "gstin": "29AADCD4455E1ZP", "number": "DE/26-27/0413",
        "date_dmy": "09-10-2026", "date_iso": "2026-10-09", "po": "PO-1013", "pos": "Tamil Nadu (33)", "gst": "IGST", "rate": "0.18",
        "lines": [("HT-CBL-240", "HT cable 240 sq mm (metre)", "8544", 800, "720.00")],
        "bank_name": "HDFC Bank", "bank": "77450011223344", "ifsc": "HDFC0000789",
        "expected": {"decision": "HOLD_FOR_REVIEW", "reason": "APPROVAL_LIMIT", "story": "Inter-state supply (Karnataka to Tamil Nadu) with IGST, correct. Everything matches, but INR 6.8 lakh is above the INR 5 lakh auto-approval limit."},
    },
]


def render_page(d: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Invoice {d['number']}")
    d["layout"](c, d)
    c.showPage()
    c.save()
    return buf.getvalue()


def to_scanned(pdf_bytes: bytes) -> bytes:
    """Rasterise, tilt slightly, add speckle noise and greyness, and save as an image-only PDF (no text layer)."""
    from PIL import Image, ImageFilter

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.pdf"
        src.write_bytes(pdf_bytes)
        subprocess.run(["pdftoppm", "-r", "200", "-png", "-singlefile", str(src), str(Path(tmp) / "page")], check=True)
        img = Image.open(Path(tmp) / "page.png").convert("L")
    rnd = random.Random(42)
    img = img.rotate(0.8, expand=True, fillcolor=255, resample=Image.BICUBIC)
    px = img.load()
    w, h = img.size
    for _ in range(w * h // 400):
        x, y = rnd.randrange(w), rnd.randrange(h)
        px[x, y] = rnd.choice((90, 140, 200))
    img = img.filter(ImageFilter.GaussianBlur(0.6)).point(lambda v: min(255, int(v * 0.93 + 8)))
    out = io.BytesIO()
    img.convert("RGB").save(out, "PDF", resolution=200)
    return out.getvalue()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    labels = {}
    for d in SAMPLES:
        pdf = render_page(d)
        if d.get("scan"):
            pdf = to_scanned(pdf)
        (OUT / d["file"]).write_bytes(pdf)
        sub, tax, tot = totals(d["lines"], d["rate"])
        labels[d["file"]] = {**d["expected"], "fields": {
            "invoice_number": d["number"], "vendor_tax_id": d["gstin"], "invoice_date": d["date_iso"], "po_number": d["po"],
            "total": f"{tot:.2f}", "gst_type": d["gst"], "bank_account": d["bank"], "line_count": len(d["lines"])}}
        print("wrote", d["file"], len(pdf), "bytes")
    (OUT / "labels.json").write_text(json.dumps(labels, indent=2) + "\n")


if __name__ == "__main__":
    main()
