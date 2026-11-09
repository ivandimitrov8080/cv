from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from peewee import IntegrityError
from requests import get
from toon import encode

from cv.config import Config
from cv.data.web import FetchState, SearchState


class Tools:

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.crawler = AsyncWebCrawler(config=BrowserConfig(browser_mode="custom", cdp_url="http://localhost:9222"))
    
    def search(self, query: str) -> str:
        """Search the web for query.
        
        Args:
        query: The search terms to look up.
        """
        try:
            SearchState(query=query, result="In progress.").save()
            results = get(f"http://localhost:8080/search?q={query}&format=json").json()["results"]
            data = [{"title": a["title"], "url": a["url"]} for a in results]
            SearchState.update(result=data).where(SearchState.query == query).execute()
            return encode(data)
        except IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                return "That query has already been searched. Try another one."
            else:
                raise

    async def fetch(self, url: str) -> str:
        """Fetch a url and get the content.
        Args:
        url: The url to fetch.
        """
        try:
            FetchState(url=url, result="In progress.").save()
            run = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                wait_until="networkidle",
                page_timeout=60_000,
            )
            r = await self.crawler.arun(url, config=run)
            FetchState.update(result=r.markdown).where(FetchState.url == url).execute()
            return r.markdown
        except IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                return "That url has already been scraped. Try another one."
            else:
                raise
