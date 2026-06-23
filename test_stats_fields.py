"""
test_stats_fields.py — Tests para _calcular_racha.
Ejecutar desde la raíz: python test_stats_fields.py
No requiere credenciales de Supabase ni Groq.
"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
sys.path.insert(0, os.path.dirname(__file__))


class TestCalcularRacha(unittest.TestCase):

    def _racha(self, acertos):
        from memory import _calcular_racha
        preds = [{"acerto": a} for a in acertos]
        return _calcular_racha(preds)

    def test_racha_aciertos(self):
        self.assertEqual(self._racha([True, True, True, False]), "3 aciertos seguidos 🔥")

    def test_racha_fallos(self):
        self.assertEqual(self._racha([False, False, True]), "2 fallos consecutivos")

    def test_racha_uno_solo_es_none(self):
        self.assertIsNone(self._racha([True, False, True]))

    def test_racha_lista_vacia(self):
        self.assertIsNone(self._racha([]))

    def test_racha_todos_aciertos(self):
        self.assertEqual(self._racha([True, True, True, True, True]), "5 aciertos seguidos 🔥")

    def test_racha_un_elemento(self):
        self.assertIsNone(self._racha([True]))

    def test_racha_primer_elemento_none_es_none(self):
        self.assertIsNone(self._racha([None, None, None]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
