"""Adapter for the kaigiroku.net JSONP service.

The public UI uses a shared JSONP API.  The service currently disallows the
API and shared JavaScript paths in robots.txt, so this module deliberately
routes every request through ``polite_fetch``.  That means a live crawl stops
before an API request unless the site's robots policy changes.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date
from typing import Any, Iterable
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from .base import Adapter, FetchResult, polite_fetch

API_ROOT = "https://ssp.kaigiroku.net/dnp/search/"
API_ENDPOINTS = {
    "councils": "councils/index",
    "view_years": "councils/get_view_years",
    "minute_index": "minutes/get_index",
    "minute_index_list": "minutes/get_index_list",
    "minutes": "minutes/get_minute",
}
CALLBACK_NAME = "lcaoMinutesCallback"

_TEXT_KEYS = (
    "text",
    "speech",
    "body",
    "content",
    "minute",
    "statement",
    "speech_text",
    "minute_text",
)
_SPEAKER_KEYS = (
    "speaker",
    "speaker_name",
    "name",
    "speaker_label",
)
_ROLE_KEYS = ("speaker_role", "role", "position", "speaker_position")
_ID_KEYS = ("council_id", "schedule_id", "minute_id")


class KaigirokuNetError(RuntimeError):
    """Raised when the remote response cannot be interpreted safely."""


def resolve_tenant(url: str) -> tuple[str, str]:
    """Return ``(tenant_slug, tenant_id)`` from an explicit tenant URL.

    A ``tenant_id`` query parameter wins when the official page supplies one.
    Otherwise the explicit tenant path segment is used.  The latter mapping is
    implemented from the documented tenant URL shape but remains live
    unverified because the API path is currently disallowed by robots.txt.
    """

    parsed = urlparse(url)
    if parsed.hostname != "ssp.kaigiroku.net":
        raise ValueError("kaigiroku.net URL must use host ssp.kaigiroku.net")
    match = re.match(r"^/tenant/([^/]+)(?:/|$)", parsed.path)
    if not match:
        raise ValueError("URL must contain an explicit /tenant/<name>/ path")
    tenant_slug = match.group(1)
    explicit = parse_qs(parsed.query).get("tenant_id", [])
    tenant_id = explicit[0] if explicit and explicit[0] else tenant_slug
    return tenant_slug, tenant_id


def unwrap_jsonp(raw: str | bytes) -> Any:
    """Decode JSON or a single JSONP callback without evaluating JavaScript."""

    if isinstance(raw, bytes):
        raw = _decode_bytes(raw)
    text = raw.lstrip("\ufeff \t\r\n")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.fullmatch(
        r"\s*([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*"
        r"\(\s*(.*?)\s*\)\s*;?\s*",
        text,
        flags=re.DOTALL,
    )
    if not match:
        raise KaigirokuNetError("response is neither JSON nor a safe JSONP wrapper")
    try:
        return json.loads(match.group(2))
    except json.JSONDecodeError as exc:
        raise KaigirokuNetError("JSONP callback contained invalid JSON") from exc


def _decode_bytes(body: bytes, encoding: str | None = None) -> str:
    candidates = [encoding, "utf-8-sig", "cp932", "shift_jis"]
    tried: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate.lower() in tried:
            continue
        tried.add(candidate.lower())
        try:
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _unbox(payload: Any) -> Any:
    """Remove common API envelopes while retaining unknown response shapes."""

    current = payload
    for _ in range(4):
        if not isinstance(current, dict):
            break
        for key in ("data", "result", "response"):
            if key in current and current[key] is not None:
                current = current[key]
                break
        else:
            break
    return current


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _dicts_with(value: Any, required_any: tuple[str, ...]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[int] = set()
    for node in _walk(_unbox(value)):
        if (
            isinstance(node, dict)
            and any(key in node for key in required_any)
            and id(node) not in seen
        ):
            seen.add(id(node))
            found.append(node)
    return found


def _first(record: dict[str, Any], keys: Iterable[str], default: Any = "") -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None and value != "":
            return value
    return default


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?is)<(?:br|/p|/div|/li)\b[^>]*>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", "", text)
    text = text.replace("\u3000", " ")
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def _normalize_date(value: Any) -> str:
    text = _clean_text(value)
    match = re.search(r"(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})日?", text)
    if match:
        try:
            return date(*(int(part) for part in match.groups())).isoformat()
        except ValueError:
            return text
    era_match = re.search(
        r"(令和|平成|昭和)(元|\d{1,2})年(\d{1,2})月(\d{1,2})日", text
    )
    if era_match:
        offsets = {"令和": 2018, "平成": 1988, "昭和": 1925}
        era_year = 1 if era_match.group(2) == "元" else int(era_match.group(2))
        try:
            return date(
                offsets[era_match.group(1)] + era_year,
                int(era_match.group(3)),
                int(era_match.group(4)),
            ).isoformat()
        except ValueError:
            return text
    return text


def _stable_meeting_id(tenant: str, ids: dict[str, Any]) -> str:
    material = json.dumps(ids, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{tenant}\0{material}".encode("utf-8")).hexdigest()[:20]
    return f"kaigiroku_net:{tenant}:{digest}"


def _meeting_source_url(tenant_url: str, ids: dict[str, Any]) -> str:
    """Build a UI locator only from identifiers returned by the official API."""

    params = {
        key: value
        for key, value in ids.items()
        if key in _ID_KEYS and value not in (None, "")
    }
    return f"{urljoin(tenant_url, 'MinuteBrowse.html')}?{urlencode(params)}"


def _extract_speeches(payload: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for node in _walk(_unbox(payload)):
        if not isinstance(node, dict):
            continue
        text_value = _first(node, _TEXT_KEYS, default=None)
        if text_value is None or not _clean_text(text_value):
            continue
        # Container objects often have a generic "content" key.  Prefer leaf
        # records, but accept them when they also carry speaker/sequence data.
        nested_text = any(
            isinstance(value, (dict, list))
            for key, value in node.items()
            if key in _TEXT_KEYS
        )
        if nested_text:
            continue
        candidates.append(node)

    speeches: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for fallback_seq, record in enumerate(candidates, start=1):
        text = _clean_text(_first(record, _TEXT_KEYS))
        speaker = _clean_text(_first(record, _SPEAKER_KEYS))
        role = _clean_text(_first(record, _ROLE_KEYS))
        seq_value = _first(
            record,
            ("seq", "sequence", "speech_no", "minute_seq", "id"),
            default=fallback_seq,
        )
        try:
            seq = int(seq_value)
        except (TypeError, ValueError):
            seq = fallback_seq
        identity = (speaker, text)
        if identity in seen:
            continue
        seen.add(identity)
        locator_value = _first(
            record,
            ("locator", "page", "speech_no", "minute_seq", "id"),
            default=f"speech:{seq}",
        )
        speeches.append(
            {
                "seq": seq,
                "speaker": speaker,
                "speaker_role": role,
                "text": text,
                "locator": str(locator_value),
            }
        )

    speeches.sort(key=lambda item: item["seq"])
    for seq, speech in enumerate(speeches, start=1):
        speech["seq"] = seq
        if not speech["locator"]:
            speech["locator"] = f"speech:{seq}"
    return speeches


class KaigirokuNetAdapter(Adapter):
    """Read meetings from an explicit kaigiroku.net tenant URL."""

    adapter_name = "kaigiroku_net"

    def __init__(self, url: str, cache_dir: str | None = None):
        self.url = url
        self.cache_dir = cache_dir
        self.tenant_slug, self.tenant_id = resolve_tenant(url)
        self.tenant_url = (
            f"https://ssp.kaigiroku.net/tenant/{self.tenant_slug}/"
        )
        self._meeting_refs: dict[str, dict[str, Any]] = {}
        self._council_names: dict[str, str] = {}
        self._last_fetch: FetchResult | None = None

    def detect_capabilities(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter_name,
            "tenant": self.tenant_slug,
            "list_meetings": True,
            "fetch_meeting": True,
            "transport": "jsonp",
            "status": "implemented, live-unverified",
            "note": (
                "API requests are blocked while robots.txt disallows "
                "/dnp/search/; URL-path tenant_id mapping is unverified"
            ),
        }

    def _api(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> tuple[Any, FetchResult]:
        request_params = {
            "tenant_id": self.tenant_id,
            **{key: value for key, value in (params or {}).items() if value is not None},
            "callback": CALLBACK_NAME,
        }
        api_url = urljoin(API_ROOT, endpoint)
        separator = "&" if "?" in api_url else "?"
        result = polite_fetch(
            f"{api_url}{separator}{urlencode(request_params, doseq=True)}",
            cache_dir=self.cache_dir,
        )
        self._last_fetch = result
        text = _decode_bytes(result.body, result.encoding)
        payload = unwrap_jsonp(text)
        if isinstance(payload, dict):
            error = _first(payload, ("error", "error_message"), default=None)
            status = str(payload.get("status", "")).lower()
            if error or status in {"error", "failed", "failure"}:
                raise KaigirokuNetError(str(error or payload))
        return payload, result

    @staticmethod
    def _entity_id(record: dict[str, Any], *keys: str) -> str:
        return str(_first(record, keys, default=""))

    def _councils(self) -> list[dict[str, Any]]:
        payload, _ = self._api(API_ENDPOINTS["councils"])
        records = _dicts_with(payload, ("council_id",))
        councils: list[dict[str, Any]] = []
        seen: set[str] = set()
        for record in records:
            council_id = self._entity_id(record, "council_id", "id")
            if not council_id or council_id in seen:
                continue
            seen.add(council_id)
            name = _clean_text(
                _first(record, ("council_name", "name", "title"), default="")
            )
            self._council_names[council_id] = name
            councils.append({"council_id": council_id, "council_name": name})
        if not councils:
            raise KaigirokuNetError("councils response contained no council_id")
        return councils

    def _years(self, council_id: str) -> list[str]:
        payload, _ = self._api(
            API_ENDPOINTS["view_years"], {"council_id": council_id}
        )
        years: list[str] = []
        for node in _walk(_unbox(payload)):
            value: Any = None
            if isinstance(node, dict):
                value = _first(
                    node, ("view_year", "year", "western_year"), default=None
                )
            elif isinstance(node, (str, int)) and re.fullmatch(r"\d{4}", str(node)):
                value = node
            if value is not None and str(value) not in years:
                years.append(str(value))
        if not years:
            raise KaigirokuNetError("view-years response contained no year")
        return years

    def _index_records(
        self, council_id: str, year: str, remaining: int | None
    ) -> list[dict[str, Any]]:
        params = {"council_id": council_id, "year": year}
        payload, _ = self._api(API_ENDPOINTS["minute_index"], params)
        direct = _dicts_with(payload, ("minute_id",))
        if direct:
            return direct[:remaining] if remaining is not None else direct

        schedules = _dicts_with(payload, ("schedule_id",))
        records: list[dict[str, Any]] = []
        if not schedules:
            schedules = [params]
        for schedule in schedules:
            list_params = {
                "council_id": council_id,
                "year": year,
                "schedule_id": schedule.get("schedule_id"),
            }
            list_payload, _ = self._api(
                API_ENDPOINTS["minute_index_list"], list_params
            )
            records.extend(_dicts_with(list_payload, ("minute_id", "schedule_id")))
            if remaining is not None and len(records) >= remaining:
                break
        return records[:remaining] if remaining is not None else records

    def list_meetings(self, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is not None and limit < 0:
            raise ValueError("limit must be zero or greater")
        if limit == 0:
            return []

        meetings: list[dict[str, Any]] = []
        seen: set[str] = set()
        for council in self._councils():
            council_id = council["council_id"]
            for year in self._years(council_id):
                remaining = None if limit is None else limit - len(meetings)
                if remaining == 0:
                    return meetings
                for raw in self._index_records(council_id, year, remaining):
                    ids = {
                        "council_id": str(
                            _first(raw, ("council_id",), default=council_id)
                        ),
                        "schedule_id": str(
                            _first(raw, ("schedule_id",), default="")
                        ),
                        "minute_id": str(_first(raw, ("minute_id", "id"), default="")),
                    }
                    if not ids["minute_id"] and not ids["schedule_id"]:
                        continue
                    meeting_id = _stable_meeting_id(self.tenant_slug, ids)
                    if meeting_id in seen:
                        continue
                    seen.add(meeting_id)
                    ref = {
                        **ids,
                        "year": year,
                        "_raw": raw,
                    }
                    default_source_url = _meeting_source_url(self.tenant_url, ids)
                    self._meeting_refs[meeting_id] = ref
                    meetings.append(
                        {
                            "meeting_id": meeting_id,
                            "council_name": council["council_name"],
                            "meeting_name": _clean_text(
                                _first(
                                    raw,
                                    (
                                        "meeting_name",
                                        "minute_name",
                                        "schedule_name",
                                        "title",
                                        "name",
                                    ),
                                    default="",
                                )
                            ),
                            "session": _clean_text(
                                _first(
                                    raw,
                                    ("session", "session_name", "meeting_session"),
                                    default="",
                                )
                            ),
                            "date": _normalize_date(
                                _first(
                                    raw,
                                    (
                                        "date",
                                        "meeting_date",
                                        "schedule_date",
                                        "held_on",
                                    ),
                                    default="",
                                )
                            ),
                            "source_url": _clean_text(
                                _first(
                                    raw,
                                    ("source_url", "url", "minute_url"),
                                    default=default_source_url,
                                )
                            ),
                            "adapter": self.adapter_name,
                            "_adapter_ref": ref,
                        }
                    )
                    if limit is not None and len(meetings) >= limit:
                        return meetings
        return meetings

    def fetch_meeting(self, meeting_id: str | dict[str, Any]) -> dict[str, Any]:
        descriptor: dict[str, Any] = {}
        if isinstance(meeting_id, dict):
            descriptor = meeting_id
            key = str(descriptor.get("meeting_id", ""))
            ref = descriptor.get("_adapter_ref") or self._meeting_refs.get(key)
        else:
            key = meeting_id
            ref = self._meeting_refs.get(meeting_id)
        if not isinstance(ref, dict):
            raise KeyError(
                "unknown meeting_id; call list_meetings() on this adapter first"
            )

        params = {
            key_name: ref.get(key_name)
            for key_name in ("council_id", "schedule_id", "minute_id")
            if ref.get(key_name) not in (None, "")
        }
        payload, result = self._api(API_ENDPOINTS["minutes"], params)
        speeches = _extract_speeches(payload)
        raw = ref.get("_raw", {})
        council_id = str(ref.get("council_id", ""))
        source_url = _clean_text(
            _first(
                raw,
                ("source_url", "url", "minute_url"),
                default=descriptor.get(
                    "source_url", _meeting_source_url(self.tenant_url, ref)
                ),
            )
        )
        return {
            "meeting_id": key,
            "council_name": descriptor.get("council_name")
            or self._council_names.get(council_id, ""),
            "meeting_name": descriptor.get("meeting_name")
            or _clean_text(
                _first(
                    raw,
                    ("meeting_name", "minute_name", "title", "name"),
                    default="",
                )
            ),
            "session": descriptor.get("session")
            or _clean_text(
                _first(raw, ("session", "session_name"), default="")
            ),
            "date": descriptor.get("date")
            or _normalize_date(
                _first(raw, ("date", "meeting_date", "schedule_date"), default="")
            ),
            "source_url": source_url,
            "adapter": self.adapter_name,
            "fetched_at": result.fetched_at,
            "speeches": speeches,
            "provenance": {
                "adapter": self.adapter_name,
                "tenant": self.tenant_slug,
                "tenant_id_source": (
                    "query_parameter"
                    if parse_qs(urlparse(self.url).query).get("tenant_id")
                    else "tenant_path_segment_unverified"
                ),
                "index_url": self.tenant_url,
                "discovered_from": self.tenant_url,
                "resolved_url_at_fetch": result.final_url,
                "resolved_url": result.final_url,
                "fetched_at": result.fetched_at,
                "content_type": result.content_type,
                "media_type": result.content_type,
                "content_hash": f"sha256:{result.sha256}",
                "content_sha256": result.sha256,
                "cache_path": str(result.cache_path),
                "from_cache": result.from_cache,
                "status": "discovered",
            },
        }


__all__ = [
    "API_ENDPOINTS",
    "KaigirokuNetAdapter",
    "KaigirokuNetError",
    "resolve_tenant",
    "unwrap_jsonp",
]
