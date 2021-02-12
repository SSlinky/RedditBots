import re
import json
import html
import praw
import time
import logging
import requests

"""

Gets answers and comments from SO submissions.

Question Type: https://api.stackexchange.com/docs/types/question
Filter Editor: https://api.stackexchange.com/docs/questions-by-ids#order=desc&sort=activity&ids=349613&filter=!)bB87nNAB)iIh5K8KQVmOL4H40p)XRkP)79T7eSsVlD.OSeRNyIc8Pu54AHzHNiKpa4CfQSn*Atq5NmamubWvieL9*b&site=stackoverflow&run=true

"""


class Post():
    """A base class for the Question, Answer, and Comment types to inherit"""
    def __init__(self, metadata: dict):
        self.metadata = metadata

    def post_title(self) -> str:
        """Default post title"""
        return ''

    def link(self) -> str:
        """The link url to the post"""
        body = self.metadata['link']
        logger.debug(body)
        return body

    def score(self) -> str:
        """The post vote score"""
        body = self.metadata['score']
        logger.debug(body)
        return body

    def comments(self) -> list:
        """The comments attached to the post"""
        body = [Comment(comment) for comment in self.metadata['comments']]
        return body

    def author(self) -> str:
        """Returns the post author display name"""
        body = self.metadata['owner']['display_name']
        logger.debug(body)
        return body

    def post_body(self) -> str:
        """Returns the post body in markdown format"""
        body = html.unescape(self.metadata['body_markdown'])
        logger.debug(body)
        return body

    def post_footer(self) -> str:
        """Returns the post footer in markdown format"""
        body = f"^score: {self.score()} - author: {self.author()}".replace(' ', ' ^').replace(' ^ ', '  ')
        logger.debug(body)
        return body

    def reddit_post(self) -> str:
        """Generates the markdown text that nicely formats a post on reddit"""
        # Comppose the post
        post_markdown = f'{self.post_title()}\n\n{self.post_body()}\n\n{self.post_footer()}\n'
        # Add comments if they exist
        if self.metadata['comment_count'] > 0:
            post_markdown += f'\n_Comments_\n'
            for i,comment in enumerate(self.comments(), start=1):
                rp = comment.reddit_post()                          # Get the comment
                if len(post_markdown) + len(rp) > POST_CHAR_MAX:    # Check if it will exceed max
                    logger.warning(f'{len(self.comments()) - i + 1} comments truncated due to post length.')
                    break                                       
                post_markdown += f'\n{i}. {rp}'                     # Append the comment to the post

        if len(post_markdown) > POST_CHAR_MAX: logger.warning(f'Post truncated. Exceeds max length ({len(post_markdown)}).')
        return post_markdown[:POST_CHAR_MAX]
        

class Answer(Post):
    """Object representing an answer on StackOverflow"""
    def __init__(self, metadata: dict):
        super().__init__(metadata)

    def is_accepted(self):
        """True if the answer is marked as the accepted answer"""
        return self.metadata['is_accepted']

    def post_footer(self) -> str:
        """Returns the post footer in markdown format"""
        body = f"^score: {self.score()} - author: {self.author()}{' - accepted answer' if self.is_accepted() else ''}".replace(' ', ' ^').replace(' ^ ', '  ')
        logger.debug(body)
        return body
    

class Question(Post):
    """Object representing a question on StackOverflow"""
    def __init__(self, metadata: dict):
        super().__init__(metadata['items'][0])
        self.remaining_quota = metadata['quota_remaining']
    
    def quota(self) -> int:
        """Returns the remaining api quota for the day"""
        return self.remaining_quota

    def answers(self) -> list:
        """Returns a list of answer objects"""
        return [Answer(answer) for answer in self.metadata['answers']]

    def post_title(self) -> str:
        """Returns the question title in markdown format"""
        return f"##{self.metadata['title']}"

class Comment(Post):
    """Object representing a comment on StackOverflow"""
    def __init__(self, metadata: dict):
        super().__init__(metadata)
    
    def comments(self):
        """Comments do not have comments"""
        return []

    def reddit_post(self):
        """Generates the markdown text that nicely formats a SO comment on reddit"""
        # TODO Find a better way to truncate the result so that it isn't adding half comments / urls
        return f"[{self.score()}]({self.link()}) - {self.post_body()} - {self.author()}"


class Reddit():
    """Connector that makes use of Praw to interface with Reddit"""
    def __init__(self):
        # Get credentials
        with open('creds.json', 'r') as f:
            creds = json.load(f)
            pr = creds['Praw']
            me = creds['Reddit']
            self.app_key = creds['StackApp']['key']
            self.username = me['username']

        # Set up client
        self.connection = praw.Reddit(
            client_id=pr['client_id'], 
            client_secret=pr['client_secret'],
            user_agent=pr['user_agent'],
            username=me['username'],
            password=me['password'])

    def get_question_ids(self, source: str) -> list:
        """Extracts StackOverflow question ids if they exist in the passed in source"""
        PATTERN = r"(?:stackoverflow\.com/questions/)(\d+)"
        matches = re.finditer(PATTERN, source, re.MULTILINE)
        return [m.group(1) for m in matches]

    def check_posts(self):
        """Main routine that monitors new Reddit submissions"""
        subreddit = self.connection.subreddit(SUBREDDIT)
        for submission in subreddit.stream.submissions(skip_existing=True):
            # Check that we are not replying to ourselves
            if submission.author.name in [self.username, 'AutoModerator']: continue

            # TODO -- Find a better way to prevent a question being
            # linked more than once to the same thread
            """  not an issue until I find a way to get comments too """

            so_qids = self.get_question_ids(submission.selftext)
            if len(so_qids) > 0:
                for qid in so_qids:
                    # Get the submission level links
                    q = get_api(qid, self.app_key)
                    # Create a top level comment
                    comment = submission.reply(q.reddit_post())
                    answers = q.answers()
                    if len(answers) > 0:
                        # Determine the answers to post
                        accepted = [a for a in answers if a.is_accepted()]
                        hghrated = [a for a in answers if a.score() >= MIN_SCORE]

                        # Sort and trim the highest rated so that the top can be selected
                        hghrated.sort(key=lambda x: x.score())[:MAX_HIGHEST_RATED]

                        # Remove the answer from the highest rated
                        hghrated = [a for a in hghrated if a not in accepted]

                    # Generate the replies
                    if len(accepted) > 0: comment.reply(accepted[0].reddit_post())
                    for answer in hghrated: comment.reply(answer.reddit_post())
                        
    
    def check_comments(self, submission):
        pass


def backoff(seconds):
    """Sleeps the programme for the passed in seconds"""
    logger.info(f'Sleeping {seconds} seconds.')
    time.sleep(seconds)

def qurl(id: int) -> str:
    """Generates a question url from the passed in id"""
    return f'https://api.stackexchange.com/2.2/questions/{id}'

STACKAPP = {
    'ClientId': 19604,
}

PARAMS = {
    'order': 'desc',
    'sort': 'activity',
    'site': 'stackoverflow',
    'filter': '!)bB87nNAB)iIh5K8KQVmOL4H40p)XRkP)79SqrK4YsMQzAC_olD_O(l890*)eoTI-RG9Fx2-mCmPETbjX1JAtiyo3V-'
}

def get_api(question_id: int, app_key: str = None) -> Question:
    """Attempts to get a stackoverflow answer from a link"""
    try:
        # Get question response from SO
        if not app_key is None: PARAMS['key'] = app_key
        url = qurl(question_id)
        logger.info(f'GET {url}')
        r = requests.get(url=url, params=PARAMS)

        # Test the response
        if r.status_code != 200:
            logger.error(f'failed to get a valid response for question [{question_id}] with url [{url}]... response code [{r.status_code}].')
            return

        # Extract the json response
        j = r.json()
        logger.debug(f'Response:\n{j}')

        # Initialise the question with data
        q = Question(j)
        logger.info(f'Quota remaining: {q.quota()}.')

        # Respect the backoff command
        s = j.get('backoff', None)
        if s != None:
            logger.warning(f"Backoff detected: {s}")
            backoff(s)

        return q
    except Exception as e:
        logger.exception(f'get_api failed with exception: {e}')


# Configure Settings
# -----------------

POST_CHAR_MAX = 10000
MAX_HIGHEST_RATED = 2
MIN_SCORE = 20
SUBREDDIT = 'VBAMod'


# Configure logging
# -----------------
logger_format = logging.Formatter(
    fmt='%(asctime)s [%(levelname)s][%(filename)s > %(funcName)s() > %(lineno)s]:  %(message)s',
    datefmt='%Y-%m-%d %I:%M:%S %p')

# Configure file debug handler
filed_handler = logging.FileHandler('stackoverflow.debug.log', encoding='utf-8')
filed_handler.setLevel(logging.DEBUG)
filed_handler.setFormatter(logger_format)

# Configure file info handler
filei_handler = logging.FileHandler('stackoverflow.info.log', encoding='utf-8')
filei_handler.setLevel(logging.INFO)
filei_handler.setFormatter(logger_format)

# Configure console handler
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(logger_format)

# TODO Make a handler that can fire off issues to slack

# Attach handlers to loggers
for logger_name in ("praw", "prawcore", __name__):
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(filed_handler)
    logger.addHandler(filei_handler)
    logger.addHandler(stream_handler)

logger.info("Started")


vbaMod = Reddit()
vbaMod.check_posts()

# TODO - move everything to a config file
# TODO - move everything to classes and use a __main__
# TODO - split into separate files
# TODO - add functionality for async checking submissions and comments (remember backoff)

