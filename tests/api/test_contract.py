import pytest
from pydantic import ValidationError

from conftest import sample_request
from foodlabel.common.schemas import LabelRequest


def test_sample_request_is_valid():
    LabelRequest.model_validate(sample_request())


@pytest.mark.parametrize(
    "mutation",
    [
        {"fssai_license_number": "abc"},
        {"net_quantity": 0},
        {"veg_mark": "maybe"},
        {"product_name": ""},
        {"principal_display_panel_cm2": 99999},
    ],
)
def test_field_level_rules(mutation):
    with pytest.raises(ValidationError):
        LabelRequest.model_validate(sample_request(**mutation))


def test_basis_must_match_unit():
    r = sample_request()
    r["nutrition"]["basis"] = "per_100ml"
    with pytest.raises(ValidationError, match="per_100g"):
        LabelRequest.model_validate(r)


def test_serving_cannot_exceed_pack():
    r = sample_request(net_quantity=20)
    r["nutrition"]["serving_size"] = 30
    with pytest.raises(ValidationError, match="serving_size"):
        LabelRequest.model_validate(r)


def test_macros_cannot_exceed_100():
    r = sample_request()
    r["nutrition"].update(protein_g=40, carbohydrate_g=50, total_sugars_g=10, added_sugars_g=5, total_fat_g=20, saturated_fat_g=2)
    with pytest.raises(ValidationError, match="cannot exceed 100"):
        LabelRequest.model_validate(r)
