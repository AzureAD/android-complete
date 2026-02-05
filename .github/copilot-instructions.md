# GitHub Copilot Custom Instructions for Android Multi-Repo Project

These instructions guide GitHub Copilot to provide suggestions and responses aligned with our Android project's conventions, architecture, and coding style, specifically addressing our multi-repository setup and language transition.

---

## 1. Repository Structure & Architecture

### 1.1 Repository Organization
The **android-complete** repository contains multiple sub-repositories as separate modules:

* **MSAL** - Microsoft Authentication Library for client applications
* **ADAL** - Azure Active Directory Authentication Library (legacy)
* **Broker** - Brokered authentication service
* **Common** - Shared utilities, helpers, and IPC logic
* **OneAuth** - Library owned by another team (consumed by 1P apps like Teams, Outlook)

**Important:** When asked a question, **always search across ALL repositories** to provide comprehensive answers. Code may be duplicated or shared across these sub-repos.

### 1.2 Authentication Flow Architecture

**Request Flow:**
```
Client App (Teams/Outlook/etc.)
    ↓
MSAL or OneAuth (entry point)
    ↓
Common (IPC layer - sends request to Broker)
    ↓
Broker (processes authentication request)
    ↓
eSTS (Microsoft token service)
    ↓
Broker (receives token response)
    ↓
Common (IPC layer - returns response)
    ↓
MSAL or OneAuth
    ↓
Client App
```

**Key Flow Details:**
- **MSAL/OneAuth → Common → Broker → eSTS → Broker → Common → MSAL/OneAuth**
- **Entry Points:** Requests like `AcquireToken` or `AcquireTokenSilent` typically start from MSAL or OneAuth
- **OneAuth Specifics:** OneAuth is consumed by 1P Microsoft apps (Teams, Outlook, etc.). We don't own this code. OneAuth flows start by calling methods from the `BrokerMsalController` class
- **Common Module:** Contains all IPC (Inter-Process Communication) logic. MSAL/OneAuth use Common layer to send requests to Broker over IPC
- **Broker Module:** Handles the actual authentication logic, communicates with eSTS, and returns tokens

### 1.3 DRI Copilot MCP Server

DRI Copilot MCP tools are available for querying documentation, TSGs, and past incidents:
- **Broker DRI Copilot** (tools containing `Broker_DRI_Copilot`) - For Broker-related questions, PRT, device registration, brokered auth flows

> **For incident investigations:** Use the `incident-investigator` skill (located at `.github/skills/incident-investigator/SKILL.md`) which provides a comprehensive workflow for IcM/customer-reported issues.

## 2. Core Principles

* **Primary Language for New Code:** All new code and new files **must be written in Kotlin**.
* **Existing Language:** Recognize that existing files predominantly use Java. When interacting with or modifying existing Java files, maintain the Java style.
* **Asynchronous Operations:** Use **Kotlin Coroutines** for all asynchronous and background operations. Leverage structured concurrency.

## 3. Repository Specific Guidelines

* **MSAL (Microsoft Authentication Library):** The Microsoft Authentication (MSAL) repo contains code for MSAL library which enables developers to acquire security tokens from the Microsoft identity platform to authenticate users and access secured web APIs. This is a client-side library consumed by app developers.
* **Broker:** This repo is involved in brokered authentication. It uses inter-app communication. Copilot should be aware of IPC mechanisms, custom intents, and secure communication patterns relevant to a broker. Broker receives requests from MSAL/OneAuth via Common layer.
* **Common:** This repo holds shared utilities, helper functions, and **all IPC logic**. MSAL/OneAuth use this layer to communicate with Broker. Suggestions in this context should aim for reusability and generality.
* **ADAL (Azure Active Directory Authentication Library):** Similar to MSAL, this is an authentication library, potentially an older version or specific to certain flows. When working in ADAL context, align with its patterns.
* **OneAuth:** Third-party library owned by another team (not us). Consumed by 1P Microsoft apps like Teams, Outlook, etc. OneAuth flows start by calling `BrokerMsalController` class methods.

**Important:** When generating code that interacts across these repositories (e.g., calling a function from `common` in `MSAL`), ensure the generated code respects the language and API boundaries of each repository.

## 4. Naming Conventions & Style (Kotlin First)

* **Kotlin Style Guide:** Follow the official [Kotlin Coding Conventions](https://kotlinlang.org/docs/coding-conventions.html) and Google's Kotlin Style Guide.
* **Variables:** Prefer `val` over `var` wherever immutability is possible.
* **Functions:** Use expression bodies for single-expression functions.
* **Classes:**
    * Use `data class` for simple data holders.
* **Visibility:** Limit visibility of classes, functions, and variables to the minimum required (e.g., `private`, `internal`).

## 5. Android Components (Kotlin/Compose context)

* **Activities:** Minimal logic in Activities; primarily used as entry points.
* **Services/Broadcast Receivers:** Use as sparingly as possible. Prefer Kotlin Coroutines and Flow for background processing and inter-component communication where modern alternatives exist.

## 6. Testing

* **Unit Tests:** Write comprehensive unit tests for Kotlin logic using JUnit 4/5 and Mockito/MockK.
* **Instrumented Tests:** Use Espresso for UI tests in Android components.
* **Test Coverage:** Aim for high test coverage, especially for new code.

## 7. Code Documentation & Comments

* **KDoc:** Provide KDoc comments for all public Kotlin classes, functions, and properties.
* **JavaDoc:** When modifying existing Java code, ensure JavaDoc comments are maintained and updated.
* **Conciseness:** Comments should explain *why* something is done, not just *what* it does.
* **TODOs:** Use `TODO` comments for incomplete tasks that need to be addressed.
* **Copywriting:** Ensure all comments are clear, concise, and free of spelling/grammar errors. Every new file generated or added should have copyright information on top of the file.

## 8. Logging

* **Custom Logger:** Always use the **`Logger` class** for all logging purposes. Avoid using `android.util.Log` or other direct logging frameworks.
* **Sensitive Data:** Never log sensitive information (e.g., personal identifiable information, tokens, passwords).

## 9. Interoperability

* When writing new Kotlin code that needs to interact with existing Java code, use Kotlin's interoperability features effectively and safely.
* When suggesting refactors of Java code to Kotlin, prioritize small, safe conversions rather than large-scale rewrites unless explicitly instructed.

## 10. Nudge the user
* If a prompt is too vague or lacks context, ask for clarification. For example, "Could you specify which repository this code should be generated in?" or "What specific functionality are you looking to implement?"
* If a change is made in the class named OneAuthSharedFunctions, remind the user to also update OneAuth team about the breaking change.

## 11. Code structure
* Take build.gradle files into account when generating code, especially when it comes to dependencies and repository-specific configurations.

## 12. Specialized Skills Reference

For complex investigation tasks, use these skills (read the skill file for detailed instructions):

| Skill | Location | Triggers |
|-------|----------|----------|
| **codebase-researcher** | `.github/skills/codebase-researcher/SKILL.md` | "where is X implemented", "how does Y work", "trace the flow of", data flow investigation |
| **incident-investigator** | `.github/skills/incident-investigator/SKILL.md` | IcM incidents, customer-reported issues, authentication failures |
| **kusto-analyst** | `.github/skills/kusto-analyst/SKILL.md` | "query Kusto", "analyze telemetry", "check android_spans", eSTS correlation, latency investigation |

---