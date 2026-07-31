import logging

import pytest

from apps.common.logging import JSONFormatter, RedactPIIFilter, redact


@pytest.mark.parametrize(
    "text",
    [
        "login for +2348031234567 ok",
        "login for 08031234567 ok",
        "login for 2348031234567 ok",
        "login for 09131234567 ok",
    ],
)
def test_phone_numbers_never_reach_the_log(text):
    assert "234803" not in redact(text)
    assert "0803123" not in redact(text)
    assert "0913123" not in redact(text)
    assert "[redacted]" in redact(text)


def test_otp_codes_are_redacted():
    assert "482913" not in redact("sending OTP: 482913 to user")
    assert "1234" not in redact("pin=1234")


def test_labelled_nin_is_redacted():
    assert "12345678912" not in redact("NIN: 12345678912")


def test_ordinary_numbers_survive():
    # Scores, amounts and IDs must stay readable or the logs are useless.
    assert redact("score 85 of 100 for term 2") == "score 85 of 100 for term 2"
    assert "45000" in redact("invoice total 45000")


def test_filter_rewrites_the_record():
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "otp 123456 to 08031234567", (), None
    )
    RedactPIIFilter().filter(record)
    assert "123456" not in record.getMessage()
    assert "08031234567" not in record.getMessage()


def test_json_formatter_emits_one_parsable_line():
    import json

    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", (), None)
    record.tenant = "kings-college"
    payload = json.loads(JSONFormatter().format(record))
    assert payload["message"] == "hello"
    assert payload["tenant"] == "kings-college"
    assert payload["level"] == "INFO"
