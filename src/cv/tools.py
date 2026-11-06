from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from ddgs import DDGS
from ddgs.exceptions import DDGSException
from toon import encode

from cv.config import Config


class Tools:

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.crawler = AsyncWebCrawler(config=BrowserConfig(browser_mode="custom", cdp_url="http://localhost:9222"))
    
    def search(self, query: str):
        """Search the web for query.
        
        Args:
        query: The search terms to look up.
        """
        print(f"Searching for query <<{query}>>")
        try:
            with DDGS(timeout=20) as ddgs:
                cfg = self.cfg.search
                return encode(ddgs.text(query, max_results=cfg.max_results, backend=cfg.backend, region=cfg.region))
        except DDGSException as e:
            print("Error fetching: ", e)
            

    async def fetch(self, url: str):
        """Fetch a url and get the content.
        Args:
        url: The url to fetch.
        """
        run = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            wait_until="networkidle",
            page_timeout=60_000,
        )
        r = await self.crawler.arun(url, config=run)
        return r.markdown
