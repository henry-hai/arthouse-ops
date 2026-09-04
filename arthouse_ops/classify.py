"""Claude classification for one Contact Us message.

The prompt is carried over from the n8n workflow unchanged so that, while both
implementations are running side by side, a difference in the Sheet means a
difference in this code and not a difference in wording.

parse_response is separated from the API call because it is where the real risk
is: the model returns text, and every branch of "the text was not what we
asked for" has to land on a safe row rather than an exception.
"""

import json
import re

import anthropic

from . import logs, retry

log = logs.get("classify")

SYSTEM_PROMPT = (
    "You are a classifier for a nonprofit arts organization inbound contact form. "
    "Respond with ONLY a JSON object, no prose and no code fences, with exactly these keys: "
    "category (one of: sponsor, school, volunteer, general, spam), "
    "sentiment (one of: positive, neutral, urgent), "
    "summary (one sentence under 20 words). "
    "If unsure use category general and sentiment neutral."
)

MAX_INPUT_CHARS = 4000
MAX_SUMMARY_CHARS = 160

# Written to the summary column when classification did not produce a usable
# answer. The dedupe step treats a row carrying it as unfinished, so the next
# run picks it up again.
FAILED = "[classification failed]"

# Written instead when the entry has no message to classify. More than half of
# the older Contact Us entries are a name and an email with an empty body,
# because the form did not always store one. There is nothing for the model to
# read, so these are settled here rather than paid for. Unlike FAILED this is a
# final answer: a blank message will not fill in later, so the dedupe step
# treats the row as done and never asks again.
NO_MESSAGE = "No message text submitted."

_JSON_OBJECT = re.compile(r"\{.*\}", re.S)


def has_message(row):
    return bool((row.get("subject") or "").strip() or (row.get("message") or "").strip())


def build_prompt(row):
    return ("Subject: " + (row.get("subject") or "(none)")
            + " | Message: " + (row.get("message") or "(none)"))[:MAX_INPUT_CHARS]


def parse_response(text, categories, sentiments):
    """Pull a classification out of the model's text, or fall back safely.

    Anything unexpected (no JSON, bad JSON, a category outside the allowed
    list) leaves that field at its default. A summary left at FAILED is the
    signal that this entry still needs work.
    """
    result = {"category": "general", "sentiment": "neutral", "summary": FAILED}
    match = _JSON_OBJECT.search(text or "")
    if not match:
        return result
    try:
        parsed = json.loads(match.group(0))
    except (ValueError, TypeError):
        return result
    if not isinstance(parsed, dict):
        return result

    category = str(parsed.get("category", "")).lower().strip()
    sentiment = str(parsed.get("sentiment", "")).lower().strip()
    summary = str(parsed.get("summary", "")).strip()

    if category in categories:
        result["category"] = category
    if sentiment in sentiments:
        result["sentiment"] = sentiment
    if summary:
        result["summary"] = (
            summary[:MAX_SUMMARY_CHARS - 3] + "..." if len(summary) > MAX_SUMMARY_CHARS else summary
        )
    return result


def _retry_classifier(exc):
    """Rate limits, overload and transport faults are worth another attempt.

    A bad key or a bad request is not, so those raise on the first try instead
    of burning four more.
    """
    if isinstance(exc, anthropic.RateLimitError):
        header = (exc.response.headers or {}).get("retry-after")
        try:
            return retry.Retryable(exc, retry_after=float(header))
        except (TypeError, ValueError):
            return retry.Retryable(exc)
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return retry.Retryable(exc)
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return retry.Retryable(exc)
    return None


def response_text(message):
    for block in message.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


class Classifier:
    def __init__(self, config, client=None):
        self.config = config
        # The SDK retries on its own by default. Turn that off so the backoff
        # and the log lines come from one place.
        self.client = client or anthropic.Anthropic(
            api_key=config.anthropic_api_key, max_retries=0, timeout=60.0,
        )

    def classify(self, row):
        """Classify one row. Returns a classification dict, never raises."""
        entry_id = row.get("entry_id", "")

        if not has_message(row):
            log.info("skipped, no message body", entry_id=entry_id)
            return {"category": "general", "sentiment": "neutral", "summary": NO_MESSAGE}, None

        def send():
            return self.client.messages.create(
                model=self.config.llm_model,
                max_tokens=200,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_prompt(row)}],
            )

        try:
            message = retry.call(
                send, classify=_retry_classifier, op="messages.create", entry_id=entry_id,
            )
        except Exception as exc:  # noqa: BLE001 - one bad entry must not end the run
            log.error("classification failed", entry_id=entry_id, cause=str(exc)[:200])
            return {"category": "general", "sentiment": "neutral", "summary": FAILED}, exc

        if message.stop_reason == "refusal":
            log.warn("model declined the message", entry_id=entry_id)
            return {"category": "general", "sentiment": "neutral", "summary": FAILED}, None

        result = parse_response(
            response_text(message), self.config.categories, self.config.sentiments,
        )
        log.info(
            "classified", entry_id=entry_id, category=result["category"],
            sentiment=result["sentiment"], ok=result["summary"] != FAILED,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )
        return result, None
