"""
Deal Swarm — Pydantic v2 Validation Schemas (INF-086)
=====================================================
Two-stage hierarchical-parallel architecture:
  Stage 1: Screener (fast triage — proceed or reject)
  Stage 2: Four parallel agents (Extractor, Risk, Lender, Compliance)

All LLM outputs pass through these schemas as the FIRST validation gate
(deterministic, instant, free) before the behavioral watchdog.

Usage:
    from schemas.deal_swarm import validate_output, ScreenerOutput, ExtractorOutput
    result = validate_output(ScreenerOutput, raw_json_string)
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ============================================================
# Enums
# ============================================================

class DealVerdict(str, Enum):
    PROCEED = "proceed"
    REJECT = "reject"
    MANUAL_REVIEW = "manual_review"


class RiskRating(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


class PropertyType(str, Enum):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    MIXED_USE = "mixed_use"
    LAND = "land"
    MULTI_FAMILY = "multi_family"
    INDUSTRIAL = "industrial"
    OTHER = "other"


class ExitStrategy(str, Enum):
    REFINANCE_CONVENTIONAL = "refinance_conventional"
    REFINANCE_ALT_A = "refinance_alt_a"
    SALE = "sale"
    CONSTRUCTION_COMPLETION = "construction_completion"
    BUSINESS_REVENUE = "business_revenue"
    OTHER = "other"


class LoanPurpose(str, Enum):
    PURCHASE = "purchase"
    REFINANCE = "refinance"
    ETO = "equity_takeout"
    CONSTRUCTION = "construction"
    BRIDGE = "bridge"
    DEBT_CONSOLIDATION = "debt_consolidation"
    OTHER = "other"


class ComplianceFlag(str, Enum):
    CLEAR = "clear"
    WARNING = "warning"
    FAIL = "fail"


class LenderFit(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


# ============================================================
# Stage 1: Screener Output
# ============================================================

class ScreenerOutput(BaseModel):
    """Stage 1 — Fast triage. Determines if deal proceeds to Stage 2."""

    verdict: DealVerdict = Field(
        description="proceed = send to Stage 2, reject = not viable, manual_review = unclear"
    )
    reason: str = Field(
        min_length=10,
        max_length=500,
        description="Brief explanation of verdict"
    )
    borrower_name: Optional[str] = Field(
        default=None, description="Primary borrower name if identifiable"
    )
    property_address: Optional[str] = Field(
        default=None, description="Subject property address if identifiable"
    )
    estimated_loan_amount: Optional[float] = Field(
        default=None, ge=0, description="Approximate loan amount in CAD"
    )
    estimated_ltv: Optional[float] = Field(
        default=None, ge=0, le=150, description="Approximate LTV percentage"
    )
    document_types_found: list[str] = Field(
        default_factory=list,
        description="Types of documents identified (e.g., 'appraisal', 'credit_bureau', 'application')"
    )
    missing_critical_docs: list[str] = Field(
        default_factory=list,
        description="Critical documents not found in submission"
    )
    red_flags: list[str] = Field(
        default_factory=list,
        description="Any immediate concerns (fraud indicators, missing info, etc.)"
    )


# ============================================================
# Stage 2A: Extractor Output
# ============================================================

class BorrowerInfo(BaseModel):
    """Borrower details extracted from application and credit bureau."""
    name: str = Field(min_length=1)
    is_corporation: bool = False
    employment_type: Optional[str] = None  # employed, self-employed, retired, etc.
    employer: Optional[str] = None
    annual_income: Optional[float] = Field(default=None, ge=0)
    income_verification_method: Optional[str] = None  # T4, NOA, bank statements, stated
    credit_score: Optional[int] = Field(default=None, ge=300, le=900)
    credit_score_source: Optional[str] = None  # Equifax, TransUnion
    existing_mortgages: Optional[int] = Field(default=None, ge=0)
    total_debt_service: Optional[float] = Field(default=None, ge=0)
    bankruptcies: Optional[int] = Field(default=None, ge=0)
    consumer_proposals: Optional[int] = Field(default=None, ge=0)
    collections: Optional[int] = Field(default=None, ge=0)


class PropertyInfo(BaseModel):
    """Property details extracted from appraisal and application."""
    address: str = Field(min_length=1)
    city: Optional[str] = None
    province: str = Field(default="BC")
    property_type: PropertyType
    appraised_value: Optional[float] = Field(default=None, ge=0)
    purchase_price: Optional[float] = Field(default=None, ge=0)
    appraisal_date: Optional[str] = None
    appraiser_name: Optional[str] = None
    zoning: Optional[str] = None
    lot_size: Optional[str] = None
    year_built: Optional[int] = Field(default=None, ge=1800, le=2030)
    condition: Optional[str] = None
    environmental_concerns: list[str] = Field(default_factory=list)
    title_concerns: list[str] = Field(default_factory=list)


class LoanDetails(BaseModel):
    """Loan structure extracted from application."""
    purpose: LoanPurpose
    requested_amount: float = Field(ge=0)
    term_months: Optional[int] = Field(default=None, ge=1, le=360)
    amortization_months: Optional[int] = Field(default=None, ge=1, le=480)
    interest_only: bool = False
    existing_first_mortgage_balance: Optional[float] = Field(default=None, ge=0)
    existing_second_mortgage_balance: Optional[float] = Field(default=None, ge=0)
    mortgage_position: Optional[int] = Field(default=None, ge=1, le=4)
    prepayment_penalty: Optional[float] = Field(default=None, ge=0)


class ExtractorOutput(BaseModel):
    """Stage 2A — Deep document extraction."""

    borrower: BorrowerInfo
    co_borrowers: list[BorrowerInfo] = Field(default_factory=list)
    property: PropertyInfo
    loan: LoanDetails
    exit_strategy: ExitStrategy
    exit_strategy_details: str = Field(
        min_length=10, max_length=1000,
        description="Narrative explanation of how borrower will exit the mortgage"
    )

    # Calculated fields
    ltv: float = Field(ge=0, le=200, description="LTV as percentage (e.g., 75.5)")
    combined_ltv: Optional[float] = Field(
        default=None, ge=0, le=200,
        description="Combined LTV including all mortgage positions"
    )
    gds_ratio: Optional[float] = Field(default=None, ge=0, le=100)
    tds_ratio: Optional[float] = Field(default=None, ge=0, le=100)

    # Extraction metadata
    documents_processed: list[str] = Field(min_length=1)
    data_gaps: list[str] = Field(
        default_factory=list,
        description="Fields that could not be extracted from source documents"
    )
    confidence_notes: list[str] = Field(
        default_factory=list,
        description="Notes on data quality or conflicting information"
    )

    @field_validator("ltv")
    @classmethod
    def ltv_sanity_check(cls, v: float) -> float:
        if v > 100:
            pass  # valid — high LTV deals exist in private lending
        return v


# ============================================================
# Stage 2B: Risk Output
# ============================================================

class RiskFactor(BaseModel):
    """Individual risk factor assessment."""
    factor: str = Field(min_length=1, description="Name of risk factor")
    rating: RiskRating
    weight: float = Field(ge=0, le=1, description="Relative importance 0-1")
    explanation: str = Field(min_length=10, max_length=500)


class RiskOutput(BaseModel):
    """Stage 2B — Risk analysis."""

    overall_rating: RiskRating
    overall_score: float = Field(
        ge=0, le=100,
        description="Numeric risk score: 0=lowest risk, 100=highest risk"
    )
    risk_factors: list[RiskFactor] = Field(min_length=1)

    # Key risk assessments
    ltv_risk: RiskRating
    credit_risk: RiskRating
    exit_strategy_risk: RiskRating
    income_risk: RiskRating
    property_risk: RiskRating

    # Narrative
    risk_summary: str = Field(
        min_length=20, max_length=2000,
        description="Narrative risk assessment for the Executive Loan Summary"
    )
    mitigating_factors: list[str] = Field(default_factory=list)
    aggravating_factors: list[str] = Field(default_factory=list)

    # Recommendations
    recommended_rate_premium_bps: Optional[int] = Field(
        default=None, ge=0, le=2000,
        description="Suggested rate premium in basis points above base rate"
    )
    recommended_conditions: list[str] = Field(
        default_factory=list,
        description="Suggested conditions for the commitment letter"
    )

    @model_validator(mode="after")
    def validate_consistency(self) -> "RiskOutput":
        """Ensure overall_rating is consistent with overall_score."""
        score = self.overall_score
        rating = self.overall_rating
        # Loose consistency check — LLM should be roughly aligned
        if score < 25 and rating in (RiskRating.HIGH, RiskRating.VERY_HIGH):
            raise ValueError(
                f"Inconsistent: overall_score={score} but overall_rating={rating}"
            )
        if score > 75 and rating in (RiskRating.LOW,):
            raise ValueError(
                f"Inconsistent: overall_score={score} but overall_rating={rating}"
            )
        return self


# ============================================================
# Stage 2C: Lender Match Output
# ============================================================

class LenderMatch(BaseModel):
    """Individual lender recommendation."""
    lender_name: str = Field(min_length=1)
    fit: LenderFit
    estimated_rate: Optional[str] = Field(
        default=None, description="Estimated rate range (e.g., '8.99-10.99%')"
    )
    estimated_fee: Optional[str] = Field(
        default=None, description="Estimated lender fee (e.g., '1.5-2%')"
    )
    max_ltv: Optional[float] = Field(default=None, ge=0, le=100)
    term_range: Optional[str] = None
    match_reasons: list[str] = Field(
        min_length=1, description="Why this lender fits"
    )
    concerns: list[str] = Field(
        default_factory=list, description="Potential issues with this lender"
    )
    zoho_lender_id: Optional[str] = Field(
        default=None, description="Zoho CRM Lender record ID if known"
    )


class LenderOutput(BaseModel):
    """Stage 2C — Lender matching."""

    recommended_lenders: list[LenderMatch] = Field(
        min_length=1, max_length=10,
        description="Lenders ranked by fit, best first"
    )
    deal_positioning: str = Field(
        min_length=20, max_length=1000,
        description="How to position this deal for best lender reception"
    )
    rate_expectation: str = Field(
        min_length=10, max_length=300,
        description="Expected rate range for this deal profile"
    )
    fee_expectation: str = Field(
        min_length=10, max_length=300,
        description="Expected fee range for this deal profile"
    )
    market_conditions_note: Optional[str] = Field(
        default=None, max_length=500,
        description="Relevant market conditions affecting lender appetite"
    )


# ============================================================
# Stage 2D: Compliance Output
# ============================================================

class ComplianceCheck(BaseModel):
    """Individual compliance check result."""
    check_name: str = Field(min_length=1)
    category: str = Field(description="BCFSA, AML, KYC, FINTRAC, or General")
    status: ComplianceFlag
    detail: str = Field(min_length=10, max_length=500)
    action_required: Optional[str] = Field(
        default=None, max_length=300,
        description="What Josh needs to do if status is warning or fail"
    )


class ComplianceOutput(BaseModel):
    """Stage 2D — Compliance validation.

    CRITICAL: This is a DRAFT. All compliance determinations require
    Josh's manual review. AML/KYC decisions are L0 Human Only per
    FINTRAC requirements.
    """

    overall_status: ComplianceFlag = Field(
        description="clear=no issues, warning=needs review, fail=cannot proceed"
    )
    checks: list[ComplianceCheck] = Field(min_length=1)

    # BCFSA specific
    bcfsa_disclosure_required: bool = Field(
        description="Whether BCFSA Form 10 or other disclosure is needed"
    )
    bcfsa_notes: list[str] = Field(default_factory=list)

    # AML/KYC (flagging only — all decisions are L0 Human Only)
    aml_flags: list[str] = Field(
        default_factory=list,
        description="Potential AML concerns for Josh to investigate (L0 Human Only)"
    )
    kyc_checklist_gaps: list[str] = Field(
        default_factory=list,
        description="Missing KYC documentation"
    )
    suspicious_transaction_indicators: list[str] = Field(
        default_factory=list,
        description="STR indicators — Josh must evaluate personally (FINTRAC requirement)"
    )

    # Suitability
    suitability_concerns: list[str] = Field(
        default_factory=list,
        description="Concerns about whether this mortgage is suitable for the borrower"
    )

    @model_validator(mode="after")
    def validate_fail_has_actions(self) -> "ComplianceOutput":
        """If overall status is fail, at least one check must have action_required."""
        if self.overall_status == ComplianceFlag.FAIL:
            has_action = any(c.action_required for c in self.checks if c.status == ComplianceFlag.FAIL)
            if not has_action:
                raise ValueError("Compliance status is 'fail' but no check specifies action_required")
        return self


# ============================================================
# Composite Output (full deal swarm result)
# ============================================================

class DealSwarmResult(BaseModel):
    """Complete deal swarm output combining all Stage 2 agents."""

    screener: ScreenerOutput
    extractor: Optional[ExtractorOutput] = Field(
        default=None, description="None if screener verdict was reject"
    )
    risk: Optional[RiskOutput] = None
    lender: Optional[LenderOutput] = None
    compliance: Optional[ComplianceOutput] = None

    # Metadata
    deal_id: Optional[str] = None
    processing_timestamp: str = Field(description="ISO 8601 timestamp")
    total_tokens_used: Optional[int] = Field(default=None, ge=0)
    total_cost_cad: Optional[float] = Field(default=None, ge=0)
    model_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Which model was used for each stage"
    )

    @model_validator(mode="after")
    def validate_stage2_present_if_proceed(self) -> "DealSwarmResult":
        """If screener says proceed, Stage 2 outputs must exist."""
        if self.screener.verdict == DealVerdict.PROCEED:
            missing = []
            if self.extractor is None:
                missing.append("extractor")
            if self.risk is None:
                missing.append("risk")
            if self.lender is None:
                missing.append("lender")
            if self.compliance is None:
                missing.append("compliance")
            if missing:
                raise ValueError(
                    f"Screener verdict is 'proceed' but missing Stage 2 outputs: {missing}"
                )
        return self


# ============================================================
# Validation Helper
# ============================================================

def validate_output(schema_class: type[BaseModel], raw_json: str | dict) -> tuple[bool, BaseModel | str]:
    """Validate raw JSON against a Pydantic schema.

    Returns:
        (True, parsed_model) on success
        (False, error_message) on failure
    """
    import json

    try:
        if isinstance(raw_json, str):
            data = json.loads(raw_json)
        else:
            data = raw_json

        result = schema_class.model_validate(data)
        return True, result

    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"
    except Exception as e:
        return False, f"Validation failed: {e}"


def scrub_report(result: DealSwarmResult) -> dict:
    """Generate a validation report for a complete deal swarm result.

    Returns dict with pass/fail status and any issues found.
    """
    report = {
        "valid": True,
        "screener_verdict": result.screener.verdict.value,
        "issues": [],
        "warnings": [],
    }

    # Check for data gaps
    if result.extractor and result.extractor.data_gaps:
        report["warnings"].append(
            f"Data gaps in extraction: {result.extractor.data_gaps}"
        )

    # Check compliance
    if result.compliance:
        if result.compliance.overall_status == ComplianceFlag.FAIL:
            report["issues"].append("Compliance check FAILED — deal cannot proceed without resolution")
        if result.compliance.suspicious_transaction_indicators:
            report["issues"].append(
                f"STR indicators flagged — L0 Human Only review required: "
                f"{result.compliance.suspicious_transaction_indicators}"
            )
        if result.compliance.aml_flags:
            report["warnings"].append(
                f"AML flags for review: {result.compliance.aml_flags}"
            )

    # Check risk
    if result.risk:
        if result.risk.overall_rating == RiskRating.VERY_HIGH:
            report["warnings"].append(
                f"Very high risk rating (score: {result.risk.overall_score})"
            )

    if report["issues"]:
        report["valid"] = False

    return report
