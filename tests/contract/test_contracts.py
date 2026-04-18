"""Contract tests — INF-106.

For each Stage 2 output schema we verify two validators agree:
  1. Pydantic (the Python source of truth in schemas/deal_swarm.py)
  2. JS mirror (node-06.js port in contract/js_validator.js) — runs in the
     n8n container so it uses the same Node runtime as production.

Positive fixtures must pass BOTH validators.
Negative fixtures (the 3 bugs from tonight's Phase 2 Gate) must be rejected
by BOTH validators — that is what the tests assert, so a silent validator
bug in either direction becomes a visible failure.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas.deal_swarm import (
    ComplianceOutput,
    ExtractorOutput,
    LenderOutput,
    RiskOutput,
)

from tests.contract.js_mirror import JSMirrorError, validate_js

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"

SCHEMA_TO_MODEL = {
    "extractor": ExtractorOutput,
    "risk": RiskOutput,
    "lender": LenderOutput,
    "compliance": ComplianceOutput,
}


def _strip_doc(payload: dict) -> dict:
    """Remove the __doc__ key added to negative fixtures for humans."""
    return {k: v for k, v in payload.items() if k != "__doc__"}


def _load(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as f:
        return _strip_doc(json.load(f))


def _js_available() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "exec", "n8n-n8n-1", "node", "--version"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


JS_AVAILABLE = _js_available()
skip_no_js = pytest.mark.skipif(
    not JS_AVAILABLE,
    reason="docker exec n8n-n8n-1 node unavailable",
)


# ============================================================
# Positive cases — every fixture must pass BOTH validators
# ============================================================

POSITIVE_CASES = [
    pytest.param("extractor", "extractor_valid.json", id="extractor"),
    pytest.param("risk", "risk_valid.json", id="risk"),
    pytest.param("lender", "lender_valid.json", id="lender"),
    pytest.param("compliance", "compliance_valid.json", id="compliance"),
]

JS_POSITIVE_CASES = [
    pytest.param("extractor", "extractor_valid.json", id="extractor"),
    pytest.param("risk", "risk_valid.json", id="risk"),
    pytest.param("lender", "lender_valid.json", id="lender"),
    pytest.param("compliance", "compliance_valid.json", id="compliance"),
]


@pytest.mark.parametrize("schema,fixture", POSITIVE_CASES)
def test_positive_pydantic(schema: str, fixture: str) -> None:
    payload = _load(fixture)
    model = SCHEMA_TO_MODEL[schema]
    # Must not raise
    model.model_validate(payload)


@skip_no_js
@pytest.mark.parametrize("schema,fixture", JS_POSITIVE_CASES)
def test_positive_js_mirror(schema: str, fixture: str) -> None:
    payload = _load(fixture)
    result = validate_js(schema, payload)
    assert result["valid"], (
        f"JS mirror rejected {fixture}: {result.get('errors')}"
    )


# ============================================================
# Negative cases — the 3 Stage 2 bugs caught by tonight's work
# ============================================================

# Each tuple: (schema, fixture, expected_pydantic_keywords, expected_js_keywords)
# expected_*_keywords: substrings we expect to see somewhere in the
# validation errors. This keeps tests meaningful even if messages evolve.

NEGATIVE_CASES = [
    pytest.param(
        "lender",
        "lender_fit_score_bug.json",
        ["fit"],  # Pydantic: missing required 'fit' field
        ["fit"],  # JS: fit missing or fit_score-must-not-be-present
        id="lender-fit_score-instead-of-fit",
    ),
    pytest.param(
        "lender",
        "lender_rationale_bug.json",
        ["match_reasons"],  # Pydantic: missing required match_reasons
        ["match_reasons"],  # JS: match_reasons missing or rationale-must-not
        id="lender-rationale-instead-of-match_reasons",
    ),
    pytest.param(
        "extractor",
        "extractor_ltv_nested_bug.json",
        ["ltv"],  # Pydantic: ltv missing at top level
        ["ltv"],  # JS: ltv missing at top + nested-under-calculated_fields
        id="extractor-ltv-nested-under-calculated_fields",
    ),
]


@pytest.mark.parametrize("schema,fixture,py_keywords,js_keywords", NEGATIVE_CASES)
def test_negative_pydantic_rejects(
    schema: str,
    fixture: str,
    py_keywords: list,
    js_keywords: list,
) -> None:
    payload = _load(fixture)
    model = SCHEMA_TO_MODEL[schema]
    with pytest.raises(ValidationError) as exc:
        model.model_validate(payload)
    msg = str(exc.value).lower()
    for kw in py_keywords:
        assert kw.lower() in msg, (
            f"Expected '{kw}' in Pydantic error for {fixture}, got: {msg[:500]}"
        )


@skip_no_js
@pytest.mark.parametrize("schema,fixture,py_keywords,js_keywords", NEGATIVE_CASES)
def test_negative_js_rejects(
    schema: str,
    fixture: str,
    py_keywords: list,
    js_keywords: list,
) -> None:
    payload = _load(fixture)
    result = validate_js(schema, payload)
    assert not result["valid"], (
        f"JS mirror accepted negative fixture {fixture}: {result}"
    )
    joined = " | ".join(result["errors"]).lower()
    for kw in js_keywords:
        assert kw.lower() in joined, (
            f"Expected '{kw}' in JS errors for {fixture}, got: {joined[:500]}"
        )


# ============================================================
# LTV-at-top-level positive contract — explicit assertion that the
# externalized extractor prompt actually demands top-level ltv.
# ============================================================

def test_extractor_valid_has_top_level_ltv() -> None:
    payload = _load("extractor_valid.json")
    assert "ltv" in payload, "positive fixture must have top-level ltv"
    assert isinstance(payload["ltv"], (int, float))
    assert "calculated_fields" not in payload, (
        "positive fixture must not nest ltv under calculated_fields"
    )
