"""Scripted language boundary for transport/storage tests; not natural-language evaluation."""
import json


class ServiceLanguageModel:
    def __init__(self, name="Acme", fact="Bakım hizmeti veriyoruz."):
        self.name, self.fact = name, fact
        self.calls = []

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        schema = kwargs["response_schema"]["title"]
        if schema == "RequestPlan":
            assert self.name in kwargs["system"]
            return json.dumps({"requests": [{"subject_id": "company", "topic": "details", "question": "Hizmetler?"}]})
        payload = json.loads(messages[0].content)
        assert self.fact in json.dumps(payload, ensure_ascii=False)
        if schema == "LanguageReply":
            return json.dumps({"text": self.fact, "evidence_ids": ["service"]})
        assert schema == "LanguageCheck"
        return json.dumps({"unsupported_claims": [], "supported": True})
