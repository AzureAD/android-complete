"""Low-level ADO REST + `az` primitives shared by all pipeline queries."""
from __future__ import annotations

import json as _json
import shutil
import subprocess

from tools.coordinates import coords
from tools import pipelines as _pp


# ADO stage `result` values that mean the stage actually EXECUTED (vs never-ran).
# succeeded/succeededWithIssues (green/yellow) and failed (red) all count as "ran"
# — matches the release rule: only a stage that never ran (skipped/canceled/pending)
# blocks. See build_verify.mrwp_* steps.
RAN_RESULTS = {"succeeded", "succeededWithIssues", "failed"}


def _az_json(args, timeout):
    """Run `az <args> -o json` and return (ok, parsed_json, detail)."""
    az = shutil.which("az")
    if az is None:
        return (False, None, "az CLI not found")
    try:
        out = subprocess.run(
            [az, *args, "-o", "json"],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8",
        )
    except subprocess.TimeoutExpired:
        return (False, None, f"timeout running az {' '.join(args[:2])}")
    except OSError as e:
        return (False, None, f"failed to run az: {e}")
    if out.returncode != 0:
        err = (out.stderr or "").strip().splitlines()
        detail = (err[-1] if err else "az returned non-zero")[:200]
        # Surface auth problems distinctly so the step can prompt `az login`.
        low = detail.lower()
        if "login" in low or "401" in low or "unauthor" in low or "token" in low:
            detail = f"AUTH: {detail}"
        return (False, None, detail)
    try:
        return (True, _json.loads(out.stdout or "null"), "")
    except ValueError:
        return (False, None, "could not parse az output")


# ADO resource id for Azure DevOps — used to mint an access token for the few REST
# endpoints `az devops invoke` mis-routes (e.g. the Test Runs API).
_ADO_RESOURCE = coords.resource_id()


def _ado_rest_get(url, timeout):
    """GET an ADO REST url with an az-minted bearer token. Returns (ok, json, detail).
    Used only where `az devops invoke` can't reach an endpoint cleanly."""
    az = shutil.which("az")
    if az is None:
        return (False, None, "az CLI not found")
    try:
        tok = subprocess.run(
            [az, "account", "get-access-token", "--resource", _ADO_RESOURCE,
             "--query", "accessToken", "-o", "tsv"],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8")
    except (subprocess.TimeoutExpired, OSError) as e:
        return (False, None, f"failed to get token: {e}")
    if tok.returncode != 0:
        return (False, None, "AUTH: could not get an ADO token (run `az login`)")
    token = (tok.stdout or "").strip()
    if not token:
        return (False, None, "AUTH: empty ADO token (run `az login`)")
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (True, _json.loads(resp.read().decode("utf-8")), "")
    except urllib.error.HTTPError as e:
        code = e.code
        detail = f"HTTP {code}"
        if code in (401, 403):
            detail = f"AUTH: HTTP {code} (run `az login` / check access)"
        return (False, None, detail)
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        return (False, None, f"REST GET failed: {e}")


def _ado_rest_get_h(url, timeout):
    """Like _ado_rest_get but also returns the response headers:
    (ok, json, headers_lower, detail). ADO returns paging tokens in the
    `x-ms-continuationtoken` HEADER (not the body), so header access is needed to page."""
    az = shutil.which("az")
    if az is None:
        return (False, None, {}, "az CLI not found")
    try:
        tok = subprocess.run(
            [az, "account", "get-access-token", "--resource", _ADO_RESOURCE,
             "--query", "accessToken", "-o", "tsv"],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8")
    except (subprocess.TimeoutExpired, OSError) as e:
        return (False, None, {}, f"failed to get token: {e}")
    if tok.returncode != 0 or not (tok.stdout or "").strip():
        return (False, None, {}, "AUTH: could not get an ADO token (run `az login`)")
    token = tok.stdout.strip()
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return (True, _json.loads(resp.read().decode("utf-8")), hdrs, "")
    except urllib.error.HTTPError as e:
        detail = f"AUTH: HTTP {e.code}" if e.code in (401, 403) else f"HTTP {e.code}"
        return (False, None, {}, detail)
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        return (False, None, {}, f"REST GET failed: {e}")


def _ado_rest_get_all(url, timeout, cap_pages=60):
    """GET every page of a paged ADO collection, following the `x-ms-continuationtoken`
    response header. `url` must already carry its api-version (no continuationToken).
    Returns (ok, all_items, detail) where all_items is the concatenated `.value` lists."""
    items, token = [], None
    for _ in range(cap_pages):
        u = url + (f"&continuationToken={token}" if token else "")
        ok, j, hdrs, detail = _pp._ado_rest_get_h(u, timeout)
        if not ok:
            return (False, None, detail)
        items += (j or {}).get("value") or []
        token = hdrs.get("x-ms-continuationtoken")
        if not token:
            break
    return (True, items, "")


def _ado_rest_get_text(url, timeout):
    """GET an ADO REST url returning PLAIN TEXT (e.g. a build log). (ok, text, detail)."""
    az = shutil.which("az")
    if az is None:
        return (False, None, "az CLI not found")
    try:
        tok = subprocess.run(
            [az, "account", "get-access-token", "--resource", _ADO_RESOURCE,
             "--query", "accessToken", "-o", "tsv"],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8")
    except (subprocess.TimeoutExpired, OSError) as e:
        return (False, None, f"failed to get token: {e}")
    if tok.returncode != 0 or not (tok.stdout or "").strip():
        return (False, None, "AUTH: could not get an ADO token (run `az login`)")
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {(tok.stdout or '').strip()}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (True, resp.read().decode("utf-8", "replace"), "")
    except urllib.error.HTTPError as e:
        detail = f"AUTH: HTTP {e.code}" if e.code in (401, 403) else f"HTTP {e.code}"
        return (False, None, detail)
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        return (False, None, f"REST GET failed: {e}")


def _ado_rest_send(url, method, body, timeout):
    """Send a JSON REST request (POST/PATCH/PUT) to ADO with an az-minted bearer token.
    Returns (ok, json, detail). The ONE write primitive — used for test-plan creates
    where `az devops invoke` has no clean surface."""
    az = shutil.which("az")
    if az is None:
        return (False, None, "az CLI not found")
    try:
        tok = subprocess.run(
            [az, "account", "get-access-token", "--resource", _ADO_RESOURCE,
             "--query", "accessToken", "-o", "tsv"],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8")
    except (subprocess.TimeoutExpired, OSError) as e:
        return (False, None, f"failed to get token: {e}")
    if tok.returncode != 0 or not (tok.stdout or "").strip():
        return (False, None, "AUTH: could not get an ADO token (run `az login`)")
    token = tok.stdout.strip()
    import urllib.request
    import urllib.error
    data = _json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return (True, (_json.loads(raw) if raw.strip() else {}), "")
    except urllib.error.HTTPError as e:
        detail = f"HTTP {e.code}"
        if e.code in (401, 403):
            detail = f"AUTH: HTTP {e.code} (run `az login` / check access)"
        else:
            try:
                msg = _json.loads(e.read().decode("utf-8")).get("message")
                if msg:
                    detail = f"HTTP {e.code}: {str(msg)[:200]}"
            except Exception:
                pass
        return (False, None, detail)
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        return (False, None, f"REST {method} failed: {e}")


def _tag_value(tags, key):
    """Return the value of a `key=value` build tag (e.g. NextMsalVersion=8.4.2 → '8.4.2'),
    or None. Case-sensitive key match; first match wins."""
    pfx = f"{key}="
    for t in tags or []:
        if t.startswith(pfx):
            return t[len(pfx):]
    return None


def _newest_id(ids):
    """The newest build id from a list. ADO build ids increase monotonically, so the
    max numeric id is the most-recent run — this is how a re-trigger's fresh MRWP run
    wins over the failed earlier one. Returns a string, or None if empty."""
    nums = [str(i) for i in (ids or []) if str(i).isdigit()]
    if nums:
        return str(max(int(i) for i in nums))
    return (ids[0] if ids else None)

__all__ = ['RAN_RESULTS', '_ADO_RESOURCE', '_ado_rest_get', '_ado_rest_get_all', '_ado_rest_get_h', '_ado_rest_get_text', '_ado_rest_send', '_az_json', '_newest_id', '_tag_value']
