from ddgs import DDGS
from toon import encode

def search(query: str):
    """Search the web for query.
    
    Args:
        query: The search terms to look up.
    """
    return encode(DDGS().text(query, max_results = 5))
