from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from requests import get
from toon import encode

from cv.config import Config
from cv.data import FetchState, SearchState


class Tools:

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.crawler = AsyncWebCrawler(config=BrowserConfig(browser_mode="custom", cdp_url="http://localhost:9222"))
    
    def search(self, query: str) -> str:
        """Search the web for query.
        
        Args:
        query: The search terms to look up.
        """
        if SearchState(query=query).get_or_none() == None:
            results = get(f"http://localhost:8080/search?q={query}&format=json").json()["results"]
            data = [{"title": a["title"], "url": a["url"]} for a in results]
            SearchState(query=query, result=data).save()
            return encode(data)
        else:
            return "That query has already been searched. Try another one."

    async def fetch(self, url: str) -> str:
        """Fetch a url and get the content.
        Args:
        url: The url to fetch.
        """
        if FetchState(url=url).get_or_none() == None:
            run = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                wait_until="networkidle",
                page_timeout=60_000,
            )
            r = await self.crawler.arun(url, config=run)
            FetchState(url=url, result=r.markdown).save()
            return r.markdown
        else:
            return "That url has already been scraped. Try another one."
