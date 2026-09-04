"""Reading the model's answer, including every way it can be wrong."""

import anthropic
import httpx2
import pytest

from arthouse_ops import classify

CATEGORIES = ["sponsor", "school", "volunteer", "general", "spam"]
SENTIMENTS = ["positive", "neutral", "urgent"]

MESSAGE_ROW = {"entry_id": "1", "subject": "", "message": "Can we sponsor a class?"}


def parse(text):
    return classify.parse_response(text, CATEGORIES, SENTIMENTS)


def test_clean_json():
    result = parse('{"category":"sponsor","sentiment":"positive","summary":"PTA offers an auction partnership."}')
    assert result == {"category": "sponsor", "sentiment": "positive",
                      "summary": "PTA offers an auction partnership."}


def test_json_inside_a_code_fence():
    """The prompt forbids fences. The model does it anyway often enough."""
    result = parse('```json\n{"category":"spam","sentiment":"neutral","summary":"Cold sales pitch."}\n```')
    assert result["category"] == "spam"


def test_json_with_surrounding_prose():
    result = parse('Sure! Here you go:\n{"category":"school","sentiment":"neutral","summary":"Asks about signup."}\nHope that helps.')
    assert result["category"] == "school"


@pytest.mark.parametrize("text", ["", "no json at all", "{not valid json}", "[1,2,3]", None])
def test_unusable_responses_fall_back(text):
    assert parse(text) == {"category": "general", "sentiment": "neutral",
                           "summary": classify.FAILED}


def test_out_of_range_values_fall_back_field_by_field():
    """A bad category must not throw away a good summary."""
    result = parse('{"category":"partnership","sentiment":"angry","summary":"Wants to sponsor."}')
    assert result["category"] == "general"
    assert result["sentiment"] == "neutral"
    assert result["summary"] == "Wants to sponsor."


def test_case_and_padding_are_tolerated():
    result = parse('{"category":" Sponsor ","sentiment":"URGENT","summary":"x"}')
    assert result["category"] == "sponsor"
    assert result["sentiment"] == "urgent"


def test_long_summary_is_truncated_to_fit_the_column():
    result = parse('{"category":"general","sentiment":"neutral","summary":"%s"}' % ("word " * 60))
    assert len(result["summary"]) == classify.MAX_SUMMARY_CHARS
    assert result["summary"].endswith("...")


def test_empty_summary_keeps_the_failure_sentinel():
    """An empty summary means the next run should try this entry again."""
    result = parse('{"category":"sponsor","sentiment":"positive","summary":""}')
    assert result["category"] == "sponsor"
    assert result["summary"] == classify.FAILED


def test_prompt_is_capped(contact_us_page):
    row = {"subject": "s", "message": "m" * 10000}
    assert len(classify.build_prompt(row)) == classify.MAX_INPUT_CHARS


def test_prompt_labels_missing_fields():
    assert classify.build_prompt({}) == "Subject: (none) | Message: (none)"


# --- the API call itself, against a stub client ---

class StubMessage:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [type("Block", (), {"type": "text", "text": text})()]
        self.stop_reason = stop_reason
        self.usage = type("Usage", (), {"input_tokens": 100, "output_tokens": 20})()


class StubClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_classify_returns_the_parsed_result(settings):
    client = StubClient([StubMessage('{"category":"sponsor","sentiment":"positive","summary":"ok"}')])
    result, exc = classify.Classifier(settings, client=client).classify(MESSAGE_ROW)
    assert exc is None
    assert result["category"] == "sponsor"


def test_a_refusal_is_not_an_exception(settings):
    client = StubClient([StubMessage("", stop_reason="refusal")])
    result, exc = classify.Classifier(settings, client=client).classify(MESSAGE_ROW)
    assert exc is None
    assert result["summary"] == classify.FAILED


def api_error(status, headers=None):
    """An SDK error built the way the SDK builds one, so status_code and the
    headers a retry reads are both real."""
    response = httpx2.Response(
        status, headers=headers or {},
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    return anthropic.APIStatusError("boom", response=response, body=None)


def test_overload_is_retried_then_succeeds(settings, monkeypatch):
    monkeypatch.setattr("arthouse_ops.retry.time.sleep", lambda _: None)
    overloaded = api_error(529)
    client = StubClient([overloaded, StubMessage('{"category":"spam","sentiment":"neutral","summary":"s"}')])
    result, exc = classify.Classifier(settings, client=client).classify(MESSAGE_ROW)
    assert client.calls == 2
    assert exc is None
    assert result["category"] == "spam"


def test_a_bad_api_key_is_not_retried(settings, monkeypatch):
    monkeypatch.setattr("arthouse_ops.retry.time.sleep", lambda _: None)
    denied = api_error(401)
    client = StubClient([denied])
    result, exc = classify.Classifier(settings, client=client).classify(MESSAGE_ROW)
    assert client.calls == 1
    assert exc is not None
    assert result["summary"] == classify.FAILED


def test_rate_limit_waits_for_the_retry_after_header(settings, monkeypatch):
    """The server knows better than the backoff formula. Honour its number."""
    slept = []
    monkeypatch.setattr("arthouse_ops.retry.time.sleep", slept.append)
    limited = anthropic.RateLimitError(
        "slow down",
        response=httpx2.Response(
            429, headers={"retry-after": "7"},
            request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"),
        ),
        body=None,
    )
    client = StubClient([limited, StubMessage('{"category":"general","sentiment":"neutral","summary":"s"}')])
    classify.Classifier(settings, client=client).classify(MESSAGE_ROW)
    assert slept == [7.0]


def test_a_run_of_failures_stops_at_the_attempt_limit(settings, monkeypatch):
    monkeypatch.setattr("arthouse_ops.retry.time.sleep", lambda _: None)
    client = StubClient([api_error(503) for _ in range(5)])
    result, exc = classify.Classifier(settings, client=client).classify(MESSAGE_ROW)
    assert client.calls == 5
    assert exc is not None
    assert result["summary"] == classify.FAILED


def test_an_entry_with_no_body_costs_nothing(settings):
    """Most of the older Contact Us entries are a name and an email with no
    message. There is nothing to classify, so no request is sent."""
    client = StubClient([])
    result, exc = classify.Classifier(settings, client=client).classify(
        {"entry_id": "1", "subject": "", "message": ""})
    assert client.calls == 0
    assert exc is None
    assert result == {"category": "general", "sentiment": "neutral",
                      "summary": classify.NO_MESSAGE}


def test_the_no_message_answer_is_final_not_a_retry():
    """FAILED means try again next run. NO_MESSAGE means there is nothing to
    try. They have to be different strings or the empty rows never settle."""
    assert classify.NO_MESSAGE != classify.FAILED


def test_a_subject_alone_is_enough_to_classify():
    assert classify.has_message({"subject": "Sponsorship", "message": ""})
    assert classify.has_message({"subject": "", "message": "hello"})
    assert not classify.has_message({"subject": "   ", "message": ""})
    assert not classify.has_message({})
