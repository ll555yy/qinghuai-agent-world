from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.request import Request, urlopen

from .models import BudgetPolicy, utc_now_iso


@dataclass(frozen=True, slots=True)
class AFPUsage:
    quota: float
    used: float
    reset_time_ms: int
    observed_at: str = ""

    def __post_init__(self) -> None:
        if self.quota < 0 or self.used < 0:
            raise ValueError("AFP quota and usage must be non-negative")

    @property
    def remaining(self) -> float:
        return max(0.0, self.quota - self.used)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class BudgetExhausted(RuntimeError):
    def __init__(self, message: str, usage: AFPUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


class ArkAFPClient:
    """Minimal signed client for Ark Agent Plan GetAFPUsage.

    It deliberately uses separate management credentials; an inference API key
    must never be treated as permission to query account quota.
    """

    def __init__(
        self,
        *,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        host: str = "ark.cn-beijing.volces.com",
        region: str = "cn-beijing",
        timeout_seconds: float = 10.0,
    ) -> None:
        self.access_key_id = access_key_id or os.environ.get("ARK_AFP_ACCESS_KEY_ID", "").strip()
        self.secret_access_key = secret_access_key or os.environ.get("ARK_AFP_SECRET_ACCESS_KEY", "").strip()
        self.host = host
        self.region = region
        self.timeout_seconds = timeout_seconds

    def get_usage(self) -> AFPUsage:
        if not self.access_key_id or not self.secret_access_key:
            raise RuntimeError("ARK_AFP_ACCESS_KEY_ID and ARK_AFP_SECRET_ACCESS_KEY are required")
        now = datetime.now(UTC)
        x_date = now.strftime("%Y%m%dT%H%M%SZ")
        short_date = now.strftime("%Y%m%d")
        query = "Action=GetAFPUsage&Version=2024-01-01"
        payload_hash = hashlib.sha256(b"").hexdigest()
        canonical_headers = f"host:{self.host}\nx-content-sha256:{payload_hash}\nx-date:{x_date}\n"
        signed_headers = "host;x-content-sha256;x-date"
        canonical_request = f"GET\n/\n{query}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
        scope = f"{short_date}/{self.region}/ark/request"
        string_to_sign = "\n".join(
            ("HMAC-SHA256", x_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest())
        )
        date_key = _sign(self.secret_access_key.encode(), short_date)
        region_key = _sign(date_key, self.region)
        service_key = _sign(region_key, "ark")
        signing_key = _sign(service_key, "request")
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        authorization = (
            f"HMAC-SHA256 Credential={self.access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        url = f"https://{self.host}/?{query}"
        request = Request(
            url,
            headers={
                "Authorization": authorization,
                "Host": self.host,
                "X-Content-Sha256": payload_hash,
                "X-Date": x_date,
            },
            method="GET",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return self._parse_usage(payload)

    @staticmethod
    def _parse_usage(payload: Mapping[str, Any]) -> AFPUsage:
        data = payload.get("Result", payload)
        window = data.get("AFPFiveHour") or data.get("FiveHourUsage")
        if not isinstance(window, Mapping):
            raise TypeError("GetAFPUsage response is missing AFPFiveHour")
        return AFPUsage(
            quota=float(window["Quota"]),
            used=float(window["Used"]),
            reset_time_ms=int(window["ResetTime"]),
            observed_at=utc_now_iso(),
        )


class AFPBudgetGuard:
    def __init__(
        self,
        policy: BudgetPolicy,
        usage_reader: Callable[[], AFPUsage],
    ) -> None:
        self.policy = policy
        self.usage_reader = usage_reader
        self.start_usage: AFPUsage | None = None

    def start(self) -> AFPUsage:
        usage = self.usage_reader()
        allowed_remaining = min(self.policy.afp_five_hour_cap, usage.remaining)
        if allowed_remaining <= self.policy.reserve_afp:
            raise BudgetExhausted("AFP reserve reached before experiment start", usage)
        self.start_usage = usage
        return usage

    def check(self) -> AFPUsage:
        if self.start_usage is None:
            return self.start()
        usage = self.usage_reader()
        consumed = max(0.0, usage.used - self.start_usage.used)
        if consumed >= self.policy.usable_afp:
            raise BudgetExhausted("experiment AFP cap reached", usage)
        if usage.remaining <= self.policy.reserve_afp:
            raise BudgetExhausted("provider AFP reserve reached", usage)
        if usage.reset_time_ms != self.start_usage.reset_time_ms:
            self.start_usage = usage
        return usage


__all__ = ["AFPBudgetGuard", "AFPUsage", "ArkAFPClient", "BudgetExhausted"]
