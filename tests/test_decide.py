import pytest

from kino.db import models, session
from kino.pipeline.decide import Decision, Thresholds, decide
from kino.pipeline.matcher import KidInfo, KidScore
from kino.pipeline.quality import Tier

TH = Thresholds()


def kid(auto=True, offset=0.0) -> KidInfo:
    return KidInfo(1, auto, offset)


def sc(score: float, margin: float = 0.5) -> list[KidScore]:
    return [KidScore(1, score, margin), KidScore(2, score - margin, margin)]


def run(tier, score, margin=0.5, k=None, mapped=True):
    return decide(tier, sc(score, margin), k or kid(), TH, mapped)


def test_empty_scores_drop():
    d = decide(Tier.L, [], kid(), TH, True)
    assert d == Decision(None, "drop", 0.0, 0.0, d.reason) and d.reason


def test_xs_drops_even_with_high_score():
    assert run(Tier.XS, 0.99).action == "drop"


def test_s_never_auto():
    assert run(Tier.S, 0.99).action == "review"
    assert run(Tier.S, 0.30).action == "drop"


def test_tier_l_unmapped_boundaries():
    # unmapped: auto 0.45+0.05=0.50, review 0.32+0.05=0.37
    assert run(Tier.L, 0.501, mapped=False).action == "auto_save"
    assert run(Tier.L, 0.499, mapped=False).action == "review"
    assert run(Tier.L, 0.371, mapped=False).action == "review"
    assert run(Tier.L, 0.369, mapped=False).action == "drop"


def test_tier_l_mapped_boundaries():
    # mapped: auto 0.42, review 0.29
    assert run(Tier.L, 0.421).action == "auto_save"
    assert run(Tier.L, 0.419).action == "review"
    assert run(Tier.L, 0.291).action == "review"
    assert run(Tier.L, 0.289).action == "drop"


def test_tier_m_thresholds():
    assert run(Tier.M, 0.471).action == "auto_save"  # 0.50 - 0.03
    assert run(Tier.M, 0.469).action == "review"
    assert run(Tier.M, 0.321).action == "review"  # 0.35 - 0.03
    assert run(Tier.M, 0.319).action == "drop"


def test_margin_below_min_forces_review():
    d = run(Tier.L, 0.9, margin=0.05)
    assert d.action == "review" and "margin" in d.reason


def test_margin_exactly_min_passes():
    assert run(Tier.L, 0.9, margin=0.08).action == "auto_save"


def test_auto_save_disabled_demotes():
    d = run(Tier.L, 0.9, k=kid(auto=False))
    assert d.action == "review" and d.kid_id == 1


def test_threshold_offset_shifts_both():
    k = kid(offset=0.1)  # mapped: auto 0.52, review 0.39
    assert run(Tier.L, 0.521, k=k).action == "auto_save"
    assert run(Tier.L, 0.519, k=k).action == "review"
    assert run(Tier.L, 0.391, k=k).action == "review"
    assert run(Tier.L, 0.389, k=k).action == "drop"


def test_decision_fields():
    d = run(Tier.L, 0.9, margin=0.4)
    assert (d.kid_id, d.score, d.margin) == (1, 0.9, 0.4)


def test_from_settings_default_and_override(tmp_path):
    eng = session.init_engine(f"sqlite:///{tmp_path / 'k.db'}")
    models.Base.metadata.create_all(eng)
    with session.get_session() as s:
        assert Thresholds.from_settings(s) == Thresholds()
        s.add(
            models.Setting(
                key="thresholds",
                value={"L": {"auto": 0.6, "review": 0.4}, "prior_bonus": 0.05},
            )
        )
        s.flush()
        t = Thresholds.from_settings(s)
    assert t.L_auto == pytest.approx(0.6) and t.L_review == pytest.approx(0.4)
    assert t.M_auto == 0.50 and t.prior_bonus == 0.05
    eng.dispose()


def test_float32_score_on_threshold_auto_saves():
    import numpy as np

    score = float(np.float32(0.42))  # 0.41999999..., mapped L auto threshold is 0.42
    assert run(Tier.L, score).action == "auto_save"


def test_kid_mismatch_raises():
    with pytest.raises(ValueError):
        decide(Tier.L, sc(0.9), KidInfo(2, True, 0.0), TH, True)


@pytest.mark.parametrize(
    "bad",
    [
        {"L": {"auto": float("nan"), "review": 0.4}},
        {"L": {"auto": "0.6", "review": 0.4}},
        {"L": {"auto": 0.3, "review": 0.4}},
        {"L": {"auto": 5, "review": 0.4}},
    ],
)
def test_invalid_overrides_fall_back(tmp_path, bad):
    eng = session.init_engine(f"sqlite:///{tmp_path / 'k.db'}")
    models.Base.metadata.create_all(eng)
    with session.get_session() as s:
        s.add(models.Setting(key="thresholds", value={**bad, "M": {"auto": 0.6, "review": 0.4}}))
        s.flush()
        t = Thresholds.from_settings(s)
    assert (t.L_auto, t.L_review) == (0.45, 0.32)
    assert (t.M_auto, t.M_review) == (0.6, 0.4)  # valid tier still applied
    eng.dispose()
