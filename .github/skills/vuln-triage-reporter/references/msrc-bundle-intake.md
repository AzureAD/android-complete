# MSRC evidence bundle intake — the PoC is the highest-signal artifact

> **Why this file exists.** A run received an MSRC case as a **password-protected zip** containing the
> submission text, a compiled PoC APK, and the PoC source. The skill had no guidance on any of it — the zip
> would not extract with the default tooling, and nothing told the engineer that the PoC source was worth
> reading. It turned out to be the single most decisive artifact in the investigation: it named the *exact*
> attacker-controlled keys, which let the Scope Contract be written precisely instead of from the report's
> prose. Meanwhile the report's own narrative overstated what had been demonstrated.

---

## 1. Opening the bundle

MSRC case bundles arrive as an encrypted zip. Typical layout:

```
case/msrcfs_SubmissionData.txt      <- the filed report (read first)
submission/msrcfs_poc_<id>.apk      <- compiled PoC
submission/msrcfs_poc_source_<id>.zip <- PoC source (often ALSO encrypted, same password)
```

- The archive uses legacy **ZipCrypto**, which `Expand-Archive` cannot read — it fails with
  *"the archive entry was compressed using an unsupported compression method."* That message is misleading;
  the real problem is encryption. Use `7z` (or any tool supporting ZipCrypto).
- MSRC uses a small set of standard bundle passwords; the inner `poc_source` zip normally uses the same
  one. If none works, the case's IcM/portal page carries it — ask rather than guess indefinitely.
- Inspect before extracting: `7z l -slt <bundle>.zip` shows `Encrypted = +` and the entry list.

```powershell
& "C:\Program Files\7-Zip\7z.exe" l -slt <bundle>.zip                       # inspect
& "C:\Program Files\7-Zip\7z.exe" x "-p<pw>" "-o<dest>" <bundle>.zip -y     # extract
& "C:\Program Files\7-Zip\7z.exe" x "-p<pw>" "-o<dest>\src" <dest>\submission\*_poc_source_*.zip -y
```

> 🛑 **Handle the APK as untrusted.** Do **not** install or run it. Read the **source**; treat the compiled
> APK as an artifact to reference, not execute. Extract to a scratch directory **outside** the repo tree
> and **outside** the private workspace, and delete it when the run ends.
>
> 🛑 **Never copy bundle contents into the repo**, and never paste PoC payloads into a report or a commit
> (Non-Negotiable #8). Cite *what the PoC targets*, not how to reproduce it.

---

## 2. Read the PoC source before writing the Scope Contract

This is the point of the whole file. The PoC tells you, unambiguously and without prose:

| What to extract | Why it matters |
|---|---|
| **The exact entry point** — component/action/interface bound or invoked | Fixes the **channel** in the Scope Contract. Reports often name the wrong or an approximate one. |
| **The exact request keys the attacker controls** | These are the untrusted inputs. Report prose frequently names keys that **do not exist in our tree** — verify each against the codebase. |
| **What is asserted vs. what is attested** | Separates "the caller claimed X" from "the OS said X" — the crux of most caller-identity findings. |
| **Which methods are actually exercised** | Distinguishes demonstrated from asserted-by-analogy (see §3). |
| **Declared preconditions** (manifest `<queries>`, permissions, target SDK) | Real preconditions for the severity rubric. |

**Cross-check every key the report names against the codebase.** In a real run the report cited a bundle
key that returned **zero matches** across all repos — the actual keys were different. That single check
reframed the finding. Record the mismatch in the audit trail as an absence proof; it is legitimate,
citable evidence that the report's mechanism description was inaccurate.

---

## 3. Critique the filed report's evidence quality

The submission is an **argument**, not a finding of fact. Assess it explicitly, and record the assessment
in the report's *How It Can Be Exploited* section. Ask:

1. **What did the PoC actually demonstrate?** Read the attached logs. A PoC that binds successfully but
   returns an **empty** result set has demonstrated *reachability*, not *impact*.
2. **What is asserted by analogy?** Watch for "the same pipeline is used, therefore <worse impact> follows."
   That is a hypothesis. Submissions sometimes state plainly that a step was not demonstrated (e.g. "a live
   tenant was not available") while the executive summary still claims the full impact.
3. **Is the claimed impact the *demonstrated* impact?** Titles routinely say "token theft" / "account
   takeover" for a PoC that only enumerated metadata.
4. **Is the named mechanism real?** See the key cross-check above.
5. **Is intended behaviour being reported as a flaw?** An exported service *designed* for third-party SSO
   is not vulnerable merely because a third party can bind to it. The question is what it hands back.

> **Say it fairly, and say it in the report.** Note both directions: where the report is *accurate*
> (including where it accurately describes behaviour we have since fixed) and where it overstates. A
> rebuttal that only attacks the report reads as defensive; one that concedes the valid part and
> disciplines the overstated part is the one that survives review.
>
> **Never** use weak PoC evidence alone to dismiss a finding. A researcher's inability to obtain a test
> tenant says nothing about whether the code path is exploitable — that question is settled by **our**
> code analysis, not by their demo. Weak evidence changes how we *describe* the impact; only code evidence
> changes the verdict.

---

## 4. Record it

Save the extracted **submission text** (not the APK) under the shift's `_intake/` folder so the finding is
reproducible without re-opening the bundle. In the finding report:

- Quote the filed claims **verbatim** into the **Claim Ledger** — one row per sub-claim (see
  `references/report-template.md` and the per-part disposition rules). Bundles routinely contain two or
  three *separable* claims that need different answers.
- Note in **Searches Run** the key cross-check and its result, including empty results.
- Note what the PoC demonstrated vs. asserted in **How It Can Be Exploited**.

**Clean up:** delete the scratch extraction directory (APK included) at the end of the run.
