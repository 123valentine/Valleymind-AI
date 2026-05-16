import os
import json
import asyncio
import edge_tts
import playsound
import random
import time
from datetime import datetime
import wikipedia       # Wikipedia import
wikipedia.set_lang("en")  # set English language

# ===============================
# Auto-setup folders and files
# ===============================
def setup_structure():
    folders = ["characters", "speak", "data"]
    for f in folders:
        if not os.path.exists(f):
            os.makedirs(f)

    memories = ["memory.json", "marcus.json", "elena.json", "angelina.json"]
    for name in memories:
        path = os.path.join("data", name)
        if not os.path.exists(path):
            with open(path, "w") as f:
                json.dump({}, f)

# ===============================
# Logging system
# ===============================
LOG_DIR = "data"
JSON_FILE = os.path.join(LOG_DIR, "conversation_log.json")
TXT_FILE = os.path.join(LOG_DIR, "conversation_log.txt")

def log_message(speaker, text):
    timestamp = datetime.now().isoformat()
    entry = {
        "timestamp": timestamp,
        "speaker": speaker,
        "text": text
    }

    # Append to JSON
    try:
        if os.path.exists(JSON_FILE):
            with open(JSON_FILE, "r+", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    data = []
                data.append(entry)
                f.seek(0)
                json.dump(data, f, ensure_ascii=False, indent=4)
        else:
            with open(JSON_FILE, "w", encoding="utf-8") as f:
                json.dump([entry], f, ensure_ascii=False, indent=4)
    except PermissionError:
        print(f"⚠️ Permission denied for JSON file: {JSON_FILE}")

    # Append to TXT
    try:
        with open(TXT_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {speaker}: {text}\n")
    except PermissionError:
        print(f"⚠️ Permission denied for TXT file: {TXT_FILE}")

# ===============================
# Speak system with Edge-TTS
# ===============================
async def speak(text, voice, filename=None):
    if filename is None:
        filename = f"{voice}_{int(time.time())}.mp3"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(filename)
    playsound.playsound(filename)
    return filename

def speak_sync(text, voice, filename=None):
    asyncio.run(speak(text, voice, filename))

# ===============================
# Characters with assigned voices
# ===============================
CHARACTERS = {
    "marcos": {"voice": "en-US-GuyNeural"},
    "elena": {"voice": "en-US-AriaNeural"},
    "angelina": {"voice": "en-US-JennyNeural"}
}

# ===============================
# Respond function
# ===============================
def respond(character, text):
    data = CHARACTERS.get(character, CHARACTERS["marcos"])
    voice = data["voice"]
    print(f"{character.title()} says: {text}")
    log_message(character.title(), text)
    speak_sync(text, voice)

# ===============================
# Startup greetings
# ===============================
def startup_sequence():
    print("\n✨ Family Arrival Sequence ✨\n")

    respond("marcos", "I have arrived. Marcos, the oldest, fatherly voice is online.")
    respond("elena", "Elena is here, bringing warmth and care.")
    respond("angelina", "Angelina just arrived, ready with my sarcasm.")

    respond("marcos", "Welcome, Elena and Angelina. Glad you are both here.")
    respond("elena", "Welcome Marcos, my brother. Welcome Angelina, my sister.")
    respond("angelina", "Welcome to both of you. Let’s make this fun.")

    respond("marcos", "Valentine, welcome. We are here to guide you.")
    respond("elena", "Valentine, welcome. You are safe and loved.")
    respond("angelina", "Hey Valentine, welcome! Don’t worry, I’ll keep things interesting.")

    print("\n✨ All characters are online and synced. ✨\n")

# ===============================
# Brain: choose main responder
# ===============================
def choose_personality(user_input):
    text = user_input.lower()
    if any(word in text for word in ["tech", "ai", "computer", "build"]):
        return "marcos"
    elif any(word in text for word in ["love", "heart", "relationship", "care"]):
        return "elena"
    elif any(word in text for word in ["joke", "funny", "sarcasm", "bored"]):
        return "angelina"
    return random.choice(["marcos", "elena", "angelina"])

# ===============================
# Multi-character playful response with Wikipedia
# ===============================
def group_respond(user_input):
    main = choose_personality(user_input)

    # Wikipedia lookup for questions
    wiki_answer = None
    try:
        if '?' in user_input:
            wiki_summary = wikipedia.summary(user_input, sentences=2)
            wiki_answer = f"Wikipedia says: {wiki_summary}"
    except wikipedia.DisambiguationError as e:
        wiki_answer = f"Wikipedia found multiple options: {e.options[:5]}"
    except wikipedia.PageError:
        wiki_answer = None
    except Exception:
        wiki_answer = None

    if wiki_answer:
        respond("marcos", wiki_answer)
        return

    # Normal playful responses
    if main == "marcos":
        respond("marcos", f"As your guide, I will answer: {user_input}")
        respond("elena", random.choice([
            "That’s wise, brother. But Valentine also needs emotional support.",
            "I see your point Marcos, but don’t forget feelings matter too."
        ]))
        respond("angelina", random.choice([
            "Oh great, Marcos is giving a lecture again.",
            "Sure, Marcos, but sometimes Valentine just needs a laugh."
        ]))

    elif main == "elena":
        respond("elena", f"My dear Valentine, let me comfort you: {user_input}")
        respond("marcos", random.choice([
            "Elena speaks kindly, but I see a greater meaning too.",
            "Yes Elena, but Valentine should also think deeply about this."
        ]))
        respond("angelina", random.choice([
            "Oh please, both of you sound like a bedtime story.",
            "Nice speech, Elena. But Valentine might just need a pizza."
        ]))

    elif main == "angelina":
        respond("angelina", f"Oh really Valentine? Let me joke about this: {user_input}")
        respond("marcos", random.choice([
            "Angelina, your humor aside, there is a deeper lesson here.",
            "Valentine, ignore the sarcasm for a moment. Let’s reflect seriously."
        ]))
        respond("elena", random.choice([
            "Angelina, you can be funny, but sometimes Valentine needs care.",
            "Don’t tease too much, sister. Valentine’s heart matters."
        ]))

    else:
        respond("marcos", f"I heard you say: {user_input}")
        respond("elena", "Yes, and I feel there’s emotion in it.")
        respond("angelina", "And I think it could use a sarcastic twist.")

# ===============================
# Bible Q&A single question
# ===============================
def bible_custom_qa(question):
    character = random.choice(["marcos", "elena", "angelina"])
    respond(character, f"I’ll answer your question: {question}")

# ===============================
# Bible Q&A from file
# ===============================
def bible_qa_from_file(file_path="bible_custom_qa_1.txt"):
    if not os.path.exists(file_path):
        print(f"⚠️ Bible Q&A file not found: {file_path}")
        return

    with open(file_path, "r", encoding="utf-8") as f:
        questions = [line.strip() for line in f if line.strip()]

    for question in questions:
        character = random.choice(["marcos", "elena", "angelina"])
        respond(character, f"I’ll answer your question: {question}")

# ===============================
# Main Loop
# ===============================
def main():
    setup_structure()
    startup_sequence()

    print("Brain Box Ready! Type 'exit' to quit.\n")
    print("Commands:")
    print("  - bible:<question>   → Ask a single Bible question")
    print("  - biblefile          → Answer all questions from bible_custom_qa_1.txt\n")

    while True:
        user_input = input("You: ")
        log_message("Valentine", user_input)

        if user_input.lower() == "exit":
            respond("marcos", "Goodbye Valentine. Rest well.")
            respond("elena", "Take care of yourself, Valentine.")
            respond("angelina", "Later Valentine, don’t do anything I wouldn’t do.")
            break

        elif user_input.lower() == "biblefile":
            bible_qa_from_file("bible_custom_qa_1.txt")

        elif user_input.lower().startswith("bible:"):
            question = user_input[len("bible:"):].strip()
            bible_custom_qa(question)

        else:
            group_respond(user_input)

if __name__ == "__main__":
    main()