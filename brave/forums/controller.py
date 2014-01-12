# encoding: utf-8

from __future__ import unicode_literals

from binascii import hexlify, unhexlify
from hashlib import sha256
from web.auth import authenticate, user
from web.core import config, Controller, url, request, response
from web.core.http import HTTPFound, HTTPNotFound
from web.auth import authenticated
from marrow.mailer import Mailer
from ecdsa.keys import SigningKey, VerifyingKey
from ecdsa.curves import NIST256p

from brave.core.api.client import API
from brave.forums import util
from brave.forums.model import Forum, Thread, Comment
from brave.forums.live import Channel


log = __import__('logging').getLogger(__name__)


def get_channel(*tokens):
    return '/_live?id={0}'.format(sha256('-'.join(tokens)))


class ThreadController(Controller):
    def __init__(self, forum, id):
        self.forum = forum
        self.thread = Thread.objects.get(id=id)
        self.channel = Channel(self.forum.id, self.thread.id)
        super(ThreadController, self).__init__()
    
    def index(self, page=1, message=None, upload=None, vote=None):
        if request.method == 'POST':
            if not message or not message.strip():
                return 'json:', dict(success=False, message="Empty message.")
            
            self.thread.stat.comments += 1
            self.thread.comments.append(Comment(
                    message = message,
                    creator = user._current_obj(),
                ))
            self.thread.save()
            
            payload = dict(
                    index = self.thread.stat.comments,
                    character = dict(id=unicode(user.id), nid=user.character.id, name=user.character.name),
                    when = dict(
                            iso = self.thread.comments[-1].created.strftime('%Y-%m-%dT%H:%M:%S%z'),
                            pretty = self.thread.comments[-1].created.strftime('%B %e, %G at %H:%M:%S')
                        ),
                    message = bbcode.render_html(message)
                )
            
            self.channel.send('commented', payload)
            
            return 'json:', dict(success=True)
        
        Thread.objects(id=self.thread.id).update_one(inc__stat__views=1)
        return 'brave.forums.template.thread', dict(page=1, forum=self.forum, thread=self.thread, endpoint=self.channel.receiver)
    
    def __default__(self, page, id=None):
        return 'brave.forums.template.thread', dict(page=int(page), forum=self.forum, thread=self.thread), dict(only='comments')


class ForumController(Controller):
    def __init__(self, short):
        try:
            self.forum = Forum.objects.get(short=short)
        except Forum.DoesNotExist:
            raise HTTPNotFound()
        
        super(ForumController, self).__init__()
    
    def index(self, page=1, title=None, message=None, upload=None, vote=None):
        if request.method == 'POST':
            thread = Thread(forum=self.forum, title=title)
            thread.comments.append(Comment(
                    message = message,
                    creator = user._current_obj(),
                ))
            thread.save()
            return 'json:', dict(success=True)
        
        if request.is_xhr:
            return 'brave.forums.template.forum', dict(page=int(page), forum=self.forum), dict(only='threads')
        
        return 'brave.forums.template.forum', dict(page=page, forum=self.forum)
    
    def __lookup__(self, thread, *args, **kw):
        return ThreadController(self.forum, thread), args


class RootController(Controller):
    def __init__(self):
        # Configure mail delivery services.
        util.mail = Mailer(config, 'mail')
        util.mail.start()
        
        # Load our keys into a usable form.
        config['api.private'] = SigningKey.from_string(unhexlify(config['api.private']), curve=NIST256p, hashfunc=sha256)
        config['api.public'] = VerifyingKey.from_string(unhexlify(config['api.public']), curve=NIST256p, hashfunc=sha256)
    
    def die(self):
        """Simply explode.  Useful to get the interactive debugger up."""
        1/0
    
    def index(self):
        if authenticated:
            return 'brave.forums.template.index', dict(announcements=Forum.objects.get(short='ann'))
        
        return 'brave.forums.template.welcome', dict()
    
    def authorize(self):
        # Perform the initial API call and direct the user.
        
        api = API(config['api.endpoint'], config['api.identity'], config['api.private'], config['api.public'])
        
        success = str(url.complete('/authorized'))
        failure = str(url.complete('/nolove'))
        
        result = api.core.authorize(success=success, failure=failure)
        
        raise HTTPFound(location=result.location)
    
    def authorized(self, token):
        # Capture the returned token and use it to look up the user details.
        # If we don't have this character, create them.
        # Store the token against this user account.
        # Note that our own 'sessions' may not last beyond the UTC date returned as 'expires'.
        # (Though they can be shorter!)
        
        # We request an authenticated session from the server.
        
        user = authenticate(token)
        
        raise HTTPFound(location='/')
    
    def preview(self, content):
        import bbcode

        # If no content has been submitted to preview, show an alert box instead
        if content.strip() == '':
            return 'brave.forums.template.thread', dict(), dict(only="no_preview"),
        else:
            return bbcode.render_html(content)


    def nolove(self, token):
        return 'brave.forums.template.whynolove', dict()
    
    def live(self):
        """Per-user notification channel.
        
        TODO: Eventually MUX everything through here.  Need a dispatcher.
        """
        
        if not authenticated:
            raise HTTPNotFound()
        
        response.headers['x-accel-redirect'] = '/_live?id={0}'.format(user.id)
        return ""
    
    def __lookup__(self, forum, *args, **kw):
        return ForumController(forum), args
