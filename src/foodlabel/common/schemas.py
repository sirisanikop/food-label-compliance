"""Data contracts for every external interface (D1).

Published as JSON Schema under contracts/ by scripts/export_contracts.py.
Interfaces covered:
  * REST: LabelRequest (in), SubmitResponse / RunView / RunResult (out)
  * REST: FeedbackIn
  * Verifier agent HTTP interface: VerifyRequest / VerifyResponse
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class Route(str, Enum):
    CLEAR_FOR_PRINT = "clear-for-print"
    REVISE_AND_RECHECK = "revise-and-recheck"
    REGULATORY_REVIEW = "regulatory-review"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    DEGRADED = "degraded"
    FAILED = "failed"


TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.DEGRADED, RunStatus.FAILED}


class Basis(str, Enum):
    PER_100G = "per_100g"
    PER_100ML = "per_100ml"


class QuantityUnit(str, Enum):
    G = "g"
    KG = "kg"
    ML = "ml"
    L = "l"


class VegMark(str, Enum):
    VEG = "veg"
    NON_VEG = "non_veg"
    NONE = "none"


# --------------------------------------------------------------------------
# Inbound: the label + package specification
# --------------------------------------------------------------------------


class Nutrition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    basis: Basis
    serving_size: float = Field(gt=0, le=1000, description="g or ml, matching basis")
    energy_kcal: float = Field(ge=0, le=900)
    protein_g: float = Field(ge=0, le=100)
    carbohydrate_g: float = Field(ge=0, le=100)
    total_sugars_g: float = Field(ge=0, le=100)
    added_sugars_g: float = Field(ge=0, le=100)
    total_fat_g: float = Field(ge=0, le=100)
    saturated_fat_g: float = Field(ge=0, le=100)
    trans_fat_g: float = Field(ge=0, le=100)
    sodium_mg: float = Field(ge=0, le=40000)

    @model_validator(mode="after")
    def _cross_field(self) -> "Nutrition":
        if self.added_sugars_g > self.total_sugars_g:
            raise ValueError("added_sugars_g cannot exceed total_sugars_g")
        if self.total_sugars_g > self.carbohydrate_g:
            raise ValueError("total_sugars_g cannot exceed carbohydrate_g")
        if self.saturated_fat_g + self.trans_fat_g > self.total_fat_g + 1e-9:
            raise ValueError(
                "saturated_fat_g + trans_fat_g cannot exceed total_fat_g"
            )
        if self.protein_g + self.carbohydrate_g + self.total_fat_g > 100.5:
            raise ValueError(
                "protein + carbohydrate + total fat cannot exceed 100 per 100 g/ml"
            )
        return self


class LabelRequest(BaseModel):
    """A proposed label and package specification (text only; no artwork)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    product_name: str = Field(min_length=1, max_length=200)
    category_hint: Optional[str] = Field(default=None, max_length=100)
    ingredients_text: str = Field(min_length=1, max_length=5000)
    allergen_statement: Optional[str] = Field(default=None, max_length=1000)
    veg_mark: VegMark
    net_quantity: float = Field(gt=0, le=100000)
    net_quantity_unit: QuantityUnit
    principal_display_panel_cm2: float = Field(
        gt=0, le=10000, description="Area of the principal display panel"
    )
    fssai_license_number: str = Field(pattern=r"^\d{14}$")
    front_of_pack_text: Optional[str] = Field(default=None, max_length=2000)
    claims_declared: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=20
    )
    label_text: Optional[str] = Field(default=None, max_length=20000)
    nutrition: Nutrition

    @model_validator(mode="after")
    def _cross_field(self) -> "LabelRequest":
        solid = self.net_quantity_unit in (QuantityUnit.G, QuantityUnit.KG)
        if solid and self.nutrition.basis != Basis.PER_100G:
            raise ValueError(
                "solid net quantity (g/kg) requires nutrition.basis = per_100g"
            )
        if not solid and self.nutrition.basis != Basis.PER_100ML:
            raise ValueError(
                "liquid net quantity (ml/l) requires nutrition.basis = per_100ml"
            )
        factor = 1000 if self.net_quantity_unit in (QuantityUnit.KG, QuantityUnit.L) else 1
        if self.nutrition.serving_size > self.net_quantity * factor:
            raise ValueError("serving_size cannot exceed the net quantity of the pack")
        return self


# --------------------------------------------------------------------------
# Outbound
# --------------------------------------------------------------------------


class Citation(BaseModel):
    passage_id: str
    quote: str = Field(max_length=600, description="Verbatim text from the passage")
    doc_id: Optional[str] = None
    clause_ref: Optional[str] = None


class Finding(BaseModel):
    requirement_id: str
    claim: Optional[str] = None
    summary: str = ""
    status: Literal["met", "unmet", "needs_review"]
    explanation: str = ""
    citations: list[Citation] = Field(default_factory=list)
    values: Optional[dict[str, Any]] = None


class RunResult(BaseModel):
    run_id: str
    route: Route
    reason: str
    findings: list[Finding] = Field(default_factory=list)
    escalated: bool = False
    escalation_path: list[str] = Field(default_factory=list)
    degraded: bool = False
    withheld_findings: int = 0
    claims_detected: list[str] = Field(default_factory=list)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    index_build_id: Optional[str] = None
    cache_hit: bool = False
    disclaimer: str = (
        "This is a compliance finding with supporting evidence, not an approval. "
        "A regulatory affairs professional makes the print decision."
    )


class SubmitResponse(BaseModel):
    run_id: str
    status: RunStatus
    status_url: str
    trace_url: str
    duplicate: bool = False


class RunView(BaseModel):
    run_id: str
    status: RunStatus
    submitted_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[RunResult] = None
    error: Optional[str] = None


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    satisfactory: bool
    reason: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _reason_when_negative(self) -> "FeedbackIn":
        if not self.satisfactory and not (self.reason or "").strip():
            raise ValueError("a reason is required when satisfactory is false")
        return self


# --------------------------------------------------------------------------
# Verifier agent interface
# --------------------------------------------------------------------------


class PassageRef(BaseModel):
    passage_id: str
    doc_id: str
    doc_role: str  # "labelling" | "claims" (set at ingestion)
    clause_ref: str
    text: str


class VerifyRequest(BaseModel):
    run_id: str
    claims_detected: list[str] = Field(default_factory=list)
    findings: list[Finding]
    retrieved: list[PassageRef]


class VerifyIssue(BaseModel):
    code: str
    finding_index: Optional[int] = None
    detail: str = ""


class VerifyResponse(BaseModel):
    ok: bool
    issues: list[VerifyIssue] = Field(default_factory=list)
    suggested_strategy: Optional[str] = None
    checks_run: list[str] = Field(default_factory=list)
    llm_judge: str = "skipped"  # "passed" | "failed" | "skipped"
