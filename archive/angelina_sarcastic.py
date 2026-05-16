import edge_tts
import asyncio

async def angelina_voice():
    text = "Oh, really? That’s the best idea you could come up with? Impressive."
    voice_name = "en-GB-LibbyNeural"  # UK female (sarcastic tone possible)
    output_file = "angelina_sarcastic.mp3"
    communicate = edge_tts.Communicate(text, voice_name)
    await communicate.save(output_file)
    print(f"✅ Angelina sarcastic voice saved as {output_file}")

if __name__ == "__main__":
    asyncio.run(angelina_voice())