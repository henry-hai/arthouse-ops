"""Reading form entries from the WordPress connector plugin.

The plugin is in wordpress-plugin/ and exposes GET /entries with limit and
offset. It has no cursor, so paging is offset arithmetic, and a short page
means the end. max_pages is a stop so a plugin bug cannot loop forever.
"""

import requests

from . import logs, retry

log = logs.get("wordpress")

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class NotJson(Exception):
    """The endpoint answered, but not with JSON.

    Worth its own error because the cause is never in the JSON decoder's
    message. A host that blocks datacenter IPs, a login page, a maintenance
    page and a WAF challenge all look identical until you see the body, so the
    status, content type and first part of the body travel with the error.
    """

    def __init__(self, response):
        self.status_code = response.status_code
        self.content_type = response.headers.get("Content-Type", "")
        self.snippet = " ".join(response.text[:300].split())
        super().__init__(
            f"expected JSON, got {self.status_code} {self.content_type}: {self.snippet}")


def _retry_http(exc):
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return retry.Retryable(exc)
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        if exc.response.status_code in RETRYABLE_STATUS:
            header = exc.response.headers.get("Retry-After")
            try:
                return retry.Retryable(exc, retry_after=float(header))
            except (TypeError, ValueError):
                return retry.Retryable(exc)
    return None


class Client:
    def __init__(self, config, session=None):
        self.config = config
        self.session = session or requests.Session()
        self.session.auth = (config.wp_username, config.wp_app_password)

    def _page(self, form_id, offset):
        def send():
            response = self.session.get(
                self.config.entries_url,
                params={
                    "form": form_id,
                    "limit": self.config.page_size,
                    "offset": offset,
                    "include_spam": 0,
                },
                timeout=60,
            )
            response.raise_for_status()
            try:
                return response.json()
            except ValueError:
                raise NotJson(response) from None

        return retry.call(send, classify=_retry_http, op="GET entries",
                          form=form_id, offset=offset)

    def fetch_all(self, form_id):
        """Every non-spam entry for one form, as the plugin's page payloads.

        Individual pages are logged at debug. There are ninety of them for the
        Contact Us form and one line each buries everything else in the run.
        """
        pages, fetched = [], 0
        offset = self.config.start_offset
        for page_number in range(self.config.max_pages):
            body = self._page(form_id, offset)
            entries = body.get("entries") or []
            pages.append(body)
            fetched += len(entries)
            log.debug("fetched page", form=form_id, page=page_number + 1,
                      offset=offset, entries=len(entries))
            if len(entries) < self.config.page_size:
                break
            offset += self.config.page_size
        else:
            log.warn("stopped at the page limit", form=form_id,
                     max_pages=self.config.max_pages)

        log.info("fetched entries", form=form_id, pages=len(pages),
                 entries=fetched, reported_total=pages[-1].get("total") if pages else 0)
        return pages
