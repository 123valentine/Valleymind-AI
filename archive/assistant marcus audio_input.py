# assistant/marcus/audio_input.py

import speech_recognition as sr

def listen_and_transcribe():
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Marcus is listening...")
        audio = recognizer.listen(source)

    try:
        text = recognizer.recognize_google(audio)
        print(f"Marcus heard: {text}")
        return text
    except sr.UnknownValueError:
        print("Marcus couldn’t understand.")
        return ""