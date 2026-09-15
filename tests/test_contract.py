import pytest

from enjambre import AgentResult


def test_parse_tolerates_chatter_and_fences():
    text = 'Let me think... {"draft": true}\n```json\n{"status": "completed", "result": "hi"}\n```'
    res = AgentResult.parse(text)
    assert res.ok and res.result == "hi"


def test_last_contract_object_wins():
    text = '{"status": "failed", "error": "first try"} then {"status": "completed", "result": "second"}'
    assert AgentResult.parse(text).result == "second"


def test_missing_contract_is_a_failure_with_a_code():
    res = AgentResult.parse("sure, done!")
    assert res.status == "failed" and res.error_code == "contract_invalid_json"


def test_aliases_and_invalid_status():
    assert AgentResult.from_any({"status": "success"}).ok
    assert AgentResult.from_any({"status": "canceled"}).status == "cancelled"
    bad = AgentResult.from_any({"status": "probably"})
    assert bad.error_code == "contract_invalid_status"


def test_plain_text_and_empty_values():
    assert AgentResult.from_any("hello").ok
    assert AgentResult.from_any("").error_code == "empty_result"
    assert AgentResult.from_any(None).status == "failed"


def test_constructor_rejects_non_terminal_status():
    with pytest.raises(ValueError):
        AgentResult(status="running")


def test_failure_text_keeps_the_diagnosis():
    res = AgentResult.failed("step 3 crashed", result="stack trace here")
    assert res.visible_text() == "failed: step 3 crashed\n\nstack trace here"
