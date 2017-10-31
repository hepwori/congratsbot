#!/usr/bin/python

import sys
import re
import time
import os
import signal
from collections import defaultdict
import logging; logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

DEBUG=False

class TwitterHelper:

    # you're going to need https://github.com/inueni/birdy
    from birdy.twitter import StreamClient
    from birdy.twitter import UserClient

    # the usual Twitter OAuth stuff goes here. you know the drill
    CONSUMER_KEY = os.environ['BOT_CONSUMER_KEY']
    CONSUMER_SECRET = os.environ['BOT_CONSUMER_SECRET']
    ACCESS_TOKEN = os.environ['BOT_ACCESS_TOKEN']
    ACCESS_TOKEN_SECRET = os.environ['BOT_ACCESS_TOKEN_SECRET']
        
    streaming_client = StreamClient(CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)
    rest_client = UserClient(CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

    @staticmethod
    def permalink(tweet_id, screen_name):
        return 'https://twitter.com/' + screen_name + '/statuses/' + tweet_id

    @staticmethod
    def recent_tweets(count, screen_name=None):
        return TwitterHelper.rest_client.api.statuses.user_timeline.get(screen_name=screen_name, count=count).data

    @staticmethod
    def raw_stream(raw_search_terms):
        return TwitterHelper.streaming_client.stream.statuses.filter.post(track=raw_search_terms).stream()

    @staticmethod
    def get_tweet(tweet_id):
        return TwitterHelper.rest_client.api.statuses.show.get(id=tweet_id).data

    @staticmethod
    def send_tweet(in_reply_to, text):
        if DEBUG:
            logging.info('Debug mode is on. Skipping sending tweet "' + text + '"')
        else:
            TwitterHelper.rest_client.api.statuses.update.post(in_reply_to_status_id=in_reply_to, status=text)
        
    @staticmethod
    def fav_tweet(tweet_id):
        if DEBUG:
            logging.info('Debug mode is on. Skipping favoriting tweet ' + tweet_id)
        else:
            TwitterHelper.rest_client.api.favorites.create.post(id=tweet_id)

    @staticmethod
    def send_dm(screen_name, text):
        if DEBUG:
            logging.info('Debug mode is on. Skipping sending DM "' + text + '" to ' + screen_name)
        else:
            TwitterHelper.rest_client.api.direct_messages.new.post(screen_name=screen_name, text=text)
        

class ReplyAggregatorBot:
    
    def __init__(self, owner, search_terms, response, threshold=5, recipient_blacklist=(), exclusion_heuristic=None, log_frequency=50):
        self.owner = owner
        self.search_terms = search_terms
        self.response = response
        self.threshold = threshold
        self.recipient_blacklist = recipient_blacklist
        self.reply_count = defaultdict(int)
        self.seen_tweet_count = 0
        self.interesting_tweet_count = 0
        self.exclusion_heuristic = exclusion_heuristic
        self.log_frequency = log_frequency
        self.tweets_sent = 0
    
    # occasional updates to the owner sent via DM
    def admin_dm(self, dm_text):
        try:
            dm_text = time.strftime('%Y-%m-%d %H:%M:%S') + ': ' + dm_text
            dm_text = dm_text[:140]
            TwitterHelper.send_dm(self.owner, dm_text)
            logging.info('Sent admin DM "' + dm_text + '"')
        except:
            logging.error('Couldn''t send admin DM "' + dm_text + '" because ' + str(sys.exc_info()))
    
    # keep the input stream up and running. use exponential back-off as needed
    def run(self):
        self.admin_dm("About to start @congratsbot")
        raw_terms = ','.join(self.search_terms)
        sleep_time = 1
        while True:
            try:
                logging.info('Attempting connection with search terms ' + raw_terms)
                input_stream = TwitterHelper.raw_stream(raw_terms)
                sleep_time = 1; logging.info('Connection successful'); self.admin_dm('Started stream with terms ' + raw_terms)
                self.process_stream(input_stream)
            except KeyboardInterrupt:
                self.admin_dm('Interrupted')
                return
            except:
                logging.error('Stream processing failed (' + str(sys.exc_info()) + '). Retrying in ' + str(sleep_time))
                self.admin_dm('Stream processing failed. Attempting restart in ' + str(sleep_time) + ' seconds')
                time.sleep(sleep_time)
                sleep_time *= 2
                continue

    # read messages from the stream and keep count. skip errors permissively
    def process_stream(self, stream):
        for tweet in stream:
            self.seen_tweet_count += 1

            if ( self.seen_tweet_count % self.log_frequency == 0 ):
                logging.info(str(self.seen_tweet_count) + ' tweets seen matching "' + str(self.search_terms) + '"; '
                    + str(self.interesting_tweet_count) + ' of them replies; to ' + str(len(self.reply_count)) + ' tweets; ' + str(self.tweets_sent) + ' tweets sent')

            try:
                if tweet.in_reply_to_status_id_str is None: continue
            except AttributeError:
                try:
                    logging.info('Track stream limited. Missed ' + str(tweet.limit.track) + ' tweets')
                    continue
                except:
                    logging.error('Oops decoding stream message "' + str(tweet) + '": ' + str(sys.exc_info()))
                    continue
                
            try:
                self.interesting_tweet_count += 1
                self.process_tweet(tweet)
            except:
                logging.error('Oops processing tweet "' + str(tweet) + '": ' + str(sys.exc_info()))
                
    # count up replied-to tweets; trigger activity on reaching reply count threshold
    def process_tweet(self, tweet):
        (tweet_id, screen_name) = (tweet.in_reply_to_status_id_str, tweet.in_reply_to_screen_name)
        permalink = TwitterHelper.permalink(tweet_id, screen_name)
        self.reply_count[permalink] += 1
        
        if self.reply_count[permalink] > 1:
            logging.info('+' * self.reply_count[permalink] + ' counts of ' + permalink)

        if self.reply_count[permalink] == self.threshold:
            if screen_name in self.recipient_blacklist:
                logging.info('Recipient ' + screen_name + ' is blacklisted. Skipping.')
            else:
                if (self.exclusion_heuristic is None) or (self.exclusion_heuristic(screen_name, tweet_id)):
                    self.respond_to_tweet(screen_name, tweet_id)
    
    # respond appropriately when a tweet reaches the threshold, skipping on error
    def respond_to_tweet(self, screen_name, tweet_id):
        try:
            logging.info('Favoriting tweet')
            TwitterHelper.fav_tweet(tweet_id)
            logging.info('Tweeting response')
            TwitterHelper.send_tweet(tweet_id, '@' + screen_name + ' ' + self.response)
            logging.info('Tweeted')
            self.tweets_sent += 1
        except:
            logging.error('Yeah, that didn''t work: ' + str(sys.exc_info()))
            

# grab recent tweets from the bot; we'll not respond to anyone recently responded to
recent_output = ' '.join([tweet.text for tweet in TwitterHelper.recent_tweets(count=50)])
recent_recipients = re.findall('@(\w+)', recent_output)
logging.info('Blacklisting recent recipients ' + ', '.join(recent_recipients))


# an heuristic to prevent replies to certain types of tweets which solicit congrats:
# 1. tweets containing congrats of their own, where the author is not a desired target of congrats
#       - example https://twitter.com/MICBloggers/status/604466857769197568
# 2. tweets mentioning a winner, for the same reason
#       - exampe https://twitter.com/itsKamaKazi/status/605594613634580480
def congrats_heuristic(screen_name, tweet_id):
    logging.debug('Processing heuristic for ' + TwitterHelper.permalink(tweet_id, screen_name))
    try:
        logging.info('Fetching original tweet ' + TwitterHelper.permalink(tweet_id, screen_name))
        original_tweet = TwitterHelper.get_tweet(tweet_id)
        logging.info('Original text: ' + original_tweet.text)
        # after looking at hundreds of misfires, this heuristic is reasonable enough for v1
        if ('congrat' in original_tweet.text.lower()) or ('win' in original_tweet.text.lower()):
            logging.info('Original tweet is likely congratuatory. Skipping.')
            return False
    except:
        logging.error('Oops when running heuristic check: ' + str(sys.exc_info()) + '. No tweet to ' + screen_name)
        return False
    
    return True


def signal_handler(signum, frame):
    raise KeyboardInterrupt, "Signal handler"
    
signal.signal(signal.SIGINT, signal_handler)


# configure a bot looking for tweets with the specified keywords. tally counts of those
# which are replies, and when the number of replies to a single tweet reaches the threshold,
# chime in with the specified response.
congrats_bot = ReplyAggregatorBot(
    owner='isaach',
    search_terms=('congrats','congratulations'),
    threshold=5,
    response='congratulations!',
    recipient_blacklist=recent_recipients + ["realDonaldTrump"] + ["POTUS"] + ["DonaldJTrumpJr"],
    exclusion_heuristic=congrats_heuristic)

congrats_bot.run()
