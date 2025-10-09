from TTS.api import TTS

# Load the Elena voice (xtts_v2)
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")

# Generate speech
tts.tts_to_file(text="Hi, my name is Elena. I'm your AI assistant, built with love by Valentine Egbujie.",
                file_path="elena_test_output.wav")