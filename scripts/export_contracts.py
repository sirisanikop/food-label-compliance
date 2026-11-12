"""Export the published data contracts as JSON Schema files under contracts/.

Usage:  python scripts/export_contracts.py
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from foodlabel.common import schemas  # noqa: E402

MODELS = {
    "label_request.v1": schemas.LabelRequest,
    "run_result.v1": schemas.RunResult,
    "run_view.v1": schemas.RunView,
    "submit_response.v1": schemas.SubmitResponse,
    "feedback_in.v1": schemas.FeedbackIn,
    "verify_request.v1": schemas.VerifyRequest,
    "verify_response.v1": schemas.VerifyResponse,
}

out = ROOT / "contracts"
out.mkdir(exist_ok=True)
for name, model in MODELS.items():
    path = out / f"{name}.schema.json"
    path.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8")
    print("wrote", path.relative_to(ROOT))
