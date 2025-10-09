import edge_tts
import asyncio

async def marcos_voice():
    text = "Life is not measured by the number of breaths we take, but by the moments that take our breath away."
    voice_name = "en-GB-RyanNeural"  # Deep UK male
    output_file = "marcos_deep.mp3"
    communicate = edge_tts.Communicate(text, voice_name)
    await communicate.save(output_file)
    print(f"✅ Marcos deep voice saved as {output_file}")

if __name__ == "__main__":
    asyncio.run(marcos_voice())