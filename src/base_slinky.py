import json
import asyncpraw
import logging

from typing import Tuple
from asyncpraw.models import Submission, Comment

__all__ = [
    'Filter',
    'CommentReader',
    'SubmissionReader',
    'BaseHandler',
    'LoggerHandler'
]


class Filter():
    """
    Base class that adds filtering capability to subclasses.
    """

    def __init__(self, name='()'):
        self.name = name
        self.filters = []
        self.logger = logging.getLogger(__name__)

    def add_rule(self, filter):
        """
        Adds a filter to this object. A filter must be callable,
        accept an item (submission or comment) argument, and return
        False if the item should pass through the filter or True if
        the item should be caught by it.

        e.g.

        (item) -> bool:
            return item.link_flair_text != "Unsolved"

        """

        if filter not in self.filters and callable(filter):
            self.filters.append(filter)

    def remove_rule(self, filter):
        """Removes the filter from this object"""

        if filter in self.filters:
            self.filters.remove(filter)

    def test(self, item: Tuple[Submission, Comment]) -> bool:
        """
        Determines if the item should pass through the filter.

        The default is to allow it to pass but any individual
        filter can block it.

        Returns False if the item should pass the filter.
        Returns True if the item is caught by the filter.
        """

        for f in self.filters:
            if not f.filter(item):
                self.logging.debug(f'item {item.id} blocked by "{self.name}"')
                return True
        return False


class BaseReader(Filter):
    """
    Async PRAW Wrapper that monitors submissions or comments.

    connect:    Creates an authenticated instance of asyncpraw.Reddit
                Called automatically by constructor.

    monitor:    Monitor a subreddit for submissions or comments.
                Should be overridden by a subclass
    """

    def __init__(self, credentials_path: str = None, **kwargs):
        # Connect and authenticate
        self.logger = logging.getLogger(__name__)
        self.connect(
            self.__get_auth(credentials_path)
        )
        self.handlers = []
        self.run = True

    def __get_auth(self, credentials_path: str = None) -> dict:
        """
        Reads authentication data from json

        Returns -> dict
            Auth object that can be passed to the connect method.
        """

        if credentials_path is None:
            credentials_path = './config/auth.json'

        # Read authentication file from OS
        with open(credentials_path, 'r') as f:
            creds = json.load(f)

        username = creds['Reddit']['username']
        self.skip_authors = [username, 'AutoModerator']

        # Return the configuration that should pass to Reddit
        return {
            'username':      username,
            'password':      creds['Reddit']['password'],
            'client_id':     creds['Praw']['client_id'],
            'client_secret': creds['Praw']['client_secret'],
            'user_agent':    creds['Praw']['user_agent']
        }

    def add_handler(self, handler):
        """Adds a handler to the monitoring worker"""

        self.handlers.append(handler)

    def connect(self, auth: dict):
        """Creates an authenticated connection to Reddit"""

        # Authenticate using the passed in details
        self.connection = asyncpraw.Reddit(**auth)

        # Test the connection by getting one post
        try:
            for sub in self.connection.subreddit('all').new(limit=1):
                msg = f'Connection tested with {sub.id}: {sub.title}'
                self.logger.debug(msg)
        except Exception:
            self.logger.exception('Failed to connect.')

    def monitor(self, subreddit):
        """Monitors submissions or comments for the subreddit"""

        # This should be overridden by the subclass
        raise NotImplementedError


class BaseHandler(Filter):
    """
    A base handler class that provides common functionality
    for all subclassed handlers. Not designed to be instantiated.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger(__name__)
        self.logger.info('Logging initialised')
        self.filter = Filter()

    def set_filter(self, filter: Filter):
        if isinstance(filter, Filter):
            self.filter = filter

    def handle(self, item: Tuple[Submission, Comment]):
        """Handles the submission or comment."""

        if not self.filter.test(item):
            self.__handler_action(item)

    def __handler_action(self, item: Tuple[Submission, Comment]):
        """
        Handles the submission or comment.
        Should be overridden by subclasses.
        """

        raise NotImplementedError

    @staticmethod
    def __stream_args(**kwargs):
        return {
            k: v for k, v in kwargs.items() if k in [
                'function',
                'pause_after',
                'skip_existing',
                'attribute_name',
                'exclude_before'
            ]
        }


class CommentReader(BaseReader):
    """
    Async PRAW Wrapper that monitors comments.

    Subclasses Slinky with the following overrides:
    monitor:    Monitor a subreddit for comments.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def monitor(self, subreddit: str, **kwargs):
        """
        Monitors comments for the subreddit

        Yields comments as they come in until instructed to stop.
        """

        self.logger.debug(f'Monitoring comments in {subreddit}')
        subreddit = self.connection.subreddit(subreddit)
        stream = subreddit.stream.comments(self.__stream_args(**kwargs))
        async for item in stream:
            if not self.run:
                self.logger.info('Breaking monitoring...')
                break

            if not isinstance(item, Comment):
                self.warning(f'Expected comment but got {type(item)}')
                continue

            # Check filters
            for f in self.filters:
                if f.test(item):
                    continue

            self.logger.info(f'Handling comment: {item.id}')

            # Invoke handlers
            for h in self.handlers:
                h.handle(item)


class SubmissionReader(BaseReader):
    """
    Async PRAW Wrapper that monitors comments.

    Subclasses Slinky with the following overrides:
    monitor:    Monitor a subreddit for comments.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def monitor_async(self, subreddit: str, **kwargs):
        """
        Monitors submissions for the subreddit

        Yields submissions as they come in until instructed to stop.
        """

        self.logger.debug(f'Monitoring submissions in {subreddit}')
        subreddit = self.connection.subreddit(subreddit)
        stream = subreddit.stream.submissions(self.__stream_args(**kwargs))
        async for item in stream:
            if not self.run:
                self.logger.info('Breaking monitoring...')
                break

            if not isinstance(item, Submission):
                self.warning(f'Expected submission but got {type(item)}')
                continue

            # Check filters
            for f in self.filters:
                if f.test(item):
                    continue

            self.logger.info(f'Handling submission: {item.id}')

            # Invoke each handler
            for h in self.handlers:
                h.handle(item)


class LoggerHandler(BaseHandler):
    """
    Handler that logs items.
    """

    def __init__(self, logger: logging.Logger, **kwargs):
        super().__init__(**kwargs)
        self.logger = logger

    def set_logger(self, logger: logging.Logger):
        if isinstance(logger, logging.Logger):
            self.logger = logger

    def handle(self, item: Tuple[Submission, Comment]):
        self.logger.debug(f'handled item {item.id}')
