"""Video provider chain tests: quota/queue hand-offs and final local fallback."""

import base64

import pytest
import requests

from core import hf_connectors, video_dispatcher
from core.video_dispatcher import VideoDispatcher, public_video_error
from core.video_providers import huggingface as hf_module
from core.video_providers import local_storyboard, pollinations as poll_module
from core.video_providers.base import VideoTaskStatus


MP4_BYTES = b"\x00\x00\x00\x18ftypisomvideo"


class FakeResponse:
    def __init__(self, status_code=200, content=b"", text="", headers=None, json_data=None):
        self.status_code = status_code
        self.content = content
        self.text = text
        self.headers = headers or {"content-type": "video/mp4"}
        self._json = json_data if json_data is not None else {}

    def json(self):
        if self._json is None:
            raise ValueError("Response body is not JSON")
        return self._json


def _prepare(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.delenv("HF_VIDEO_ZERO_GPU_SPACE", raising=False)
    monkeypatch.setattr(hf_module, "provider_blocked", lambda name: False)
    monkeypatch.setattr(poll_module, "provider_blocked", lambda name: False)
    monkeypatch.setattr(local_storyboard, "provider_blocked", lambda name: False)
    monkeypatch.setattr(hf_module, "record_error", lambda *a, **kw: None)
    monkeypatch.setattr(hf_module, "record_success", lambda *a, **kw: None)
    monkeypatch.setattr(poll_module, "record_error", lambda *a, **kw: None)
    monkeypatch.setattr(poll_module, "record_success", lambda *a, **kw: None)
    monkeypatch.setattr(local_storyboard, "record_success", lambda *a, **kw: None)
    monkeypatch.setattr(poll_module, "_models", lambda: ["wan"])
    monkeypatch.setattr(hf_module, "_save_video", lambda data, ext="mp4": "/static/generated/hf.mp4")
    monkeypatch.setattr(poll_module, "_save_video", lambda data, ext="mp4": "/static/generated/backup.mp4")
    monkeypatch.setattr(local_storyboard, "_build_local_video", lambda prompt: "/static/generated/local.mp4")


def _dispatcher():
    dispatcher = VideoDispatcher.__new__(VideoDispatcher)
    dispatcher._providers = [
        hf_module.HuggingFaceVideoProvider(),
        poll_module.PollinationsVideoProvider(),
        local_storyboard.LocalStoryboardVideoProvider(),
    ]
    dispatcher._tasks = {}
    return dispatcher


def _patch_hf(monkeypatch, failure, captured=None):
    """Replace the LTX ZeroGPU call seam and record its arguments."""
    captured = captured if captured is not None else {}

    def fake_ltx(prompt, reference_image=None, timeout=None):
        captured.update(
            {"prompt": prompt, "reference_image": reference_image, "timeout": timeout}
        )
        if failure.get("timeout"):
            raise requests.exceptions.Timeout("upstream timed out")
        status = failure.get("status", 200)
        if status == 200:
            return MP4_BYTES, "Lightricks/LTX-2.5"
        error = RuntimeError(f"Space HTTP {status}: {failure.get('text', 'quota exhausted')}")
        error.status_code = status
        raise error

    monkeypatch.setattr(hf_module, "_call_ltx_space", fake_ltx)
    return captured


def _patch_pollinations(monkeypatch, failure=None, async_job=False):
    spec = failure or {}

    def fake_get(url, params=None, headers=None, timeout=None, **kwargs):
        if spec.get("timeout"):
            raise requests.exceptions.Timeout("upstream timed out")
        status = spec.get("status", 200)
        if status != 200:
            return FakeResponse(status, text=spec.get("text", "rate limited"))
        if async_job and "/v1/jobs/" in url and not url.endswith("/result"):
            return FakeResponse(
                200, content=b'{"status":"succeeded"}',
                headers={"content-type": "application/json"},
                json_data={"status": "succeeded"},
            )
        if async_job and url.endswith("/result"):
            return FakeResponse(200, content=MP4_BYTES, headers={"content-type": "video/mp4"})
        if async_job:
            return FakeResponse(
                202, content=b'{"id":"job-1"}',
                headers={"content-type": "application/json"},
                json_data={"id": "job-1"},
            )
        return FakeResponse(200, content=MP4_BYTES, headers={"content-type": "video/mp4"})

    monkeypatch.setattr(poll_module.requests, "get", fake_get)


def test_hf_ltx_space_is_primary_video_provider():
    assert hf_module.SPACE == "Lightricks/LTX-2.5"


def test_hf_ltx_space_success_uses_primary_video(monkeypatch):
    _prepare(monkeypatch)
    captured = _patch_hf(monkeypatch, {})

    task = _dispatcher().generate("A drone shot over the coast")

    assert captured["prompt"] == "A drone shot over the coast"
    assert task.status == VideoTaskStatus.COMPLETED
    assert task.video_url == "/static/generated/hf.mp4"
    assert task.metadata["_provider"] == "HuggingFaceVideo"
    assert task.metadata["model"] == "Lightricks/LTX-2.5"


def test_hf_video_forwards_reference_image_for_image_to_video(monkeypatch):
    _prepare(monkeypatch)
    reference = "data:image/png;base64," + base64.b64encode(b"reference-frame").decode("ascii")
    captured = _patch_hf(monkeypatch, {})

    task = _dispatcher().generate("Animate this photo", reference_image=reference)

    assert task.status == VideoTaskStatus.COMPLETED
    assert captured["reference_image"] == reference


def test_ltx_space_maps_live_gradio_schema_to_call_arguments(monkeypatch):
    """The Space call uses view_api labels instead of hard-coded positions."""
    captured = {}

    class FakeSpaceClient:
        def view_api(self, return_format=None, print_info=False):
            captured["return_format"] = return_format
            return {
                "named_endpoints": {
                    "/run": {
                        "parameters": [
                            {"label": "Prompt", "parameter_name": "prompt"},
                            {
                                "label": "Input image (optional ? image-to-video)",
                                "parameter_name": "image_path",
                            },
                            {
                                "label": "Height (final)",
                                "parameter_name": "height",
                                "type": {"type": "number", "description": "between 512 and 1536"},
                            },
                            {
                                "label": "Width (final)",
                                "parameter_name": "width",
                                "type": {"type": "number", "description": "between 512 and 1536"},
                            },
                            {"label": "Seed", "parameter_name": "seed"},
                        ],
                        "returns": [{"label": "Result (with audio)"}],
                    },
                    "/set_dims_from_image": {"parameters": [], "returns": []},
                }
            }

        def predict(self, api_name=None, **kwargs):
            captured["api_name"] = api_name
            captured["kwargs"] = kwargs
            image_value = kwargs["image_path"]
            captured["image_file_payload"] = isinstance(image_value, dict)
            image_path = image_value["path"] if isinstance(image_value, dict) else image_value
            with open(image_path, "rb") as handle:
                captured["image_bytes"] = handle.read()
            return b"generated-video-bytes" * 8

    monkeypatch.setattr(
        hf_connectors, "space_client",
        lambda space, token=None, verbose=False: FakeSpaceClient(),
    )

    data = hf_connectors.space_media(
        "Lightricks/LTX-2.5",
        prompt="Make the subject turn around",
        image_bytes=b"reference-frame",
        size=(576, 320),
        timeout=10,
    )

    assert captured["return_format"] == "dict"
    assert captured["api_name"] == "/run"
    assert captured["kwargs"]["prompt"] == "Make the subject turn around"
    assert captured["kwargs"]["height"] == 512  # live Space clamps to its published range
    assert captured["kwargs"]["width"] == 576
    assert captured["image_file_payload"] is True
    assert captured["image_bytes"] == b"reference-frame"
    assert data == b"generated-video-bytes" * 8


def test_distilled_ltx_schema_prefers_real_video_endpoints(monkeypatch):
    """Helper upload/dimension functions must never shadow I2V/T2V generation."""
    def parameter(name, label, description=None):
        item = {"label": label, "parameter_name": name}
        if description:
            item["type"] = {"description": description}
        return item

    api = {
        "named_endpoints": {
            "/handle_image_upload_for_dims": {
                "parameters": [parameter("image_filepath", "Input Image")],
                "returns": [{"label": "Height"}, {"label": "Width"}],
            },
            "/text_to_video": {
                "parameters": [
                    parameter("prompt", "Prompt"),
                    parameter("input_image_filepath", "image_n"),
                    parameter("input_video_filepath", "video_n"),
                    parameter("height_ui", "Height", "between 256 and 1280"),
                    parameter("width_ui", "Width", "between 256 and 1280"),
                ],
                "returns": [{"label": "Generated Video"}],
            },
            "/image_to_video": {
                "parameters": [
                    parameter("prompt", "Prompt"),
                    parameter("input_image_filepath", "Input Image"),
                    parameter("input_video_filepath", "video_i"),
                    parameter("height_ui", "Height", "between 256 and 1280"),
                    parameter("width_ui", "Width", "between 256 and 1280"),
                    parameter("duration_ui", "Video Duration (seconds)", "between 0.3 and 8.5"),
                ],
                "returns": [{"label": "Generated Video"}],
            },
        }
    }

    def patch_client(captured):
        class FakeSpaceClient:
            def view_api(self, return_format=None, print_info=False):
                return api

            def predict(self, api_name=None, **kwargs):
                captured.update({"api_name": api_name, "kwargs": kwargs})
                return MP4_BYTES * 5

        monkeypatch.setattr(
            hf_connectors, "space_client",
            lambda space, token=None, verbose=False: FakeSpaceClient(),
        )

    i2v = {}
    patch_client(i2v)
    hf_connectors.space_media(
        "Lightricks/ltx-video-distilled",
        prompt="Animate this photo",
        image_bytes=b"reference-frame",
        size=(576, 320),
        timeout=10,
    )
    assert i2v["api_name"] == "/image_to_video"
    assert isinstance(i2v["kwargs"]["input_image_filepath"], dict)
    assert i2v["kwargs"]["prompt"] == "Animate this photo"
    assert "video_i" not in i2v["kwargs"]

    t2v = {}
    patch_client(t2v)
    hf_connectors.space_media(
        "Lightricks/ltx-video-distilled",
        prompt="A drone shot over the coast",
        size=(576, 320),
        timeout=10,
    )
    assert t2v["api_name"] == "/text_to_video"
    assert t2v["kwargs"].get("input_image_filepath") is None


@pytest.mark.parametrize(
    "failure",
    [
        {"status": 402, "text": "payment required"},
        {"status": 429, "text": "rate limited"},
        {"timeout": True},
    ],
)
def test_hf_failure_hands_off_to_backup_video(monkeypatch, failure):
    _prepare(monkeypatch)
    _patch_hf(monkeypatch, failure)
    _patch_pollinations(monkeypatch)

    task = _dispatcher().generate("A drone shot over the coast")

    assert task.status == VideoTaskStatus.COMPLETED
    assert task.video_url == "/static/generated/backup.mp4"
    assert task.metadata["_provider"] == "PollinationsVideo"
    assert task.metadata["model"] == "wan"


def test_backup_video_accepts_async_job_response(monkeypatch):
    _prepare(monkeypatch)
    _patch_hf(monkeypatch, {"status": 402})
    _patch_pollinations(monkeypatch, async_job=True)

    task = _dispatcher().generate("A drone shot over the coast")

    assert task.status == VideoTaskStatus.COMPLETED
    assert task.video_url == "/static/generated/backup.mp4"


@pytest.mark.parametrize(
    "failure",
    [
        {"status": 402, "text": "payment required"},
        {"status": 429, "text": "rate limited"},
        {"timeout": True},
    ],
)
def test_backup_failure_uses_local_storyboard(monkeypatch, failure):
    _prepare(monkeypatch)
    _patch_hf(monkeypatch, {"status": 402})
    _patch_pollinations(monkeypatch, failure)

    task = _dispatcher().generate("A drone shot over the coast")

    assert task.status == VideoTaskStatus.COMPLETED
    assert task.video_url == "/static/generated/local.mp4"
    assert task.metadata["_provider"] == "LocalStoryboard"


def test_exhausted_video_chain_never_leaks_provider_names(monkeypatch):
    _prepare(monkeypatch)
    _patch_hf(monkeypatch, {"status": 402, "text": "HuggingFace quota exhausted"})
    _patch_pollinations(monkeypatch, {"status": 429, "text": "Pollinations rate limit"})
    monkeypatch.setattr(
        local_storyboard, "_build_local_video",
        lambda prompt: (_ for _ in ()).throw(RuntimeError("Local encoder failed")),
    )
    dispatcher = _dispatcher()

    task = dispatcher.generate("A drone shot over the coast")
    assert task.status == VideoTaskStatus.FAILED

    frames = list(dispatcher.generate_stream("A drone shot over the coast"))
    errors = [frame.get("error", "") for frame in frames if "error" in frame]
    assert errors
    for error in errors:
        assert error == public_video_error(task.error)
        lowered = error.lower()
        for name in ("hugging", "pollinations", "local", "402", "429", "quota"):
            assert name not in lowered


def test_video_chain_priority_order():
    assert hf_module.HuggingFaceVideoProvider.priority == 5
    assert poll_module.PollinationsVideoProvider.priority == 15
    assert local_storyboard.LocalStoryboardVideoProvider.priority == 900


def test_public_video_error_is_provider_neutral():
    message = public_video_error("HuggingFace HTTP 402 on Lightricks/LTX-2.5")
    assert "hugging" not in message.lower()
    assert "402" not in message
    assert video_dispatcher.GENERIC_VIDEO_ERROR == message


def test_ltx_stops_after_first_shared_quota_error(monkeypatch):
    """A global ZeroGPU quota failure must not retry every video Space."""
    _prepare(monkeypatch)
    attempts = []

    def fake_space_media(space, **kwargs):
        attempts.append(space)
        raise RuntimeError("You have exceeded your free ZeroGPU quota. Try again later.")

    monkeypatch.setattr(hf_connectors, "space_media", fake_space_media)

    with pytest.raises(RuntimeError, match="ZeroGPU quota"):
        hf_module._call_ltx_space("A drone shot over the coast")

    assert len(attempts) == 1, attempts
    assert attempts[0] == hf_module.HF_VIDEO_SPACES[0]


def test_ltx_retries_non_quota_errors_across_spaces(monkeypatch):
    _prepare(monkeypatch)
    attempts = []

    def fake_space_media(space, **kwargs):
        attempts.append(space)
        if len(attempts) < len(hf_module.HF_VIDEO_SPACES):
            raise RuntimeError("temporary queue busy")
        return MP4_BYTES

    monkeypatch.setattr(hf_connectors, "space_media", fake_space_media)

    data, space = hf_module._call_ltx_space("A drone shot over the coast")

    assert data == MP4_BYTES
    assert space == hf_module.HF_VIDEO_SPACES[-1]
    assert len(attempts) == len(hf_module.HF_VIDEO_SPACES)
