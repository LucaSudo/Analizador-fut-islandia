"""Tests para backend/stats_colectivas.py
Ejecutar desde la raíz: python test_stats_colectivas.py
No requiere credenciales de Supabase ni Groq.
"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
sys.path.insert(0, os.path.dirname(__file__))


# ── Task 1: helpers internos ─────────────────────────────────────────────────

class TestExtaerRango(unittest.TestCase):

    def test_extraer_rango_corners_over(self):
        from backend.stats_colectivas import _extraer_rango
        texto = "[Combinada] Recomendación: Over 9.5 Corners totales. Total esperado: 10.23"
        self.assertEqual(_extraer_rango("corners", texto), "Over 9.5")

    def test_extraer_rango_corners_snap_al_bucket(self):
        from backend.stats_colectivas import _extraer_rango
        texto = "Recomendación: Over 10.1 Corners. Total esperado: 11.0"
        self.assertEqual(_extraer_rango("corners", texto), "Over 10.5")

    def test_extraer_rango_goles(self):
        from backend.stats_colectivas import _extraer_rango
        texto = "Apuesta recomendada: Over 2.5 (Alta)"
        self.assertEqual(_extraer_rango("goles", texto), "Over 2.5")

    def test_extraer_rango_btts_si(self):
        from backend.stats_colectivas import _extraer_rango
        texto = "Recomendación: Ambos Anotan Sí (confianza: Alta)"
        self.assertEqual(_extraer_rango("btts", texto), "Ambos Anotan Sí")

    def test_extraer_rango_btts_no(self):
        from backend.stats_colectivas import _extraer_rango
        texto = "Recomendación: Ambos Anotan No"
        self.assertEqual(_extraer_rango("btts", texto), "Ambos Anotan No")

    def test_extraer_rango_sin_linea(self):
        from backend.stats_colectivas import _extraer_rango
        texto = "Análisis completo del partido..."
        self.assertIsNone(_extraer_rango("corners", texto))


class TestAcumular(unittest.TestCase):

    def test_acumular_nuevo_key(self):
        from backend.stats_colectivas import _acumular
        d = {}
        _acumular(d, "corners__Premier League", True)
        self.assertEqual(d["corners__Premier League"], {"muestras": 1, "aciertos": 1})

    def test_acumular_acumula(self):
        from backend.stats_colectivas import _acumular
        d = {}
        _acumular(d, "goles", True)
        _acumular(d, "goles", False)
        _acumular(d, "goles", True)
        self.assertEqual(d["goles"], {"muestras": 3, "aciertos": 2})


# ── Task 2: refresh_stats y get_track_record ─────────────────────────────────

class TestGetTrackRecord(unittest.TestCase):

    def _mock_stats(self):
        return {
            "por_foco": {
                "corners": {"muestras": 3, "aciertos": 2},   # < 5 (_MIN_MUESTRAS_A), no A
                "goles":   {"muestras": 6, "aciertos": 4},   # ≥ 5, nivel A ok
            },
            "por_foco_liga": {
                "goles__Premier League": {"muestras": 12, "aciertos": 9},
            },
            "por_foco_liga_rango": {
                "goles__Premier League__Over 2.5": {"muestras": 10, "aciertos": 8},
            },
        }

    def test_get_track_record_nivel_c(self):
        from unittest.mock import patch
        import backend.stats_colectivas as sc
        with patch.object(sc, "_cache_stats", self._mock_stats()):
            r = sc.get_track_record("goles", "Premier League", "Over 2.5")
        self.assertIsNotNone(r)
        self.assertEqual(r["nivel"], "C")
        self.assertEqual(r["muestras"], 10)
        self.assertEqual(r["aciertos"], 8)
        self.assertAlmostEqual(r["tasa"], 0.8, places=2)

    def test_get_track_record_fallback_nivel_b(self):
        from unittest.mock import patch
        import backend.stats_colectivas as sc
        with patch.object(sc, "_cache_stats", self._mock_stats()):
            r = sc.get_track_record("goles", "Premier League", "Over 3.5")
        self.assertIsNotNone(r)
        self.assertEqual(r["nivel"], "B")
        self.assertEqual(r["liga"], "Premier League")

    def test_get_track_record_fallback_nivel_a(self):
        from unittest.mock import patch
        import backend.stats_colectivas as sc
        with patch.object(sc, "_cache_stats", self._mock_stats()):
            r = sc.get_track_record("goles", "Liga Inexistente", "Over 2.5")
        self.assertIsNotNone(r)
        self.assertEqual(r["nivel"], "A")
        self.assertIsNone(r["liga"])

    def test_get_track_record_none_sin_datos(self):
        from unittest.mock import patch
        import backend.stats_colectivas as sc
        with patch.object(sc, "_cache_stats", self._mock_stats()):
            r = sc.get_track_record("corners", "Premier League", "Over 9.5")
        self.assertIsNone(r)  # corners: 3 muestras < _MIN_MUESTRAS_A=5

    def test_get_track_record_cache_none(self):
        from unittest.mock import patch
        import backend.stats_colectivas as sc
        with patch.object(sc, "_cache_stats", None):
            r = sc.get_track_record("goles", "Premier League", "Over 2.5")
        self.assertIsNone(r)


# ── Task 3: get_resumen_global ────────────────────────────────────────────────

class TestGetResumenGlobal(unittest.TestCase):

    def _mock_stats(self):
        return {
            "por_foco": {
                "goles":   {"muestras": 10, "aciertos": 7},
                "corners": {"muestras": 20, "aciertos": 13},
                "faltas":  {"muestras": 3,  "aciertos": 1},  # < 5, excluido
            },
            "por_foco_liga": {},
            "por_foco_liga_rango": {},
        }

    def test_get_resumen_global_con_datos(self):
        from unittest.mock import patch
        import backend.stats_colectivas as sc
        with patch.object(sc, "_cache_stats", self._mock_stats()):
            r = sc.get_resumen_global()
        # (7+13)/(10+20) = 20/30 = 66.67% → round = 67%
        self.assertIn("67%", r)
        self.assertIn("goles", r)
        self.assertIn("corners", r)
        self.assertNotIn("faltas", r)

    def test_get_resumen_global_sin_datos(self):
        from unittest.mock import patch
        import backend.stats_colectivas as sc
        with patch.object(sc, "_cache_stats", None):
            self.assertEqual(sc.get_resumen_global(), "")


# ── Task 4: _ajustar_confianza_por_track_record ──────────────────────────────

class TestAjustarConfianza(unittest.TestCase):

    def test_baja_si_mal_track_record(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
        from backend.engine import _ajustar_confianza_por_track_record
        tr = {"nivel": "B", "foco": "corners", "liga": "X", "rango": None,
              "muestras": 10, "aciertos": 4, "tasa": 0.40}
        conf, _ = _ajustar_confianza_por_track_record("corners", "X", "Over 9.5", "Alta 🟢", tr)
        self.assertEqual(conf, "Media 🟡")

    def test_sube_si_buen_track_record(self):
        from backend.engine import _ajustar_confianza_por_track_record
        tr = {"nivel": "B", "foco": "goles", "liga": "X", "rango": None,
              "muestras": 10, "aciertos": 8, "tasa": 0.80}
        conf, _ = _ajustar_confianza_por_track_record("goles", "X", "Over 2.5", "Media 🟡", tr)
        self.assertEqual(conf, "Alta 🟢")

    def test_sin_cambio_en_rango_medio(self):
        from backend.engine import _ajustar_confianza_por_track_record
        tr = {"nivel": "A", "foco": "goles", "liga": None, "rango": None,
              "muestras": 6, "aciertos": 3, "tasa": 0.55}
        conf, _ = _ajustar_confianza_por_track_record("goles", None, None, "Media 🟡", tr)
        self.assertEqual(conf, "Media 🟡")

    def test_none_track_record(self):
        from backend.engine import _ajustar_confianza_por_track_record
        conf, tr = _ajustar_confianza_por_track_record("corners", "X", "Over 9.5", "Alta 🟢", None)
        self.assertEqual(conf, "Alta 🟢")
        self.assertIsNone(tr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
