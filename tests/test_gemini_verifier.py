"""
Tests for Gemini Cross-Verification Module - INF-089
All tests run in TEST_MODE or with mocked LiteLLM - no live API calls.
"""

import importlib
import json
import os
from unittest.mock import patch, MagicMock

import pytest

os.environ["TEST_MODE"] = "true"
import config
importlib.reload(config)


def _reload_modules():
    """Reload config and gemini_verifier with current TEST_MODE."""
    importlib.reload(config)
    import gemini_verifier
    importlib.reload(gemini_verifier)
    return gemini_verifier


def _make_mock_deal_result():
    """Build a valid DealSwarmResult using deal_swarm mock factories."""
    os.environ["TEST_MODE"] = "true"
    importlib.reload(config)
    import deal_swarm
    importlib.reload(deal_swarm)
    import asyncio
    result = asyncio.run(deal_swarm.run_deal_swarm(
        ["Sample mortgage doc for testing."],
        "test-verify-001",
    ))
    return result


def _make_rejected_deal_result():
    """Build a rejected DealSwarmResult."""
    os.environ["TEST_MODE"] = "true"
    importlib.reload(config)
    import deal_swarm
    importlib.reload(deal_swarm)
    from schemas.deal_swarm import DealSwarmResult, ScreenerOutput, DealVerdict
    return DealSwarmResult(
        screener=ScreenerOutput(
            verdict=DealVerdict.REJECT,
            reason="Insufficient documentation for analysis. No core documents present.",
            borrower_name=None,
            property_address=None,
            document_types_found=[],
            missing_critical_docs=["application", "appraisal", "credit_bureau"],
            red_flags=[],
        ),
        deal_id="test-reject-001",
        processing_timestamp="2026-04-12T00:00:00+00:00",
        model_versions={"screener": "claude-haiku-4-5-20251001"},
    )


# Sample Gemini response JSON for mocking
MOCK_GEMINI_AGREE = json.dumps({
    "verification_fields": {
        "ltv": {
            "verdict": "agree",
            "original_value": 62.5,
            "verified_value": 62.3,
            "confidence": 0.95,
            "reasoning": "LTV recalculation matches within 0.2% tolerance."
        },
        "risk_rating": {
            "verdict": "agree",
            "original_value": "Medium",
            "verified_value": "Medium",
            "confidence": 0.88,
            "reasoning": "Risk profile consistent with moderate rating."
        },
        "risk_score": {
            "verdict": "agree",
            "original_value": 42,
            "verified_value": 44,
            "confidence": 0.82,
            "reasoning": "Score within acceptable range."
        },
        "compliance_status": {
            "verdict": "agree",
            "original_value": "clear",
            "verified_value": "clear",
            "confidence": 0.90,
            "reasoning": "No compliance issues found."
        },
        "exit_strategy": {
            "verdict": "agree",
            "original_value": "refinance_conventional",
            "verified_value": "refinance_conventional",
            "confidence": 0.85,
            "reasoning": "Exit strategy viable."
        },
        "lender_fit": {
            "verdict": "agree",
            "original_value": "1 lender",
            "verified_value": "appropriate",
            "confidence": 0.80,
            "reasoning": "Lender selection reasonable."
        }
    },
    "overall_verdict": "agree",
    "overall_confidence": 0.87,
    "missed_items": [],
    "false_positives": [],
    "disagreement_summary": ""
})

MOCK_GEMINI_DISAGREE = json.dumps({
    "verification_fields": {
        "ltv": {
            "verdict": "disagree",
            "original_value": 62.5,
            "verified_value": 75.0,
            "confidence": 0.92,
            "reasoning": "LTV should be calculated on purchase price, not appraised value. Correct LTV is 75%."
        },
        "risk_rating": {
            "verdict": "disagree",
            "original_value": "Medium",
            "verified_value": "High",
            "confidence": 0.85,
            "reasoning": "Corrected LTV of 75% pushes this into high risk territory."
        },
        "risk_score": {
            "verdict": "disagree",
            "original_value": 42,
            "verified_value": 68,
            "confidence": 0.80,
            "reasoning": "Score should be higher given corrected LTV."
        },
        "compliance_status": {
            "verdict": "disagree",
            "original_value": "clear",
            "verified_value": "warning",
            "confidence": 0.88,
            "reasoning": "Missing KYC documentation warrants a warning."
        },
        "exit_strategy": {
            "verdict": "uncertain",
            "original_value": "refinance_conventional",
            "verified_value": "uncertain",
            "confidence": 0.50,
            "reasoning": "Exit strategy depends on credit improvement - timeline uncertain."
        },
        "lender_fit": {
            "verdict": "agree",
            "original_value": "1 lender",
            "verified_value": "appropriate",
            "confidence": 0.75,
            "reasoning": "Lender selection is reasonable even with corrected risk."
        }
    },
    "overall_verdict": "disagree",
    "overall_confidence": 0.60,
    "missed_items": ["KYC documentation gaps", "Stale credit bureau"],
    "false_positives": [],
    "disagreement_summary": "Material LTV calculation error and missing compliance flags."
})

MOCK_GEMINI_WITH_FENCES = "```json\n" + MOCK_GEMINI_AGREE + "\n```"


# ============================================================
# Tests: TEST_MODE mock verification
# ============================================================

class TestMockVerification:

    def test_mock_returns_verification_result(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        vr = gv.verify_deal_swarm(result, ["test doc"])
        assert isinstance(vr, gv.VerificationResult)

    def test_mock_all_fields_agree(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        vr = gv.verify_deal_swarm(result, ["test doc"])
        assert vr.fields_agreed == 6
        assert vr.fields_disagreed == 0
        assert vr.overall_verdict == gv.OverallVerdict.AGREE

    def test_mock_deal_id_preserved(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        vr = gv.verify_deal_swarm(result, ["test doc"])
        assert vr.deal_id == "test-verify-001"

    def test_mock_has_timestamp(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        vr = gv.verify_deal_swarm(result, ["test doc"])
        assert len(vr.timestamp) > 10

    def test_reject_raises_error(self):
        gv = _reload_modules()
        rejected = _make_rejected_deal_result()
        with pytest.raises(ValueError, match="Cannot verify a rejected deal"):
            gv.verify_deal_swarm(rejected, ["test doc"])


# ============================================================
# Tests: Response parsing (agreement)
# ============================================================

class TestParseAgreement:

    def test_parse_full_agreement(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        vr = gv._parse_gemini_response(MOCK_GEMINI_AGREE, result)
        assert vr.overall_verdict == gv.OverallVerdict.AGREE
        assert vr.ltv.verdict == gv.FieldVerdict.AGREE
        assert vr.risk_rating.verdict == gv.FieldVerdict.AGREE
        assert vr.fields_agreed == 6

    def test_parse_confidence_values(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        vr = gv._parse_gemini_response(MOCK_GEMINI_AGREE, result)
        assert vr.ltv.confidence == 0.95
        assert vr.overall_confidence == 0.87

    def test_parse_strips_code_fences(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        vr = gv._parse_gemini_response(MOCK_GEMINI_WITH_FENCES, result)
        assert vr.overall_verdict == gv.OverallVerdict.AGREE

    def test_parse_reasoning_present(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        vr = gv._parse_gemini_response(MOCK_GEMINI_AGREE, result)
        assert len(vr.ltv.reasoning) > 5
        assert len(vr.risk_rating.reasoning) > 5


# ============================================================
# Tests: Response parsing (disagreement)
# ============================================================

class TestParseDisagreement:

    def test_parse_disagreement_verdict(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        vr = gv._parse_gemini_response(MOCK_GEMINI_DISAGREE, result)
        assert vr.overall_verdict == gv.OverallVerdict.DISAGREE

    def test_disagreement_counts(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        vr = gv._parse_gemini_response(MOCK_GEMINI_DISAGREE, result)
        assert vr.fields_disagreed == 4
        assert vr.fields_agreed == 1
        assert vr.fields_uncertain == 1

    def test_missed_items_captured(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        vr = gv._parse_gemini_response(MOCK_GEMINI_DISAGREE, result)
        assert len(vr.missed_items) == 2
        assert "KYC documentation gaps" in vr.missed_items

    def test_disagreement_summary(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        vr = gv._parse_gemini_response(MOCK_GEMINI_DISAGREE, result)
        assert "LTV" in vr.disagreement_summary

    def test_verified_values_differ(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        vr = gv._parse_gemini_response(MOCK_GEMINI_DISAGREE, result)
        assert vr.ltv.original_value == "62.5"
        assert vr.ltv.verified_value == "75.0"


# ============================================================
# Tests: Edge cases
# ============================================================

class TestEdgeCases:

    def test_malformed_json_raises(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        with pytest.raises(json.JSONDecodeError):
            gv._parse_gemini_response("not valid json at all", result)

    def test_missing_fields_get_defaults(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        minimal = json.dumps({
            "verification_fields": {},
            "overall_verdict": "needs_review",
            "overall_confidence": 0.3
        })
        vr = gv._parse_gemini_response(minimal, result)
        assert vr.overall_verdict == gv.OverallVerdict.NEEDS_REVIEW
        assert vr.ltv.verdict == gv.FieldVerdict.UNCERTAIN
        assert vr.ltv.confidence == 0.5

    def test_invalid_verdict_defaults_to_needs_review(self):
        gv = _reload_modules()
        result = _make_mock_deal_result()
        bad_verdict = json.dumps({
            "verification_fields": {},
            "overall_verdict": "maybe",
            "overall_confidence": 0.5
        })
        vr = gv._parse_gemini_response(bad_verdict, result)
        assert vr.overall_verdict == gv.OverallVerdict.NEEDS_REVIEW


# ============================================================
# Tests: Model independence
# ============================================================

class TestModelIndependence:

    def test_verification_input_has_no_system_prompt(self):
        """The input to Gemini must not contain Claude's system prompt."""
        gv = _reload_modules()
        result = _make_mock_deal_result()
        input_text = gv._build_verification_input(result, ["test document text"])
        # Should NOT contain any of Claude's stage prompts
        assert "## Role\nYou are a mortgage deal intake screener" not in input_text
        assert "stage1_screener" not in input_text
        assert "stage2_extractor" not in input_text

    def test_verification_input_contains_source_docs(self):
        """Gemini must see the same source documents Claude saw."""
        gv = _reload_modules()
        result = _make_mock_deal_result()
        docs = ["Document A content here", "Document B content here"]
        input_text = gv._build_verification_input(result, docs)
        assert "Document A content here" in input_text
        assert "Document B content here" in input_text

    def test_verification_input_contains_original_output(self):
        """Gemini must see Claude's final output values to verify."""
        gv = _reload_modules()
        result = _make_mock_deal_result()
        input_text = gv._build_verification_input(result, ["test doc"])
        assert "Original Analysis Output" in input_text
        assert "ltv" in input_text
        assert "risk_rating" in input_text


# ============================================================
# Tests: Verification schemas
# ============================================================

class TestVerificationSchemas:

    def test_field_verdict_enum_values(self):
        gv = _reload_modules()
        assert gv.FieldVerdict.AGREE.value == "agree"
        assert gv.FieldVerdict.DISAGREE.value == "disagree"
        assert gv.FieldVerdict.UNCERTAIN.value == "uncertain"

    def test_overall_verdict_enum_values(self):
        gv = _reload_modules()
        assert gv.OverallVerdict.AGREE.value == "agree"
        assert gv.OverallVerdict.DISAGREE.value == "disagree"
        assert gv.OverallVerdict.NEEDS_REVIEW.value == "needs_review"

    def test_confidence_bounds(self):
        gv = _reload_modules()
        with pytest.raises(Exception):
            gv.FieldVerification(
                verdict=gv.FieldVerdict.AGREE,
                confidence=1.5,
                reasoning="Invalid confidence.",
            )

    def test_field_verification_valid(self):
        gv = _reload_modules()
        fv = gv.FieldVerification(
            verdict=gv.FieldVerdict.AGREE,
            original_value="62.5",
            verified_value="62.5",
            confidence=0.95,
            reasoning="Values match exactly.",
        )
        assert fv.verdict == gv.FieldVerdict.AGREE
        assert fv.confidence == 0.95
