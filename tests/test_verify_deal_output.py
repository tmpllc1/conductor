"""
Tests for verify_deal_output.py - Gemini cross-verification integration wrapper.

Uses GT-001 Curkovic ground truth data as test fixtures.
Tests: matching ratings pass, mismatched ratings flag discrepancy,
missing fields handled gracefully.
"""

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gemini_verifier import (
    FieldVerdict,
    FieldVerification,
    OverallVerdict,
    VerificationResult,
)
from schemas.deal_swarm import (
    ComplianceFlag,
    DealSwarmResult,
    DealVerdict,
    ExitStrategy,
    LenderFit,
    LoanPurpose,
    PropertyType,
    RiskRating,
)
from verify_deal_output import (
    build_output,
    load_stage2_json,
    stage2_to_deal_swarm_result,
)


# ============================================================
# GT-001 Curkovic fixture - minimal valid Stage 2 JSON
# ============================================================

def _gt001_screener():
    return {
        "verdict": "proceed",
        "reason": "Industrial refinance in Burnaby, LTV ~67%, corporate borrower with personal guarantee. Viable for private lending.",
        "borrower_name": "Curkovic Holdings Ltd.",
        "property_address": "7406 Conway Avenue, Burnaby, BC",
        "estimated_loan_amount": 2265000,
        "estimated_ltv": 67.41,
        "document_types_found": ["appraisal", "credit_bureau", "application", "corporate_docs"],
        "missing_critical_docs": ["title_search"],
        "red_flags": ["stale_credit_bureau"],
    }


def _gt001_extractor():
    return {
        "borrower": {
            "name": "Curkovic Holdings Ltd.",
            "is_corporation": True,
            "employment_type": "self-employed",
            "annual_income": 352000,
            "income_verification_method": "bank statements",
            "credit_score": 687,
            "credit_score_source": "Equifax",
            "existing_mortgages": 2,
        },
        "property": {
            "address": "7406 Conway Avenue, Burnaby, BC",
            "city": "Burnaby",
            "province": "BC",
            "property_type": "industrial",
            "appraised_value": 3360000,
            "year_built": 1987,
            "condition": "fair",
        },
        "loan": {
            "purpose": "refinance",
            "requested_amount": 2265000,
            "term_months": 12,
            "interest_only": True,
            "existing_first_mortgage_balance": 1800000,
            "mortgage_position": 1,
        },
        "exit_strategy": "refinance_conventional",
        "exit_strategy_details": "Refinance when lease renews at market rate; AMEC operating income paydown as secondary exit.",
        "ltv": 67.41,
        "combined_ltv": 67.41,
        "documents_processed": ["appraisal", "credit_bureau", "application"],
        "data_gaps": ["title_search", "current_credit_bureau"],
        "confidence_notes": ["Credit bureau is 24 months stale"],
    }


def _gt001_risk():
    return {
        "overall_rating": "Medium",
        "overall_score": 2.8,
        "risk_factors": [
            {"factor": "LTV Risk", "rating": "Medium", "weight": 0.3, "explanation": "LTV of 67.41% is within acceptable range for private lending."},
            {"factor": "Credit Risk", "rating": "Medium", "weight": 0.2, "explanation": "Credit score 687 is borderline - stale bureau adds uncertainty."},
            {"factor": "Exit Strategy Risk", "rating": "Medium", "weight": 0.25, "explanation": "Conventional refinance exit viable but depends on lease renewal."},
            {"factor": "Income Risk", "rating": "High", "weight": 0.15, "explanation": "Self-employed income via AMEC - verified by bank statements only."},
            {"factor": "Property Risk", "rating": "Medium", "weight": 0.1, "explanation": "Industrial property in established Burnaby corridor, 1987 build."},
        ],
        "ltv_risk": "Medium",
        "credit_risk": "Medium",
        "exit_strategy_risk": "Medium",
        "income_risk": "High",
        "property_risk": "Medium",
        "risk_summary": "Moderate risk industrial refinance. LTV acceptable at 67.41%, but property-only DSCR is insufficient - deal depends on AMEC business income. Stale credit bureau and undisclosed debts increase uncertainty.",
        "mitigating_factors": ["Established Burnaby industrial location", "Personal guarantee from Curkovic"],
        "aggravating_factors": ["Stale credit bureau", "DSCR shortfall on property income alone"],
        "recommended_conditions": ["Current credit bureau required", "Title search required"],
    }


def _gt001_lender():
    return {
        "recommended_lenders": [
            {
                "lender_name": "Tier 2 Credit Union",
                "fit": "moderate",
                "estimated_rate": "8.5-10.0%",
                "estimated_fee": "1.5-2%",
                "max_ltv": 75.0,
                "match_reasons": ["Industrial lending experience", "BC-based"],
                "concerns": ["May require current credit bureau"],
            },
        ],
        "deal_positioning": "Position as established industrial refinance with strong property fundamentals and confirmed business income coverage.",
        "rate_expectation": "8.5% to 11.5% depending on lender tier and conditions",
        "fee_expectation": "1.5% to 2.5% lender fee typical for this profile",
    }


def _gt001_compliance():
    return {
        "overall_status": "warning",
        "checks": [
            {"check_name": "BCFSA Disclosure", "category": "BCFSA", "status": "warning", "detail": "BPCPA cost of credit disclosure required for this transaction."},
            {"check_name": "Client ID Verification", "category": "KYC", "status": "warning", "detail": "Credit bureau is 24 months stale - current bureau required before proceeding."},
            {"check_name": "Source of Funds", "category": "FINTRAC", "status": "warning", "detail": "Lite Access Technologies sale-and-buyback and Vault Financial connection require source-of-funds review."},
            {"check_name": "s.347 EAR Check", "category": "General", "status": "clear", "detail": "All modeled rate scenarios (8.5%, 10.0%, 11.5%) clear under s.347 Criminal Code."},
        ],
        "bcfsa_disclosure_required": True,
        "bcfsa_notes": ["Form 10 disclosure required", "Cost of credit disclosure under BPCPA"],
        "aml_flags": ["Lite Access Technologies transaction history"],
        "kyc_checklist_gaps": ["Current credit bureau", "Title search", "Corporate registration confirmation"],
        "suspicious_transaction_indicators": [],
        "suitability_concerns": [],
    }


def _gt001_stage2_json():
    """Full Stage 2 JSON as n8n would produce it."""
    return {
        "deal_id": "GT-001",
        "status": "analysis_complete",
        "screener": _gt001_screener(),
        "extractor": _gt001_extractor(),
        "risk": _gt001_risk(),
        "lender": _gt001_lender(),
        "compliance": _gt001_compliance(),
        "processing_timestamp": "2026-04-12T10:00:00Z",
        "total_tokens_used": 12000,
        "model_versions": {
            "screener": "claude-haiku-4-5-20251001",
            "extractor": "claude-sonnet-4-6",
            "risk": "claude-sonnet-4-6",
            "lender": "claude-sonnet-4-6",
            "compliance": "claude-sonnet-4-6",
        },
    }


# ============================================================
# Helper: build a VerificationResult with custom field verdicts
# ============================================================

def _make_field(verdict, original="Medium", verified="Medium", conf=0.85):
    return FieldVerification(
        verdict=verdict,
        original_value=original,
        verified_value=verified,
        confidence=conf,
        reasoning="Test reasoning for verification.",
    )


def _make_verification_result(
    overall=OverallVerdict.AGREE,
    risk_verdict=FieldVerdict.AGREE,
    risk_original="Medium",
    risk_verified="Medium",
    ltv_verdict=FieldVerdict.AGREE,
    compliance_verdict=FieldVerdict.AGREE,
    confidence=0.86,
    missed_items=None,
    false_positives=None,
):
    return VerificationResult(
        deal_id="GT-001",
        timestamp=datetime.now(timezone.utc).isoformat(),
        ltv=_make_field(ltv_verdict, "67.41", "67.41"),
        risk_rating=_make_field(risk_verdict, risk_original, risk_verified),
        risk_score=_make_field(FieldVerdict.AGREE, "2.8", "3.0"),
        compliance_status=_make_field(compliance_verdict, "warning", "warning"),
        exit_strategy=_make_field(FieldVerdict.AGREE, "refinance_conventional", "refinance_conventional"),
        lender_fit=_make_field(FieldVerdict.AGREE, "1 lender recommended", "appropriate"),
        overall_verdict=overall,
        overall_confidence=confidence,
        missed_items=missed_items or [],
        false_positives=false_positives or [],
        fields_checked=6,
        fields_agreed=6 if overall == OverallVerdict.AGREE else 4,
        fields_disagreed=0 if overall == OverallVerdict.AGREE else 2,
        fields_uncertain=0,
    )


# ============================================================
# Tests: stage2_to_deal_swarm_result
# ============================================================

class TestStage2Parsing:
    """Test converting raw n8n JSON into DealSwarmResult."""

    def test_valid_gt001_parses(self):
        data = _gt001_stage2_json()
        result = stage2_to_deal_swarm_result(data)
        assert isinstance(result, DealSwarmResult)
        assert result.deal_id == "GT-001"

    def test_screener_verdict_preserved(self):
        data = _gt001_stage2_json()
        result = stage2_to_deal_swarm_result(data)
        assert result.screener.verdict == DealVerdict.PROCEED

    def test_extractor_ltv_preserved(self):
        data = _gt001_stage2_json()
        result = stage2_to_deal_swarm_result(data)
        assert result.extractor.ltv == 67.41

    def test_risk_rating_preserved(self):
        data = _gt001_stage2_json()
        result = stage2_to_deal_swarm_result(data)
        assert result.risk.overall_rating == RiskRating.MEDIUM
        assert result.risk.overall_score == 2.8

    def test_compliance_status_preserved(self):
        data = _gt001_stage2_json()
        result = stage2_to_deal_swarm_result(data)
        assert result.compliance.overall_status == ComplianceFlag.WARNING

    def test_missing_extractor_on_proceed_fails(self):
        data = _gt001_stage2_json()
        data["extractor"] = None
        with pytest.raises(ValueError, match="schema validation"):
            stage2_to_deal_swarm_result(data)

    def test_invalid_json_structure_fails(self):
        with pytest.raises(ValueError, match="schema validation"):
            stage2_to_deal_swarm_result({"garbage": True})

    def test_empty_dict_fails(self):
        with pytest.raises(ValueError):
            stage2_to_deal_swarm_result({})


# ============================================================
# Tests: build_output - matching ratings pass
# ============================================================

class TestMatchingRatingsPass:
    """When Gemini agrees with Claude, output should be verified=True."""

    def test_all_agree_verified_true(self):
        vr = _make_verification_result(overall=OverallVerdict.AGREE)
        output = build_output(vr)
        assert output["verified"] is True

    def test_all_agree_no_discrepancies(self):
        vr = _make_verification_result(overall=OverallVerdict.AGREE)
        output = build_output(vr)
        assert output["discrepancies"] == []

    def test_all_agree_confidence_present(self):
        vr = _make_verification_result(overall=OverallVerdict.AGREE, confidence=0.92)
        output = build_output(vr)
        assert output["confidence"] == 0.92

    def test_risk_ratings_match(self):
        vr = _make_verification_result(
            risk_original="Medium", risk_verified="Medium"
        )
        output = build_output(vr)
        assert output["claude_risk_rating"] == "Medium"
        assert output["gemini_risk_rating"] == "Medium"

    def test_deal_id_in_output(self):
        vr = _make_verification_result()
        output = build_output(vr)
        assert output["deal_id"] == "GT-001"


# ============================================================
# Tests: build_output - mismatched ratings flag discrepancy
# ============================================================

class TestMismatchedRatingsFlag:
    """When Gemini disagrees, output should capture discrepancies."""

    def test_disagree_verified_false(self):
        vr = _make_verification_result(
            overall=OverallVerdict.DISAGREE,
            risk_verdict=FieldVerdict.DISAGREE,
            risk_original="Medium",
            risk_verified="High",
        )
        output = build_output(vr)
        assert output["verified"] is False

    def test_disagree_has_discrepancy(self):
        vr = _make_verification_result(
            overall=OverallVerdict.DISAGREE,
            risk_verdict=FieldVerdict.DISAGREE,
            risk_original="Medium",
            risk_verified="High",
        )
        output = build_output(vr)
        assert len(output["discrepancies"]) >= 1
        risk_disc = [d for d in output["discrepancies"] if d["field"] == "risk_rating"]
        assert len(risk_disc) == 1
        assert risk_disc[0]["claude_value"] == "Medium"
        assert risk_disc[0]["gemini_value"] == "High"

    def test_needs_review_not_verified(self):
        vr = _make_verification_result(overall=OverallVerdict.NEEDS_REVIEW)
        output = build_output(vr)
        assert output["verified"] is False

    def test_uncertain_field_in_discrepancies(self):
        vr = _make_verification_result(
            overall=OverallVerdict.NEEDS_REVIEW,
            ltv_verdict=FieldVerdict.UNCERTAIN,
        )
        output = build_output(vr)
        uncertain_items = [d for d in output["discrepancies"] if d.get("uncertain")]
        assert len(uncertain_items) >= 1

    def test_missed_items_in_discrepancies(self):
        vr = _make_verification_result(
            overall=OverallVerdict.DISAGREE,
            missed_items=["Stale credit bureau not flagged as risk"],
        )
        output = build_output(vr)
        missed = [d for d in output["discrepancies"] if d["field"] == "missed_item"]
        assert len(missed) == 1

    def test_false_positives_in_discrepancies(self):
        vr = _make_verification_result(
            overall=OverallVerdict.DISAGREE,
            false_positives=["Seismic risk overstated"],
        )
        output = build_output(vr)
        fps = [d for d in output["discrepancies"] if d["field"] == "false_positive"]
        assert len(fps) == 1

    def test_multiple_field_disagreements(self):
        vr = _make_verification_result(
            overall=OverallVerdict.DISAGREE,
            risk_verdict=FieldVerdict.DISAGREE,
            compliance_verdict=FieldVerdict.DISAGREE,
        )
        output = build_output(vr)
        disagree_fields = [d["field"] for d in output["discrepancies"]]
        assert "risk_rating" in disagree_fields
        assert "compliance_status" in disagree_fields


# ============================================================
# Tests: missing fields handled gracefully
# ============================================================

class TestMissingFields:
    """Edge cases with missing or incomplete data."""

    def test_rejected_deal_raises_on_verify(self):
        """A rejected deal has no Stage 2 outputs - verify_deal_swarm should raise."""
        data = _gt001_stage2_json()
        data["screener"]["verdict"] = "reject"
        data["extractor"] = None
        data["risk"] = None
        data["lender"] = None
        data["compliance"] = None
        result = stage2_to_deal_swarm_result(data)
        with pytest.raises(ValueError, match="rejected"):
            from gemini_verifier import verify_deal_swarm
            verify_deal_swarm(result, [])

    def test_output_fields_always_present(self):
        """All expected output keys must exist even on full agreement."""
        vr = _make_verification_result()
        output = build_output(vr)
        required_keys = ["verified", "discrepancies", "gemini_risk_rating",
                         "claude_risk_rating", "confidence", "deal_id"]
        for key in required_keys:
            assert key in output, f"Missing key: {key}"

    def test_load_from_nonexistent_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_stage2_json("/tmp/nonexistent_deal_output_xyz.json")

    @patch("verify_deal_output.verify_deal_swarm")
    def test_mock_mode_returns_agreement(self, mock_verify):
        """In TEST_MODE, verify_deal_swarm returns mock data - wrapper should handle it."""
        mock_verify.return_value = _make_verification_result()
        data = _gt001_stage2_json()
        result = stage2_to_deal_swarm_result(data)
        verification = mock_verify(result, [])
        output = build_output(verification)
        assert output["verified"] is True
