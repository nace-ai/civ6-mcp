"""Drex HTTP client against the documented /v1/systemone Choice contract."""

import asyncio
import json

import httpx
import pytest

from civ_mcp.drex.client import (
    DrexAuthError,
    DrexClient,
    DrexConfig,
    DrexModelMismatch,
    DrexProtocolError,
    DrexRequestRejected,
    DrexUnavailable,
    load_env_file,
)

KEY = "apikey_test_0123456789abcdef"
OPTIONS = {"Pottery": {"turns": 5}, "Mining": None}


def _ok_body(model="drex-1.1", probs=None, choice="Pottery"):
    probs = probs or {"Pottery": 0.7, "Mining": 0.3}
    return {
        "model": model,
        "answers": {
            "decision": {
                "type": "choice",
                "choice": choice,
                "probabilities": probs,
                "confidence": 0.41,
            }
        },
        "usage": {"input_tokens": 71, "output_tokens": 3},
    }


def _client(handler, **cfg) -> DrexClient:
    config = DrexConfig(api_key=KEY, **cfg)
    return DrexClient(config, transport=httpx.MockTransport(handler))


def _choose(client: DrexClient, options=OPTIONS):
    async def go():
        try:
            return await client.choose(
                state={"turn": 5}, instructions="Which tech?", options=options
            )
        finally:
            await client.aclose()

    return asyncio.run(go())


class TestRequest:
    def test_body_and_headers_follow_wire_contract(self):
        seen = {}

        def handler(request: httpx.Request):
            seen["url"] = str(request.url)
            seen["method"] = request.method
            seen["auth"] = request.headers["authorization"]
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json=_ok_body())

        _choose(_client(handler))
        assert seen["method"] == "POST"
        assert seen["url"] == "https://drex.nace.ai/v1/systemone"
        assert seen["auth"] == f"Bearer {KEY}"
        assert seen["body"] == {
            "model": "drex-latest",
            "state": {"turn": 5},
            "questions": {
                "decision": {
                    "type": "choice",
                    "instructions": "Which tech?",
                    "criteria": {"Pottery": {"turns": 5}, "Mining": None},
                }
            },
        }

    def test_more_options_than_limit_are_refused_before_sending(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=_ok_body())

        many = {f"opt{i}": None for i in range(256)}
        with pytest.raises(ValueError, match="255"):
            _choose(_client(handler), options=many)
        assert calls == []

    def test_single_option_is_refused(self):
        with pytest.raises(ValueError):
            _choose(
                _client(lambda r: httpx.Response(200, json=_ok_body())), {"A": None}
            )

    def test_config_repr_hides_key(self):
        assert KEY not in repr(DrexConfig(api_key=KEY))


class TestResponse:
    def test_parses_choice_answer_with_usage_and_request_id(self):
        def handler(request):
            return httpx.Response(
                200, json=_ok_body(), headers={"x-typesafe-request-id": "req_1"}
            )

        ans = _choose(_client(handler))
        assert dict(ans.probabilities) == {"Pottery": 0.7, "Mining": 0.3}
        assert ans.choice == "Pottery"
        assert ans.confidence == pytest.approx(0.41)
        assert ans.model == "drex-1.1"
        assert ans.usage == {"input_tokens": 71, "output_tokens": 3}
        assert ans.request_id == "req_1"
        assert ans.latency_ms is not None

    def test_duplicate_json_keys_are_rejected(self):
        raw = (
            '{"model":"drex-1.1","answers":{"decision":{"type":"choice",'
            '"choice":"Pottery","probabilities":{"Pottery":0.6,"Pottery":0.1,'
            '"Mining":0.3},"confidence":0.4}},"usage":{"input_tokens":1,"output_tokens":1}}'
        )
        with pytest.raises(DrexProtocolError, match="duplicate"):
            _choose(_client(lambda r: httpx.Response(200, content=raw.encode())))

    def test_non_choice_answer_is_rejected(self):
        body = _ok_body()
        body["answers"]["decision"] = {"type": "noul", "noul": 0.9}
        with pytest.raises(DrexProtocolError):
            _choose(_client(lambda r: httpx.Response(200, json=body)))

    def test_missing_answer_is_rejected(self):
        body = _ok_body()
        body["answers"] = {}
        with pytest.raises(DrexProtocolError):
            _choose(_client(lambda r: httpx.Response(200, json=body)))

    def test_non_json_body_is_rejected(self):
        with pytest.raises(DrexProtocolError):
            _choose(_client(lambda r: httpx.Response(200, content=b"<html>")))

    def test_model_other_than_drex_is_refused(self):
        with pytest.raises(DrexModelMismatch):
            _choose(
                _client(
                    lambda r: httpx.Response(200, json=_ok_body(model="jev-1.13.0"))
                )
            )

    def test_model_guard_can_be_disabled_explicitly(self):
        ans = _choose(
            _client(
                lambda r: httpx.Response(200, json=_ok_body(model="jev-1.13.0")),
                require_model_prefix=None,
            )
        )
        assert ans.model == "jev-1.13.0"


class TestErrors:
    def test_auth_failure_is_not_retryable_and_hides_key(self):
        body = {
            "error": {"type": "authentication_error", "message": "Invalid API key."}
        }
        with pytest.raises(DrexAuthError) as exc:
            _choose(_client(lambda r: httpx.Response(401, json=body)))
        assert exc.value.retryable is False
        assert KEY not in str(exc.value)

    @pytest.mark.parametrize("status", [400, 422])
    def test_rejected_request_is_not_retryable(self, status):
        with pytest.raises(DrexRequestRejected) as exc:
            _choose(_client(lambda r: httpx.Response(status, json={"detail": "bad"})))
        assert exc.value.retryable is False

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 529])
    def test_overload_and_server_errors_are_retryable(self, status):
        with pytest.raises(DrexUnavailable) as exc:
            _choose(
                _client(lambda r: httpx.Response(status, headers={"retry-after": "2"}))
            )
        assert exc.value.retryable is True

    def test_retry_after_is_parsed(self):
        with pytest.raises(DrexUnavailable) as exc:
            _choose(
                _client(lambda r: httpx.Response(429, headers={"retry-after": "3"}))
            )
        assert exc.value.retry_after_s == pytest.approx(3.0)

    def test_timeout_is_retryable(self):
        def handler(request):
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(DrexUnavailable) as exc:
            _choose(_client(handler))
        assert exc.value.retryable is True


class TestConfig:
    def test_env_file_parsing(self, tmp_path):
        f = tmp_path / "drex.env"
        f.write_text('# comment\nDREX_API_KEY="abc"\nDREX_MODEL=drex-1.1\n\n')
        assert load_env_file(f) == {"DREX_API_KEY": "abc", "DREX_MODEL": "drex-1.1"}

    def test_process_env_overrides_file(self, tmp_path):
        f = tmp_path / "drex.env"
        f.write_text("DREX_API_KEY=from_file\nDREX_BASE_URL=https://example.test\n")
        cfg = DrexConfig.from_env({"DREX_API_KEY": "from_env"}, env_file=f)
        assert cfg.api_key == "from_env"
        assert cfg.base_url == "https://example.test"
        assert cfg.model == "drex-latest"

    def test_missing_key_is_a_config_error(self, tmp_path):
        from civ_mcp.drex.client import DrexConfigError

        with pytest.raises(DrexConfigError):
            DrexConfig.from_env({}, env_file=tmp_path / "absent.env")


def test_list_models_uses_get_endpoint():
    seen = {}

    def handler(request):
        seen["method"], seen["url"] = request.method, str(request.url)
        return httpx.Response(200, json={"models": [{"name": "drex-latest"}]})

    async def go():
        client = _client(handler)
        try:
            return await client.list_models()
        finally:
            await client.aclose()

    assert asyncio.run(go()) == [{"name": "drex-latest"}]
    assert seen == {"method": "GET", "url": "https://drex.nace.ai/v1/models"}


def test_non_finite_json_constants_are_rejected():
    raw = (
        b'{"model":"drex-1.1","answers":{"decision":{"type":"choice","choice":"Pottery",'
        b'"probabilities":{"Pottery":NaN,"Mining":0.3},"confidence":0.4}},'
        b'"usage":{"input_tokens":Infinity}}'
    )
    with pytest.raises(DrexProtocolError):
        _choose(_client(lambda r: httpx.Response(200, content=raw)))


def test_body_decoding_failure_is_retryable():
    def handler(request):
        raise httpx.DecodingError("bad gzip", request=request)

    with pytest.raises(DrexUnavailable):
        _choose(_client(handler))


def test_invalid_request_is_a_non_retryable_drex_error():
    from civ_mcp.drex.client import DrexRequestInvalid

    with pytest.raises(DrexRequestInvalid) as exc:
        _choose(_client(lambda r: httpx.Response(200, json=_ok_body())), {"A": None})
    assert exc.value.retryable is False
