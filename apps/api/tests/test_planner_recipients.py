"""Phone formatting must neither break outreach nor authorize invented recipients."""

import json

import pytest

from src.modules.admin_chat import planner, workflow_intents


@pytest.mark.parametrize(
    "literal,model_value",
    [
        ("+90 555 010 20 30", "+905550102030"),
        ("+90 (555) 010-20-30", "+90 5550102030"),
        ("0090 555 010 20 30", "00905550102030"),
        ("+905550102030", "+905550102030"),
    ],
)
async def test_formatted_phone_opens_review_workflow(monkeypatch, literal, model_value):
    class Model:
        async def complete(self, *args, **kwargs):
            return json.dumps(
                {"tool": "outreach", "recipients": [model_value], "purpose": "mesaj yoll"}
            )

    monkeypatch.setattr(planner, "get_llm_client", lambda: Model())
    intent, source = await planner.plan(f"{literal} mesaj yoll")
    assert source == "model"
    assert intent.recipients == [literal]
    workflow = workflow_intents.normalize(intent)
    assert workflow.workflow_kind == "outreach"
    assert workflow.workflow_action == "start"
    assert workflow.workflow_fields == {
        "recipients": "00905550102030" if literal.startswith("00") else "+905550102030",
        "purpose": "mesaj yoll",
    }


@pytest.mark.parametrize(
    "message,value",
    [
        ("+90 555 010 20 30 mesaj yoll", "+905550102031"),
        ("+90 555 010 20 30 mesaj yoll", "+90555010203"),
        ("+90 555 010 20 30 mesaj yoll", "+9055501020300"),
        ("0555 010 20 30 mesaj yoll", "+905550102030"),
        ("0090 555 010 20 30 mesaj yoll", "+905550102030"),
        ("+90 555 010 20 30 mesaj yoll", "00905550102030"),
        ("+15550102030 ve +15550102031 mesaj yoll", "+1555010203015550102031"),
    ],
)
async def test_model_cannot_change_phone_identity(monkeypatch, message, value):
    class Model:
        async def complete(self, *args, **kwargs):
            return json.dumps({"tool": "outreach", "recipients": [value]})

    monkeypatch.setattr(planner, "get_llm_client", lambda: Model())
    with pytest.raises(ValueError, match="Ungrounded recipient"):
        await planner.plan(message)


def test_multiple_recipients_remain_separate():
    message = "+1 (555) 010-2030 ve +1 555 010 2031 mesaj yoll"
    assert planner.literal_recipient("+15550102030", message) == "+1 (555) 010-2030"
    assert planner.literal_recipient("+15550102031", message) == "+1 555 010 2031"
