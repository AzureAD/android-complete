# New Team Onboarding — Presentation Script

A running speaker script for walking a brand-new team through deploying the DRI MCP server.
Sections are added as needed. Each section is written to be spoken aloud or pasted into slide notes.

> Companion diagrams live in [onboarding-diagrams.md](onboarding-diagrams.md).

---

## Section: ICM Certificate Setup

Another important thing we need to discuss is the **ICM certificate setup**. This certificate is needed for **two reasons**.

**Job one — talking to the IcM service to get data.**
The IcM team exposes a few APIs. There's one particular API that **DRICopilot** was already using, and we simply reused that same approach. It's the **`/api/cert/` endpoint**, and it requires **TLS client-certificate–based authentication**. So that alone is the reason we have to set up a certificate — without it, the server can't pull incident data from IcM at all.

**Job two — the On-Behalf-Of (OBO) user flow.**
For **restricted incidents**, we don't want the server to see everything — we want it to see only what the **asking user** is allowed to see. To do that, the server performs an **on-behalf-of token exchange**: it takes the user's sign-in token and asks **Entra ID** for a downstream token **in that user's name**.

To make that request, the server has to **prove it's the legitimate app** — and it does that by **signing the request with the same certificate**.

**How the certificate is split across two places** (added here for clarity):

- The **public half** of the certificate is uploaded to the **App Registration** in Entra ID — that's what Entra uses to **verify** the signature.
- The **private key** is stored in **Key Vault** — and the **MCP server** fetches that private key from Key Vault (via its managed identity) to **sign** the request.

So it's **one certificate doing two jobs**: TLS client auth to reach IcM, and the signing credential that lets the server act on behalf of the user — all with **no secrets** stored anywhere.

> **One-line wrap-up:** Same cert, two uses — it's how we *reach* IcM, and it's how we safely act *as the user* for restricted incidents. Public half verifies in the App Registration, private half signs from Key Vault.

---

## Section: What the Deploy Step Does

Once our prerequisites are in place, the actual deployment is a **single script** — `deploy.ps1`. We give it our filled-in config file, and it provisions everything that *can* be automated. Let me walk through what it's doing under the hood, because it's helpful to know what's being created in our subscription.

Think of it in two halves: first it lays down the **plumbing and identity**, then it **builds and ships the application**.

**First, the foundation:**

- It creates a **Resource Group** to hold everything for our team.
- It creates a **Container Registry** — that's where the Docker images for the server and the indexers will live. It's the Basic tier, and admin access is disabled because we authenticate with identity, not keys.
- It creates a **user-assigned Managed Identity**. This is the single most important piece — it's the identity the server and jobs run as, and it's how they talk to everything else *without secrets*.
- It then assigns the **RBAC roles** that identity needs: pull images from the registry, read from Azure AI Search, and call Azure OpenAI. (One role — write access to our storage blob — is granted manually if our storage lives in a different resource group.)

**Next, the search backend:**

- It sets up **Azure AI Search** — creating the two indexes (TSGs and incidents), the data source, and the pull indexer *inside* the existing search service we provisioned earlier.

**Then it builds and ships the app:**

- It **builds two Docker images** directly in the registry — one for the MCP server, one for the indexer — so we don't need Docker locally.
- It **deploys the MCP server** as a Container App, wiring in all the config as environment variables — endpoints, managed identity, and the auth settings that lock it to our **security group**. It scales to zero when idle, and **OBO starts off** (we enable it later, once the cert is in place).
- Finally, it **deploys the two indexer jobs**: the **ICM indexer** runs daily, and the **TSG indexer** runs every twelve hours, each on a schedule.

When it finishes, it prints our **server URL** and the exact snippet to drop into VS Code's `mcp.json` to connect.

**One thing to call out:** the script stops at the automatable boundary. It does **not** grant ADO/Kusto/Key Vault access and it does **not** start the indexers — it just **prints a checklist** of manual steps for us to run, because they depend on the **managed identity it just created**. The resources themselves — the ICM cert, Key Vault, ADO wiki, and Kusto — were set up as prerequisites; here we're just **wiring that new identity to them**, and order matters:

- **Grant the identity ADO wiki + Kusto access** — the indexers can't run without these (the TSG job clones the wiki, the ICM job queries Kusto).
- **Grant it Key Vault access** (and confirm the cert is uploaded) — this is for the **MCP server**, so it can read the ICM cert for `/api/cert/` calls and OBO; it's a separate track from the indexers.
- **Only then start the indexers** — the first run **backfills** both indexes (the full TSG wiki and roughly a year of historical incidents; later the scheduled jobs only top up the last few days).
- **Finally flip OBO on.**

---
