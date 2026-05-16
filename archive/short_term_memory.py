# short_term_memory.py

short_memory = {}

def remember(key, value):
    short_memory[key.lower()] = value

def recall(key):
    return short_memory.get(key.lower(), "I can't remember that right now.")

def forget_all():
    short_memory.clear()