# Onboarding Diagrams

Companion diagrams for [new-team-onboarding-script.md](new-team-onboarding-script.md).

---

## ICM Certificate — one cert, two jobs

```mermaid
flowchart TB
    KV["Key Vault<br/>(private key)"]
    APP["App Registration (Entra ID)<br/>(public cert + thumbprint)"]
    MCP["MCP Server"]

    KV -- "fetch private key via MSI" --> MCP

    subgraph Job1["Job 1 — Reach IcM"]
        direction LR
        MCP -- "TLS client cert" --> ICM["IcM OData<br/>/api/cert/ endpoint"]
    end

    subgraph Job2["Job 2 — Act on behalf of user (OBO)"]
        direction LR
        MCP -- "client-assertion JWT<br/>(signed w/ private key)" --> ENTRA["Entra ID"]
        APP -. "verifies signature<br/>(public cert by thumbprint)" .-> ENTRA
        ENTRA -- "user-context token" --> MCP
        MCP -- "query as the user" --> KUSTO["Kusto<br/>(restricted-CRI check)"]
    end
```

---

## OBO token exchange — who proves what

```mermaid
sequenceDiagram
    participant U as User (VS Code)
    participant M as MCP Server
    participant KV as Key Vault
    participant E as Entra ID
    participant K as Kusto

    U->>M: request + user Bearer token
    M->>KV: get certificate (via MSI)
    KV-->>M: private key + thumbprint
    Note over M: Sign client-assertion JWT<br/>with the cert private key
    M->>E: OBO request<br/>user_assertion = user token (delegation)<br/>client_assertion = cert-signed JWT (app identity)
    E->>E: verify client assertion vs registered public cert<br/>validate user token
    E-->>M: Kusto token (in user's context)
    M->>K: restricted-CRI check as the user
```

---
