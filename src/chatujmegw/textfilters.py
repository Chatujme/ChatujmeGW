"""Translation of Chatujme HTML message markup to plain IRC text."""

import re
import urllib.parse


def clean_highlight(msg):
    return re.sub(r"<span style='background:#eded1a'>([^<]+)</span>", r"\1", msg)


def clean_urls(msg):
    def extract_real_url(match):
        href = match.group(1)
        # Extract real URL from redirect links like //link.chatujme.cz/redirect?url=https%3A%2F%2F...
        if 'link.chatujme.cz/redirect?url=' in href:
            try:
                # Get the url parameter and decode it
                url_param = href.split('url=', 1)[1]
                return urllib.parse.unquote(url_param)
            except Exception:
                pass
        # Fix protocol-relative URLs
        if href.startswith('//'):
            return 'https:' + href
        return href
    return re.sub(r'<a href="([^"]+)" target="_blank">([^<]+)</a>', extract_real_url, msg)


def clean_urls_mailto(msg):
    return re.sub(r'<a href="mailto:([^"]+)">([^<]+)</a>', r"\1", msg)


def clean_smiles(msg, show_smiles):
    if show_smiles == 0:
        pattern = ""
    elif show_smiles == 1:
        # Extract smile ID and display as *ID*
        pattern = r"*\2*"
    else:
        pattern = r"\1"
    # Updated regex to handle new API format with aria-label and title attributes
    # Old format: <img src='url' alt='text'>
    # New format: <img src='url' alt='text' aria-label='desc' title='desc'>
    return re.sub(r"<img src='(.+?smiles/([^.]+).gif)' alt='(.+?)'[^>]*>", pattern, msg)


def clean_message(msg, show_smiles):
    """Full pipeline in the order the poller historically applied it."""
    msg = clean_highlight(msg)
    msg = clean_smiles(msg, show_smiles)
    msg = clean_urls_mailto(msg)
    return clean_urls(msg)
