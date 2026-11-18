"""Quoted values are how .env, YAML and JSON spell a credential; they must not survive."""

import json

import pytest
from agent_memory.core.redaction import REDACTED, redact, redact_with_report


@pytest.mark.parametrize(
    ("line", "secret"),
    [
        ('password: "hunter2"', "hunter2"),
        ("password: 'hunter2'", "hunter2"),
        ('export PASSWORD="s3cr3t"', "s3cr3t"),
        ('{"api_key": "abcd1234efgh5678"}', "abcd1234efgh5678"),
        ('token = "ghp_Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8"', "ghp_Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8"),
        ("secret='a-b_c.d'", "a-b_c.d"),
    ],
)
def test_a_quoted_value_is_redacted(line, secret):
    cleaned = redact(line)

    assert secret not in cleaned
    assert REDACTED in cleaned


def test_the_quote_is_kept_so_the_line_still_reads_as_config():
    assert redact('password: "hunter2"') == 'password: "[REDACTED]"'
    assert redact("password: 'hunter2'") == "password: '[REDACTED]'"


def test_an_unquoted_value_is_still_redacted():
    assert redact("api_key=plain-value-here") == "api_key=[REDACTED]"
    assert redact("Authorization: Bearer plain-token-here") == "Authorization: Bearer [REDACTED]"


def test_a_quoted_value_without_a_colon_is_not_touched():
    assert redact('the string "hunter2" appears alone') == 'the string "hunter2" appears alone'


@pytest.mark.parametrize(
    ("line", "secret"),
    [
        ('password: "correct horse battery staple"', "horse"),
        ("PASSWORD='my secret phrase'", "secret phrase"),
        ('{"password": "pa,ss word"}', "pa,ss"),
        ('{"api_key": "abc,def123"}', "def123"),
        ('secret = "it\'s a secret"', "a secret"),
    ],
)
def test_a_quoted_value_with_spaces_or_commas_is_redacted_whole(line, secret):
    cleaned = redact(line)

    assert secret not in cleaned
    assert REDACTED in cleaned


def test_a_quoted_value_with_spaces_keeps_its_quotes_and_the_rest_of_the_line():
    assert redact('password: "a b c" and more') == 'password: "[REDACTED]" and more'
    assert redact('{"token": "a b", "user": "ann"}') == '{"token": "[REDACTED]", "user": "ann"}'


def test_an_escaped_quote_does_not_end_a_quoted_value():
    assert redact(r'{"password": "say \"hi\" now"}') == '{"password": "[REDACTED]"}'
    assert redact(r"secret: 'it\'s a b'") == "secret: '[REDACTED]'"


def test_a_trailing_backslash_still_ends_a_quoted_value():
    assert redact(r'password: "C:\dir\"') == 'password: "[REDACTED]"'


def test_an_empty_quoted_value_is_left_alone():
    assert redact('password: ""') == 'password: ""'


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('password: "unterminated', 'password: "[REDACTED]'),
        ("secret = 'truncated phrase", "secret = '[REDACTED]"),
        ('password: "unterminated\nuser=ann', 'password: "[REDACTED]\nuser=ann'),
    ],
)
def test_an_unterminated_quoted_value_is_redacted_to_the_line_end(line, expected):
    assert redact(line) == expected


def test_a_malformed_second_key_cannot_leave_its_value_visible():
    cleaned = redact('api_key: "abc | secret: "xyz"')

    assert "abc" not in cleaned
    assert "xyz" not in cleaned
    assert cleaned == 'api_key: "[REDACTED]"'


def test_nested_key_in_a_well_formed_value_preserves_siblings_and_report_order():
    key = "ghp_" + "A" * 36
    line = f'{key}; password="label secret=inside" ok=yes; token="sample"'
    expected = '[REDACTED]; password="[REDACTED]" ok=yes; token="[REDACTED]"'
    assert redact_with_report(line) == (expected, ["github_token", "credential", "credential"])
    assert redact_with_report(expected) == (expected, [])


def test_nested_json_serialization_retains_structure_and_counts_each_field_once():
    log = json.dumps({"password": 'nested "value"', "token": "unit-test-only", "health": "ok"})
    envelope = json.dumps({"log": log, "status": "ready"})
    cleaned, found = redact_with_report(envelope)
    decoded = json.loads(cleaned)
    assert decoded["status"] == "ready"
    assert json.loads(decoded["log"]) == {"password": REDACTED, "token": REDACTED, "health": "ok"}
    assert found == ["credential", "credential"]
    assert redact_with_report(cleaned) == (cleaned, [])


@pytest.mark.parametrize("newline", ["\r\n", "\n", "\r"])
def test_empty_assignment_does_not_consume_the_next_line(newline):
    line = f"password:  {newline}public-status=available"
    assert redact_with_report(line) == (line, [])


def test_adjacent_fields_preserve_separators_and_existing_punctuated_markers():
    line = "token=fixture-a;secret=fixture-b|password=[REDACTED_PHONE_CN]."
    clean = "token=[REDACTED];secret=[REDACTED]|password=[REDACTED_PHONE_CN]."
    assert redact_with_report(line) == (clean, ["credential", "credential"])
    for marker in ("[REDACTED]", "[REDACTED_PHONE_CN]", "[REDACTED_ID_CN]"):
        text = f"token={marker}. password='{marker}' secret={marker}）"
        assert redact_with_report(text) == (text, [])


def test_empty_quoted_secret_before_text_is_preserved():
    line = '''password="" status=pending token='unit-test' '''
    expected = '''password="" status=pending token='[REDACTED]' '''
    assert redact_with_report(line) == (expected, ["credential"])


def test_nested_quotes_around_an_existing_marker_do_not_count_or_change_category():
    line = '''token="'[REDACTED_PHONE_CN]'"'''
    assert redact_with_report(line) == (line, [])


@pytest.mark.parametrize("depth", [0, 1, 2])
def test_a_quoted_literal_ending_in_a_key_preserves_other_json_fields(depth):
    line = '{"password":"🔒 test label secret:", "status":"ready", "token":"sample"}'
    for _ in range(depth):
        line = json.dumps({"inner": line})
    expected = {"password": REDACTED, "status": "ready", "token": REDACTED}
    cleaned, report = redact_with_report(line)
    decoded = json.loads(cleaned)
    for _ in range(depth):
        decoded = json.loads(decoded["inner"])
    assert decoded == expected
    assert report == ["credential", "credential"]


def test_malformed_nested_value_starting_with_whitespace_remains_private():
    line = 'password: "label secret: " status=fixture-value"'
    assert redact_with_report(line) == ('password: "[REDACTED]"', ["credential"])


def test_cn_id_requires_gregorian_day_as_well_as_checksum():
    def with_check_digit(first_seventeen):
        # ISO 7064 MOD 11-2, written as the recurrence rather than the production weights.
        remainder = 0
        for digit in first_seventeen:
            remainder = (remainder + int(digit)) * 2 % 11
        return first_seventeen + "0123456789X"[(1 - remainder) % 11]

    invalid = with_check_digit("32010219990431014")
    valid = with_check_digit("32010220000229014")
    assert redact_with_report(invalid) == (invalid, [])
    # A business identifier with the same valid shape has no distinguishing feature in text.
    assert redact_with_report(f"订单 {valid}") == ("订单 [REDACTED_ID_CN]", ["id_card_cn"])
