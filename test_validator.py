#!/usr/bin/env python3
"""Tests for Bad Deed Validator."""

import unittest
from validator import (
    DeedFields, Finding, validate, validate_dates, validate_amounts,
    validate_apn, validate_status, match_county, words_to_number,
    parse_with_regex,
)

COUNTIES = [
    {"name": "Santa Clara", "tax_rate": 0.012},
    {"name": "San Mateo", "tax_rate": 0.011},
    {"name": "Santa Cruz", "tax_rate": 0.010},
]


# --- Amount parsing (most critical) ---

class TestWordsToNumber(unittest.TestCase):
    def test_task_input(self):
        self.assertEqual(words_to_number("One Million Two Hundred Thousand Dollars"), 1_200_000.0)

    def test_complex(self):
        self.assertEqual(words_to_number("one million two hundred thirty four thousand five hundred sixty seven"), 1_234_567.0)

    def test_simple(self):
        self.assertEqual(words_to_number("five hundred thousand dollars"), 500_000.0)

    def test_millions(self):
        self.assertEqual(words_to_number("two million dollars"), 2_000_000.0)

    def test_with_and(self):
        self.assertEqual(words_to_number("one hundred and fifty thousand"), 150_000.0)

    def test_hundreds_after_thousand(self):
        self.assertEqual(words_to_number("five thousand five hundred"), 5_500.0)

    def test_billion(self):
        self.assertEqual(words_to_number("two billion dollars"), 2_000_000_000.0)

    def test_teens(self):
        self.assertEqual(words_to_number("thirteen thousand dollars"), 13_000.0)

    def test_small(self):
        self.assertEqual(words_to_number("fifty dollars"), 50.0)

    def test_empty(self):
        self.assertIsNone(words_to_number(""))

    def test_gibberish(self):
        self.assertIsNone(words_to_number("xyzzy foobar"))

    def test_case_insensitive(self):
        self.assertEqual(words_to_number("THREE HUNDRED THOUSAND"), 300_000.0)

    def test_extra_whitespace(self):
        self.assertEqual(words_to_number("  five   hundred   thousand  "), 500_000.0)

    def test_commas(self):
        self.assertEqual(words_to_number("one million, two hundred thousand"), 1_200_000.0)

    def test_hyphenated_compound(self):
        self.assertEqual(words_to_number("one hundred twenty-five thousand dollars"), 125_000.0)

    def test_hyphenated_tens(self):
        self.assertEqual(words_to_number("sixty-seven thousand dollars"), 67_000.0)

    def test_hyphenated_with_million(self):
        self.assertEqual(words_to_number("two million thirty-three thousand dollars"), 2_033_000.0)

    def test_bare_hundred(self):
        self.assertEqual(words_to_number("hundred dollars"), 100.0)

    def test_bare_thousand(self):
        self.assertEqual(words_to_number("thousand dollars"), 1_000.0)


# --- word2number bug proof ---

class TestWord2NumberBug(unittest.TestCase):
    """Proves the word2number library bug and that our parser is correct."""
    CASES = [
        ("one million one hundred thousand", 1_100_000),
        ("one million two hundred thousand", 1_200_000),
        ("one million five hundred thousand", 1_500_000),
        ("two million three hundred thousand", 2_300_000),
    ]

    def test_our_parser_correct(self):
        for text, expected in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(words_to_number(text), float(expected))

    def test_word2number_bug_exists(self):
        try:
            from word2number import w2n
        except ImportError:
            self.skipTest("word2number not installed")
        for text, expected in self.CASES:
            self.assertNotEqual(w2n.word_to_num(text), expected,
                                f"word2number fixed the bug for '{text}' — update README")


# --- Date validation ---

class TestDates(unittest.TestCase):
    def test_before_is_critical(self):
        deed = DeedFields(date_signed="2024-01-15", date_recorded="2024-01-10")
        self.assertEqual(validate_dates(deed)[0].severity, "CRITICAL")

    def test_same_day_warning(self):
        deed = DeedFields(date_signed="2024-01-15", date_recorded="2024-01-15")
        self.assertEqual(validate_dates(deed)[0].severity, "WARNING")

    def test_normal_passes(self):
        deed = DeedFields(date_signed="2024-01-10", date_recorded="2024-01-15")
        self.assertEqual(validate_dates(deed)[0].severity, "INFO")

    def test_missing(self):
        self.assertEqual(validate_dates(DeedFields())[0].severity, "WARNING")

    def test_malformed(self):
        deed = DeedFields(date_signed="NOT-A-DATE", date_recorded="2024-01-10")
        self.assertEqual(validate_dates(deed)[0].severity, "WARNING")


# --- Amount validation ---

class TestAmounts(unittest.TestCase):
    def test_discrepancy(self):
        deed = DeedFields(amount_numeric=1_250_000.0, amount_written="One Million Two Hundred Thousand Dollars")
        f = validate_amounts(deed)[0]
        self.assertEqual(f.severity, "CRITICAL")
        self.assertIn("50,000.00", f.message)

    def test_match(self):
        deed = DeedFields(amount_numeric=1_200_000.0, amount_written="One Million Two Hundred Thousand Dollars")
        self.assertEqual(validate_amounts(deed)[0].severity, "INFO")

    def test_missing_numeric(self):
        self.assertEqual(validate_amounts(DeedFields(amount_written="One Million"))[0].severity, "WARNING")

    def test_missing_written(self):
        self.assertEqual(validate_amounts(DeedFields(amount_numeric=1e6))[0].severity, "WARNING")

    def test_unparseable(self):
        deed = DeedFields(amount_numeric=1e6, amount_written="@#$%")
        self.assertEqual(validate_amounts(deed)[0].severity, "WARNING")


# --- County matching ---

class TestCounty(unittest.TestCase):
    def test_abbreviation(self):
        deed = DeedFields(county="S. Clara", amount_numeric=1_250_000.0)
        f = match_county(deed, COUNTIES)[0]
        self.assertEqual(f.severity, "INFO")
        self.assertIn("Santa Clara", f.message)

    def test_exact(self):
        deed = DeedFields(county="Santa Clara")
        self.assertIn("100%", match_county(deed, COUNTIES)[0].message)

    def test_misspelled(self):
        deed = DeedFields(county="Sant Clara")
        self.assertIn("Santa Clara", match_county(deed, COUNTIES)[0].message)

    def test_unknown(self):
        deed = DeedFields(county="Atlantis")
        self.assertEqual(match_county(deed, COUNTIES)[0].severity, "CRITICAL")

    def test_missing(self):
        self.assertEqual(match_county(DeedFields(), COUNTIES)[0].severity, "WARNING")


# --- Regex parser ---

class TestRegex(unittest.TestCase):
    SAMPLE = (
        "Doc: DEED-TRUST-0042\nCounty: S. Clara  |  State: CA\n"
        "Date Signed: 2024-01-15\nDate Recorded: 2024-01-10\n"
        "Grantor:  T.E.S.L.A. Holdings LLC\nGrantee:  John  &  Sarah  Connor\n"
        "Amount: $1,250,000.00 (One Million Two Hundred Thousand Dollars)\n"
        "APN: 992-001-XA\nStatus: PRELIMINARY"
    )

    def test_all_fields(self):
        d = parse_with_regex(self.SAMPLE)
        self.assertEqual(d.doc_id, "DEED-TRUST-0042")
        self.assertEqual(d.county, "S. Clara")
        self.assertEqual(d.amount_numeric, 1_250_000.0)
        self.assertEqual(d.status, "PRELIMINARY")

    def test_whitespace_normalized(self):
        d = parse_with_regex(self.SAMPLE)
        self.assertNotIn("  ", d.grantor)

    def test_empty_no_crash(self):
        d = parse_with_regex("")
        self.assertIsNone(d.doc_id)


# --- APN & Status ---

class TestAPN(unittest.TestCase):
    def test_nonstandard(self):
        self.assertEqual(validate_apn(DeedFields(apn="992-001-XA"))[0].severity, "WARNING")
    def test_valid(self):
        self.assertEqual(validate_apn(DeedFields(apn="992-001-123"))[0].severity, "INFO")
    def test_missing(self):
        self.assertEqual(len(validate_apn(DeedFields())), 0)


class TestStatus(unittest.TestCase):
    def test_preliminary(self):
        self.assertEqual(validate_status(DeedFields(status="PRELIMINARY"))[0].severity, "WARNING")
    def test_recorded(self):
        self.assertEqual(len(validate_status(DeedFields(status="RECORDED"))), 0)
    def test_missing(self):
        self.assertEqual(len(validate_status(DeedFields())), 0)


# --- End to end ---

class TestEndToEnd(unittest.TestCase):
    def test_bad_deed_rejected(self):
        from pathlib import Path
        bad = (Path(__file__).parent / "samples" / "bad_deed.txt").read_text().strip()
        _, findings, _ = validate(bad)
        crit = [f for f in findings if f.severity == "CRITICAL"]
        self.assertEqual(len(crit), 2)

    def test_good_deed_passes(self):
        from pathlib import Path
        good = (Path(__file__).parent / "samples" / "good_deed.txt").read_text().strip()
        _, findings, _ = validate(good)
        crit = [f for f in findings if f.severity == "CRITICAL"]
        warn = [f for f in findings if f.severity == "WARNING"]
        self.assertEqual(len(crit), 0)
        self.assertEqual(len(warn), 0)


# ---------------------------------------------------------------------------
# Golden Set — realistic deeds that must produce correct verdicts
# ---------------------------------------------------------------------------

class TestGoldenSet(unittest.TestCase):
    """Full deed inputs → expected verdict. These are the acceptance tests."""

    def _verdict(self, ocr: str) -> tuple[int, int]:
        """Returns (critical_count, warning_count)."""
        _, findings, _ = validate(ocr)
        return (
            sum(1 for f in findings if f.severity == "CRITICAL"),
            sum(1 for f in findings if f.severity == "WARNING"),
        )

    def test_clean_deed_passes(self):
        """Standard valid deed — all checks should pass."""
        ocr = (
            "Doc: DEED-GRANT-0101\nCounty: Santa Clara  |  State: CA\n"
            "Date Signed: 2024-06-01\nDate Recorded: 2024-06-05\n"
            "Grantor: Acme Holdings LLC\nGrantee: Jane Smith\n"
            "Amount: $500,000.00 (Five Hundred Thousand Dollars)\n"
            "APN: 100-200-300\nStatus: RECORDED"
        )
        crit, warn = self._verdict(ocr)
        self.assertEqual(crit, 0)
        self.assertEqual(warn, 0)

    def test_only_date_error(self):
        """Valid amounts, valid county, but dates are swapped."""
        ocr = (
            "Doc: DEED-TRUST-0202\nCounty: San Mateo  |  State: CA\n"
            "Date Signed: 2024-08-15\nDate Recorded: 2024-08-10\n"
            "Grantor: Seller Corp\nGrantee: Buyer Inc\n"
            "Amount: $750,000.00 (Seven Hundred Fifty Thousand Dollars)\n"
            "APN: 200-300-400\nStatus: RECORDED"
        )
        crit, warn = self._verdict(ocr)
        self.assertEqual(crit, 1)  # only temporal logic

    def test_only_amount_error(self):
        """Valid dates, valid county, but amounts don't match."""
        ocr = (
            "Doc: DEED-GRANT-0303\nCounty: Santa Cruz  |  State: CA\n"
            "Date Signed: 2024-03-01\nDate Recorded: 2024-03-10\n"
            "Grantor: ABC Trust\nGrantee: XYZ LLC\n"
            "Amount: $900,000.00 (Eight Hundred Thousand Dollars)\n"
            "APN: 300-400-500\nStatus: RECORDED"
        )
        crit, warn = self._verdict(ocr)
        self.assertEqual(crit, 1)  # only monetary

    def test_both_errors(self):
        """Dates swapped AND amounts don't match — 2 critical."""
        ocr = (
            "Doc: DEED-TRUST-0404\nCounty: S. Clara  |  State: CA\n"
            "Date Signed: 2024-05-20\nDate Recorded: 2024-05-15\n"
            "Grantor: Fraud LLC\nGrantee: Victim Inc\n"
            "Amount: $1,000,000.00 (Nine Hundred Thousand Dollars)\n"
            "APN: 400-500-600\nStatus: RECORDED"
        )
        crit, warn = self._verdict(ocr)
        self.assertEqual(crit, 2)

    def test_hyphenated_amount_matches(self):
        """Written amount with hyphens should parse and match."""
        ocr = (
            "Doc: DEED-GRANT-0505\nCounty: San Mateo  |  State: CA\n"
            "Date Signed: 2024-04-01\nDate Recorded: 2024-04-05\n"
            "Grantor: Seller LLC\nGrantee: Buyer LLC\n"
            "Amount: $125,000.00 (One Hundred Twenty-Five Thousand Dollars)\n"
            "APN: 500-600-700\nStatus: RECORDED"
        )
        crit, warn = self._verdict(ocr)
        self.assertEqual(crit, 0)


# ---------------------------------------------------------------------------
# Adversarial — inputs designed to break the validator
# ---------------------------------------------------------------------------

class TestAdversarial(unittest.TestCase):
    """Hard-path inputs: malformed, missing, or deceptive data."""

    def test_empty_input_no_crash(self):
        _, findings, _ = validate("")
        self.assertTrue(len(findings) > 0)  # warnings, not crash

    def test_random_text_no_crash(self):
        _, findings, _ = validate("This is not a deed at all. Just random text.")
        self.assertTrue(len(findings) > 0)

    def test_partial_deed_missing_amount(self):
        ocr = (
            "Doc: DEED-TRUST-9999\nCounty: Santa Clara  |  State: CA\n"
            "Date Signed: 2024-01-01\nDate Recorded: 2024-01-05\n"
            "Grantor: Test\nGrantee: Test\nStatus: RECORDED"
        )
        _, findings, _ = validate(ocr)
        amount_f = [f for f in findings if f.check == "Monetary Reconciliation"]
        self.assertEqual(amount_f[0].severity, "WARNING")

    def test_county_not_in_database(self):
        """Los Angeles is a real county but not in our 3-county reference DB."""
        ocr = (
            "Doc: DEED-GRANT-8888\nCounty: Los Angeles  |  State: CA\n"
            "Date Signed: 2024-02-01\nDate Recorded: 2024-02-10\n"
            "Grantor: LA Seller\nGrantee: LA Buyer\n"
            "Amount: $2,000,000.00 (Two Million Dollars)\n"
            "APN: 111-222-333\nStatus: RECORDED"
        )
        _, findings, _ = validate(ocr)
        county_f = [f for f in findings if f.check == "County Matching"]
        self.assertEqual(county_f[0].severity, "CRITICAL")

    def test_decimal_amount_rejected(self):
        """'one point five million' should not silently return a wrong number."""
        result = words_to_number("one point five million dollars")
        self.assertIsNone(result)

    def test_negative_amount_guardrail(self):
        """Pydantic should reject negative amounts from LLM."""
        deed = DeedFields(amount_numeric=-500000)
        self.assertIsNone(deed.amount_numeric)

    def test_malformed_date_guardrail(self):
        """Pydantic should reject non-ISO dates from LLM."""
        deed = DeedFields(date_signed="January 15 2024")
        self.assertIsNone(deed.date_signed)

    def test_state_too_long_guardrail(self):
        """Pydantic should reject 'California' as state (must be 2-letter)."""
        deed = DeedFields(state="California")
        self.assertIsNone(deed.state)


if __name__ == "__main__":
    unittest.main()
