"""
test_pred_fields.py — Tests para ganancia, probabilidad_modelo y edge.
Ejecutar desde la raíz: python test_pred_fields.py
No requiere credenciales de Supabase ni Groq.
"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
sys.path.insert(0, os.path.dirname(__file__))


class TestGananciaCalculo(unittest.TestCase):
    """Verifica cálculo de ganancia al verificar predicciones."""

    def _ganancia(self, acerto, cuota):
        from memory import _calcular_ganancia
        return _calcular_ganancia(acerto, cuota)

    def test_ganancia_acierto_con_cuota(self):
        self.assertAlmostEqual(self._ganancia(True, 1.80), 0.80, places=4)

    def test_ganancia_fallo_con_cuota(self):
        self.assertAlmostEqual(self._ganancia(False, 2.50), -1.0, places=4)

    def test_ganancia_sin_cuota_true(self):
        self.assertIsNone(self._ganancia(True, None))

    def test_ganancia_sin_cuota_false(self):
        self.assertIsNone(self._ganancia(False, None))

    def test_ganancia_acerto_none(self):
        self.assertIsNone(self._ganancia(None, 1.80))


class TestProbabilidadModelo(unittest.TestCase):
    """Verifica conversión rough de total esperado a probabilidad."""

    def _prob(self, total):
        from memory import _prob_desde_total
        return _prob_desde_total(total)

    def test_prob_total_9(self):
        self.assertAlmostEqual(self._prob(9.0), 0.9, places=4)

    def test_prob_total_0(self):
        self.assertAlmostEqual(self._prob(0.0), 0.0, places=4)

    def test_prob_resultado_redondeado(self):
        expected = round(13.5 / 14.5, 4)
        self.assertAlmostEqual(self._prob(13.5), expected, places=4)


class TestEdgeCalculo(unittest.TestCase):
    """Verifica cálculo de edge (probabilidad modelo − probabilidad implícita)."""

    def _edge(self, probabilidad_modelo, cuota):
        from memory import _calcular_edge
        return _calcular_edge(probabilidad_modelo, cuota)

    def test_edge_positivo(self):
        # prob=0.70, cuota=1.80 → implícita=1/1.80≈0.5556 → edge≈14.4
        result = self._edge(0.70, 1.80)
        self.assertAlmostEqual(result, 14.4, delta=0.1)

    def test_edge_negativo(self):
        # prob=0.40, cuota=1.50 → implícita≈0.6667 → edge≈-26.7
        result = self._edge(0.40, 1.50)
        self.assertAlmostEqual(result, -26.7, delta=0.1)

    def test_edge_cuota_none(self):
        self.assertIsNone(self._edge(0.70, None))

    def test_edge_prob_none(self):
        self.assertIsNone(self._edge(None, 1.80))


if __name__ == "__main__":
    unittest.main(verbosity=2)
