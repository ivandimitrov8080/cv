from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from peewee import IntegrityError
from requests import get
from toon import encode

from cv.config import Config
from cv.data import db
from cv.data.lead import Comment, Lead, LeadState
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
            if r.markdown is not None:
                FetchState.update(result=r.markdown).where(FetchState.url == url).execute()
                return r.markdown
            else:
                return "Fetch returned no content."
        except IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                return "That url has already been scraped. Try another one."
            else:
                raise

    async def save_lead(self, url: str, title: str, description: str, comments: list[str]):
        """Persist a Lead to db for later processing.
        Args:
        url: The job board/site careers/contacts page for the Lead
        title: The position name
        description: A short summary of the company and the position
        comments: Any additional comments to the lead in question
        """
        try:
            lead = Lead.create(url=url, title=title, description=description)
            for comment in comments:
                Comment.create(lead=lead, text=comment)
        except IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                return "This Lead has already been saved. Try another one."
            else:
                raise

    def next_lead(self):
        """Fetches the next NEW lead from the DB to be processed.
        """
        with db.atomic():
            lead = Lead.get(Lead.state == LeadState.NEW)
            Lead.update(state=LeadState.PROCESSING).where(Lead.url == lead.url).execute()
        return lead.url

    def update_lead(self, url: str, new_state: str):
        """Sets the lead state after attempting an application.
        Args:
        url: The Lead url.
        new_state: The state to be updated as such:
        applied = After successfuly applying to the lead.
        blocked = When there were problems applying to the lead like there was no answer for a question.
        cancelled = Cancelled if the lead does not meet apply criteria such as asking questions that the user does not wish to answer.
        """
        Lead.update(state=LeadState(new_state)).where(Lead.url == url).execute()
