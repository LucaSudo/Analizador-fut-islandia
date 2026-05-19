import asyncio
import edge_tts

async def hablar(texto, archivo="analisis.mp3"):
    # Voz en español
    communicate = edge_tts.Communicate(texto, voice="es-AR-TomasNeural")
    await communicate.save(archivo)
    print(f"✅ Audio guardado en {archivo}")

# Texto de prueba
texto = """
Breidablik tiene una ligera ventaja debido a su forma reciente sólida y su defensa compacta.
Sin embargo, el ataque prolífico de Valur Reykjavik los convierte en un oponente muy peligroso.
El partido promete ser muy disputado y cualquier resultado es posible.
"""

asyncio.run(hablar(texto))