import re

def classify_intent(text):
    text = text.lower().strip()

    greetings = [
        "hello", "hi", "hey", "good morning", "good afternoon", "good evening", 
        "howdy", "yo", "hi there", "hiya", "what's up", "sup", "greetings"
    ]

    farewells = [
        "bye", "goodbye", "see you", "farewell", "later", "see ya", 
        "talk to you later", "catch you later", "take care", "peace out"
    ]

    thanks = [
        "thank you", "thanks", "i appreciate it", "much appreciated", 
        "cheers", "grateful", "many thanks", "thx", "ty"
    ]

    questions = [
        "what", "how", "when", "where", "why", "who", "?", 
        "can you", "could you", "do you know", "is it", "are you", 
        "would you", "should i", "may i", "help me", "tell me"
    ]

    commands = [
        "open", "run", "start", "stop", "play", "show", "search", "check", 
        "turn on", "turn off", "activate", "deactivate", "enable", "disable",
        "go to", "fetch", "read", "download", "install", "uninstall", 
        "calculate", "translate", "explain", "write", "summarize", "generate"
    ]

    emotions = [
        "i'm sad", "i'm happy", "i feel tired", "i'm excited", 
        "i'm angry", "i feel good", "i'm depressed", "i'm anxious", 
        "i'm relaxed", "i'm bored", "i'm frustrated", "i'm scared",
        "i feel great", "i'm confused", "i'm fine"
    ]

    if any(greet in text for greet in greetings):
        return "greeting"
    elif any(fare in text for fare in farewells):
        return "farewell"
    elif any(thank in text for thank in thanks):
        return "thanks"
    elif any(emo in text for emo in emotions):
        return "emotion"
    elif any(text.startswith(q) for q in questions) or "?" in text:
        return "question"
    elif any(text.startswith(cmd) for cmd in commands):
        return "command"
    else:
        return "unknown"