#!/usr/bin/env python3
"""Call the configured Key智API-compatible image generation and edit APIs."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path
import sys
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen


DEFAULT_API_BASE_URL = "https://aitokey.top/v1"
DEFAULT_MODEL = "gpt-image-2"
MAX_GENERATION_COUNT = 3
ASYNC_TASK_POLL_INTERVAL_SECONDS = 2
ASYNC_TASK_REQUEST_TIMEOUT_SECONDS = 30
DEFAULT_ASYNC_TASK_TIMEOUT_SECONDS = 30 * 60
SETTINGS_PATH_ENV = "KEYZHI_SETTINGS_PATH"
IMAGE_MODEL_OVERRIDE_ENV = "KEYZHI_IMAGE_MODELS"
IMAGE_MODEL_MARKERS = (
    "image",
    "flux",
    "imagen",
    "dall-e",
    "seedream",
    "ideogram",
    "nano-banana",
    "grok-imagine",
)
NON_IMAGE_MODEL_MARKERS = (
    "video",
    "audio",
    "embedding",
    "whisper",
    "moderation",
    "transcription",
    "chat",
    "completion",
)
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
EPHEMERAL_TASK_HEADER = "X-ImageGen2-Task-Mode"
EPHEMERAL_TASK_VALUE = "ephemeral"
EPHEMERAL_TASK_RESPONSE_FORMAT = "b64_json"
LEGACY_EPHEMERAL_ACK_STATUS_CODES = {404, 405, 501}
UNSUPPORTED_ASYNC_STATUS_CODES = {404, 405, 501}


class ClientError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def configure_stdio() -> None:
    """Keep child-process output readable when the parent is an UTF-8 MCP server."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def imagegen2_settings_path() -> Path:
    configured = os.environ.get(SETTINGS_PATH_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    app_data = os.environ.get("APPDATA", "").strip()
    base = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
    return base / "KeyZhiAPI" / "settings.json"


def load_imagegen2_settings() -> dict[str, str]:
    try:
        # Windows PowerShell 5.1 writes UTF-8 settings with a BOM by default.
        value = json.loads(imagegen2_settings_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str) and isinstance(item, str)}


def configured_imagegen2_key() -> str | None:
    value = load_imagegen2_settings().get("api_key", "").strip()
    return value or None


def normalize_api_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    for suffix in (
        "/images/generations/async",
        "/images/generations",
        "/images/edits/async",
        "/images/edits",
    ):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def load_api_base_url() -> str:
    settings = load_imagegen2_settings()
    value = os.environ.get("KEYZHI_BASE_URL", "").strip()
    if not value:
        value = settings.get("base_url", "").strip()
    return normalize_api_base_url(value or DEFAULT_API_BASE_URL)


def load_api_key() -> str:
    value = os.environ.get("KEYZHI_API_KEY", "").strip()
    if value:
        return value

    value = configured_imagegen2_key()
    if value:
        return value

    raise ClientError(
        "No image API key found. Configure KEYZHI_API_KEY or local "
        "KeyZhiAPI settings for aitokey.top."
    )


def request_json_with_headers(
    url: str,
    key: str,
    payload: bytes | None,
    content_type: str | None,
    timeout: int,
    method: str,
    extra_headers: dict[str, str] | None = None,
    allow_empty_response: bool = False,
) -> tuple[dict, object]:
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": "keyzhi-imagegen/1.0",
    }
    if extra_headers:
        headers.update(extra_headers)
    if content_type:
        headers["Content-Type"] = content_type
    request = Request(
        url,
        data=payload,
        method=method,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            response_headers = response.headers
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:4000]
        raise ClientError(f"Image API returned HTTP {exc.code}: {body}", exc.code) from None
    except URLError as exc:
        raise ClientError(f"Could not reach the image API: {exc.reason}") from None

    if not raw and allow_empty_response:
        return {}, response_headers
    try:
        value = json.loads(raw)
    except ValueError:
        preview = raw.decode("utf-8", errors="replace")[:1000]
        raise ClientError(f"Image API returned non-JSON data: {preview}") from None
    if not isinstance(value, dict):
        raise ClientError("Image API returned an unexpected JSON value.")
    return value, response_headers


def request_json(url: str, key: str, payload: bytes, content_type: str, timeout: int) -> dict:
    response, _ = request_json_with_headers(url, key, payload, content_type, timeout, "POST")
    return response


def async_image_task_poll_url(endpoint: str, task_id: str) -> str:
    base_url, separator, _ = endpoint.rpartition("/images/")
    if not separator or not base_url:
        raise ClientError("Image API endpoint cannot be converted to an async task URL.")
    return f"{base_url}/images/tasks/{quote(task_id, safe='')}"


def async_image_task_ack_url(endpoint: str, task_id: str) -> str:
    base_url, separator, _ = endpoint.rpartition("/images/")
    if not separator or not base_url:
        raise ClientError("Image API endpoint cannot be converted to an async task URL.")
    return f"{base_url}/images/tasks/{quote(task_id, safe='')}/ack"


def acknowledge_ephemeral_image_task(
    endpoint: str,
    key: str,
    task_id: str,
    timeout: int,
) -> bool:
    """Ask an ephemeral-task server to drop a result after local files are saved.

    Older servers do not have the acknowledgement route. Their normal image result
    remains a local success and is cleaned by the server's existing task retention.
    """
    try:
        request_json_with_headers(
            async_image_task_ack_url(endpoint, task_id),
            key,
            None,
            None,
            min(timeout, ASYNC_TASK_REQUEST_TIMEOUT_SECONDS),
            "POST",
            {EPHEMERAL_TASK_HEADER: EPHEMERAL_TASK_VALUE},
            allow_empty_response=True,
        )
    except ClientError as exc:
        if exc.status_code not in LEGACY_EPHEMERAL_ACK_STATUS_CODES:
            print(
                f"warning: local image files were saved, but ephemeral task cleanup failed: {exc}",
                file=sys.stderr,
            )
        return False
    return True


def task_error_message(task: dict) -> str:
    error = task.get("error")
    if isinstance(error, dict):
        message = str(error.get("message", "")).strip()
        if message:
            return message
    if error:
        return str(error)
    return "image generation task failed"


def submit_and_poll_image_task_with_id(
    endpoint: str,
    key: str,
    payload: bytes,
    content_type: str,
    timeout: int,
) -> dict:
    submission, _ = request_json_with_headers(
        f"{endpoint.rstrip('/')}/async",
        key,
        payload,
        content_type,
        min(timeout, ASYNC_TASK_REQUEST_TIMEOUT_SECONDS),
        "POST",
        {EPHEMERAL_TASK_HEADER: EPHEMERAL_TASK_VALUE},
    )
    task_id = str(submission.get("task_id") or submission.get("id") or "").strip()
    if not task_id:
        raise ClientError("Image API accepted an async task without a task_id.")

    poll_url = async_image_task_poll_url(endpoint, task_id)
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ClientError(f"Image task {task_id} did not finish before the {timeout}-second timeout.")
        time.sleep(min(ASYNC_TASK_POLL_INTERVAL_SECONDS, remaining))
        task, _ = request_json_with_headers(
            poll_url,
            key,
            None,
            None,
            max(1, min(int(remaining), ASYNC_TASK_REQUEST_TIMEOUT_SECONDS)),
            "GET",
        )
        status = str(task.get("status", "")).strip().lower()
        if status == "completed":
            result = task.get("result")
            if not isinstance(result, dict):
                raise ClientError(f"Image task {task_id} completed without an image result.")
            return task_id, result
        if status in {"failed", "cancelled"}:
            raise ClientError(f"Image task {task_id} failed: {task_error_message(task)}")
        if status != "processing":
            raise ClientError(f"Image task {task_id} returned an unknown status: {status or 'empty'}")


def submit_and_poll_image_task(
    endpoint: str,
    key: str,
    payload: bytes,
    content_type: str,
    timeout: int,
) -> dict:
    """Compatibility wrapper for callers that only need the completed result."""
    _, result = submit_and_poll_image_task_with_id(endpoint, key, payload, content_type, timeout)
    return result


def request_image(
    endpoint: str,
    key: str,
    payload: bytes,
    content_type: str,
    timeout: int,
) -> tuple[str | None, dict]:
    try:
        return submit_and_poll_image_task_with_id(endpoint, key, payload, content_type, timeout)
    except ClientError as exc:
        if exc.status_code not in UNSUPPORTED_ASYNC_STATUS_CODES:
            raise
    return None, request_json(endpoint, key, payload, content_type, timeout)


def request_fields(args: argparse.Namespace) -> dict[str, object]:
    fields: dict[str, object] = {
        "model": args.model,
        "n": args.n,
        "response_format": EPHEMERAL_TASK_RESPONSE_FORMAT,
    }
    for name in ("size", "quality", "background", "output_format", "aspect_ratio", "resolution"):
        value = getattr(args, name)
        if value is not None:
            fields[name] = value
    return fields


def json_payload(args: argparse.Namespace) -> tuple[bytes, str]:
    body = request_fields(args)
    body["prompt"] = args.prompt
    return json.dumps(body).encode("utf-8"), "application/json"


def multipart_payload(args: argparse.Namespace) -> tuple[bytes, str]:
    boundary = f"----keyzhi-imagegen-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    fields = request_fields(args)
    fields["prompt"] = args.prompt

    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )

    files = [("image", path) for path in args.image]
    if args.mask is not None:
        files.append(("mask", args.mask))
    for field_name, path in files:
        content = path.read_bytes()
        mime, suffix = validate_image_bytes(content)
        filename = f"{path.stem or field_name}.{suffix}"
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {mime}\r\n\r\n".encode(),
                content,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def output_path(base: Path, index: int, count: int, output_format: str | None) -> Path:
    suffix = base.suffix or f".{output_format or 'png'}"
    stem = base.stem if base.suffix else base.name
    filename = f"{stem}-{index + 1}{suffix}" if count > 1 else f"{stem}{suffix}"
    return base.with_name(filename)


def resolve_output_format(base: Path, requested: str | None) -> str:
    suffix = base.suffix.lower()
    suffix_format = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp"}
    if suffix and suffix not in suffix_format:
        raise ClientError("Output extension must be .png, .jpg, .jpeg, or .webp.")
    inferred = suffix_format.get(suffix)
    if requested is not None and inferred is not None and requested != inferred:
        raise ClientError(
            f"Output extension {suffix} does not match --output-format {requested}."
        )
    return requested or inferred or "png"


def download(url: str, timeout: int, api_key: str = "", api_base_url: str = "") -> bytes:
    if url.startswith("data:"):
        try:
            return base64.b64decode(url.split(",", 1)[1], validate=True)
        except (IndexError, ValueError):
            raise ClientError("The API returned an invalid data URL.") from None
    try:
        parsed = urlparse(url)
        if not parsed.scheme and not parsed.netloc:
            if not api_base_url:
                raise ClientError("The API returned a relative image URL without an API base URL.")
            url = urljoin(api_base_url.rstrip("/") + "/", url)
            parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ClientError("The API returned an unsafe image URL.")
        headers = {"User-Agent": "keyzhi-imagegen/1.0"}
        if api_key and _same_origin(url, api_base_url):
            headers["Authorization"] = f"Bearer {api_key}"
        request = Request(url, headers=headers)
        with urlopen(request, timeout=timeout) as response:
            content = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(content) > MAX_DOWNLOAD_BYTES:
                raise ClientError("The API image result is larger than 50 MB.")
            return content
    except (HTTPError, URLError, ValueError) as exc:
        raise ClientError(f"Could not download an image result: {exc}") from None


def validate_image_bytes(content: bytes) -> tuple[str, str]:
    is_png = (
        len(content) >= 24
        and content.startswith(b"\x89PNG\r\n\x1a\n")
        and content[12:16] == b"IHDR"
    )
    is_jpeg = (
        len(content) >= 4
        and content.startswith(b"\xff\xd8\xff")
        and content.endswith(b"\xff\xd9")
    )
    is_webp = (
        len(content) >= 12
        and content.startswith(b"RIFF")
        and content[8:12] == b"WEBP"
    )
    if not (is_png or is_jpeg or is_webp):
        raise ClientError("Image API returned data that is not a valid PNG, JPEG, or WebP image.")
    if is_png:
        return "image/png", "png"
    if is_jpeg:
        return "image/jpeg", "jpg"
    return "image/webp", "webp"


def _same_origin(url: str, base_url: str) -> bool:
    target = urlparse(url)
    base = urlparse(base_url)
    return (
        target.scheme == base.scheme
        and target.hostname == base.hostname
        and target.port == base.port
    )


def _looks_like_image_model(model_id: str) -> bool:
    value = model_id.lower()
    configured = os.environ.get(IMAGE_MODEL_OVERRIDE_ENV, "").strip()
    if configured:
        allowed = {item.strip().lower() for item in configured.split(",") if item.strip()}
        return value in allowed
    if any(word in value for word in NON_IMAGE_MODEL_MARKERS):
        return False
    return any(word in value for word in IMAGE_MODEL_MARKERS)


def filter_image_models(payload: dict) -> dict:
    key = "data" if isinstance(payload.get("data"), list) else "models"
    data = payload.get(key, [])
    if not isinstance(data, list):
        return {**payload, key: []}
    filtered = []
    for item in data:
        model_id = item if isinstance(item, str) else item.get("id") if isinstance(item, dict) else None
        if model_id and _looks_like_image_model(str(model_id)):
            filtered.append(item)
    filtered.sort(key=lambda item: str(item if isinstance(item, str) else item.get("id", "")).lower())
    return {**payload, key: filtered}


def save_results(
    response: dict,
    base: Path,
    output_format: str | None,
    timeout: int,
    api_key: str = "",
    api_base_url: str = "",
) -> list[Path]:
    data = response.get("data")
    if not isinstance(data, list) or not data:
        error = response.get("error")
        raise ClientError(f"Image API returned no image data: {error or response}")

    paths: list[Path] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ClientError("Image API returned a malformed image item.")
        if item.get("b64_json"):
            try:
                content = base64.b64decode(item["b64_json"], validate=True)
            except (ValueError, TypeError):
                raise ClientError("Image API returned invalid base64 image data.") from None
        elif item.get("url"):
            content = download(str(item["url"]), timeout, api_key, api_base_url)
        else:
            raise ClientError("Image API result has neither b64_json nor url.")

        validate_image_bytes(content)
        path = output_path(base, index, len(data), output_format)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        paths.append(path.resolve())
    return paths


def existing_file(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"File not found: {path}")
    return path


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt", required=True, help="Image prompt or edit instruction")
    parser.add_argument("--output", required=True, type=Path, help="Output image path")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--n",
        type=int,
        default=1,
        choices=range(1, MAX_GENERATION_COUNT + 1),
        metavar="1-3",
    )
    parser.add_argument("--size")
    parser.add_argument("--quality")
    parser.add_argument("--background", choices=("opaque", "transparent", "auto"))
    parser.add_argument("--output-format", choices=("png", "jpeg", "webp"))
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_ASYNC_TASK_TIMEOUT_SECONDS,
        help="Maximum seconds to wait for an asynchronous image task",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate images from text")
    add_common_options(generate)

    edit = subparsers.add_parser("edit", help="Edit an existing image")
    add_common_options(edit)
    edit.add_argument("--image", required=True, action="append", type=existing_file)
    edit.add_argument("--mask", type=existing_file)
    for parser_item in (generate, edit):
        parser_item.add_argument("--aspect-ratio")
        parser_item.add_argument("--resolution")
    return parser.parse_args()


def main() -> int:
    configure_stdio()
    args = parse_args()
    try:
        args.output_format = resolve_output_format(args.output, args.output_format)
        key = load_api_key()
        api_base_url = load_api_base_url()
        if args.command == "generate":
            payload, content_type = json_payload(args)
            endpoint = f"{api_base_url}/images/generations"
        else:
            payload, content_type = multipart_payload(args)
            endpoint = f"{api_base_url}/images/edits"
        task_id, response = request_image(
            endpoint,
            key,
            payload,
            content_type,
            args.timeout,
        )
        paths = save_results(
            response,
            args.output.expanduser(),
            args.output_format,
            args.timeout,
            key,
            api_base_url,
        )
        if task_id is not None:
            acknowledge_ephemeral_image_task(endpoint, key, task_id, args.timeout)
    except (ClientError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
