"""
Watches the oTree firebase for changes.
It looks for two types of objects: logs and decision vectors.
All objects are scoped by the session.

Logs:
    Logs are generated by the otree-log component. Each log message has
    several built-in fields:
        subsession, round, group, role, participantCode
    These are all generated based on the oTree variables for the participant
    running the otree-log component that generated the log message.
    When using the otree-log component, keep this in mind. For example, if
    5 participants send a log event for some change, you will see 5 distinct
    log messages. For this reason, it is a best practice to only log user
    generated events, like the sending of a chat message.

    Logs also have an event field. This field is set by the component and
    the shape varies for each component. For example, otree-chat generates
    events of the shape:
        component: 'otree-chat'
        event: 'msg'
        msg: <user-generated text>

Decision Vectors:
    Decision vectors represent a subject's decision at a given point in
    time. Components can generate decision vectors following a common
    format.
"""
import atexit
from collections import defaultdict
import fasteners
import json
import logging
import re
from sseclient import SSEClient
import sys
import threading

from otree.models.participant import Participant
from otree.models.session import Session

from otree_redwood.models import Decision

logger = logging.getLogger(__name__)


def start():
    global _watchThread
    _watchThread = Thread('https://otree.firebaseio.com/.json')
    _watchThread.daemon = True
    _watchThread.start()


_DECISION_RE = re.compile(
    '/session/(?P<session>.*)' +
    '/app/(?P<app>.*)' +
    '/subsession/(?P<subsession>.*)' +
    '/round/(?P<round>.*)' +
    '/group/(?P<group>.*)' +
    '/component/(?P<component>.*)' +
    '/decisions/(?P<participant_code>.*)')


def _handleDecisionEvent(match, data):
    g = match.groupdict()
    d = Decision()
    d.component = g['component']
    try:
        d.session = Session.objects.get(code=g['session'])
    except Session.DoesNotExist:
        # Ignore events from sessions not managed by this instance of oTree
        return
    try:
        d.subsession = int(g['subsession'])
    except ValueError:
        pass
    d.round = int(g['round'])
    d.group = int(g['group'])
    d.participant = Participant.objects.get(code=g['participant_code'])
    d.app = g['app']
    d.value = data
    d.save()


def register_path(path, handlerFunction):
    global _watchThread
    _watchThread.matchers.append((re.compile(path), handlerFunction))


class Thread(threading.Thread):

    def __init__(self, fbURL):
        super(Thread, self).__init__()
        self.fbURL = fbURL
        self.decisions = defaultdict()
        self.matchers = [
            (_DECISION_RE, _handleDecisionEvent),
        ]

    def run(self):
        logger.info('Firewatch watching %s', self.fbURL)
        messages = SSEClient(self.fbURL)
        for msg in messages:
            if msg.event == 'put':
                message_payload = json.loads(msg.data)
                matches = []
                for (regex, handlerFunc) in self.matchers:
                    match = regex.match(message_payload['path'])
                    if match:
                        matches.append(
                            (handlerFunc, match, message_payload['data']))
                if len(matches) == 0:
                    logging.warning('no match for %s (matchers=%s)', message_payload['path'], self.matchers)
                for (f, match, data) in matches:
                    try:
                        f(match, data)
                    except:
                        logger.exception(
                            "data at firebase path %s caused exception",
                            message_payload['path'])
