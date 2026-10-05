# Protocol & Platform Constraints — what is *ours* to fix, and what is not

> **Why this file exists.** A run classified a finding using only codebase evidence and had no way to
> express the conclusion a principal engineer reached in one sentence: *"we're dealing with public clients
> that cannot be validated — a client supplying an invalid clientId is not something we can do much about
> unless OAuth evolves the public client model."* The skill had **zero** protocol knowledge (no mention of
> OAuth, RFC, or public clients anywhere), so it could only answer "is there a control in the code?" —
> never "is a control even *possible* here?" Those are different questions, and the second one is what
> tells MSRC a finding is **not worth fixing**.

**Use this file in every investigation.** Pass it as a standing constraint alongside the Scope Contract
(Step 2.5), and consult it before finalizing any verdict — especially before writing
`Not-Fixable (By-Design)`.

---

## The central question

For every claim, ask **both**:

1. **Is there a control today?** (codebase evidence — the existing Gate 0 question)
2. **Could a control exist at all, at this layer?** (protocol/platform evidence — *this* file)

A finding where the answer to (2) is **no** is `Not-Fixable (By-Design)`. It is **not** "already covered"
(nothing covers it) and **not** "not covered" (implying we should go build something). Those two words
would send an engineer off to write a fix that cannot work.

> ⚠️ **The bar is high, and it cuts both ways.** `Not-Fixable (By-Design)` is a strong claim that we are
> asking MSRC to withdraw a finding. Earn it: name the specific RFC/platform guarantee, state what an
> attacker gains, and — critically — **check whether a neighbouring mechanism closes it anyway** (see
> "The trap" below). Most findings are *not* in this category.

---

## OAuth 2.0 public clients — the constraint that comes up most

Android apps are **public clients**. Per **RFC 6749 §2.1** and **RFC 8252 (OAuth 2.0 for Native Apps)**, a
native app cannot hold a client secret: anything shipped in the APK is extractable by anyone who downloads
it. Therefore:

- **A client id is an identifier, not a credential.** It is public by design. Any local app can put any
  `client_id` in a request — including a first-party one.
- **No client-side code change can make client-id assertion trustworthy.** Any secret we shipped would be
  extractable; any check we wrote would run on data the caller supplied.
- **RFC 9700 (Best Current Practice for OAuth 2.0 Security)** is the current consolidated guidance. It
  assumes public clients cannot be authenticated and directs you to compensating controls (redirect-URI
  exact matching, PKCE, sender-constrained tokens) rather than client authentication.

**Therefore:** *"a malicious app supplied a first-party client id"* is, on its own, **not a vulnerability
in our code.** It is a property of the model.

### 🛑 The trap — do not stop at "public clients can't be authenticated"

This is where the analysis usually goes wrong in **our** favour, incorrectly. The correct question is not
"can we authenticate the client id?" (no) but:

> **Does anything the client id names actually get *authorized* on the strength of that assertion?**

If the answer is "no — authorization is re-anchored on something the OS *can* attest," the finding is
genuinely closed, and it is closed by **our design**, not by the RFC. If the answer is "yes — naming that
client id widened what came back," then **we have a real, fixable defect**, and the public-client argument
is *irrelevant to it*. Say so plainly.

On Android we have three things the platform attests that are **strictly stronger** than a client id:

| Mechanism | What it proves | Where it comes from |
|---|---|---|
| `Binder.getCallingUid()` | The kernel-attested UID of the calling process | Kernel; unspoofable across the IPC boundary |
| `PackageManager.getPackagesForUid(uid)` | Which packages that UID actually owns | OS |
| Signing-certificate digest of the installed package | The caller's real signing identity | OS package database |

**The redirect URI is the bridge.** A broker redirect of the form `msauth://<package>/<signature-hash>` is
**not** a caller-asserted string taken on faith — we *recompute* it from the installed package's real
signing certificate and compare. Producing another app's redirect URI requires that app's signing key.
Composed with a UID→package ownership check, this yields a genuine
**`uid → package → signature → redirectUri → clientId`** binding.

> **Consequence — write this down when it applies:** a claim that looks like an unfixable public-client
> problem often *is* fixable, because the redirect URI converts an unauthenticatable assertion into a
> signature-verifiable one. Check for that binding **before** concluding `Not-Fixable (By-Design)`.
> A real run initially framed a sub-claim as "inherent to OAuth" when the accurate framing was:
> *client-id assertion is unfixable, therefore the design correctly stops depending on it and re-anchors
> authorization on uid + signature.* The second framing is the one that survives review — and it is
> stronger, because it says our design is right rather than that our hands are tied.

### FOCI (Family of Client IDs)

FOCI lets a family of first-party apps share a refresh token. Two things follow:

- **Family membership is public information.** The token service will truthfully answer "is this client id
  in a family?" for a client id the caller does not own. That question's answer cannot be withheld and is
  **not** a disclosure defect on its own.
- **But the shared FOCI cache is a genuine cross-app asset.** Whether *this caller* may read it must be
  decided on attested identity (uid/signature), never on the client id it names. A finding that a caller
  reached family-shared material by *naming* a family client id is **ours, and fixable** — do not let the
  public-client argument launder it into "by design."

---

## Other constraints that are genuinely not ours

Apply the same "could a control exist at this layer?" test. Cite the guarantee.

| Constraint | Why no client-side fix exists | What we *can* still be asked for |
|---|---|---|
| **Public-client id assertion** (above) | RFC 6749 §2.1 / 8252 — no secret can be shipped | Re-anchor authorization on uid/signature; never authorize on client id |
| **Rooted / physically-compromised device** | The attacker is above our trust boundary; any check runs on hardware they control | Defense-in-depth only; see the SOLE-path rule in `severity-rubric.md` |
| **User explicitly grants a permission / completes consent** | The platform's consent model delegates the decision to the user | Clear, non-misleading consent UX; correct scoping |
| **Another app legitimately installed by the user** | We cannot prevent installation | Validate *what it can reach*, not that it exists |
| **OS-level API behaviour we do not control** | e.g. UID recycling after uninstall, `getPackagesForUid` shared-UID semantics | Compensating cleanup/reconciliation on our side — often *is* actionable, so check before dismissing |
| **Server-side policy we cannot observe** | Conditional Access, risk/fraud scoring, per-tenant policy are not in client source | Do **not** assert safety *or* exposure — use the Scope & Verification Boundary disclaimer |

> **Shared-UID note.** `getPackagesForUid()` can return several packages, and `[0]` is an arbitrary element.
> `android:sharedUserId` is deprecated for new apps but persists for legacy ones. If a control's correctness
> depends on which element is picked, that is **our** bug — not a platform constraint.

---

## How to write it up

When a claim (or sub-claim) lands in this category, the report must carry:

1. **The exact sub-claim**, quoted verbatim from the filed report.
2. **The constraint**, named with its standard: *"RFC 6749 §2.1 / RFC 8252 — native apps are public
   clients and cannot hold a secret."*
3. **Why no client-side change closes it** — one or two sentences, concrete.
4. **What we do instead** — the compensating control we *do* implement, cited `file:line`. This is the part
   that makes it persuasive rather than dismissive.
5. **The explicit ask to MSRC** — normally: *withdraw this sub-claim, or re-file the valid part separately.*

### Template paragraph

> **Sub-claim:** "<verbatim quote>"
> **Disposition:** Not-Fixable (By-Design) — OAuth public-client model.
> Per RFC 6749 §2.1 and RFC 8252, a native app is a public client and cannot hold a client secret, so any
> local application can *assert* any client id; no client-side change can make that assertion trustworthy.
> Accordingly our design does not authorize on the asserted client id at all: authorization is re-anchored
> on the kernel-attested calling UID and the caller's real signing certificate (`<file:line>`), and the
> client id is demoted to a selector over an already-uid-scoped view (`<file:line>`).
> **Ask:** we request this sub-claim be withdrawn. The remaining sub-claims are addressed separately above.

---

## Reference index

| Standard | Covers | Use it for |
|---|---|---|
| **RFC 6749** §2.1 | Client types; public vs confidential | Any "app asserted a client id" claim |
| **RFC 8252** | OAuth 2.0 for Native Apps | Redirect URIs, custom schemes, external user-agents |
| **RFC 9700** | BCP for OAuth 2.0 Security | Current consolidated guidance; supersedes older BCP drafts |
| **RFC 7636** | PKCE | Authorization-code interception on native platforms |
| **RFC 8628** | Device Authorization Grant | Device-code flow findings |
| **RFC 9449** | DPoP | Sender-constrained tokens; "token replay" claims |
| **RFC 6819** | Threat model & security considerations | Threat-model framing for a filed claim |

> Cite the **section**, not just the RFC number — a bare RFC reference reads as decoration. If you are not
> certain a section says what you need, verify it before citing; a wrong citation in a rebuttal to the
> security team is worse than no citation.
