"""Tests para decaimiento temporal y fuerza de rival (engine.py)."""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))


# ── _weighted_avg ──────────────────────────────────────────────────────────────

from engine import _weighted_avg


def test_weighted_avg_basic():
    pairs = [(10.0, 1.0), (20.0, 1.0)]
    assert abs(_weighted_avg(pairs) - 15.0) < 0.001


def test_weighted_avg_heavier_recent():
    """Valor más reciente (peso 2.0) pesa más que el viejo (peso 1.0)."""
    pairs = [(5.0, 1.0), (15.0, 2.0)]
    expected = (5.0 * 1.0 + 15.0 * 2.0) / (1.0 + 2.0)
    assert abs(_weighted_avg(pairs) - expected) < 0.001


def test_weighted_avg_empty_returns_none():
    assert _weighted_avg([]) is None


def test_weighted_avg_zero_weights_returns_none():
    assert _weighted_avg([(5.0, 0.0), (10.0, 0.0)]) is None


# ── temporal decay weight ──────────────────────────────────────────────────────

def test_temporal_weight_today_is_one():
    w = math.exp(-0.02 * 0)
    assert abs(w - 1.0) < 0.001


def test_temporal_weight_30_days_is_55_percent():
    w = math.exp(-0.02 * 30)
    assert 0.50 < w < 0.60


def test_temporal_weight_decays_monotonically():
    weights = [math.exp(-0.02 * d) for d in [0, 7, 30, 60, 90]]
    assert all(weights[i] > weights[i + 1] for i in range(len(weights) - 1))
