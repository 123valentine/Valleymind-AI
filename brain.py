import datetime

def respond_to_input(user_input):
    user_input = user_input.lower()

    if "hello" in user_input:
        return "Hi there! I'm Valleymind-AI. How can I help you today?"
    elif "time" in user_input:
        return f"The current time is {datetime.datetime.now().strftime('%I:%M %p')}."
    elif "date" in user_input:
        return f"Today's date is {datetime.datetime.now().strftime('%A, %B %d, %Y')}."
    elif "your name" in user_input:
        return "I'm Valleymind-AI, your personal assistant."
    elif "bye" in user_input:
        return "Goodbye! Talk to you later."
    else:
        return "I'm not sure how to respond to that. Try asking something else!"

# Run a simple loop to chat
if __name__ == "__main__":
    print("Welcome to Valleymind-AI!")
    while True:
        user_input = input("You: ")
        if user_input.lower() in ["exit", "quit", "bye"]:
            print("Valleymind-AI: Goodbye!")
            break
        response = respond_to_input(user_input)
        print(f"Valleymind-AI: {response}")