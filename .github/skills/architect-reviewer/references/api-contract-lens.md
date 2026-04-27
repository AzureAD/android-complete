# API & SDK Contract Lens

This lens checks for breaking changes, versioning correctness, and SDK surface impact in design docs for the Android Identity Platform.

## What to Check

### Public API Surface
- All new public classes, interfaces, methods, and constants must be listed with full signatures
- Any removal or renaming of existing public symbols is a breaking change — must be identified explicitly
- API additions that are non-breaking must still be listed so downstream consumers can plan adoption
- `@Deprecated` annotations with migration guidance required for any API being retired (not deleted immediately)
- Public API must be documented (KDoc/JavaDoc) — missing docs on public symbols is a quality gate

### Breaking vs. Non-Breaking Change Classification

| Change Type | Classification |
|-------------|---------------|
| Remove public method/class | 🔴 Breaking |
| Rename public method/class | 🔴 Breaking |
| Change method signature (add required param) | 🔴 Breaking |
| Add new required field to Parcelable/Serializable | 🔴 Breaking |
| Change enum values | 🔴 Breaking |
| Add new optional method with default implementation | 🟢 Non-breaking |
| Add new public class/interface | 🟢 Non-breaking |
| Add new optional field to Bundle/Parcelable (with backwards-compatible default) | 🟢 Non-breaking |
| IPC Bundle key rename or removal | 🔴 Breaking (broker compat) |

### Versioning
- Breaking changes require a major version bump in the affected library
- Non-breaking additions require a minor version bump
- Any change to the public API must describe its versioning impact
- `minApiVersion` / `minBrokerVersion` must be specified if the feature depends on a minimum broker or SDK version
- Library consumers (app developers using MSAL) must not be required to change their integration for non-breaking changes

### Serialization / Parcelable Contracts
- If the design adds fields to a `Parcelable`, it must describe backward compatibility (older serialized instances deserialized by newer code)
- `Bundle` key additions are safe; key removals require deprecation period
- JSON serialization schemas must have explicit versioning if used in IPC or storage

### Cross-Repo Impact
- Design must list which repos require changes: MSAL, Common, Broker, ADAL
- Changes in Common that affect the IPC contract affect both MSAL and Broker simultaneously — both must be updated atomically or the IPC must be versioned
- Changes in MSAL public API require consumer impact analysis (app developer-facing)
- Changes in Broker that affect OneAuth require OneAuth team notification

### Rollout / Feature Flags
- New public API features should be gated behind a feature flag for initial rollout
- API that is released publicly cannot be removed in a patch release — only in a major version after a documented deprecation period

## Red Flags — Auto-escalate to 🔴

- Breaking public API change without major version bump
- Public method or class removed without deprecation period
- IPC Bundle key renamed or removed without backward-compatible handling
- No cross-repo impact analysis for a feature that clearly spans repos
- Parcelable schema change without backward compatibility analysis

## Yellow Flags — 🟡 Raise for Discussion

- New public API added without KDoc/JavaDoc
- Feature flag absent for new public-facing API
- Versioning impact not stated (patch / minor / major?)
- Only one repo listed as impacted when flow analysis suggests others are affected
- `@Deprecated` annotation proposed but no migration path described

## Questions to Generate

- If the doc adds or modifies public API: "Is this change breaking? What is the library version impact? Have downstream consumers (app developers) been considered?"
- If the doc modifies IPC Bundle schema: "Are all Bundle key changes additive? What is the behavior when an older broker/MSAL version receives this new bundle?"
- If the doc lists only one repo as impacted: "This feature touches [X flow] — does Common also need changes? Does Broker? What is the cross-repo change order?"
- If deprecations are involved: "What is the deprecation timeline? What is the migration path for existing consumers?"
- If the doc introduces a new public interface: "How is this versioned? Can it evolve without breaking changes in the future?"
