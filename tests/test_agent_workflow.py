import json

from bughound_agent import BugHoundAgent
from llm_client import MockClient


class FakeClient:
    """Test double whose analyzer response is a fixed string we control."""

    def __init__(self, analyzer_reply: str):
        self._analyzer_reply = analyzer_reply

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if "Return ONLY valid JSON" in system_prompt:
            return self._analyzer_reply
        # Fixer path: return non-empty rewrite so propose_fix doesn't fall back.
        return "# fixed\n"


def test_workflow_runs_in_offline_mode_and_returns_shape():
    agent = BugHoundAgent(client=None)  # heuristic-only
    code = "def f():\n    print('hi')\n    return True\n"
    result = agent.run(code)

    assert isinstance(result, dict)
    assert "issues" in result
    assert "fixed_code" in result
    assert "risk" in result
    assert "logs" in result

    assert isinstance(result["issues"], list)
    assert isinstance(result["fixed_code"], str)
    assert isinstance(result["risk"], dict)
    assert isinstance(result["logs"], list)
    assert len(result["logs"]) > 0


def test_offline_mode_detects_print_issue():
    agent = BugHoundAgent(client=None)
    code = "def f():\n    print('hi')\n    return True\n"
    result = agent.run(code)

    assert any(issue.get("type") == "Code Quality" for issue in result["issues"])


def test_offline_mode_proposes_logging_fix_for_print():
    agent = BugHoundAgent(client=None)
    code = "def f():\n    print('hi')\n    return True\n"
    result = agent.run(code)

    fixed = result["fixed_code"]
    assert "logging" in fixed
    assert "logging.info(" in fixed


def test_mock_client_forces_llm_fallback_to_heuristics_for_analysis():
    # MockClient returns non-JSON for analyzer prompts, so agent should fall back.
    agent = BugHoundAgent(client=MockClient())
    code = "def f():\n    print('hi')\n    return True\n"
    result = agent.run(code)

    assert any(issue.get("type") == "Code Quality" for issue in result["issues"])
    # Ensure we logged the fallback path
    assert any("Falling back to heuristics" in entry.get("message", "") for entry in result["logs"])


def test_all_malformed_llm_issues_fall_back_to_heuristics():
    # Parseable JSON, but no item has a valid msg/severity -> untrustworthy output.
    agent = BugHoundAgent(client=FakeClient(json.dumps([{"foo": "bar"}])))
    code = "def f():\n    print('hi')\n    return True\n"
    result = agent.run(code)

    # Heuristic analyzer should have run instead and caught the print issue.
    assert any(issue.get("type") == "Code Quality" for issue in result["issues"])
    assert any("all malformed" in entry.get("message", "") for entry in result["logs"])


def test_mixed_llm_issues_keep_valid_and_drop_malformed():
    reply = json.dumps(
        [
            {"type": "Reliability", "severity": "High", "msg": "Bare except is risky."},
            {"type": "Noise", "severity": "High", "msg": "   "},  # empty msg -> dropped
        ]
    )
    agent = BugHoundAgent(client=FakeClient(reply))
    result = agent.run("def f():\n    return True\n")

    msgs = [i.get("msg") for i in result["issues"]]
    assert msgs == ["Bare except is risky."]
    assert any("Dropped 1 malformed" in entry.get("message", "") for entry in result["logs"])


def test_invalid_severity_issue_is_dropped():
    # Invalid severity ("Critical") AND empty msg -> the lone item is malformed.
    reply = json.dumps([{"type": "X", "severity": "Critical", "msg": ""}])
    agent = BugHoundAgent(client=FakeClient(reply))
    code = "def f():\n    print('hi')\n    return True\n"
    result = agent.run(code)

    assert all(i.get("severity") != "Critical" for i in result["issues"])
    # Whole response was malformed -> heuristic fallback catches the print.
    assert any(issue.get("type") == "Code Quality" for issue in result["issues"])


def test_empty_llm_array_is_trusted_no_fallback():
    agent = BugHoundAgent(client=FakeClient("[]"))
    # Code the heuristics WOULD flag (print), to prove we don't cross-check.
    code = "def f():\n    print('hi')\n    return True\n"
    result = agent.run(code)

    assert result["issues"] == []
    assert not any("Falling back to heuristics" in entry.get("message", "") for entry in result["logs"])
