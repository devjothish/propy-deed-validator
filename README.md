# Bad Deed Validator

Validates OCR-scanned real estate deeds before blockchain recording by combining LLM-based parsing with strict programmatic validation.

## Quick Start

```bash
pip install -r requirements.txt
python validator.py                       # bad deed → REJECTED
python validator.py samples/good_deed.txt # clean deed → PASSED
```

Set `ANTHROPIC_API_KEY` for Claude-powered parsing. Falls back to regex if no key is set.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Architecture

Two layers, one principle: **LLM for fuzz, code for math.**

On-chain data is immutable — a bad deed recorded to the blockchain can't be undone. Validation must be deterministic.

```
OCR Text (noisy, abbreviated, extra whitespace)
  │
  ▼
┌───────────────────────────────────┐
│  Layer 1: LLM Parsing (fuzzy)    │  Claude Haiku extracts structured fields
│  parse_with_llm()                │  from messy OCR into a Pydantic model.
│  parse_with_regex()  (fallback)  │  Handles noise regex can't anticipate.
└──────────────┬────────────────────┘
               │ DeedFields (Pydantic, validated)
               ▼
┌───────────────────────────────────┐
│  Layer 2: Validation (strict)    │  Deterministic checks. No LLM.
│  validate_dates()                │  Pure date arithmetic.
│  validate_amounts()              │  Custom word-to-number parser.
│  match_county()                  │  Abbreviation expansion + SequenceMatcher.
│  validate_apn()                  │  Regex format check.
│  validate_status()               │  String comparison.
└──────────────┬────────────────────┘
               │ Findings[]
               ▼
           Verdict: PASSED / NEEDS REVIEW / REJECTED
```

## Why LLM for parsing, code for validation?

| Check | Method | Why |
|-------|--------|-----|
| **OCR → structured data** | Claude Haiku 4.5 | OCR text has unpredictable noise. LLMs handle abbreviations, extra whitespace, and formatting artifacts that would require fragile regex. Haiku is right-sized for extraction — fast and cheap. Pydantic validators act as guardrails on the LLM output (reject negative amounts, malformed dates, invalid state codes). |
| **Date comparison** | `datetime` | Binary question. No interpretation needed. |
| **Amount reconciliation** | Custom parser | Must be exact. See below. |
| **County matching** | `difflib.SequenceMatcher` | Controlled fuzziness with explicit confidence threshold. |

## We found a bug in `word2number`

While building the amount reconciliation check, I tested the popular [`word2number`](https://pypi.org/project/word2number/) library. It has a **systematic bug** with any amount matching `"X million Y hundred thousand"`:

```
word2number returns:                  Correct:
  "one million two hundred thousand"   → 1,201,200    (should be 1,200,000)
  "one million five hundred thousand"  → 1,501,500    (should be 1,500,000)
  "two million three hundred thousand" → 2,301,300    (should be 2,300,000)
```

The library double-counts the hundreds component. Every case fails. In a financial validator, this silently produces wrong results — flagging valid deeds as fraudulent or missing real discrepancies.

I wrote a custom 3-level accumulator (`total` / `group` / `current`) and tested it against 16 edge cases. The test suite includes `TestWord2NumberBug` which both proves the library bug exists and verifies our parser handles every case correctly.

## What It Catches

**Bad deed** (`samples/bad_deed.txt`):

| Check | Severity | Finding |
|-------|----------|---------|
| Temporal Logic | CRITICAL | Recording (Jan 10) before signing (Jan 15) |
| Amount Reconciliation | CRITICAL | $50K discrepancy: $1,250,000 vs $1,200,000 |
| County | INFO | "S. Clara" → "Santa Clara" (100%) |
| APN | WARNING | Letters in APN (expected digits-only) |
| Status | WARNING | "PRELIMINARY" — not yet RECORDED |

**Good deed** (`samples/good_deed.txt`): all checks pass → PASSED.

**Exit codes:** `0` passed, `1` critical, `2` warnings.

## Tests

```bash
python -m pytest test_validator.py -v
```

60 tests across 4 categories:
- **Unit tests** (45): amount parsing, dates, amounts, county, regex, APN, status, word2number bug proof
- **End-to-end** (2): bad deed rejects, good deed passes
- **Golden set** (5): realistic deed variations with expected verdicts
- **Adversarial** (8): empty input, random text, missing fields, unknown counties, Pydantic guardrails for LLM hallucinations

## What's Next

This validator handles a single deed. In production — closer to what Agent Avery does at scale — you'd extend this to:

- **Batch processing** across acquired title firms (Boss Law, etc.)
- **Multi-document workflows** where one deed references another (trust deeds, liens)
- **Agentic orchestration** — the validator becomes one tool in an agent's toolkit, alongside contract opening, compliance checks, and escrow communication

The two-layer architecture (LLM for fuzz, code for math) scales cleanly into that world. The validation functions become tools an agent calls; the Pydantic models become the shared schema between agents.

## Files

```
validator.py          ~400 lines — parsing, guardrails, validation, report
test_validator.py     60 tests (unit + golden set + adversarial)
counties.json         Reference database
requirements.txt      pydantic, anthropic
.env.example          API key template
.gitignore            .env, __pycache__, .pytest_cache
samples/
  bad_deed.txt        5 issues (2 critical)
  good_deed.txt       Clean deed, all checks pass
```
