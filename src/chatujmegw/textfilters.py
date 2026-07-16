"""Translation of Chatujme HTML message markup to plain IRC text."""

import html
import re
import urllib.parse

# Smiley display modes (User.show_smiles)
SMILES_HIDE = 0
SMILES_TEXT = 1  # human description from aria-label, *ID* code when none exists
SMILES_URL = 2
SMILES_CODE = 3  # always the *ID* source code (round-trippable back to the web)

# Web markup (BasePresenter::streplaceSmiles):
# <img src='.../smiles/ID.gif' alt='&ast;ID&ast;' aria-label='Smajlík - text'
#      title='...' class='chat-smiley'>
# alt is the entity-encoded *ID* source code; aria-label/title carry the human
# description ("Smajlík - {text}" from DB, "Smajlík z kategorie {k}" otherwise).
SMILEY_PATTERN = re.compile(r"<img src='(.+?smiles/([^.]+)\.gif)' alt='(.+?)'([^>]*)>")
ARIA_LABEL_PATTERN = re.compile(r"aria-label='([^']*)'")
SMILEY_TEXT_PREFIX = "Smajlík - "


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
    def render(match):
        url, smiley_id, _alt, rest = match.groups()
        if show_smiles == SMILES_HIDE:
            return ""
        if show_smiles == SMILES_URL:
            return url
        if show_smiles == SMILES_TEXT:
            aria = ARIA_LABEL_PATTERN.search(rest)
            if aria:
                label = html.unescape(aria.group(1))
                # Only 52 of ~9700 smileys have a real description; the rest carry
                # just "Smajlík z kategorie X" which is noise in a 512-byte IRC line,
                # so those fall back to the *ID* code (which the web renders back)
                if label.startswith(SMILEY_TEXT_PREFIX):
                    return f"*{label[len(SMILEY_TEXT_PREFIX):]}*"
        return f"*{smiley_id}*"

    return SMILEY_PATTERN.sub(render, msg)


def clean_message(msg, show_smiles):
    """Full pipeline in the order the poller historically applied it."""
    msg = clean_highlight(msg)
    msg = clean_smiles(msg, show_smiles)
    msg = clean_urls_mailto(msg)
    return clean_urls(msg)
