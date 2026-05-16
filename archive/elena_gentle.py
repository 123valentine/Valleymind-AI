import edge_tts
import asyncio

async def elena_voice():
    text = "You are stronger than you think, and more loved than you realize."
    voice_name = "en-GB-SoniaNeural"  # Soft UK female
    output_file = "elena_gentle.mp3"
    communicate = edge_tts.Communicate(text, voice_name)
    await communicate.save(output_file)
    print(f"✅ Elena gentle voice saved as {output_file}")

if __name__ == "__main__":
    asyncio.run(elena_voice())