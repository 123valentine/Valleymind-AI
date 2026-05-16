from bark import SAMPLE_RATE, generate_audio, preload_models
from scipy.io.wavfile import write as write_wav

# Load the voice models
preload_models()

# Your test message
text_prompt = "Hi Valentine, I’m Elena. I’m so glad you made it this far. I’m ready to help you."

# Generate the voice
audio_array = generate_audio(text_prompt)

# Save the audio to a file
write_wav("elena_voice.wav", SAMPLE_RATE, audio_array)