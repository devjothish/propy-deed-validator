#!/usr/bin/env python3
"""
Bad Deed Validator — Propy Take-Home

Validates OCR-scanned real estate deeds before blockchain recording.

Architecture:
  Layer 1 (Fuzzy/AI):  Claude Haiku extracts structured fields from noisy OCR into Pydantic.
  Layer 2 (Strict/Code): Deterministic validation — dates, amounts, county lookup. No LLM.

Why this split?
  On-chain data is immutable. Validation logic must be deterministic and auditable.
  LLMs are great at understanding messy text; they should never decide if $1.25M == $1.2M.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from difflib import SequenceMatcher

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
COUNTIES_PATH = Path(__file__).parent / "counties.json"

EXTRACTION_PROMPT = (
    "Extract fields from this OCR-scanned deed as JSON with keys: "
    "doc_id, county, state, date_signed (YYYY-MM-DD), date_recorded "
    "(YYYY-MM-DD), grantor, grantee, amount_numeric (number only), "
    "amount_written (the words in parentheses), apn, status. "
    "Use null for missing fields."
)


# ---------------------------------------------------------------------------
# Models (Pydantic — used by both LLM extraction and validation)
# ---------------------------------------------------------------------------

class DeedFields(BaseModel):
    """
    Structured fields extracted from an OCR-scanned deed.

    Pydantic validators act as guardrails on LLM output — if the model
    hallucinates a negative amount or an invalid date, validation rejects
    the field rather than passing garbage to the financial checks.
    """
    doc_id: str | None = None
    county: str | None = None
    state: str | None = None
    date_signed: str | None = Field(None, description="YYYY-MM-DD")
    date_recorded: str | None = Field(None, description="YYYY-MM-DD")
    grantor: str | None = None
    grantee: str | None = None
    amount_numeric: float | None = None
    amount_written: str | None = None
    apn: str | None = None
    status: str | None = None

    @field_validator("date_signed", "date_recorded")
    @classmethod
    def validate_date_format(cls, v: str | None) -> str | None:
        """Reject dates the LLM hallucinated in wrong formats."""
        if v is None:
            return None
        try:
            date.fromisoformat(v)
        except ValueError:
            return None  # Discard bad date rather than propagate
        return v

    @field_validator("amount_numeric")
    @classmethod
    def validate_amount_positive(cls, v: float | None) -> float | None:
        """Reject negative or zero amounts — likely hallucinated."""
        if v is not None and v <= 0:
            return None
        return v

    @field_validator("state")
    @classmethod
    def validate_state(cls, v: str | None) -> str | None:
        """State must be a 2-letter code."""
        if v and len(v) != 2:
            return None
        return v.upper() if v else None


class Finding(BaseModel):
    """A single validation finding."""
    check: str
    severity: str  # CRITICAL, WARNING, INFO
    message: str
    details: str | None = None


# ---------------------------------------------------------------------------
# Layer 1: Parsing — LLM extracts structured data from noisy OCR
# ---------------------------------------------------------------------------

def parse_with_llm(ocr_text: str) -> DeedFields | None:
    """
    Use Claude Haiku to extract structured fields from OCR text.

    Why Haiku? This is a simple extraction task (11 fields from semi-structured text).
    Haiku is fast, cheap, and more than capable. Using a larger model here would be
    over-provisioning — the complexity is in validation, not parsing.
    """
    try:
        import anthropic
    except ImportError:
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": f"{EXTRACTION_PROMPT}\n\n{ocr_text}"}],
        )
        raw = response.content[0].text
        # Extract JSON from response (Haiku may wrap in markdown code blocks)
        json_match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
        if not json_match:
            return None
        return DeedFields.model_validate(json.loads(json_match.group()))
    except Exception:
        return None


def parse_with_regex(ocr_text: str) -> DeedFields:
    """Deterministic regex fallback — zero dependencies, works offline."""
    def extract(pattern: str) -> str | None:
        m = re.search(pattern, ocr_text)
        return m.group(1).strip() if m else None

    county = state = None
    m = re.search(r"County:\s*(.+?)\s*\|\s*State:\s*(\w+)", ocr_text)
    if m:
        county, state = m.group(1).strip(), m.group(2).strip()

    amount_numeric = amount_written = None
    m = re.search(r"Amount:\s*\$([\d,]+(?:\.\d{2})?)\s*\((.+?)\)", ocr_text)
    if m:
        amount_numeric = float(m.group(1).replace(",", ""))
        amount_written = m.group(2).strip()

    grantor = extract(r"Grantor:\s*(.+)")
    grantee = extract(r"Grantee:\s*(.+)")

    return DeedFields(
        doc_id=extract(r"Doc:\s*(.+)"),
        county=county,
        state=state,
        date_signed=extract(r"Date Signed:\s*([\d-]+)"),
        date_recorded=extract(r"Date Recorded:\s*([\d-]+)"),
        grantor=re.sub(r"\s+", " ", grantor) if grantor else None,
        grantee=re.sub(r"\s+", " ", grantee) if grantee else None,
        amount_numeric=amount_numeric,
        amount_written=amount_written,
        apn=extract(r"APN:\s*(.+)"),
        status=extract(r"Status:\s*(.+)"),
    )


def parse_deed(ocr_text: str) -> tuple[DeedFields, str]:
    """Try LLM first, fall back to regex."""
    deed = parse_with_llm(ocr_text)
    if deed is not None and deed.doc_id is not None:
        return deed, f"Claude ({ANTHROPIC_MODEL})"
    return parse_with_regex(ocr_text), "regex (fallback)"


# ---------------------------------------------------------------------------
# Layer 2: Validation — deterministic, auditable, no LLM
# ---------------------------------------------------------------------------

def validate_dates(deed: DeedFields) -> list[Finding]:
    """A deed cannot be recorded before it is signed."""
    try:
        signed = date.fromisoformat(deed.date_signed) if deed.date_signed else None
        recorded = date.fromisoformat(deed.date_recorded) if deed.date_recorded else None
    except ValueError:
        return [Finding(check="Temporal Logic", severity="WARNING", message="Malformed date field(s).")]

    if signed is None or recorded is None:
        return [Finding(check="Temporal Logic", severity="WARNING", message="Missing date field(s).")]

    delta = (recorded - signed).days
    if delta < 0:
        return [Finding(
            check="Temporal Logic", severity="CRITICAL",
            message=f"Recording date ({recorded}) precedes signature date ({signed}) by {abs(delta)} day(s).",
            details=f"Signed: {signed} | Recorded: {recorded} | Delta: {delta} days",
        )]
    if delta == 0:
        return [Finding(check="Temporal Logic", severity="WARNING", message="Same-day recording — unusual but possible.")]
    return [Finding(check="Temporal Logic", severity="INFO", message=f"Date sequence valid. Recorded {delta} day(s) after signing.")]


def words_to_number(text: str) -> float | None:
    """
    Convert written dollar amount to numeric value.

    We intentionally avoid the word2number library — it has a systematic bug
    with compound amounts like "X million Y hundred thousand" (returns 1,201,200
    instead of 1,200,000). In a financial validator, silent arithmetic errors
    are unacceptable. See TestWord2NumberBug in test_validator.py for proof.
    """
    cleaned = text.lower().replace("dollars", "").replace("dollar", "").replace("-", " ").strip()
    if not cleaned:
        return None

    DIGITS = {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
        "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
        "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
        "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    }

    total = 0    # after million/billion
    group = 0    # thousands level
    current = 0  # within a group

    for word in cleaned.split():
        word = word.strip(",")
        if not word or word == "and":
            continue
        if word in ("point", "dot", "decimal"):
            return None  # Decimal amounts aren't standard in deeds — bail out
        if word in DIGITS:
            current += DIGITS[word]
        elif word == "hundred":
            current = (current or 1) * 100
        elif word == "thousand":
            group += (current or 1) * 1_000
            current = 0
        elif word == "million":
            total += (group + (current or 1)) * 1_000_000
            group = current = 0
        elif word == "billion":
            total += (group + (current or 1)) * 1_000_000_000
            group = current = 0

    total += group + current
    return float(total) if total > 0 else None


def validate_amounts(deed: DeedFields) -> list[Finding]:
    """Reconcile numeric vs written amounts. Any discrepancy blocks recording."""
    if deed.amount_numeric is None:
        return [Finding(check="Monetary Reconciliation", severity="WARNING", message="Numeric amount not found.")]
    if deed.amount_written is None:
        return [Finding(check="Monetary Reconciliation", severity="WARNING", message="Written amount not found.")]

    written = words_to_number(deed.amount_written)
    if written is None:
        return [Finding(check="Monetary Reconciliation", severity="WARNING", message=f'Could not parse: "{deed.amount_written}"')]

    diff = abs(deed.amount_numeric - written)
    if diff > 0.01:
        return [Finding(
            check="Monetary Reconciliation", severity="CRITICAL",
            message=f"${diff:,.2f} discrepancy. Numeric: ${deed.amount_numeric:,.2f} | Written: ${written:,.2f}",
            details="On-chain data is immutable — must resolve before recording.",
        )]
    return [Finding(check="Monetary Reconciliation", severity="INFO", message=f"Amounts match: ${deed.amount_numeric:,.2f}")]


def match_county(deed: DeedFields, counties: list[dict]) -> list[Finding]:
    """Map OCR-abbreviated county to canonical name via fuzzy matching."""
    if not deed.county:
        return [Finding(check="County Matching", severity="WARNING", message="No county found.")]

    # Expand common abbreviations
    normalized = re.sub(r"^S\.\s*", "Santa ", deed.county.strip())
    normalized = re.sub(r"^St\.\s*", "Saint ", normalized)

    best, best_score = None, 0
    for c in counties:
        # Simple ratio: shared characters / max length
        a, b = normalized.lower(), c["name"].lower()
        if a == b:
            score = 100
        else:
            score = int(SequenceMatcher(None, a, b).ratio() * 100)
        if score > best_score:
            best_score, best = score, c

    if best_score < 60 or best is None:
        return [Finding(check="County Matching", severity="CRITICAL", message=f'Unknown county: "{deed.county}"')]

    tax = f" | Est. tax: ${deed.amount_numeric * best['tax_rate']:,.2f}" if deed.amount_numeric else ""
    sev = "INFO" if best_score >= 90 else "WARNING"
    return [Finding(check="County Matching", severity=sev,
                    message=f'"{deed.county}" -> "{best["name"]}" ({best_score}%) | Rate: {best["tax_rate"]:.1%}{tax}')]


def validate_apn(deed: DeedFields) -> list[Finding]:
    if not deed.apn:
        return []
    if not re.match(r"^\d+-\d+-\d+$", deed.apn):
        return [Finding(check="APN Format", severity="WARNING", message=f'"{deed.apn}" — expected digits-only NNN-NNN-NNN.')]
    return [Finding(check="APN Format", severity="INFO", message=f"Valid: {deed.apn}")]


def validate_status(deed: DeedFields) -> list[Finding]:
    if deed.status and deed.status.upper() != "RECORDED":
        return [Finding(check="Document Status", severity="WARNING",
                        message=f'"{deed.status}" — must be RECORDED before on-chain commitment.')]
    return []


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def validate(ocr_text: str) -> tuple[DeedFields, list[Finding], str]:
    """Full pipeline: parse then validate."""
    deed, method = parse_deed(ocr_text)
    counties = json.loads(COUNTIES_PATH.read_text())["counties"]

    findings = []
    findings += validate_dates(deed)
    findings += validate_amounts(deed)
    findings += match_county(deed, counties)
    findings += validate_apn(deed)
    findings += validate_status(deed)

    return deed, findings, method


ICON = {"CRITICAL": "[!!]", "WARNING": "[! ]", "INFO": "[ok]"}


def main() -> int:
    # Input: file arg, stdin, or built-in sample
    if len(sys.argv) > 1 and sys.argv[1] == "-":
        ocr_text = sys.stdin.read().strip()
    elif len(sys.argv) > 1:
        ocr_text = Path(sys.argv[1]).read_text().strip()
    else:
        ocr_text = (Path(__file__).parent / "samples" / "bad_deed.txt").read_text().strip()

    deed, findings, method = validate(ocr_text)

    # Report
    sep = "=" * 70
    print(f"\n{sep}\n  DEED VALIDATION REPORT\n{sep}\n")
    print(f"  Parser:       {method}")
    print(f"  Document:     {deed.doc_id or 'N/A'}")
    print(f"  County:       {deed.county or 'N/A'}, {deed.state or 'N/A'}")
    print(f"  Signed:       {deed.date_signed or 'N/A'}")
    print(f"  Recorded:     {deed.date_recorded or 'N/A'}")
    print(f"  Grantor:      {deed.grantor or 'N/A'}")
    print(f"  Grantee:      {deed.grantee or 'N/A'}")
    print(f"  Amount (num): ${deed.amount_numeric:,.2f}" if deed.amount_numeric else "  Amount (num): N/A")
    print(f"  Amount (txt): {deed.amount_written or 'N/A'}")
    print(f"  APN:          {deed.apn or 'N/A'}")
    print(f"  Status:       {deed.status or 'N/A'}")

    print(f"\n{sep}\n  FINDINGS\n{sep}")
    for i, f in enumerate(findings, 1):
        print(f"\n  {i}. {ICON.get(f.severity, '[??]')} {f.check} ({f.severity})")
        print(f"     {f.message}")
        if f.details:
            print(f"     -> {f.details}")

    crit = sum(1 for f in findings if f.severity == "CRITICAL")
    warn = sum(1 for f in findings if f.severity == "WARNING")

    print(f"\n{sep}")
    if crit:
        print(f"  VERDICT: REJECTED — {crit} critical issue(s). Do NOT record on-chain.")
    elif warn:
        print(f"  VERDICT: NEEDS REVIEW — {warn} warning(s).")
    else:
        print("  VERDICT: PASSED — safe to record on-chain.")
    print(sep)

    return 1 if crit else 2 if warn else 0


if __name__ == "__main__":
    sys.exit(main())
