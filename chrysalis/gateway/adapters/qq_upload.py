"""QQ Bot chunked upload helper for local media files."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional


logger = logging.getLogger(__name__)

FILE_UPLOAD_TIMEOUT = 120.0
_BIZ_CODE_DAILY_LIMIT = 40093002
_BIZ_CODE_PART_RETRYABLE = 40093001
_DEFAULT_CONCURRENT_PARTS = 1
_MAX_CONCURRENT_PARTS = 10
_PART_UPLOAD_TIMEOUT = 300.0
_PART_UPLOAD_MAX_RETRIES = 2
_PART_FINISH_RETRY_INTERVAL = 1.0
_PART_FINISH_DEFAULT_TIMEOUT = 120.0
_PART_FINISH_MAX_TIMEOUT = 600.0
_COMPLETE_UPLOAD_MAX_RETRIES = 2
_COMPLETE_UPLOAD_BASE_DELAY = 2.0
_MD5_10M_SIZE = 10_002_432


class UploadDailyLimitExceededError(Exception):
    """Raised when QQ rejects uploads because the daily quota is exhausted."""

    def __init__(self, file_name: str, file_size: int, message: str = "") -> None:
        self.file_name = file_name
        self.file_size = file_size
        super().__init__(message or f"Daily upload limit exceeded for {file_name!r}")

    @property
    def file_size_human(self) -> str:
        return format_size(self.file_size)


class UploadFileTooLargeError(Exception):
    """Raised when a file exceeds the platform upload limit."""

    def __init__(
        self,
        file_name: str,
        file_size: int,
        limit_bytes: int = 0,
        message: str = "",
    ) -> None:
        self.file_name = file_name
        self.file_size = file_size
        self.limit_bytes = limit_bytes
        limit_str = f" ({format_size(limit_bytes)})" if limit_bytes else ""
        super().__init__(
            message
            or (
                f"File {file_name!r} ({format_size(file_size)}) "
                f"exceeds platform limit{limit_str}"
            )
        )

    @property
    def file_size_human(self) -> str:
        return format_size(self.file_size)

    @property
    def limit_human(self) -> str:
        return format_size(self.limit_bytes) if self.limit_bytes else "unknown"


@dataclass
class _UploadProgress:
    total_parts: int = 0
    total_bytes: int = 0
    completed_parts: int = 0
    uploaded_bytes: int = 0


@dataclass
class _PreparePart:
    index: int
    presigned_url: str
    block_size: int = 0


@dataclass
class _PrepareResult:
    upload_id: str
    block_size: int
    parts: List[_PreparePart]
    concurrency: int = _DEFAULT_CONCURRENT_PARTS
    retry_timeout: float = 0.0


@dataclass
class _PutResponse:
    status_code: int
    text: str = ""


ApiRequestFn = Callable[..., Awaitable[Dict[str, Any]]]
HttpPutFn = Callable[..., Awaitable[Any]]


def _parse_prepare_response(raw: Dict[str, Any]) -> _PrepareResult:
    src = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    upload_id = str(src.get("upload_id", ""))
    if not upload_id:
        raise ValueError(f"upload_prepare response missing upload_id: {str(raw)[:200]}")
    block_size = int(src.get("block_size", 0))
    raw_parts = src.get("parts") or src.get("part_list") or []
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ValueError(f"upload_prepare response missing parts: {str(raw)[:200]}")
    parts: List[_PreparePart] = []
    for part in raw_parts:
        if not isinstance(part, dict):
            continue
        parts.append(
            _PreparePart(
                index=int(part.get("part_index") or part.get("index") or 0),
                presigned_url=str(part.get("presigned_url") or part.get("url") or ""),
                block_size=int(part.get("block_size", 0)),
            )
        )
    return _PrepareResult(
        upload_id=upload_id,
        block_size=block_size,
        parts=parts,
        concurrency=int(src.get("concurrency", _DEFAULT_CONCURRENT_PARTS)) or _DEFAULT_CONCURRENT_PARTS,
        retry_timeout=float(src.get("retry_timeout", 0.0) or 0.0),
    )


class ChunkedUploader:
    """Run the prepare -> PUT parts -> complete flow for local QQ uploads."""

    def __init__(
        self,
        api_request: ApiRequestFn,
        http_put: HttpPutFn,
        log_tag: str = "QQ",
    ) -> None:
        self._api_request = api_request
        self._http_put = http_put
        self._log_tag = log_tag

    async def upload(
        self,
        chat_type: str,
        target_id: str,
        file_path: str,
        file_type: int,
        file_name: str,
    ) -> Dict[str, Any]:
        if chat_type not in {"c2c", "group"}:
            raise ValueError(f"unsupported chat_type: {chat_type!r}")

        path = Path(file_path)
        file_size = path.stat().st_size
        hashes = await asyncio.get_running_loop().run_in_executor(
            None, _compute_file_hashes, file_path, file_size
        )
        prepare = await self._prepare(
            chat_type, target_id, file_type, file_name, file_size, hashes
        )

        max_concurrent = min(prepare.concurrency, _MAX_CONCURRENT_PARTS)
        retry_timeout = min(
            prepare.retry_timeout if prepare.retry_timeout > 0 else _PART_FINISH_DEFAULT_TIMEOUT,
            _PART_FINISH_MAX_TIMEOUT,
        )
        progress = _UploadProgress(total_parts=len(prepare.parts), total_bytes=file_size)
        tasks: List[Callable[[], Awaitable[None]]] = [
            functools.partial(
                self._upload_one_part,
                chat_type=chat_type,
                target_id=target_id,
                file_path=file_path,
                file_size=file_size,
                upload_id=prepare.upload_id,
                rsp_block_size=prepare.block_size,
                part=part,
                retry_timeout=retry_timeout,
                progress=progress,
            )
            for part in prepare.parts
        ]
        await _run_with_concurrency(tasks, max_concurrent)
        return await self._complete(chat_type, target_id, prepare.upload_id)

    async def _prepare(
        self,
        chat_type: str,
        target_id: str,
        file_type: int,
        file_name: str,
        file_size: int,
        hashes: Dict[str, str],
    ) -> _PrepareResult:
        base = "/v2/users" if chat_type == "c2c" else "/v2/groups"
        path = f"{base}/{target_id}/upload_prepare"
        body = {
            "file_type": file_type,
            "file_name": file_name,
            "file_size": file_size,
            "md5": hashes["md5"],
            "sha1": hashes["sha1"],
            "md5_10m": hashes["md5_10m"],
        }
        try:
            raw = await self._api_request("POST", path, body=body, timeout=FILE_UPLOAD_TIMEOUT)
        except RuntimeError as exc:
            if f"{_BIZ_CODE_DAILY_LIMIT}" in str(exc):
                raise UploadDailyLimitExceededError(file_name, file_size, str(exc)) from exc
            raise
        return _parse_prepare_response(raw)

    async def _upload_one_part(
        self,
        chat_type: str,
        target_id: str,
        file_path: str,
        file_size: int,
        upload_id: str,
        rsp_block_size: int,
        part: _PreparePart,
        retry_timeout: float,
        progress: _UploadProgress,
    ) -> None:
        part_index = part.index
        actual_block_size = part.block_size if part.block_size > 0 else rsp_block_size
        offset = (part_index - 1) * rsp_block_size
        length = min(actual_block_size, file_size - offset)
        data = await asyncio.get_running_loop().run_in_executor(
            None, _read_file_chunk, file_path, offset, length
        )
        md5_hex = hashlib.md5(data).hexdigest()
        await self._put_to_presigned_url(part.presigned_url, data, part_index, progress.total_parts)
        await self._part_finish_with_retry(
            chat_type,
            target_id,
            upload_id,
            part_index,
            length,
            md5_hex,
            retry_timeout,
        )
        progress.completed_parts += 1
        progress.uploaded_bytes += length

    async def _put_to_presigned_url(
        self,
        url: str,
        data: bytes,
        part_index: int,
        total_parts: int,
    ) -> None:
        last_exc: Optional[Exception] = None
        for attempt in range(_PART_UPLOAD_MAX_RETRIES + 1):
            try:
                resp = await asyncio.wait_for(
                    self._http_put(
                        url,
                        data=data,
                        headers={"Content-Length": str(len(data))},
                    ),
                    timeout=_PART_UPLOAD_TIMEOUT,
                )
                status = getattr(resp, "status_code", 0)
                if 200 <= status < 300:
                    return
                body_preview = ""
                try:
                    body_preview = getattr(resp, "text", "")[:200]
                except Exception:
                    pass
                raise RuntimeError(f"COS PUT returned {status}: {body_preview}")
            except Exception as exc:
                last_exc = exc
                if attempt < _PART_UPLOAD_MAX_RETRIES:
                    await asyncio.sleep(1.0 * (2**attempt))
        raise RuntimeError(
            f"Part {part_index}/{total_parts} upload failed after "
            f"{_PART_UPLOAD_MAX_RETRIES + 1} attempts: {last_exc}"
        )

    async def _part_finish_with_retry(
        self,
        chat_type: str,
        target_id: str,
        upload_id: str,
        part_index: int,
        block_size: int,
        md5: str,
        retry_timeout: float,
    ) -> None:
        base = "/v2/users" if chat_type == "c2c" else "/v2/groups"
        path = f"{base}/{target_id}/upload_part_finish"
        body = {
            "upload_id": upload_id,
            "part_index": part_index,
            "block_size": block_size,
            "md5": md5,
        }
        loop = asyncio.get_running_loop()
        start = loop.time()
        attempt = 0
        while True:
            try:
                await self._api_request("POST", path, body=body, timeout=FILE_UPLOAD_TIMEOUT)
                return
            except RuntimeError as exc:
                if f"{_BIZ_CODE_PART_RETRYABLE}" not in str(exc):
                    raise
                elapsed = loop.time() - start
                if elapsed >= retry_timeout:
                    raise RuntimeError(
                        f"upload_part_finish persistent retry timed out after {retry_timeout:.0f}s "
                        f"({attempt} retries): {exc}"
                    ) from exc
                attempt += 1
                await asyncio.sleep(_PART_FINISH_RETRY_INTERVAL)

    async def _complete(
        self,
        chat_type: str,
        target_id: str,
        upload_id: str,
    ) -> Dict[str, Any]:
        base = "/v2/users" if chat_type == "c2c" else "/v2/groups"
        path = f"{base}/{target_id}/files"
        body = {"upload_id": upload_id}
        last_exc: Optional[Exception] = None
        for attempt in range(_COMPLETE_UPLOAD_MAX_RETRIES + 1):
            try:
                return await self._api_request("POST", path, body=body, timeout=FILE_UPLOAD_TIMEOUT)
            except Exception as exc:
                last_exc = exc
                if attempt < _COMPLETE_UPLOAD_MAX_RETRIES:
                    await asyncio.sleep(_COMPLETE_UPLOAD_BASE_DELAY * (2**attempt))
        raise RuntimeError(f"complete_upload failed after {_COMPLETE_UPLOAD_MAX_RETRIES + 1} attempts: {last_exc}")


def format_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def _read_file_chunk(file_path: str, offset: int, length: int) -> bytes:
    with open(file_path, "rb") as fh:
        fh.seek(offset)
        data = fh.read(length)
        if len(data) != length:
            raise IOError(
                f"Short read from {file_path}: expected {length} bytes at offset {offset}, got {len(data)}"
            )
        return data


def _compute_file_hashes(file_path: str, file_size: int) -> Dict[str, str]:
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    md5_10m = hashlib.md5()

    need_10m = file_size > _MD5_10M_SIZE
    bytes_read = 0
    with open(file_path, "rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            md5.update(chunk)
            sha1.update(chunk)
            if need_10m:
                remaining = _MD5_10M_SIZE - bytes_read
                if remaining > 0:
                    md5_10m.update(chunk[:remaining])
            bytes_read += len(chunk)

    full_md5 = md5.hexdigest()
    return {
        "md5": full_md5,
        "sha1": sha1.hexdigest(),
        "md5_10m": md5_10m.hexdigest() if need_10m else full_md5,
    }


async def _run_with_concurrency(
    tasks: List[Callable[[], Awaitable[None]]],
    concurrency: int,
) -> None:
    concurrency = max(concurrency, 1)
    sem = asyncio.Semaphore(concurrency)

    async def _wrap(thunk: Callable[[], Awaitable[None]]) -> None:
        async with sem:
            await thunk()

    await asyncio.gather(*(_wrap(t) for t in tasks))
