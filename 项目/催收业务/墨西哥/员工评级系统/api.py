"""
Quick BI OpenAPI 调用层。

复用日报/周报项目已验证的 HMAC-SHA1 签名逻辑（Signature / Timestamp / Nonce），
不重新实现。对外只暴露一个 query()。
"""

import base64
import hmac
import json
import time
import urllib.parse as up

import requests

from . import config
from .utils import warn, error


def _sign_and_call(action, extra_params):
    """HMAC-SHA1 签名 → POST Quick BI → 返回 parsed JSON。"""
    params = {
        "Format": "json",
        "Version": "2022-01-01",
        "AccessKeyId": config.QBI_AK,
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "SignatureVersion": "1.0",
        "SignatureNonce": str(int(time.time() * 1000000) + hash(action) % 1000000),
        "Action": action,
    }
    params.update(extra_params)

    sorted_keys = sorted(params.keys())
    canonicalized = "&".join(
        f"{up.quote(k, safe='')}={up.quote(str(params[k]), safe='')}"
        for k in sorted_keys
    )
    string_to_sign = f"POST&{up.quote('/', safe='')}&{up.quote(canonicalized, safe='')}"
    sig = base64.b64encode(
        hmac.new(f"{config.QBI_SK}&".encode(), string_to_sign.encode(), "sha1").digest()
    ).decode()
    params["Signature"] = sig

    url = f"https://{config.QBI_ENDPOINT}/?" + up.urlencode(params)
    resp = requests.post(url, timeout=config.API_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def query(api_id, conditions, return_fields=None):
    """
    调用一个 Quick BI 数据集 API，返回 Values 行列表。

    自动重试 config.API_RETRIES 次；超时 config.API_TIMEOUT 秒。
    失败（HTTP 错误 / Success=False / 超时）会记录日志（HTTP 状态、接口、异常、重试次数）。

    Args:
        api_id: Quick BI API ID
        conditions: dict，如 {"statis_date": "20260701"}
        return_fields: list[str]，可选

    Returns:
        list[dict]: Result.Values

    Raises:
        RuntimeError: 重试耗尽仍失败
    """
    extra = {
        "ApiId": api_id,
        "Conditions": json.dumps(conditions, ensure_ascii=False),
    }
    if return_fields:
        extra["ReturnFields"] = json.dumps(return_fields)

    last_err = None
    for attempt in range(1, config.API_RETRIES + 1):
        try:
            data = _sign_and_call("QueryDataService", extra)
            if not data.get("Success"):
                # 业务层失败（签名/参数/权限等），无重试意义但仍按重试流程记录
                raise RuntimeError(
                    f"Success=False: {data.get('Message') or data.get('Code') or data}")
            return data.get("Result", {}).get("Values", [])

        except requests.exceptions.HTTPError as e:
            status = getattr(e.response, "status_code", "?")
            last_err = e
            warn(f"API 请求失败 [HTTP {status}] ApiId={api_id} cond={conditions} "
                 f"第 {attempt}/{config.API_RETRIES} 次：{e}")
        except requests.exceptions.RequestException as e:
            # 超时 / 连接错误
            last_err = e
            warn(f"API 请求异常 [网络/超时] ApiId={api_id} cond={conditions} "
                 f"第 {attempt}/{config.API_RETRIES} 次：{e}")
        except RuntimeError as e:
            last_err = e
            warn(f"API 返回失败 ApiId={api_id} cond={conditions} "
                 f"第 {attempt}/{config.API_RETRIES} 次：{e}")

        if attempt < config.API_RETRIES:
            time.sleep(attempt * config.API_RETRY_BACKOFF)

    error(f"API 最终失败（已重试 {config.API_RETRIES} 次）ApiId={api_id} cond={conditions}：{last_err}")
    raise RuntimeError(
        f"Quick BI 调用失败 (ApiId={api_id}, cond={conditions})，重试 {config.API_RETRIES} 次：{last_err}"
    ) from last_err
