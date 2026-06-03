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


# ── _calcular_fuerza_rival_ligera ─────────────────────────────────────────────

from unittest.mock import patch, MagicMock
from engine import _calcular_fuerza_rival_ligera, _CACHE_FUERZA_RIVAL


def _make_evento(home_goles, away_goles, home_name="Rival FC", away_name="Otro FC"):
    return {
        "homeTeam": {"name": home_name},
        "awayTeam": {"name": away_name},
        "homeScore": {"current": home_goles},
        "awayScore": {"current": away_goles},
        "status": {"type": "finished"},
        "startTimestamp": 1700000000,
    }


def test_fuerza_rival_calcula_ataque_y_defensa():
    partidos = [
        _make_evento(2, 1),
        _make_evento(3, 0),
        _make_evento(1, 2),
    ]
    sesion = MagicMock()
    with patch("engine.obtener_partidos_equipo", return_value=partidos):
        result = _calcular_fuerza_rival_ligera(sesion, "Rival FC", 1, 1, 10)
    assert abs(result["attack"]  - 2.0) < 0.01
    assert abs(result["defense"] - 1.0) < 0.01


def test_fuerza_rival_default_cuando_sin_partidos():
    sesion = MagicMock()
    with patch("engine.obtener_partidos_equipo", return_value=[]):
        result = _calcular_fuerza_rival_ligera(sesion, "Sin Datos FC", 1, 1, 10)
    assert result["attack"]  == 1.2
    assert result["defense"] == 1.2


def test_fuerza_rival_usa_cache():
    import time
    cache_key = "cached team_99"
    _CACHE_FUERZA_RIVAL[cache_key] = (time.time(), {"attack": 2.5, "defense": 0.8})
    sesion = MagicMock()
    with patch("engine.obtener_partidos_equipo") as mock_fetch:
        result = _calcular_fuerza_rival_ligera(sesion, "Cached Team", 99, 1, 10)
    mock_fetch.assert_not_called()
    assert result["attack"] == 2.5


def test_fuerza_rival_calcula_visitante_correctamente():
    # Usa nombre único para evitar colisión con cache de tests anteriores
    partidos = [_make_evento(1, 3, home_name="Local CF", away_name="Visitante FC")]
    sesion = MagicMock()
    with patch("engine.obtener_partidos_equipo", return_value=partidos):
        result = _calcular_fuerza_rival_ligera(sesion, "Visitante FC", 1, 1, 10)
    assert result["attack"]  == 3.0
    assert result["defense"] == 1.0
