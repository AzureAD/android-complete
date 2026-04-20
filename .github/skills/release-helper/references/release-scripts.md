# Release Scripts (android-complete)

Scripts in `android-complete/scripts/release/` are consumed by both the monthly release and hotfix orchestrators in AuthClientAndroidPipelines.

## File Paths and Constants (`libs/constants.ps1`)

Defines all file paths and gradle variable names used across scripts:

### Versioning Files
| Variable | Path |
|---|---|
| `COMMON4J_VERSIONING_FILE` | `common/common4j/versioning/version.properties` |
| `COMMON_VERSIONING_FILE` | `common/versioning/version.properties` |
| `BROKER4J_VERSIONING_FILE` | `broker/broker4j/versioning/version.properties` |
| `BROKER_VERSIONING_FILE` | `broker/AADAuthenticator/versioning/version.properties` |
| `MSAL_VERSIONING_FILE` | `msal/msal/versioning/version.properties` |
| `ADAL_VERSIONING_FILE` | `adal/adal/versioning/version.properties` |

### Build Gradle Files
| Variable | Path |
|---|---|
| `COMMON_BUILD_GRADLE_FILE` | `common/common/build.gradle` |
| `MSAL_BUILD_GRADLE_FILE` | `msal/msal/build.gradle` |
| `BROKER_BUILD_GRADLE_FILE` | `broker/AADAuthenticator/build.gradle` |
| `BROKER4J_BUILD_GRADLE_FILE` | `broker/broker4j/build.gradle` |
| `ADAL_BUILD_GRADLE_FILE` | `adal/adal/build.gradle` |

### Changelog Files
| Variable | Path |
|---|---|
| `COMMON_CHANGELOG_FILE` | `common/changelog.txt` |
| `BROKER_CHANGELOG_FILE` | `broker/changes.txt` |
| `MSAL_CHANGELOG_FILE` | `msal/changelog` |
| `ADAL_CHANGELOG_FILE` | `adal/changelog.txt` |

### Gradle Variable Names
- `broker4jVersion`, `common4jVersion`, `commonVersion`, `msalVersion`, `adalVersion`

## Scripts

### `init_release.ps1`
**Called during:** Monthly release, Stage 2 (BranchSetup)

Takes 5 version parameters (msal, broker, common, common4j, broker4j). Appends `-RC1` suffix and updates:
- Version properties files (`versionName=X.Y.Z-RC1`)
- Changelog files (adds new version header under vNext)
- Gradle files (updates dependency versions)

Updates all libraries: common4j, common, msal, broker4j, broker.

### `update_rc.ps1`
**Called during:** Monthly release Stage 3 (TriggerRC), Hotfix Stage 4

Takes `-rc <number>` parameter. Replaces `-RCn` suffix with new RC number across all versioning, gradle, and changelog files. Supports `--skipCommon`, `--skipMsal`, `--skipBroker` flags.

### `remove_rc.ps1`
**Called during:** Monthly release Stage 4 (RemoveRCTags), Hotfix Stage 5

No parameters. Strips all `-RCn` suffixes from all versioning, gradle, and changelog files across all libraries.

### `init_hotfix_broker.ps1`
**Called during:** Broker-only hotfix

Takes `brokerVersion` and `CommonVersion`. Sets broker + broker4j versions to RC1, updates changelog with common version reference.

### `init_hotfix_msal.ps1`
**Called during:** MSAL-only hotfix

Similar to broker hotfix but for MSAL.

### `init_hotfix_for_all.ps1`
**Called during:** Full hotfix affecting all libraries

Updates all components similar to `init_release.ps1`.

## Helper Methods (`libs/helper_methods.ps1`)

| Function | Purpose |
|---|---|
| `Update-VersionNumber` | Updates `versionName=` in version.properties files |
| `Update-ChangelogHeader` | Replaces vNext section with new version header |
| `Update-ChangelogHeaderForHotfix` | Hotfix variant of changelog update |
| `Update-GradeFile` | Updates gradle variable values (dependency versions) |
| `Remove-AllRCVersionsInFile` | Strips `-RCn` from a file |
| `Update-AllRCVersionsInFile` | Replaces RC number in a file |
| `Get-VNextHeader` | Returns the vNext changelog header format |
| `Get-ReplacementtHeader` | Returns the replacement header with version number |

## Version Auto-Detection (`AuthClientAndroidPipelines/scripts/get_next_version.py`)

Reads a changelog file, extracts the latest `Version X.Y.Z`, then checks the vNext section:
- `[MAJOR]` tag → bumps major, resets minor+patch
- `[MINOR]` tag → bumps minor, resets patch  
- Otherwise → bumps patch

Used by `release-util.yml` to auto-compute next versions.
