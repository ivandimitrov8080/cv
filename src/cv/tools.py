import random

from ddgs import DDGS

def roll_dice() -> str:
    """Roll a six-sided die and return the result."""
    return str(random.randint(1, 6))

def search():
    return DDGS().text("speedtest", max_results = 5)
