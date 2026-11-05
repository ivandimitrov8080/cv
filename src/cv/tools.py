from ddgs import DDGS
from toon import encode
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

state = {}

def init(cfg: Config):
    state["crawler"] = AsyncWebCrawler(config=BrowserConfig(browser_mode="custom", cdp_url="http://localhost:9222"))
    state["cfg"] = cfg

def search(query: str):
    """Search the web for query.
    
    Args:
        query: The search terms to look up.
    """
    print(f"Searching for query <<{query}>>")
    return encode(DDGS().text(query, max_results = 5, backend=state["cfg"].search.backend))

async def fetch(url: str):
    """Fetch a url and get the content.
    Args:
        url: The url to fetch.
    """
    run = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_until="networkidle",
        page_timeout=60_000,
    )
    r = await state["crawler"].arun(url, config=run)
    return r.markdown
