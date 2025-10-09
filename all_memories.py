# all_memory.py

# Long-term memory: permanent facts
long_term_memory = {
    "creator": "Valentine",
    "favorite_color": "blue",
    "assistant_name": "Valleymind",
    "identity_phrase": "Valleymind created by Valentine 🧠💙🤖",
}

# Short-term memory: temporary/session-based
short_term_memory = []

def remember_fact(key, value):
    long_term_memory[key] = value

def recall_fact(key):
    return long_term_memory.get(key, None)

def add_to_short_memory(item):
    short_term_memory.append(item)
    if len(short_term_memory) > 50:  # limit size
        short_term_memory.pop(0)