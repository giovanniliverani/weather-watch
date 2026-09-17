"""identity.yaml: the file in the repository is what config exposes, and a typo fails loudly."""

import pytest
import yaml

from eww import config


def write(tmp_path, data) -> str:
    path = tmp_path / "identity.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def valid() -> dict:
    return {
        "aggregation": {"radius_km": {"default": 25, "tropical_cyclone": 200}},
        "blocking": {"hazards": {"flood": {"km": 250, "days": 5}}, "named_storm_km": 5000},
        "thresholds": {"auto_merge": 0.9, "proposal": 0.6},
        "score": {"weights": {"spatial": 0.5, "temporal": 0.3, "text": 0.2}},
    }


def test_the_repository_file_is_in_force():
    assert config.IDENTITY["path"].endswith("identity.yaml")
    assert config.aggregation_radius_km("flood") == 25.0
    assert config.aggregation_radius_km("tropical_cyclone") == 200.0
    assert config.aggregation_radius_km("earthquake") == config.aggregation_radius_km(None) == config.aggregation_radius_km("other") == 25.0
    assert config.BLOCKING["flood"] == (250.0, 5.0) and config.BLOCKING["tropical_cyclone"] == (500.0, 10.0)
    assert config.STORM_NAME_BLOCK_KM == 5000.0
    assert config.PROPOSAL_THRESHOLD == 0.6 < config.AUTO_MERGE_THRESHOLD == 0.9
    assert abs(sum(config.SCORE_WEIGHTS.values()) - 1.0) < 1e-9
    assert config.SCORE_KEY_EQUAL == config.SCORE_GLIDE_EQUAL == 1.0 and config.SCORE_STORM_NAME_EQUAL == 0.95


def test_load_fills_the_optional_defaults(tmp_path):
    loaded = config.load_identity(write(tmp_path, valid()))
    assert loaded["aggregation_radius_km"] == {"default": 25.0, "tropical_cyclone": 200.0}
    assert loaded["blocking"] == {"flood": (250.0, 5.0)}
    assert (loaded["key_equal"], loaded["glide_equal"], loaded["storm_name_equal"]) == (1.0, 1.0, 0.95)
    assert loaded["path"].endswith("identity.yaml")


def _unknown_hazard(data):
    data["aggregation"]["radius_km"]["flod"] = 10


def _no_default(data):
    del data["aggregation"]["radius_km"]["default"]


def _proposal_above_auto(data):
    data["thresholds"]["proposal"] = 0.95


def _weights_off(data):
    data["score"]["weights"]["text"] = 0.5


def _negative_km(data):
    data["blocking"]["hazards"]["flood"]["km"] = -1


def _half_block(data):
    data["blocking"]["hazards"]["flood"] = {"km": 250}


def _not_a_number(data):
    data["aggregation"]["radius_km"]["default"] = "25 km"


@pytest.mark.parametrize(
    "mutate, message",
    [
        (_unknown_hazard, "unknown hazard 'flod'"),
        (_no_default, "'default'"),
        (_proposal_above_auto, "below thresholds.auto_merge"),
        (_weights_off, "sum to 1"),
        (_negative_km, "at least 0"),
        (_half_block, "needs 'km' and 'days'"),
        (_not_a_number, "must be a number"),
    ],
)
def test_a_typo_fails_at_load_time(tmp_path, mutate, message):
    data = valid()
    mutate(data)
    with pytest.raises(config.IdentityConfigError, match=message):
        config.load_identity(write(tmp_path, data))


def test_a_missing_file_says_where_it_looked(tmp_path):
    with pytest.raises(config.IdentityConfigError, match="not found"):
        config.load_identity(tmp_path / "nowhere.yaml")
    (tmp_path / "list.yaml").write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(config.IdentityConfigError, match="mapping of sections"):
        config.load_identity(tmp_path / "list.yaml")
