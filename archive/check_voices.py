import pyttsx3

engine = pyttsx3.init()
voices = engine.getProperty('voices')

for i, v in enumerate(voices):
    print(f"{i}: {v.name} ({v.id})")