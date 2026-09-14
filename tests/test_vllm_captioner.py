"""The remote caption contract, including failures that must not become 'done'."""

from __future__ import annotations

import base64
import json
import threading
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import cv2
import httpx
import numpy as np
import pytest
from sentinel.core.config import settings
from sentinel.pipelines import base
from sentinel.pipelines.describe import DescribeWorker
from sentinel.pipelines.models.captioner import (
    Description,
    StubCaptioner,
    VllmCaptioner,
    load_captioner,
    parse_description_xml,
)

VEHICLE_XML = """<image>
<thinking>The green body is visible.

The yellow canopy and three-wheel shape support an autorickshaw identification.

The badges are unreadable, so make and model are unknown.</thinking>
<type>autorickshaw</type>
<colour>green</colour>
<make/>
<model/>
<features><feature>yellow canopy</feature><feature>R&amp;S signage</feature></features>
<caption>A green autorickshaw with a yellow canopy and R&amp;S signage.</caption>
</image>"""


def chat_response(content=VEHICLE_XML, finish_reason="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}


@pytest.fixture
def crop():
    return np.full((48, 64, 3), 127, dtype=np.uint8)


@pytest.fixture
def remote(monkeypatch):
    clients = []
    client_type = httpx.Client

    def make(handler, base_url="http://vllm:8008"):
        def client(**kwargs):
            result = client_type(transport=httpx.MockTransport(handler), **kwargs)
            clients.append(result)
            return result

        monkeypatch.setattr(httpx, "Client", client)
        return VllmCaptioner(base_url, "test-vision-model")

    yield make
    for client in clients:
        client.close()


def test_multiline_thinking_does_not_leak_into_description_fields():
    description = parse_description_xml(VEHICLE_XML)
    assert description == Description(
        colour="green",
        vtype="autorickshaw",
        make=None,
        model=None,
        features=["yellow canopy", "R&S signage"],
        caption="A green autorickshaw with a yellow canopy and R&S signage.",
    )


@pytest.mark.parametrize("unknown", ["", "unknown", "None", "null", "N/A"])
def test_unknown_identification_stays_empty(unknown):
    xml = VEHICLE_XML.replace("<make/>", f"<make>{unknown}</make>")
    xml = xml.replace("<model/>", f"<model>{unknown}</model>")
    result = parse_description_xml(xml)
    assert result.make is None
    assert result.model is None


def test_fenced_xml_is_accepted():
    assert parse_description_xml(f"```xml\n{VEHICLE_XML}\n```") == parse_description_xml(
        VEHICLE_XML
    )


@pytest.mark.parametrize(
    "content",
    [
        "",
        None,
        '{"type":"car"}',
        "<image><type>car</type></image>",
        VEHICLE_XML.replace("</image>", ""),
        VEHICLE_XML.replace("<model/>", "<make/>"),
        VEHICLE_XML.replace("<model/>", "<model><name>guessed</name></model>"),
        VEHICLE_XML.replace("<features>", "<features>roof rack"),
        VEHICLE_XML.replace("<image>", '<image source="example">'),
        "Here is the answer: " + VEHICLE_XML,
        '<!DOCTYPE image [<!ENTITY brand "invented">]>' + VEHICLE_XML,
    ],
)
def test_invalid_output_cannot_silently_become_an_empty_description(content):
    with pytest.raises(ValueError):
        parse_description_xml(content)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://vllm:8008",
        "http://vllm:8008/",
        "http://vllm:8008/v1/",
    ],
)
def test_request_has_three_xml_examples_and_the_actual_crop(remote, crop, monkeypatch, base_url):
    monkeypatch.setattr(settings, "vllm_max_tokens", 6000)

    def handle(request):
        assert str(request.url) == "http://vllm:8008/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == "test-vision-model"
        assert payload["max_tokens"] == 6000
        assert payload["stream"] is False
        messages = payload["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant"] * 3 + ["user"]
        examples = [parse_description_xml(m["content"]) for m in messages[1:6:2]]
        assert [d.vtype for d in examples] == ["car", "motorcycle", "autorickshaw"]
        assert "multiple paragraphs" in messages[0]["content"]
        text, image = messages[-1]["content"]
        assert "Detector class hint: auto" in text["text"]
        prefix, encoded = image["image_url"]["url"].split(",", 1)
        assert prefix == "data:image/jpeg;base64"
        decoded = cv2.imdecode(np.frombuffer(base64.b64decode(encoded), np.uint8), cv2.IMREAD_COLOR)
        assert decoded.shape == crop.shape
        return httpx.Response(200, json=chat_response())

    result = remote(handle, base_url)(crop, "auto")
    assert result.vtype == "autorickshaw"
    assert result.colour == "green"


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"choices": []},
        chat_response(content=None),
        chat_response(content="not XML"),
        chat_response(finish_reason="length"),
    ],
)
def test_invalid_or_truncated_completions_raise(remote, crop, result):
    captioner = remote(lambda request: httpx.Response(200, json=result))
    with pytest.raises(ValueError):
        captioner(crop, "car")


def test_http_failure_is_propagated_for_retry(remote, crop):
    captioner = remote(lambda request: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        captioner(crop, "car")


def test_timeout_is_propagated_for_retry(remote, crop):
    def timeout(request):
        raise httpx.ReadTimeout("model busy", request=request)

    with pytest.raises(httpx.ReadTimeout):
        remote(timeout)(crop, "car")


def test_invalid_crop_is_rejected_before_http_request(remote, monkeypatch, crop):
    handler = Mock()
    captioner = remote(handler)
    with pytest.raises(ValueError, match="empty crop"):
        captioner(np.empty((0, 0, 3), dtype=np.uint8), "car")
    monkeypatch.setattr(cv2, "imencode", lambda *args: (False, None))
    with pytest.raises(ValueError, match="encode"):
        captioner(crop, "car")
    handler.assert_not_called()


def test_loader_selects_vllm_when_url_is_set(monkeypatch):
    monkeypatch.setattr(settings, "vllm_url", "http://vllm:8008")
    monkeypatch.setattr(settings, "vllm_model", "served-model")
    loaded = load_captioner()
    try:
        assert isinstance(loaded, VllmCaptioner)
        assert loaded.model == "served-model"
    finally:
        loaded.client.close()


def test_loader_keeps_stub_when_vllm_is_unconfigured(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "vllm_url", "")
    monkeypatch.setattr(settings, "caption_model_path", tmp_path / "missing.onnx")
    assert isinstance(load_captioner(), StubCaptioner)


async def test_describe_persists_xml_attributes_and_embeds_only_caption(remote, crop):
    worker = DescribeWorker()
    worker.captioner = remote(lambda request: httpx.Response(200, json=chat_response()))
    worker.embedder = Mock(return_value=[1.0, 0.0])
    worker.model_id = 7
    conn = AsyncMock()
    job = SimpleNamespace(
        read_id=uuid.uuid4(),
        payload={
            "class": "auto",
            "permitted_attributes": ["type", "features"],
        },
    )
    await worker.process(conn, job, crop)
    args = conn.execute.call_args.args
    assert args[1:8] == (
        job.read_id,
        None,
        "autorickshaw",
        None,
        None,
        ["yellow canopy", "R&S signage"],
        "A green autorickshaw with a yellow canopy and R&S signage.",
    )
    assert args[9] == 7
    worker.embedder.assert_called_once_with(args[7])


async def test_caption_request_runs_outside_the_shared_event_loop(crop):
    loop_thread = threading.get_ident()

    def captioner(image, vehicle_class):
        assert threading.get_ident() != loop_thread
        return Description()

    worker = DescribeWorker()
    worker.captioner = captioner
    worker.embedder = Mock()
    job = SimpleNamespace(read_id=uuid.uuid4(), payload={})
    await worker.process(AsyncMock(), job, crop)


async def test_failed_caption_retries_without_ack_or_successful_write(remote, crop, monkeypatch):
    worker = DescribeWorker()
    worker.captioner = remote(lambda request: httpx.Response(503))
    worker.embedder = Mock()
    monkeypatch.setattr(worker, "_load_crop", lambda ref: crop)
    conn = AsyncMock()

    @asynccontextmanager
    async def transaction():
        yield conn

    monkeypatch.setattr(base, "transaction", transaction)
    fail, ack, post = AsyncMock(return_value=False), AsyncMock(), AsyncMock()
    monkeypatch.setattr(base.queue, "fail", fail)
    monkeypatch.setattr(base.queue, "ack", ack)
    monkeypatch.setattr(base.outbox, "post", post)
    job = SimpleNamespace(
        id=1,
        read_id=uuid.uuid4(),
        crop_ref="crops/test.jpg",
        payload={},
        attempts=1,
    )
    await worker._handle(job)
    fail.assert_awaited_once()
    assert "HTTPStatusError" in fail.call_args.args[2]
    conn.execute.assert_not_awaited()
    ack.assert_not_awaited()
    post.assert_not_awaited()
    assert worker.failed == 1
    assert worker.processed == 0


@pytest.mark.parametrize(("concurrency", "expected_limit"), [(1, 1), (2, 2), (12, 8)])
async def test_long_inference_does_not_leave_prefetched_jobs_waiting_on_a_lease(
    concurrency,
    expected_limit,
    monkeypatch,
):
    worker = DescribeWorker(concurrency=concurrency, drain=True)
    monkeypatch.setattr(worker, "setup", AsyncMock())
    monkeypatch.setattr(worker, "_resolve_model_id", AsyncMock())
    monkeypatch.setattr(settings, "queue_claim_batch", 8)
    conn = AsyncMock()

    @asynccontextmanager
    async def acquire():
        yield conn

    monkeypatch.setattr(base, "acquire", acquire)
    claim = AsyncMock(return_value=[])
    monkeypatch.setattr(base.queue, "claim", claim)
    await worker.run()
    claim.assert_awaited_once_with(conn, worker.pipeline, limit=expected_limit)
