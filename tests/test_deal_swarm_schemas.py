"""
Tests for Deal Swarm Pydantic Schemas (INF-086)
Run: python -m pytest /opt/conductor/tests/test_deal_swarm_schemas.py -v
"""

import json
import pytest
from schemas.deal_swarm import (
    ScreenerOutput,
    ExtractorOutput,
    RiskOutput,
    LenderOutput,
    ComplianceOutput,
    DealSwarmResult,
    BorrowerInfo,
    PropertyInfo,
    LoanDetails,
    RiskFactor,
    LenderMatch,
    ComplianceCheck,
    DealVerdict,
    RiskRating,
    PropertyType,
    ExitStrategy,
    LoanPurpose,
    ComplianceFlag,
    LenderFit,
    validate_output,
    scrub_report,
)


# ============================================================
# Fixtures — minimal valid objects
# ============================================================

@pytest.fixture
def valid_screener():
    return {
        "verdict": "proceed",
        "reason": "Complete deal package with appraisal, credit bureau, and application.",
        "borrower_name": "John Smith",
        "property_address": "123 Main St, Vancouver, BC",
        "estimated_loan_amount": 500000,
        "estimated_ltv": 65.0,
        "document_types_found": ["appraisal", "credit_bureau", "application"],
        "missing_critical_docs": [],
        "red_flags": [],
    }


@pytest.fixture
def valid_borrower():
    return {
        "name": "John Smith",
        "is_corporation": False,
        "employment_type": "employed",
        "employer": "Acme Corp",
        "annual_income": 120000,
        "income_verification_method": "T4",
        "credit_score": 720,
        "credit_score_source": "Equifax",
        "existing_mortgages": 1,
        "total_debt_service": 2800,
        "bankruptcies": 0,
        "consumer_proposals": 0,
        "collections": 0,
    }


@pytest.fixture
def valid_property():
    return {
        "address": "123 Main St, Vancouver, BC",
        "city": "Vancouver",
        "province": "BC",
        "property_type": "residential",
        "appraised_value": 800000,
        "purchase_price": 780000,
        "appraisal_date": "2026-03-15",
        "appraiser_name": "BC Appraisals Inc.",
        "year_built": 1995,
        "condition": "Good",
        "environmental_concerns": [],
        "title_concerns": [],
    }


@pytest.fixture
def valid_loan():
    return {
        "purpose": "purchase",
        "requested_amount": 500000,
        "term_months": 12,
        "amortization_months": 12,
        "interest_only": True,
        "mortgage_position": 1,
    }


@pytest.fixture
def valid_extractor(valid_borrower, valid_property, valid_loan):
    return {
        "borrower": valid_borrower,
        "co_borrowers": [],
        "property": valid_property,
        "loan": valid_loan,
        "exit_strategy": "refinance_conventional",
        "exit_strategy_details": "Borrower plans to refinance with a conventional lender within 12 months after credit improvement.",
        "ltv": 64.1,
        "combined_ltv": 64.1,
        "documents_processed": ["appraisal", "credit_bureau", "application"],
        "data_gaps": [],
        "confidence_notes": [],
    }


@pytest.fixture
def valid_risk():
    return {
        "overall_rating": "Medium",
        "overall_score": 2.8,
        "risk_factors": [
            {
                "factor": "LTV ratio",
                "rating": "Low",
                "weight": 0.3,
                "explanation": "LTV of 64.1% is well within acceptable range for private lending.",
            }
        ],
        "ltv_risk": "Low",
        "credit_risk": "Medium",
        "exit_strategy_risk": "Medium",
        "income_risk": "Low",
        "property_risk": "Low",
        "risk_summary": "Moderate risk deal with strong LTV position. Primary concern is credit history requiring improvement for conventional refinance exit.",
        "mitigating_factors": ["Strong LTV", "Verified employment"],
        "aggravating_factors": ["Below-average credit score"],
        "recommended_rate_premium_bps": 200,
        "recommended_conditions": ["Appraisal must be less than 90 days old at funding"],
    }


@pytest.fixture
def valid_lender():
    return {
        "recommended_lenders": [
            {
                "lender_name": "ABC Mortgage Investment Corp",
                "fit": "strong",
                "estimated_rate": "8.99-9.99%",
                "estimated_fee": "1.5%",
                "max_ltv": 75.0,
                "match_reasons": ["Accepts this property type", "Within LTV range"],
                "concerns": [],
            }
        ],
        "deal_positioning": "Position as a straightforward purchase with strong equity and clear exit via conventional refinance.",
        "rate_expectation": "Expected rate range 8.99-10.99% for this risk profile.",
        "fee_expectation": "Expected lender fee 1-2% of loan amount.",
    }


@pytest.fixture
def valid_compliance():
    return {
        "overall_status": "clear",
        "checks": [
            {
                "check_name": "BCFSA Disclosure",
                "category": "BCFSA",
                "status": "clear",
                "detail": "Standard disclosure requirements met. No conflicts of interest identified.",
            }
        ],
        "bcfsa_disclosure_required": True,
        "bcfsa_notes": ["Form 10 required"],
        "aml_flags": [],
        "kyc_checklist_gaps": [],
        "suspicious_transaction_indicators": [],
        "suitability_concerns": [],
    }


# ============================================================
# Screener Tests
# ============================================================

class TestScreener:
    def test_valid_proceed(self, valid_screener):
        result = ScreenerOutput.model_validate(valid_screener)
        assert result.verdict == DealVerdict.PROCEED
        assert result.borrower_name == "John Smith"

    def test_valid_reject(self):
        data = {
            "verdict": "reject",
            "reason": "No appraisal or credit bureau found. Cannot proceed without core documents.",
            "document_types_found": ["application"],
            "missing_critical_docs": ["appraisal", "credit_bureau"],
            "red_flags": ["Incomplete submission"],
        }
        result = ScreenerOutput.model_validate(data)
        assert result.verdict == DealVerdict.REJECT

    def test_reason_too_short(self, valid_screener):
        valid_screener["reason"] = "No"
        with pytest.raises(Exception):
            ScreenerOutput.model_validate(valid_screener)

    def test_invalid_verdict(self, valid_screener):
        valid_screener["verdict"] = "maybe"
        with pytest.raises(Exception):
            ScreenerOutput.model_validate(valid_screener)

    def test_ltv_out_of_range(self, valid_screener):
        valid_screener["estimated_ltv"] = 200
        with pytest.raises(Exception):
            ScreenerOutput.model_validate(valid_screener)


# ============================================================
# Extractor Tests
# ============================================================

class TestExtractor:
    def test_valid_extractor(self, valid_extractor):
        result = ExtractorOutput.model_validate(valid_extractor)
        assert result.ltv == 64.1
        assert result.borrower.credit_score == 720

    def test_high_ltv_allowed(self, valid_extractor):
        valid_extractor["ltv"] = 110.0
        result = ExtractorOutput.model_validate(valid_extractor)
        assert result.ltv == 110.0  # valid in private lending

    def test_missing_documents_processed(self, valid_extractor):
        valid_extractor["documents_processed"] = []
        with pytest.raises(Exception):
            ExtractorOutput.model_validate(valid_extractor)

    def test_credit_score_range(self, valid_extractor):
        valid_extractor["borrower"]["credit_score"] = 200
        with pytest.raises(Exception):
            ExtractorOutput.model_validate(valid_extractor)

    def test_negative_income(self, valid_extractor):
        valid_extractor["borrower"]["annual_income"] = -50000
        with pytest.raises(Exception):
            ExtractorOutput.model_validate(valid_extractor)

    def test_co_borrowers(self, valid_extractor):
        valid_extractor["co_borrowers"] = [
            {"name": "Jane Smith", "credit_score": 680}
        ]
        result = ExtractorOutput.model_validate(valid_extractor)
        assert len(result.co_borrowers) == 1


# ============================================================
# Risk Tests
# ============================================================

class TestRisk:
    def test_valid_risk(self, valid_risk):
        result = RiskOutput.model_validate(valid_risk)
        assert result.overall_rating == RiskRating.MEDIUM

    def test_inconsistent_score_high_rating_low_score(self, valid_risk):
        valid_risk["overall_score"] = 1.5
        valid_risk["overall_rating"] = "High"
        with pytest.raises(Exception):
            RiskOutput.model_validate(valid_risk)

    def test_inconsistent_score_low_rating_high_score(self, valid_risk):
        valid_risk["overall_score"] = 4.5
        valid_risk["overall_rating"] = "Low"
        with pytest.raises(Exception):
            RiskOutput.model_validate(valid_risk)

    def test_risk_factor_weight_range(self, valid_risk):
        valid_risk["risk_factors"][0]["weight"] = 1.5
        with pytest.raises(Exception):
            RiskOutput.model_validate(valid_risk)

    def test_empty_risk_factors(self, valid_risk):
        valid_risk["risk_factors"] = []
        with pytest.raises(Exception):
            RiskOutput.model_validate(valid_risk)


# ============================================================
# Lender Tests
# ============================================================

class TestLender:
    def test_valid_lender(self, valid_lender):
        result = LenderOutput.model_validate(valid_lender)
        assert len(result.recommended_lenders) == 1
        assert result.recommended_lenders[0].fit == LenderFit.STRONG

    def test_empty_lenders(self, valid_lender):
        valid_lender["recommended_lenders"] = []
        with pytest.raises(Exception):
            LenderOutput.model_validate(valid_lender)

    def test_too_many_lenders(self, valid_lender):
        valid_lender["recommended_lenders"] = [valid_lender["recommended_lenders"][0]] * 11
        with pytest.raises(Exception):
            LenderOutput.model_validate(valid_lender)


# ============================================================
# Compliance Tests
# ============================================================

class TestCompliance:
    def test_valid_compliance(self, valid_compliance):
        result = ComplianceOutput.model_validate(valid_compliance)
        assert result.overall_status == ComplianceFlag.CLEAR

    def test_fail_requires_action(self, valid_compliance):
        valid_compliance["overall_status"] = "fail"
        valid_compliance["checks"][0]["status"] = "fail"
        # No action_required set — should fail validation
        with pytest.raises(Exception):
            ComplianceOutput.model_validate(valid_compliance)

    def test_fail_with_action_passes(self, valid_compliance):
        valid_compliance["overall_status"] = "fail"
        valid_compliance["checks"][0]["status"] = "fail"
        valid_compliance["checks"][0]["action_required"] = "Obtain updated KYC documentation"
        result = ComplianceOutput.model_validate(valid_compliance)
        assert result.overall_status == ComplianceFlag.FAIL


# ============================================================
# DealSwarmResult Tests
# ============================================================

class TestDealSwarmResult:
    def test_proceed_requires_all_stage2(
        self, valid_screener, valid_extractor, valid_risk, valid_lender, valid_compliance
    ):
        result = DealSwarmResult.model_validate({
            "screener": valid_screener,
            "extractor": valid_extractor,
            "risk": valid_risk,
            "lender": valid_lender,
            "compliance": valid_compliance,
            "processing_timestamp": "2026-04-11T10:00:00-07:00",
        })
        assert result.screener.verdict == DealVerdict.PROCEED

    def test_proceed_missing_extractor_fails(
        self, valid_screener, valid_risk, valid_lender, valid_compliance
    ):
        with pytest.raises(Exception):
            DealSwarmResult.model_validate({
                "screener": valid_screener,
                "risk": valid_risk,
                "lender": valid_lender,
                "compliance": valid_compliance,
                "processing_timestamp": "2026-04-11T10:00:00-07:00",
            })

    def test_reject_no_stage2_ok(self):
        screener = {
            "verdict": "reject",
            "reason": "Incomplete submission — missing all critical documents.",
        }
        result = DealSwarmResult.model_validate({
            "screener": screener,
            "processing_timestamp": "2026-04-11T10:00:00-07:00",
        })
        assert result.extractor is None


# ============================================================
# validate_output() Tests
# ============================================================

class TestValidateOutput:
    def test_valid_json_string(self, valid_screener):
        ok, result = validate_output(ScreenerOutput, json.dumps(valid_screener))
        assert ok is True
        assert isinstance(result, ScreenerOutput)

    def test_valid_dict(self, valid_screener):
        ok, result = validate_output(ScreenerOutput, valid_screener)
        assert ok is True

    def test_invalid_json(self):
        ok, error = validate_output(ScreenerOutput, "not json at all {{{")
        assert ok is False
        assert "Invalid JSON" in error

    def test_validation_failure(self):
        ok, error = validate_output(ScreenerOutput, {"verdict": "invalid"})
        assert ok is False
        assert "Validation failed" in error


# ============================================================
# scrub_report() Tests
# ============================================================

class TestScrubReport:
    def test_clean_report(
        self, valid_screener, valid_extractor, valid_risk, valid_lender, valid_compliance
    ):
        result = DealSwarmResult.model_validate({
            "screener": valid_screener,
            "extractor": valid_extractor,
            "risk": valid_risk,
            "lender": valid_lender,
            "compliance": valid_compliance,
            "processing_timestamp": "2026-04-11T10:00:00-07:00",
        })
        report = scrub_report(result)
        assert report["valid"] is True
        assert len(report["issues"]) == 0

    def test_compliance_fail_flagged(
        self, valid_screener, valid_extractor, valid_risk, valid_lender, valid_compliance
    ):
        valid_compliance["overall_status"] = "fail"
        valid_compliance["checks"][0]["status"] = "fail"
        valid_compliance["checks"][0]["action_required"] = "Fix KYC"
        result = DealSwarmResult.model_validate({
            "screener": valid_screener,
            "extractor": valid_extractor,
            "risk": valid_risk,
            "lender": valid_lender,
            "compliance": valid_compliance,
            "processing_timestamp": "2026-04-11T10:00:00-07:00",
        })
        report = scrub_report(result)
        assert report["valid"] is False
        assert any("FAILED" in i for i in report["issues"])

    def test_str_indicators_flagged(
        self, valid_screener, valid_extractor, valid_risk, valid_lender, valid_compliance
    ):
        valid_compliance["suspicious_transaction_indicators"] = [
            "Large unexplained cash deposit"
        ]
        result = DealSwarmResult.model_validate({
            "screener": valid_screener,
            "extractor": valid_extractor,
            "risk": valid_risk,
            "lender": valid_lender,
            "compliance": valid_compliance,
            "processing_timestamp": "2026-04-11T10:00:00-07:00",
        })
        report = scrub_report(result)
        assert report["valid"] is False
        assert any("L0 Human Only" in i for i in report["issues"])
