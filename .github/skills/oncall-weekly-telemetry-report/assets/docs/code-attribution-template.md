# Code Attribution Card — Per-Spike Checklist

Use this template for **every** spike-attribution card in the report. The HTML markup matches the `code-attr` / `pr-card` / `origin-tag` styles already in [`report-template.html`](../templates/report-template.html).

A card without a populated **Originator + Top throw site + Likely PRs + Next step** is not acceptable. "Caller hot-spots", "Underlying cause", and "Top error_messages" are required for any error where the originator is *not* obvious from the error name alone (Android system errors, 3rd-party library wrappers, environmental).

---

## Required fields

### 1. Originator

One of:

- 🟥 `<span class="origin-tag origin-broker">broker</span>` — error originates in our `broker/` or `broker4j/` code
- 🟥 `<span class="origin-tag origin-broker">common</span>` — originates in `common/` or `common4j/`
- 🟧 `<span class="origin-tag origin-android">Android system</span>` — Android SDK (WebView, Conscrypt, Keystore, okhttp, KeyStore HAL)
- 🟦 `<span class="origin-tag origin-thirdparty">3rd-party lib</span>` — Nimbus JOSE+JWT, Gson, etc.
- 🟦 `<span class="origin-tag origin-thirdparty">eSTS</span>` — server-returned OAuth error (`invalid_grant`, `invalid_resource`, `unauthorized_client`, etc.)
- ⬜ `<span class="origin-tag origin-env">environmental</span>` — enterprise TLS interception (Zscaler), OEM keystore quirks, network-policy

### 2. Top throw site

Fully-qualified `Class.method:line` plus % of cases that throw from this single site. Example:

> `com.nimbusds.jwt.SignedJWT.getJWTClaimsSet:28` &nbsp; **97% of cases** &nbsp; thrown as `ParseException`

How to find: query raw `android_spans` filtered to the spiking error code over a tight time window, group by `error_location` (or first frame of `error.stack_trace`), order desc.

### 3. Wrapper

The broker/common method that catches the originator's exception and re-throws it as the user-visible error code. Often `IDToken.parseJWT()`, `ServiceException(...)`, `ExceptionAdapter.exceptionFromAuthorizationResult()`, `ClientException("Code:" + err, ...)`.

How to find: walk up the stack from the throw site; look for `try { ... } catch (X e) { throw new Y(...); }` patterns in `broker/` and `common/`.

### 4. Caller hot-spots

Top 1–3 callers of the wrapper, with device counts. Helps pin the regression to a specific code path. Example:

> `GetRegistrationStateV0LegacyExecutor.execute:90` (84 dev) · `AndroidDeviceRegistrationClientController.execute:234` (47 dev)

### 5. Underlying cause

The proximate cause one level deeper than "the error fired". Example:

> 99% `CertificateException` from `TrustManagerImpl.verifyChain` · cert-chain rejection at TLS layer

How to find: slice on `error.cause` or first 80 chars of `error_message`.

### 6. Top error_messages

Top 3–5 distinct `error_message` strings with counts. Often the strongest signal for environmental errors (e.g. `net::ERR_SSL_PROTOCOL_ERROR`, Zscaler-issued cert names, OEM keystore exception text).

```kql
android_spans
| where EventInfo_Time between (ago(7d) .. now())
| where error_code == "<code>"
| summarize count() by tostring(error_message)
| top 10 by count_
```

### 7. Likely PRs

1–3 PRs (or explicit "None"), each rendered as a `pr-card` with:

- **Confidence**: `high` / `medium` / `low` / `none` (use the matching `pr-conf-*` CSS class)
- **GitHub URL** (full link, not bare SHA)
- **Commit SHA** (short)
- **Author** (`@username`)
- **AB#** if available
- **Why-it's-the-suspect** — 1 sentence explaining the *causal* link, not just the title. Bad: "touches MicrosoftStsAccountCredentialAdapter". Good: "touches the IDToken parse path on MSA interactive flows; matches the Apr 30 climb date."

| Confidence | Use when |
|---|---|
| 🔴 **high** | Trajectory + flight rollout date both line up; PR diff touches the exact throw site |
| 🟡 **medium** | Code path matches but no flight gate evidence, or matches one of two suspects |
| 🟢 **low** | Candidate from grep, plausible but unverified |
| ⚪ **none** | No broker PR identified — explicitly say *why* (Android system error, eSTS-returned, OEM-specific, environmental) |

### 8. Next step

Concrete action with a **named owner** and a **measurable outcome**. Examples:

- "Disable `ENABLE_OPENID_VC_HANDLING_IN_WEBVIEW_REDIRECT` flight for the affected slice (Outlook + msapps + 16.0.1) and verify spike subsides. Owner: **@somalaya**."
- "Pull 5–10 correlation IDs from Outlook devices hitting this and check eSTS logs for the actual rejected resource ID. Owner: **Outlook + eSTS teams**."
- "Slice by `bound_service_status` vs `content_provider_status` attributes to identify which IPC strategy is failing. Owner: **@pedroro**."

---

## HTML skeleton (copy-paste, then fill in)

```html
<div class="code-attr">
  <div class="code-attr-title">Code attribution</div>

  <div class="origin-row">
    <div class="origin-label">Originator</div>
    <div class="origin-value"><span class="origin-tag origin-broker">broker</span> short description</div>
  </div>

  <div class="origin-row">
    <div class="origin-label">Top throw site</div>
    <div class="origin-value"><span class="stack">fully.qualified.Class.method:line</span> &nbsp; <strong>NN% of cases</strong></div>
  </div>

  <div class="origin-row">
    <div class="origin-label">Wrapper</div>
    <div class="origin-value"><span class="stack">wrapping.method</span> wraps it as <code>NewException(...)</code></div>
  </div>

  <div class="origin-row">
    <div class="origin-label">Caller hot-spots</div>
    <div class="origin-value"><span class="stack">caller.A:NN</span> (X dev) &nbsp;·&nbsp; <span class="stack">caller.B:NN</span> (Y dev)</div>
  </div>

  <div class="origin-row">
    <div class="origin-label">Underlying cause</div>
    <div class="origin-value">NN% <code>RootCauseException</code> from <span class="stack">root.method</span></div>
  </div>

  <div class="origin-row">
    <div class="origin-label">Top error_messages</div>
    <div class="origin-value">N× <code>message 1</code> &nbsp;·&nbsp; N× <code>message 2</code> &nbsp;·&nbsp; N× <code>message 3</code></div>
  </div>

  <div class="origin-row">
    <div class="origin-label">Likely PRs</div>
    <div class="origin-value">
      <div class="pr-list">
        <div class="pr-card">
          <span class="pr-conf pr-conf-high">🔴 High</span>
          <div class="pr-body">
            <a class="pr-id" href="https://github.com/.../pull/NN" target="_blank" rel="noopener">repo#NN</a> · <span class="pr-title">PR title</span>
            <div class="pr-meta">commit <code>shortsha</code> · 2026-MM-DD · author @user · AB#NNNNNNN</div>
            <div class="pr-why">One-sentence causal explanation.</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div class="origin-row">
    <div class="origin-label">Next step</div>
    <div class="origin-value">Concrete action. <strong>Owner: @name / team</strong>.</div>
  </div>
</div>
```
