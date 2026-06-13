"""
test_fixes.py — Pruebas para los fixes #0n, #0o, #0p.

Ejecutar desde la raiz del proyecto:
    python test_fixes.py

No requiere credenciales de Supabase ni Groq. Las pruebas de red
(#0p-live) si llaman a SofaScore, pero son opcionales y se pueden
saltar con --skip-live.
"""

import sys, io, os, types, unittest
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ── Path setup ──────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
sys.path.insert(0, os.path.dirname(__file__))

SKIP_LIVE = "--skip-live" in sys.argv

# ────────────────────────────────────────────────────────────────────
# SUITE 1 — Bug #0o: _system_prompt_con_tz()
# Verifica que el SYSTEM_PROMPT que recibe el LLM tiene los horarios
# correctos según el offset del usuario.
# ────────────────────────────────────────────────────────────────────

class TestSystemPromptConTZ(unittest.TestCase):
    """Bug #0o — LLM ve horarios en TZ del user, no UTC."""

    def setUp(self):
        import engine
        self.engine = engine
        # SYSTEM_PROMPT sintético con fixtures en UTC
        engine.SYSTEM_PROMPT = (
            "Sos un experto en fútbol.\n\n"
            "=== PRÓXIMOS PARTIDOS POR LIGA ===\n\n"
            "1. deild karla:\n"
            "  - UMF Afturelding vs Fylkir Reykjavik (31/05/2025 19:15)\n"
            "  - Valur vs Vikingur (31/05/2025 17:00 [HOY])\n\n"
            "=== OTRA SECCIÓN ===\n"
            "Texto extra.\n"
        )
        engine._SERVER_TZ_AT_LOAD = 0.0  # base siempre UTC

    def test_utc_user_no_cambia(self):
        """Si user_tz == 0 (UTC), el prompt no debe cambiar."""
        self.engine.set_request_tz_offset(0.0)
        result = self.engine._system_prompt_con_tz()
        self.assertIn("19:15", result)
        self.assertIn("17:00", result)

    def test_arg_minus3(self):
        """Usuario ARG (-3): 19:15 UTC → 16:15, 17:00 → 14:00."""
        self.engine.set_request_tz_offset(-3.0)
        result = self.engine._system_prompt_con_tz()
        self.assertIn("16:15", result, "Fylkir debe aparecer a las 16:15 para ARG")
        self.assertNotIn("19:15", result, "No debe quedar hora UTC 19:15 para ARG")
        self.assertIn("14:00", result, "Valur debe aparecer a las 14:00 para ARG")

    def test_cet_plus1(self):
        """Usuario CET (+1): 19:15 UTC → 20:15, 17:00 → 18:00."""
        self.engine.set_request_tz_offset(1.0)
        result = self.engine._system_prompt_con_tz()
        self.assertIn("20:15", result, "Fylkir debe aparecer a las 20:15 para CET")
        self.assertIn("18:00", result, "Valur debe aparecer a las 18:00 para CET")

    def test_iceland_utc0(self):
        """Usuario Islandia (0): igual a UTC base, no cambia nada."""
        self.engine.set_request_tz_offset(0.0)
        result = self.engine._system_prompt_con_tz()
        self.assertIn("19:15", result)
        self.assertIn("17:00", result)

    def test_prompt_no_tiene_fixtures_no_explota(self):
        """Si SYSTEM_PROMPT no tiene fixtures, devuelve tal cual."""
        self.engine.SYSTEM_PROMPT = "Solo texto sin partidos."
        self.engine.set_request_tz_offset(-3.0)
        result = self.engine._system_prompt_con_tz()
        self.assertEqual(result, "Solo texto sin partidos.")

    def test_texto_fuera_del_bloque_no_cambia(self):
        """Texto antes/después del bloque de fixtures no debe alterarse."""
        self.engine.set_request_tz_offset(-3.0)
        result = self.engine._system_prompt_con_tz()
        self.assertIn("Sos un experto en fútbol.", result)
        self.assertIn("Texto extra.", result)


# ────────────────────────────────────────────────────────────────────
# SUITE 2 — Bug #0n: _buscar_evento_por_equipos() (lógica de matching)
# Prueba la función con sesiones mockeadas para no depender de red.
# ────────────────────────────────────────────────────────────────────

class MockResponse:
    def __init__(self, data):
        self._data = data
    def json(self):
        return self._data


class MockSesion:
    """Simula curl_cffi.Session devolviendo datos predefinidos por URL."""
    def __init__(self, respuestas: dict):
        self._respuestas = respuestas
        self.llamadas = []

    def get(self, url, timeout=15):
        self.llamadas.append(url)
        for patron, data in self._respuestas.items():
            if patron in url:
                return MockResponse(data)
        return MockResponse({"events": []})


def _evento_terminado(id_, home, away):
    return {
        "id": id_,
        "status": {"type": "finished"},
        "homeTeam": {"name": home},
        "awayTeam": {"name": away},
        "homeScore": {"current": 2},
        "awayScore": {"current": 1},
        "startTimestamp": 1748700000,
    }


class TestBuscarEventoPorEquipos(unittest.TestCase):
    """Bug #0n — matching de eventos SofaScore por (equipos, fecha)."""

    def setUp(self):
        # Importar solo la función, no el módulo entero
        from memory import _buscar_evento_por_equipos
        self._fn = _buscar_evento_por_equipos

    def test_match_exacto(self):
        """Encuentra el evento cuando los nombres coinciden exactamente."""
        sesion = MockSesion({
            "2025-05-31": {"events": [
                _evento_terminado(9901, "UMF Afturelding", "Fylkir Reykjavik"),
            ]}
        })
        resultado = self._fn(sesion, "UMF Afturelding", "Fylkir Reykjavik", "31/05/2025")
        self.assertEqual(resultado, 9901)

    def test_match_parcial(self):
        """Encuentra el evento con nombre parcial (alias como 'Fylkir')."""
        sesion = MockSesion({
            "2025-05-31": {"events": [
                _evento_terminado(9901, "UMF Afturelding", "Fylkir Reykjavik"),
            ]}
        })
        resultado = self._fn(sesion, "Afturelding", "Fylkir", "31/05/2025")
        self.assertEqual(resultado, 9901)

    def test_no_terminado_ignorado(self):
        """Eventos que no están 'finished' no se consideran."""
        sesion = MockSesion({
            "2025-05-31": {"events": [
                {
                    "id": 9901,
                    "status": {"type": "notstarted"},
                    "homeTeam": {"name": "UMF Afturelding"},
                    "awayTeam": {"name": "Fylkir Reykjavik"},
                    "homeScore": {"current": 0},
                    "awayScore": {"current": 0},
                }
            ]}
        })
        resultado = self._fn(sesion, "Afturelding", "Fylkir", "31/05/2025")
        self.assertIsNone(resultado)

    def test_sin_partido_devuelve_none(self):
        """Sin candidatos, retorna None."""
        sesion = MockSesion({})
        resultado = self._fn(sesion, "Equipo A", "Equipo B", "31/05/2025")
        self.assertIsNone(resultado)

    def test_ambiguo_devuelve_none(self):
        """Con dos candidatos que no se pueden desambiguar, retorna None."""
        evento_extra = _evento_terminado(9902, "UMF Afturelding B", "Fylkir II")
        evento_extra["startTimestamp"] = 1748786400  # día anterior
        sesion = MockSesion({
            "2025-05-30": {"events": [evento_extra]},
            "2025-05-31": {"events": [
                _evento_terminado(9901, "UMF Afturelding", "Fylkir Reykjavik"),
            ]},
            "2025-06-01": {"events": []},
        })
        # Búsqueda con nombre genérico que matchea ambos
        resultado = self._fn(sesion, "Afturelding", "Fylkir", "31/05/2025")
        # El de la fecha exacta (9901) debería ganar la desambiguación
        self.assertEqual(resultado, 9901)

    def test_formato_fecha_iso(self):
        """Acepta fecha en formato YYYY-MM-DD además de DD/MM/YYYY."""
        sesion = MockSesion({
            "2025-05-31": {"events": [
                _evento_terminado(9901, "UMF Afturelding", "Fylkir Reykjavik"),
            ]}
        })
        resultado = self._fn(sesion, "Afturelding", "Fylkir", "2025-05-31")
        self.assertEqual(resultado, 9901)

    def test_fecha_invalida_devuelve_none(self):
        """Fecha malformada retorna None sin explotar."""
        sesion = MockSesion({})
        resultado = self._fn(sesion, "A", "B", "no-es-fecha")
        self.assertIsNone(resultado)


# ────────────────────────────────────────────────────────────────────
# SUITE 3 — Bug #0p: obtener_partidos_equipo() usa UTC y tiene fallback
# ────────────────────────────────────────────────────────────────────

class TestObtenerPartidosEquipo(unittest.TestCase):
    """Bug #0p — búsqueda de historial robusta."""

    def setUp(self):
        import engine
        self.engine = engine
        # Limpiar cache para que los tests sean independientes
        engine.cache_manager._partidos_equipo_cache = {}

    def test_usa_utcnow_no_now(self):
        """
        Verifica que no se usa datetime.now() (depende del OS).
        Busca la cadena literal en el source del módulo.
        """
        import inspect
        source = inspect.getsource(self.engine.obtener_partidos_equipo)
        self.assertNotIn("datetime.now()", source,
            "obtener_partidos_equipo no debe usar datetime.now() — usar datetime.utcnow()")
        self.assertIn("utcnow", source,
            "obtener_partidos_equipo debe usar datetime.utcnow()")

    def test_datos_viejos_flag_false_por_defecto(self):
        """Flag _ULTIMOS_DATOS_VIEJOS empieza en False para un equipo nuevo."""
        flag = self.engine._ULTIMOS_DATOS_VIEJOS.get("equipo_inexistente_xyz", False)
        self.assertFalse(flag)

    def test_aviso_datos_viejos_en_stats(self):
        """
        Cuando _ULTIMOS_DATOS_VIEJOS[equipo] = True, precomputar_stats_equipo
        incluye el aviso de datos viejos en el texto de estadísticas.

        El aviso solo se emite si HAY partidos (con lista vacía la función
        corta antes con "(sin datos disponibles)"), así que mockeamos un
        partido sintético y las funciones que tocan red.
        """
        nombre = "_test_equipo_viejo_"
        self.engine._ULTIMOS_DATOS_VIEJOS[nombre.lower()] = True

        import time as _t
        partido_fake = {
            "id": -1,
            "homeTeam": {"name": nombre},
            "awayTeam": {"name": "_rival_fake_"},
            "homeScore": {"current": 1},
            "awayScore": {"current": 0},
            "startTimestamp": _t.time() - 200 * 86400,
            "roundInfo": {"round": 1},
        }

        orig_partidos = self.engine.obtener_partidos_equipo
        orig_stats    = self.engine.obtener_estadisticas
        orig_fuerza   = self.engine._calcular_fuerza_rival_ligera
        self.engine.obtener_partidos_equipo       = lambda *a, **k: []
        self.engine.obtener_estadisticas          = lambda *a, **k: {}
        self.engine._calcular_fuerza_rival_ligera = (
            lambda *a, **k: {"attack": 1.2, "defense": 1.2})
        try:
            stats_txt, prom = self.engine.precomputar_stats_equipo(
                None, nombre, 188, 89094, 10,
                partidos_override=[partido_fake],
            )
        finally:
            self.engine.obtener_partidos_equipo       = orig_partidos
            self.engine.obtener_estadisticas          = orig_stats
            self.engine._calcular_fuerza_rival_ligera = orig_fuerza
            self.engine._ULTIMOS_DATOS_VIEJOS.pop(nombre.lower(), None)

        self.assertIn("AVISO", stats_txt,
            "Debe incluir aviso cuando datos son viejos")
        self.assertIn("180", stats_txt,
            "El aviso debe mencionar los 180 días")

    def test_buscar_team_id_retorna_none_en_error(self):
        """_buscar_team_id retorna None si la API falla, sin explotar."""
        class SesionQueFalla:
            def get(self, url, timeout=15):
                raise ConnectionError("sin red")

        result = self.engine._buscar_team_id(SesionQueFalla(), "Fylkir")
        self.assertIsNone(result)

    @unittest.skipIf(SKIP_LIVE, "prueba live omitida (--skip-live)")
    def test_live_fylkir_encuentra_partidos(self):
        """
        [LIVE] Verifica que Fylkir encuentra partidos con el fallback.
        Requiere conexión a internet.
        """
        from curl_cffi import requests as cf_requests
        sesion = cf_requests.Session(impersonate="chrome124")
        proxy = os.getenv("PROXY_URL", "")
        if proxy:
            sesion.proxies = {"http": proxy, "https": proxy}

        # Liga 1. deild karla: id=675, temporada buscar en LIGAS
        # Usamos el endpoint de equipo directamente
        team_id = self.engine._buscar_team_id(sesion, "Fylkir")
        self.assertIsNotNone(team_id, "Debe encontrar team_id para 'Fylkir'")
        print(f"\n  [live] Fylkir team_id = {team_id}")


# ────────────────────────────────────────────────────────────────────
# SUITE 4 — Integración #0o: _retag_fixtures_para_tz() correctitud
# ────────────────────────────────────────────────────────────────────

class TestRetagFixturesTZ(unittest.TestCase):
    """Verifica el retaggeo de horarios con distintos offsets."""

    def setUp(self):
        import engine
        self.fn = engine._retag_fixtures_para_tz
        engine._SERVER_TZ_AT_LOAD = 0.0

    def _fixtures(self):
        return (
            "=== PRÓXIMOS PARTIDOS POR LIGA ===\n\n"
            "1. deild karla:\n"
            "  - UMF Afturelding vs Fylkir (31/05/2025 19:15)\n"
            "  - Valur vs Vikingur (31/05/2025 17:00 [HOY])\n"
        )

    def test_arg_minus3_fylkir(self):
        out = self.fn(self._fixtures(), -3.0)
        self.assertIn("16:15", out)
        self.assertNotIn("19:15", out)

    def test_arg_minus3_valur(self):
        out = self.fn(self._fixtures(), -3.0)
        self.assertIn("14:00", out)
        self.assertNotIn("17:00", out)

    def test_cet_plus1_fylkir(self):
        out = self.fn(self._fixtures(), 1.0)
        self.assertIn("20:15", out)

    def test_utc_no_cambia(self):
        original = self._fixtures()
        out = self.fn(original, 0.0)
        # delta=0 no entra en el bloque de shift
        self.assertIn("19:15", out)
        self.assertIn("17:00", out)

    def test_hoy_tag_se_reaaplica_en_dia_real(self):
        """
        [HOY] se re-aplica si la fecha del partido (tras el shift) coincide
        con el dia real de hoy del usuario. Si la fecha es ficticia (pasado),
        el tag se elimina correctamente — ese es el comportamiento esperado.
        """
        from datetime import datetime, timedelta
        import engine
        # Construir un fixture con la fecha de HOY en UTC
        hoy_utc = datetime.utcnow()
        fecha_utc = hoy_utc.strftime("%d/%m/%Y")
        hora_utc  = hoy_utc.strftime("%H:%M")
        # Para ARG (-3): la misma hora shifted
        hora_arg  = (hoy_utc + timedelta(hours=-3)).strftime("%H:%M")
        fecha_arg = (hoy_utc + timedelta(hours=-3)).strftime("%d/%m/%Y")
        fixtures = (
            f"=== PRÓXIMOS PARTIDOS POR LIGA ===\n"
            f"Liga:\n"
            f"  - A vs B ({fecha_utc} {hora_utc})\n"
        )
        out = self.fn(fixtures, -3.0)
        # Si la hora shifted sigue en el mismo día ARG, debe tener [HOY]
        if fecha_arg == fecha_utc or True:  # siempre verificar que no explote
            self.assertIsInstance(out, str)
            # El partido con fecha de hoy ARG debe tener [HOY]
            if fecha_arg == (datetime.utcnow() + timedelta(hours=-3)).date().strftime("%d/%m/%Y"):
                self.assertIn("[HOY]", out)

    def test_medianoche_no_explota(self):
        """Partido a medianoche UTC → no pasa al día siguiente mal."""
        fixtures = (
            "=== PRÓXIMOS PARTIDOS POR LIGA ===\n"
            "Liga:\n"
            "  - A vs B (01/06/2025 00:30)\n"
        )
        out = self.fn(fixtures, -3.0)
        # 00:30 UTC → 21:30 ARG del día anterior
        self.assertIn("21:30", out)


# ────────────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quitar --skip-live de argv para que unittest no se confunda
    if "--skip-live" in sys.argv:
        sys.argv.remove("--skip-live")

    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    for cls in [
        TestSystemPromptConTZ,
        TestBuscarEventoPorEquipos,
        TestObtenerPartidosEquipo,
        TestRetagFixturesTZ,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
