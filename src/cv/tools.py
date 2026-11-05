import random

def roll_dice() -> str:
    """Roll a six-sided die and return the result."""
    return str(random.randint(1, 6))
