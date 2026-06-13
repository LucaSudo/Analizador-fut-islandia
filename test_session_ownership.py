"""
test_session_ownership.py — Control de propiedad de sesiones (session_store).

Cubre la corrección de seguridad: una sesión de un usuario no puede ser
leída, escrita ni borrada por otro usuario (ni por requests anónimos).
Mockea la capa Supabase para correr sin red.
"""
import sys, os, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

os.environ.setdefault("SUPABASE_URL", "https://dummy-test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "dummy-key")

import session_store


class TestSessionOwnership(unittest.TestCase):
    def setUp(self):
        session_store._cache.clear()
        self._orig_load = session_store._load_from_db
        self._orig_save = session_store._save_to_db
        self._db: dict = {}

        def fake_load(sid):
            row = self._db.get(sid)
            return (list(row[0]), row[1]) if row else ([], "default")

        def fake_save(sid, history, uid):
            self._db[sid] = (list(history), uid)

        session_store._load_from_db = fake_load
        session_store._save_to_db = fake_save

        # clear_session/cleanup_expired usan `db` directo → stub sin red
        class _FakeQuery:
            def __getattr__(self, _name):
                return lambda *a, **k: self
        class _FakeDb:
            def table(self, _name):
                return _FakeQuery()
        self._orig_db = session_store.db
        session_store.db = _FakeDb()

    def tearDown(self):
        session_store._load_from_db = self._orig_load
        session_store._save_to_db = self._orig_save
        session_store.db = self._orig_db
        session_store._cache.clear()

    def test_usuario_no_lee_sesion_ajena(self):
        session_store.append_message("s1", "user", "hola", user_id="user_A")
        self.assertEqual(len(session_store.get_history("s1", "user_A")), 1)
        self.assertEqual(session_store.get_history("s1", "user_B"), [])

    def test_anonimo_no_lee_sesion_de_usuario(self):
        session_store.append_message("s1", "user", "secreto", user_id="user_A")
        self.assertEqual(session_store.get_history("s1", "default"), [])

    def test_usuario_no_escribe_sesion_ajena(self):
        session_store.append_message("s1", "user", "hola", user_id="user_A")
        session_store.append_message("s1", "user", "intruso", user_id="user_B")
        h = session_store.get_history("s1", "user_A")
        self.assertEqual([m["content"] for m in h], ["hola"])

    def test_usuario_no_modifica_assistant_ajeno(self):
        session_store.append_message("s1", "assistant", "respuesta", user_id="user_A")
        session_store.replace_last_assistant("s1", "hackeado", user_id="user_B")
        h = session_store.get_history("s1", "user_A")
        self.assertEqual(h[-1]["content"], "respuesta")

    def test_usuario_no_borra_sesion_ajena(self):
        session_store.append_message("s1", "user", "hola", user_id="user_A")
        self.assertFalse(session_store.clear_session("s1", "user_B"))
        self.assertEqual(len(session_store.get_history("s1", "user_A")), 1)
        self.assertTrue(session_store.clear_session("s1", "user_A"))

    def test_usuario_reclama_sesion_anonima(self):
        session_store.append_message("s1", "user", "hola", user_id="default")
        h = session_store.get_history("s1", "user_A")  # reclama
        self.assertEqual(len(h), 1)
        # tras el reclamo, otros usuarios (y anónimos) quedan afuera
        self.assertEqual(session_store.get_history("s1", "user_B"), [])
        self.assertEqual(session_store.get_history("s1", "default"), [])

    def test_get_history_devuelve_copia(self):
        session_store.append_message("s1", "user", "hola", user_id="user_A")
        h = session_store.get_history("s1", "user_A")
        h.append({"role": "user", "content": "mutado por fuera"})
        self.assertEqual(len(session_store.get_history("s1", "user_A")), 1)

    def test_ownership_sobrevive_reinicio_de_cache(self):
        """Aunque la cache en memoria se vacíe (restart), la sesión cargada
        desde DB mantiene su dueño."""
        session_store.append_message("s1", "user", "hola", user_id="user_A")
        session_store._cache.clear()  # simula restart del server
        self.assertEqual(session_store.get_history("s1", "user_B"), [])
        self.assertEqual(len(session_store.get_history("s1", "user_A")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
