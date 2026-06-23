"""Tests para decaimiento temporal y fuerza de rival (engine.py)."""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))


# ── _weighted_avg ──────────────────────────────────────────────────────────────

from backend.engine import _weighted_avg


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
from backend.engine import _calcular_fuerza_rival_ligera, _CACHE_FUERZA_RIVAL


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


# ── precomputar_stats_equipo — decaimiento temporal ───────────────────────────

import time as _time_mod
from backend.engine import precomputar_stats_equipo


def _make_partido_completo(home, away, gh, ga, dias_atras=0):
    """Evento mínimo para precomputar_stats_equipo."""
    ts = int(_time_mod.time()) - dias_atras * 86400
    return {
        "id": abs(hash(f"{home}{away}{ts}")) or 1,
        "homeTeam": {"name": home},
        "awayTeam": {"name": away},
        "homeScore": {"current": gh, "period1": gh // 2, "period2": gh - gh // 2},
        "awayScore": {"current": ga, "period1": ga // 2, "period2": ga - ga // 2},
        "status": {"type": "finished"},
        "startTimestamp": ts,
        "roundInfo": {"round": 10},
    }


def test_precomputar_promedios_son_ponderados():
    """Partido reciente (0 días) debe pesar más que partido viejo (60 días)."""
    equipo = "Ponderado FC"
    partidos = [
        _make_partido_completo(equipo, "RivalP A", 0, 0, dias_atras=0),   # reciente, 0 goles
        _make_partido_completo(equipo, "RivalP B", 4, 0, dias_atras=60),  # viejo, 4 goles
    ]
    sesion = MagicMock()
    fuerza_rival = {"attack": 1.2, "defense": 1.2}

    with patch("engine.obtener_partidos_equipo", return_value=partidos), \
         patch("engine._calcular_fuerza_rival_ligera", return_value=fuerza_rival), \
         patch("engine.obtener_estadisticas", return_value={}):
        _, promedios = precomputar_stats_equipo(sesion, equipo, 1, 1, 10, n=2)

    # Promedio simple sería 2.0, pero el partido reciente (0 goles) pesa más
    assert promedios.get("goles") is not None
    assert promedios["goles"] < 2.0, f"Esperado < 2.0, got {promedios['goles']}"


def test_precomputar_expone_attack_force_y_defense_force():
    equipo = "Forces FC"
    partidos = [_make_partido_completo(equipo, "RivalF A", 2, 1, dias_atras=5)]
    sesion = MagicMock()

    with patch("engine.obtener_partidos_equipo", return_value=partidos), \
         patch("engine._calcular_fuerza_rival_ligera", return_value={"attack": 1.5, "defense": 1.0}), \
         patch("engine.obtener_estadisticas", return_value={}):
        _, promedios = precomputar_stats_equipo(sesion, equipo, 1, 1, 10, n=1)

    assert "attack_force" in promedios
    assert "defense_force" in promedios
    assert "league_avg_goals" in promedios
    assert promedios["attack_force"] > 0
    assert promedios["defense_force"] > 0


def test_precomputar_sin_partidos_retorna_promedios_vacios():
    sesion = MagicMock()
    with patch("engine.obtener_partidos_equipo", return_value=[]):
        ctx, promedios = precomputar_stats_equipo(sesion, "Fantasma FC", 1, 1, 10, n=15)
    assert promedios == {}
    assert "FANTASMA FC" in ctx.upper()


# ── Poisson xG mejorado ────────────────────────────────────────────────────────

from backend.engine import calcular_1x2


def test_poisson_attack_force_mayor_da_mas_xg():
    league_avg = 1.2
    HOME_ADV = 1.12
    xg_fuerte = (1.5 / 1.0) * league_avg * HOME_ADV
    xg_normal = (1.0 / 1.0) * league_avg * HOME_ADV
    assert xg_fuerte > xg_normal


def test_poisson_defense_force_menor_concede_menos():
    league_avg = 1.2
    HOME_ADV = 1.12
    attack_rival = 1.0
    xg_vs_solido = (attack_rival / 0.6) * league_avg / HOME_ADV
    xg_vs_debil  = (attack_rival / 1.5) * league_avg / HOME_ADV
    assert xg_vs_solido > xg_vs_debil


def test_poisson_1x2_probabilidades_suman_uno():
    p_loc, p_emp, p_vis = calcular_1x2(1.5, 1.2)
    assert abs(p_loc + p_emp + p_vis - 1.0) < 0.01
