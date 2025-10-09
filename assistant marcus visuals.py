# assistant/marcus/visuals.py

import matplotlib.pyplot as plt

def draw_outline_car():
    fig, ax = plt.subplots()
    ax.set_facecolor("black")

    # Drawing car as boxes and circles (outline only)
    body = plt.Rectangle((0.2, 0.4), 0.6, 0.3, edgecolor='yellow', facecolor='none', linewidth=2)
    wheel1 = plt.Circle((0.3, 0.3), 0.05, edgecolor='yellow', facecolor='none', linewidth=2)
    wheel2 = plt.Circle((0.7, 0.3), 0.05, edgecolor='yellow', facecolor='none', linewidth=2)

    ax.add_patch(body)
    ax.add_patch(wheel1)
    ax.add_patch(wheel2)

    ax.text(0.05, 0.05, "This is how the body and wheels of a basic car form together.",
            color='white', fontsize=8)

    plt.xlim(0, 1)
    plt.ylim(0, 1)
    plt.axis('off')
    plt.title("Car Structure - Marcus", color='yellow')
    plt.show()

# Example call
# draw_outline_car()
