import pyttsx3

# Your script text
text = """
Hello! This is Marcos speaking in David's voice.
We are now syncing my voice to the video using Wav2Lip.
"""

# Initialize pyttsx3
engine = pyttsx3.init()

# Get all installed voices
voices = engine.getProperty('voices')

# Try to select "David" explicitly
david_voice = None
for voice in voices:
 if "David" in voice.name:  # Look for Microsoft David Desktop
        david_voice = voice.id
        break

if david_voice:
    engine.setProperty('voice', david_voice)
    print("✅ Using Microsoft David voice.")
else:
    print("⚠ David voice not found! Using default voice instead.")

# Save audio to file
engine.save_to_file(text, "audio.wav")
engine.runAndWait()

print("✅ Audio saved as audio.wav")