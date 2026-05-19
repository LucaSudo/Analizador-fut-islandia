from kokoro_onnx import Kokoro
import soundfile as sf

kokoro = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")

samples, sample_rate = kokoro.create(
    "Breidablik tiene ventaja clara sobre Valur en los últimos partidos.",
    voice="ef_dora",
    speed=1.0,
    lang="es"
)

sf.write("test_voz.wav", samples, sample_rate)
print("✅ Audio guardado en test_voz.wav")