"""No generated artifact may carry a credential."""
import re
from urllib.parse import urlparse

from builder.config import settings


def leaks(value):
    """Places where an artifact would carry a credential. Paths only, never the value."""
    secrets = settings.secret_values()
    sensitive = ('authorization', 'apikey', 'accesstoken', 'password', 'secret', 'token')
    found = []

    def visit(item, path):
        if len(found) >= 20:
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if re.sub(r'[^a-z]', '', str(key).lower()) in sensitive:
                    found.append(path + ': credential field')
                visit(child, path + '.' + (str(key) if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,40}', str(key)) else '*'))
        elif isinstance(item, list):
            for i, child in enumerate(item):
                visit(child, path + '[' + str(i) + ']')
        elif isinstance(item, str):
            if any(s in item for s in secrets):
                found.append(path + ': a secret from the environment')
            elif re.search(r'(?i)\bfak_[a-z0-9_-]{6,}|\bsk-ant-[a-z0-9_-]{6,}|\bbearer\s+[a-z0-9._-]{12,}', item):
                found.append(path + ': credential-like text')
            for url in re.findall(r'https?://[^\s"<>]+', item):
                try:
                    parsed = urlparse(url)
                    if parsed.username or parsed.password:
                        found.append(path + ': credentials inside a URL')
                except ValueError:
                    pass
    visit(value, '$')
    return found
