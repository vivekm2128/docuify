#!/usr/bin/env python3
"""Generate the sample documents used to test Docufy end to end.

Pure stdlib + reportlab-free: writes PDFs directly, so there is nothing extra
to install. Also renders the "messy scanned" loan application as a noisy,
skewed image PDF so OCR has something realistic to struggle with.

    python tools/make_samples.py            # writes into samples/
"""

from __future__ import annotations

import random
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "samples"

# ---------------------------------------------------------------- pdf writer


class Pdf:
    """Minimal single/multi-page PDF writer (Helvetica, text + rules)."""

    def __init__(self, width: float = 612, height: float = 792) -> None:
        self.width = width
        self.height = height
        self.ops: list[str] = []

    # y is measured from the top, like a web page.
    def _y(self, y: float) -> float:
        return self.height - y

    def text(self, x: float, y: float, s: str, size: float = 10, bold: bool = False,
             gray: float = 0.0) -> "Pdf":
        font = "F2" if bold else "F1"
        esc = s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        # Tc adds a hair of tracking: without it the OCR detector merges whole
        # runs of text into one box and loses the word boundaries.
        self.ops.append(
            f"BT /{font} {size} Tf {size * 0.04:.2f} Tc {gray:.2f} g "
            f"1 0 0 1 {x:.2f} {self._y(y):.2f} Tm ({esc}) Tj ET"
        )
        return self

    def right(self, xr: float, y: float, s: str, size: float = 10, bold: bool = False,
              gray: float = 0.0) -> "Pdf":
        # Rough Helvetica advance widths: enough to right-align money columns.
        w = len(s) * size * (0.5 if bold else 0.48)
        return self.text(xr - w, y, s, size, bold, gray)

    def line(self, x1: float, y1: float, x2: float, y2: float, gray: float = 7.0,
             width: float = 0.7) -> "Pdf":
        self.ops.append(
            f"{gray:.2f} G {width} w {x1:.2f} {self._y(y1):.2f} m {x2:.2f} {self._y(y2):.2f} l S"
        )
        return self

    def box(self, x: float, y: float, w: float, h: float, gray: float = 0.75) -> "Pdf":
        self.ops.append(
            f"{gray:.2f} G 0.7 w {x:.2f} {self._y(y + h):.2f} {w:.2f} {h:.2f} re S"
        )
        return self

    def fill(self, x: float, y: float, w: float, h: float, gray: float = 0.10) -> "Pdf":
        self.ops.append(f"{gray:.2f} g {x:.2f} {self._y(y + h):.2f} {w:.2f} {h:.2f} re f 0 g")
        return self

    def save(self, path: Path) -> Path:
        content = "\n".join(self.ops).encode("latin-1", "replace")
        cells = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
             b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>"
             % (int(self.width), int(self.height))),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        ]
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for i, body in enumerate(cells, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(cells) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                % (len(cells) + 1, xref))
        path.write_bytes(bytes(out))
        return path


# ---------------------------------------------------------------- documents


def invoice(path: Path) -> Path:
    p = Pdf()
    p.fill(0, 0, 612, 6, 0.20)
    p.text(48, 62, "NORTHWIND TRADING CO.", 20, bold=True)
    p.text(48, 80, "4100 Harbor Blvd, Suite 220", 9, gray=0.35)
    p.text(48, 94, "Long Beach, CA 90802", 9, gray=0.35)
    p.text(48, 108, "EIN 84-2910077  ·  accounts@northwindtrading.example", 9, gray=0.35)

    p.right(564, 66, "INVOICE", 22, bold=True)
    p.right(564, 88, "Invoice No: INV-2026-1042", 10)
    p.right(564, 103, "Invoice Date: 2026-02-14", 10)
    p.right(564, 118, "Due Date: 2026-03-16", 10)
    p.right(564, 133, "Terms: Net 30", 10)

    p.text(48, 160, "BILL TO", 9, bold=True, gray=0.4)
    p.text(48, 176, "Cascade Analytics Inc.", 11)
    p.text(48, 191, "1187 Pine Street, Floor 4", 10, gray=0.3)
    p.text(48, 205, "Seattle, WA 98101", 10, gray=0.3)

    p.text(340, 160, "SHIP TO", 9, bold=True, gray=0.4)
    p.text(340, 176, "Cascade Analytics Inc.", 11)
    p.text(340, 191, "Warehouse B, Dock 3", 10, gray=0.3)

    y = 240
    p.fill(48, y - 14, 516, 20, 0.92)
    p.text(56, y, "DESCRIPTION", 9, bold=True)
    p.text(300, y, "QTY", 9, bold=True)
    p.text(350, y, "UNIT PRICE", 9, bold=True)
    p.right(556, y, "AMOUNT", 9, bold=True)
    p.line(48, y + 8, 564, y + 8)

    items = [
        ("Managed data pipeline — onboarding", "1", "4,500.00", "4,500.00"),
        ("Analytics seat licence (annual)", "12", "240.00", "2,880.00"),
        ("Priority support retainer", "3", "650.00", "1,950.00"),
        ("Custom dashboard build", "1", "1,250.00", "1,250.00"),
        ("Data migration hours", "8", "175.00", "1,400.00"),
    ]
    y += 30
    for desc, qty, unit, amt in items:
        p.text(56, y, desc, 10)
        p.text(300, y, qty, 10)
        p.text(350, y, unit, 10)
        p.right(556, y, amt, 10)
        y += 22

    p.line(48, y + 4, 564, y + 4)
    y += 22
    p.right(470, y, "Subtotal", 10)
    p.right(556, y, "11,980.00", 10)
    y += 18
    p.right(470, y, "Sales tax (8.75%)", 10)
    p.right(556, y, "1,048.25", 10)
    y += 18
    p.right(470, y, "Shipping", 10)
    p.right(556, y, "0.00", 10)
    y += 10
    p.fill(300, y, 264, 30, 0.90)
    p.right(470, y + 20, "TOTAL DUE", 12, bold=True)
    p.right(556, y + 20, "USD 13,028.25", 12, bold=True)

    p.text(48, y + 20, "Currency: USD", 10)
    p.text(48, y + 40, "PO Number: PO-88214", 10)
    p.text(48, y + 56, "Remit to: Northwind Trading Co. · Acct 4471-0928 · Routing 121000248", 9, gray=0.35)

    p.line(48, 700, 564, 700)
    p.text(48, 726, "Payment is due within 30 days. Late payments accrue 1.5% interest per month.", 9, gray=0.4)
    p.text(48, 744, "Status: UNPAID", 11, bold=True)
    return p.save(path)


def paystub(path: Path) -> Path:
    p = Pdf()
    p.fill(0, 0, 612, 58, 0.15)
    p.text(48, 30, "MERIDIAN HEALTH SYSTEMS", 16, bold=True)
    p.right(564, 30, "EARNINGS STATEMENT", 12, bold=True)

    p.text(48, 86, "ABAYOMI, CHIDERA N.", 12, bold=True)
    p.text(48, 102, "Employee ID: 0094782", 10)
    p.text(48, 117, "1238 Willow Creek Rd, Apt 5B", 10, gray=0.3)
    p.text(48, 132, "Portland, OR 97205", 10, gray=0.3)

    p.text(340, 86, "Pay Period: 2026-02-01 to 2026-02-15", 10)
    p.text(340, 102, "Pay Date: 2026-02-20", 10)
    p.text(340, 117, "Pay Frequency: Bi-weekly", 10)
    p.text(340, 132, "Department: Nursing — ICU", 10)
    p.text(340, 147, "Job Title: Registered Nurse", 10)

    y = 186
    p.fill(48, y - 14, 516, 20, 0.92)
    p.text(56, y, "EARNINGS", 9, bold=True)
    p.text(300, y, "HOURS", 9, bold=True)
    p.text(360, y, "RATE", 9, bold=True)
    p.text(430, y, "CURRENT", 9, bold=True)
    p.right(556, y, "YTD", 9, bold=True)
    p.line(48, y + 8, 564, y + 8)

    # YTD totals are prior periods plus this one, so the two columns never
    # happen to share a number: telling them apart needs the header.
    rows = [
        ("Regular", "72.00", "54.25", "3,906.00", "19,530.00"),
        ("Overtime (1.5x)", "6.50", "81.38", "528.97", "1,240.31"),
        ("Shift differential", "8.00", "4.50", "36.00", "180.00"),
        ("Holiday", "8.00", "54.25", "434.00", "2,170.00"),
    ]
    y += 30
    for r in rows:
        p.text(56, y, r[0], 10)
        p.text(300, y, r[1], 10)
        p.text(360, y, r[2], 10)
        p.text(430, y, r[3], 10)
        p.right(556, y, r[4], 10)
        y += 20

    p.line(48, y + 4, 564, y + 4)
    y += 20
    p.text(56, y, "GROSS PAY", 11, bold=True)
    p.text(300, y, "474.50", 10, gray=0.45)
    p.right(556, y, "4,904.97", 11, bold=True)
    p.text(430, y, "23,837.29", 10)

    y += 34
    p.fill(48, y - 14, 516, 20, 0.92)
    p.text(56, y, "DEDUCTIONS", 9, bold=True)
    p.text(360, y, "CURRENT", 9, bold=True)
    p.right(556, y, "YTD", 9, bold=True)
    p.line(48, y + 8, 564, y + 8)
    ded = [
        ("Federal Income Tax", "612.40", "2,964.10"),
        ("Social Security", "304.11", "1,433.46"),
        ("Medicare", "71.12", "335.24"),
        ("Oregon State Tax", "388.05", "1,879.55"),
        ("Medical PPO", "128.50", "642.50"),
        ("Dental", "22.00", "110.00"),
        ("401(k) — 5%", "245.25", "1,156.02"),
    ]
    y += 30
    for d in ded:
        p.text(56, y, d[0], 10)
        p.text(360, y, d[1], 10)
        p.right(556, y, d[2], 10)
        y += 20

    p.line(48, y + 4, 564, y + 4)
    y += 22
    p.fill(300, y - 16, 264, 32, 0.90)
    p.text(310, y + 4, "NET PAY", 13, bold=True)
    p.right(556, y + 4, "USD 3,132.54", 13, bold=True)

    p.text(48, y + 4, "Total Deductions", 10)
    p.right(296, y + 4, "1,772.43", 10)

    p.line(48, 640, 564, 640)
    p.text(48, 660, "Paid by direct deposit to account ending 4417 at First Cascade Bank.", 9, gray=0.35)
    p.text(48, 690, "Year-to-date gross: 23,837.29   Year-to-date net: 15,206.88", 9, gray=0.35)
    p.text(48, 675, "Leave balances: Vacation 62.5 hrs · Sick 41.0 hrs", 9, gray=0.35)
    p.text(48, 700, "Status: PAID", 11, bold=True)
    return p.save(path)


def bank_statement(path: Path) -> Path:
    p = Pdf()
    p.fill(0, 0, 612, 70, 0.12)
    p.text(48, 26, "FIRST CASCADE BANK", 15, bold=True)
    p.text(48, 44, "Monthly Checking Statement", 10)
    p.right(564, 44, "Member FDIC", 9)

    p.text(48, 96, "STATEMENT PERIOD", 9, bold=True, gray=0.4)
    p.text(48, 112, "2026-01-01 to 2026-01-31", 11)
    p.text(48, 132, "Account Holder: ABayomi, Chidera N.", 10)
    p.text(48, 147, "Mailing Address: 1238 Willow Creek Rd, Apt 5B, Portland, OR 97205", 9, gray=0.3)

    p.text(340, 96, "ACCOUNT NUMBER", 9, bold=True, gray=0.4)
    p.text(340, 112, "Checking ····4417", 11)
    p.text(340, 132, "Statement type: Everyday Checking", 10)
    p.text(340, 147, "Routing: 325180484", 9, gray=0.3)

    y = 180
    p.box(48, y - 6, 240, 68)
    p.box(318, y - 6, 246, 68)
    p.text(58, y + 14, "BEGINNING BALANCE", 8.5, bold=True, gray=0.45)
    p.text(58, y + 34, "USD 8,412.55", 13, bold=True)
    p.text(328, y + 14, "ENDING BALANCE", 8.5, bold=True, gray=0.45)
    p.text(328, y + 34, "USD 11,904.12", 13, bold=True)

    y = 282
    p.fill(48, y - 14, 516, 20, 0.92)
    p.text(56, y, "DATE", 9, bold=True)
    p.text(120, y, "DESCRIPTION", 9, bold=True)
    p.text(360, y, "DEBIT", 9, bold=True)
    p.text(440, y, "CREDIT", 9, bold=True)
    p.right(556, y, "BALANCE", 9, bold=True)
    p.line(48, y + 8, 564, y + 8)

    txn = [
        ("01/02", "Meridian Health Systems — DIRECT DEP", "", "3,132.54", "11,545.09"),
        ("01/03", "Whole Foods Market #1042 — POS", "186.44", "", "11,358.65"),
        ("01/04", "Rent — Willow Creek Apartments", "2,450.00", "", "8,908.65"),
        ("01/06", "Card Payment — Chase Sapphire", "412.88", "", "8,495.77"),
        ("01/08", "Transfer to Savings ····9032", "1,000.00", "", "7,495.77"),
        ("01/09", "Refund — Amazon.com", "", "78.32", "7,574.09"),
        ("01/11", "Shell Gas #2281 — POS", "64.10", "", "7,509.99"),
        ("01/12", "Verizon Wireless — AUTOPAY", "95.00", "", "7,414.99"),
        ("01/15", "City of Portland — Utility Bill", "142.36", "", "7,272.63"),
        ("01/16", "Meridian Health Systems — DIRECT DEP", "", "3,132.54", "10,405.17"),
        ("01/18", "Trader Joe's #552 — POS", "133.87", "", "10,271.30"),
        ("01/20", "Netflix — SUBSCRIPTION", "22.99", "", "10,248.31"),
        ("01/22", "Transfer from Savings ····9032", "", "2,000.00", "12,248.31"),
        ("01/24", "Medical PPO — Premium", "128.50", "", "12,119.81"),
        ("01/27", "Costco Wholesale — POS", "341.22", "", "11,778.59"),
        ("01/29", "Shell Gas #2281 — POS", "59.80", "", "11,718.79"),
        ("01/30", "Rogue Brewery — POS", "48.15", "", "11,670.64"),
        ("01/31", "Monthly Service Fee", "12.00", "", "11,658.64"),
        ("01/31", "Interest Earned", "", "45.48", "11,704.12"),
    ]
    y += 28
    for d, desc, debit, credit, bal in txn:
        p.text(56, y, d, 9)
        p.text(120, y, desc, 9)
        if debit:
            p.text(360, y, debit, 9)
        if credit:
            p.text(440, y, credit, 9)
        p.right(556, y, bal, 9)
        y += 16
        if y > 700:
            break

    p.line(48, y + 2, 564, y + 2)
    p.text(48, 740, "Total debits: 4,408.75    Total credits: 5,389.32    Average daily balance: 9,982.17", 9, gray=0.35)
    p.text(48, 758, "This statement is for informational purposes. Disputes within 60 days.", 8.5, gray=0.4)
    return p.save(path)


def lending_application(path: Path) -> Path:
    p = Pdf()
    p.fill(0, 0, 612, 54, 0.15)
    p.text(48, 32, "CASCADE MUTUAL LENDING", 15, bold=True)
    p.right(564, 32, "UNIFORM RESIDENTIAL LOAN APPLICATION", 8, bold=True)

    p.text(48, 78, "Application No: CML-2026-0071845", 11, bold=True)
    p.right(564, 78, "Date: 2026-02-09", 10)
    p.text(48, 94, "Loan Type: Conventional 30-year fixed", 10, gray=0.35)

    def section(title: str, y: float) -> float:
        p.fill(48, y - 13, 516, 19, 0.90)
        p.text(56, y, title, 9.5, bold=True)
        return y + 24

    y = section("SECTION 1 — BORROWER INFORMATION", 128)
    fields = [
        ("Full legal name", "ABAYOMI, CHIDERA N."),
        ("Date of birth", "1991-07-22"),
        ("Social Security Number", "***-**-4417"),
        ("Current address", "1238 Willow Creek Rd, Apt 5B, Portland, OR 97205"),
        ("Years at address", "4"),
        ("Employer", "Meridian Health Systems"),
        ("Occupation", "Registered Nurse"),
        ("Years employed", "6"),
        ("Email", "c.abayomi@example.com"),
        ("Phone", "(503) 555-0182"),
    ]
    for label, value in fields:
        p.text(56, y, label, 9, gray=0.45)
        p.text(220, y, value, 10)
        p.line(56, y + 5, 556, y + 5, gray=8.5)
        y += 17

    y = section("SECTION 2 — INCOME AND ASSETS", y + 14)
    income = [
        ("Base monthly income", "USD 8,470.00"),
        ("Overtime / bonus (monthly)", "USD 412.00"),
        ("Total gross monthly income", "USD 8,882.00"),
        ("Other income (monthly)", "USD 0.00"),
    ]
    for label, value in income:
        p.text(56, y, label, 9.5)
        p.right(556, y, value, 9.5, bold="Total" in label)
        y += 19
    y += 12
    assets = [
        ("Checking ····4417", "USD 11,904.12"),
        ("Savings ····9032", "USD 34,260.00"),
        ("Retirement (401k)", "USD 62,115.00"),
        ("Vehicle — 2021 Subaru Outback", "USD 18,500.00"),
    ]
    p.text(56, y, "ASSETS", 9, bold=True, gray=0.4)
    y += 16
    for label, value in assets:
        p.text(66, y, label, 9.5)
        p.right(556, y, value, 9.5)
        y += 16

    y = section("SECTION 3 — LIABILITIES", y + 14)
    liab = [
        ("Auto loan — First Cascade Bank", "USD 412.88", "36 months remaining"),
        ("Student loan — Navient", "USD 287.00", "84 months remaining"),
        ("Credit card — Chase Sapphire", "USD 145.00", "revolving"),
    ]
    for label, amount, term in liab:
        p.text(56, y, label, 9.5)
        p.text(330, y, amount + " / mo", 9.5)
        p.text(440, y, term, 8.5, gray=0.4)
        y += 16

    y = section("SECTION 4 — LOAN REQUEST", y + 14)
    req = [
        ("Property address", "4820 NE Fremont St, Portland, OR 97213"),
        ("Purchase price", "USD 612,000.00"),
        ("Loan amount requested", "USD 489,600.00"),
        ("Down payment", "USD 122,400.00"),
        ("Down payment percentage", "20%"),
        ("Estimated monthly payment", "USD 3,182.00"),
        ("Loan term", "360 months"),
        ("Interest rate (indicative)", "6.625%"),
    ]
    # Rows stay on their own baselines; 14pt apart is the tightest spacing the
    # OCR line detector still separates cleanly at this font size.
    for label, value in req:
        p.text(56, y, label, 8.5, gray=0.45)
        p.text(250, y, value, 9, bold="Loan amount" in label)
        p.line(56, y + 4, 556, y + 4, gray=8.5)
        y += 14

    # Summary block on the left, beside the last two form rows, so it stays
    # clear of the OCR engine's dead zone at the very bottom of the page
    # (y > ~0.97 detections go missing). Lines keep generous room.
    sum_y = y - 42
    p.line(330, sum_y - 4, 330, sum_y + 44, gray=7.0)
    p.text(348, sum_y, "Debt-to-income ratio: 32.4%", 9.5, bold=True)
    p.text(348, sum_y + 18, "Loan-to-value: 80.0%", 9.5, bold=True)
    p.text(348, sum_y + 38, "Status: PRE-APPROVED", 10, bold=True)
    p.text(348, sum_y + 58, "Signature: ______________", 9, gray=0.35)
    return p.save(path)


def messy_lending_application(path: Path) -> Path:
    """A photo-like scan: off-white paper, speckle noise, skew, faded ink.

    Rendered as an image PDF so OCR has to actually work at it, the way it
    would on a form someone photographed on a kitchen table.
    """
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    random.seed(7)
    W, H = 1700, 2200
    img = Image.new("RGB", (W, H), (238, 234, 224))
    d = ImageDraw.Draw(img)

    try:
        big = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 34)
        mid = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 27)
        small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)
        hand = ImageFont.truetype("/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf", 33)
    except OSError:
        big = mid = small = hand = ImageFont.load_default()

    x0 = 130
    d.text((x0, 90), "CASCADE MUTUAL LENDING", font=big, fill=(40, 40, 44))
    d.text((x0, 140), "Loan Application - Handwritten Intake Form", font=small, fill=(70, 70, 74))
    d.line((x0, 180, W - 110, 180), fill=(90, 90, 95), width=3)

    rows = [
        ("Application No:", "CML-2026-0071845"),
        ("Date:", "02/09/2026"),
        ("Borrower name:", "Chidera Abayomi"),
        ("Date of birth:", "07/22/1991"),
        ("Address:", "1238 Willow Creek Rd Apt 5B"),
        ("City / State / ZIP:", "Portland OR 97205"),
        ("Employer:", "Meridian Health Systems"),
        ("Occupation:", "Registered Nurse"),
        ("Years employed:", "6"),
        ("Gross monthly income:", "8,470.00"),
        ("Other monthly income:", "412.00"),
        ("Checking balance:", "11,904.12"),
        ("Savings balance:", "34,260.00"),
        ("Auto loan payment:", "412.88"),
        ("Student loan payment:", "287.00"),
        ("Property address:", "4820 NE Fremont St"),
        ("Purchase price:", "612,000.00"),
        ("Loan amount requested:", "489,600.00"),
        ("Down payment:", "122,400.00"),
        ("Loan term (months):", "360"),
        ("Interest rate:", "6.625%"),
        ("Credit score:", "748"),
    ]

    y = 230
    for label, value in rows:
        d.text((x0, y), label, font=mid, fill=(55, 55, 60))
        # Handwriting wobbles a little around the baseline.
        jitter = random.randint(-4, 4)
        d.text((760, y + jitter), value, font=hand, fill=(28, 34, 96))
        d.line((130, y + 44, W - 120, y + 44), fill=(150, 150, 155), width=2)
        y += 78

    d.text((x0, y + 30), "Signature:", font=mid, fill=(55, 55, 60))
    d.line((340, y + 70, 1000, y + 70), fill=(40, 40, 60), width=2)
    d.text((x0, y + 100), "Status: APPROVED - pending appraisal", font=mid, fill=(140, 30, 30))

    # Scanner speckle and a soft shadow, so it is not a pristine render.
    px = img.load()
    for _ in range(26000):
        sx, sy = random.randint(0, W - 1), random.randint(0, H - 1)
        g = random.randint(150, 235)
        px[sx, sy] = (g, g, g - 8)

    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img = img.rotate(-1.6, resample=Image.BICUBIC, fillcolor=(236, 232, 222), expand=False)

    # Slight photocopy-style contrast loss.
    img = img.point(lambda v: int(210 + (v - 210) * 0.82))

    jpg = path.with_suffix(".jpg")
    img.save(jpg, "JPEG", quality=72)
    write_image_pdf(jpg, path)
    jpg.unlink(missing_ok=True)
    return path


def write_image_pdf(image_path: Path, out: Path) -> Path:
    """Wrap a JPEG in a one-page PDF without any extra dependencies."""
    from PIL import Image

    with Image.open(image_path) as im:
        w, h = im.size
    data = image_path.read_bytes()
    cell = (
        b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
        b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n" % (w, h, len(data))
        + data + b"\nendstream"
    )
    content = b"q %d 0 0 %d 0 0 cm /Im0 Do Q" % (w, h)
    cells = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
         b"/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>" % (w, h)),
        cell,
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, body in enumerate(cells, 1):
        offs.append(len(buf))
        buf += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(buf)
    buf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(cells) + 1)
    for off in offs:
        buf += b"%010d 00000 n \n" % off
    buf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(cells) + 1, xref)
    out.write_bytes(bytes(buf))
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    made = [
        invoice(OUT / "sample-invoice.pdf"),
        paystub(OUT / "sample-paystub.pdf"),
        bank_statement(OUT / "sample-bank-statement.pdf"),
        lending_application(OUT / "sample-lending-application.pdf"),
        messy_lending_application(OUT / "sample-lending-application-scan.pdf"),
    ]
    for m in made:
        print(f"  wrote {m.relative_to(OUT.parent)}  ({m.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
