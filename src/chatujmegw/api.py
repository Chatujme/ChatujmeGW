"""HTTP transport to the Chatujme.cz IRC API.

Cookie session lives on the User's url_fetcher; retry notices go back
to the IRC client through the owning session's send_raw.
"""

import json
import time
import urllib.error
import urllib.parse

from . import config
from .util import log


def is_public_ip(ip):
    """Check if IP is public (not localhost or private range)"""
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
        return not (addr.is_loopback or addr.is_private or addr.is_reserved)
    except ValueError:
        return False


class ChatujmeAPI:
    URL = "https://api.chatujme.cz/irc"  # Security: Always use HTTPS

    def __init__(self, sess):
        self.sess = sess

    def _apply_headers(self):
        headers = [('User-agent', config.USER_AGENT)]
        if self.sess.user.client_version:
            headers.append(('X-IRC-Client', self.sess.user.client_version))
        # Send client IP if it's a public address
        if is_public_ip(self.sess.address):
            headers.append(('X-IRC-IP', self.sess.address))
        self.sess.user.url_fetcher.addheaders = headers

    def get(self, url, retry_count=0):
        """Fetch URL with retry limit to prevent infinite loops"""
        self._apply_headers()
        try:
            response = self.sess.user.url_fetcher.open(url, timeout=config.API_TIMEOUT)
            return response.read().decode('utf-8')
        except Exception as e:
            if retry_count >= config.MAX_RETRIES:
                log(f"[GET_URL] Max retries ({config.MAX_RETRIES}) reached for {url}")
                return '{"code": 500, "message": "Connection failed after retries"}'
            self.sess.send_raw(f":{self.sess.server_name} NOTICE * :Connection error (retry {retry_count + 1}/{config.MAX_RETRIES}): {e}\r\n")
            time.sleep(config.RETRY_DELAY)
            return self.get(url, retry_count + 1)

    def post(self, url, postdata, retry_count=0):
        """POST URL with retry limit to prevent infinite loops"""
        self._apply_headers()
        try:
            response = self.sess.user.url_fetcher.open(url, data=postdata.encode('utf-8'), timeout=config.API_TIMEOUT)
            return response.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            # Read JSON response from HTTP errors (403, 404, etc.) - these are valid API responses
            if e.code in (400, 403, 404):
                try:
                    return e.read().decode('utf-8')
                except Exception:
                    return f'{{"code": {e.code}, "message": "{e.reason}"}}'
            # Other HTTP errors - retry
            if retry_count >= config.MAX_RETRIES:
                log(f"[POST_URL] Max retries ({config.MAX_RETRIES}) reached for {url}")
                return '{"code": 500, "message": "Connection failed after retries"}'
            self.sess.send_raw(f":{self.sess.server_name} NOTICE * :Connection error (retry {retry_count + 1}/{config.MAX_RETRIES}): {e}\r\n")
            time.sleep(config.RETRY_DELAY)
            return self.post(url, postdata, retry_count + 1)
        except Exception as e:
            if retry_count >= config.MAX_RETRIES:
                log(f"[POST_URL] Max retries ({config.MAX_RETRIES}) reached for {url}")
                return '{"code": 500, "message": "Connection failed after retries"}'
            self.sess.send_raw(f":{self.sess.server_name} NOTICE * :Connection error (retry {retry_count + 1}/{config.MAX_RETRIES}): {e}\r\n")
            time.sleep(config.RETRY_DELAY)
            return self.post(url, postdata, retry_count + 1)

    def get_no_retry(self, url):
        """Get URL without retry on failure - used for disconnect cleanup"""
        self._apply_headers()
        try:
            response = self.sess.user.url_fetcher.open(url, timeout=5)
            return response.read().decode('utf-8')
        except Exception as e:
            if config.DEBUG:
                log(f"[GET_NO_RETRY] {url} failed: {e}")
            raise

    # --- endpoints ---

    def authenticate(self, username, password):
        # Security: URL-encode username and password to prevent injection
        safe_username = urllib.parse.quote_plus(username)
        safe_password = urllib.parse.quote_plus(password)
        return self.post(f"{self.URL}/check-login", f"username={safe_username}&password={safe_password}")

    def get_rooms(self):
        return self.get(f"{self.URL}/get-rooms")

    def get_room(self, room_id):
        return self.get(f"{self.URL}/get-room?id={int(room_id)}")

    def get_users(self, room_id):
        return self.get(f"{self.URL}/get-users?id={room_id}")

    def get_messages(self, room_id, from_id):
        return self.get(f"{self.URL}/get-messages?id={room_id}&from={int(from_id)}")

    def join(self, room_id):
        """Single attempt (no retry); returns parsed JSON or an error dict."""
        self._apply_headers()
        try:
            response = self.sess.user.url_fetcher.open(f"{self.URL}/join?id={room_id}", timeout=config.API_TIMEOUT)
            return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            log(f"[JOIN] Error: {e}")
            return {"code": 500, "message": str(e)}

    def part(self, room_id):
        return self.get_no_retry(f"{self.URL}/part?id={room_id}")

    def post_text(self, room_id, text, target):
        postdata = urllib.parse.urlencode({'roomId': room_id, 'text': text, 'target': target})
        return self.post(f"{self.URL}/post-text", postdata)

    def set_topic(self, room_id, topic):
        postdata = urllib.parse.urlencode({'roomId': room_id, 'topic': topic})
        return self.post(f"{self.URL}/set-topic", postdata)

    def ping(self):
        return self.get(f"{self.URL}/ping")
