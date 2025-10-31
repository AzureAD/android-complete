
# Introduction

This repository contains a build gradle and git alias commands for building ADAL, MSAL, Authentication Broker, Common and test apps.  This project is intended for use by developers building and verifying integration primarily between ADAL, MSAL and the Android Authentication Broker.

## Pre-requisites

The android related auth projects pull artifacts from public and private package repositories.  The private artifacts are published using Azure DevOps.  You will need to generate
and store the credentials for the Identity and Aria azure devops instances.

- [Android DevX Dependency Feed](https://identitydivision.visualstudio.com/DevEx/_packaging?_a=feed&feed=AndroidADAL)
For this, you'll need a Personal Access Token (PAT) under IdentityDivision organization.
1. Go to https://identitydivision.visualstudio.com/_usersSettings/tokens
2. Select "New Token"
3. Select Organization -> IdentityDivision
4. Set the token expiration date as you see fit
5. Select Scopes -> Packaging Read

**Copy this token and save it.  It is your vstsMavenAccessToken, placed in gradle.properties below.**  It is used by your local gradle builds to access artifacts in the microsoft maven repository in visualstudio online.  If you see errors like "Could not HEAD 'https://identitydivision.pkgs.visualstudio.com/_packaging/AndroidADAL/maven/v1/com/microsoft/device/display/display-mask/0.3.0/display-mask-0.3.0.pom'. Received status code 401 from server: Unauthorized" then this token is not set up correctly.

- [Private GitHub Repositories](https://repos.opensource.microsoft.com/)
1. Go to https://repos.opensource.microsoft.com/. You'll need a github account.
2. Join 'AzureAD' organization (to get an access to Broker) via https://repos.opensource.microsoft.com/AzureAD/join
3. Join 'Microsoft' organization (to get an access to Authenticator app's submodule.) via https://repos.opensource.microsoft.com/Microsoft/join
4. Set up your github credential on your dev machine. 
    - You can [connect to github with ssh](https://help.github.com/en/github/authenticating-to-github/connecting-to-github-with-ssh). (recommended for OSX)
    - Alternatively, you can create a [Personal Access Token](https://help.github.com/en/github/authenticating-to-github/creating-a-personal-access-token-for-the-command-line) and use it as a password when prompted in command line.

Then add the following to your gradle properties (in your user folder on windows in the .gradle folder.  You may need to create this file: gradle.properties. Learn more about gradle configuration properties [here](https://docs.gradle.org/current/userguide/build_environment.html#sec:gradle_configuration_properties)) file using the token values from the generate credentials UI:

```gradle.properties
vstsUsername=VSTS 
vstsMavenAccessToken=[Insert a PAT for the Android DevX Feed here]
adoMsazureAuthAppAccessToken=[Insert a PAT for the Authenticator App Feed here] (Only needed if you set up Authenticator App Dependency Feed)
vstsOfficeMavenAccessToken=[Insert a PAT for Office Feed here]
```

>NOTE: By default, this global gradle.properties is located at
>1. ~/.gradle/gradle.properties (OSX)
>2. C:\Users\\<USER_NAME>\\.gradle\gradle.properties (Windows)
>
> (The folders could be hidden)

>NOTE: The sample configuration produced by Azure DevOps changed when the service was renamed from Visual Studio Online to Azure DevOps... the vstsUsername VSTS is still accepted.

## Install

1. Clone the repo
2. Run the following commands from within the repo to register the custom aliases and initiate the clone and setup for the Android projects/repositories

```bash
# Include the .gitconfig file included with project to your local gitconfig
git config --local include.path ../.gitconfig
# Run this newly minted command to clone each repo as a subfolder
git droidSetup
```

3. Open Android Studio and open project from the folder you cloned into (project: android_auth)
4. Update your build variants to point to use localDebug.  See more in the next section.
5. (OSX only) Install [Powershell](https://docs.microsoft.com/en-us/powershell/scripting/install/installing-powershell-core-on-macos?view=powershell-7.1)
6. Install [Lombok Plugin](https://plugins.jetbrains.com/plugin/6317-lombok) in Android Studio, see next section.

## Installing Lombok to Android Studio

Lombok plugin is usually loaded automatically. If not, install it by following the official guide: : https://projectlombok.org/setup/android

## Build Variants

All projects with the exception of "Common" and "MSAuthenticator" have local, dist and snapshot variants.  Where:

- local: Indicates that local dependencies and build configuration should be used.  
- snapshot: Indicates that nightly build artifacts and build configuration should be used.
- dist: Indicates that release dependencies and build configuration should be used.

The default build variants, cannot be configured via gradle, to the best of my knowledge.  As a result you'll need to configure them.  Generally you will want to set everything to:

localDebug

Where "local" is the name of the variant and "Debug" is the build type.

For MSAuthenticator, please use "devDebug" to test against PROD, and "integrationDebug" to test against INT.

## Projects Properties (Command Line Build flags)

We support a number of different project properties as command line flags across some of our modules. Please read the doc on [Gradle Project Properties](./docs/ProjectBuild/gradle_project_properties.md) to learn more about them.

## Usage - Custom git commands

Running droidSetup will clone ADAL, MSAL, Broker (AD Accounts) and Common into sub-folders.  Each of these folders is a separate git repo.
In order to help ease the management of changes to those repos the following custom git commands are provided for your convenience.  Please feel free to propose
additional commands and/or changes to this initial set.

A typical flow would include:

```bash
# Create a new feature branch in each repo
git droidNewFeature githubid-newfeature

# Make the changes for your feature/change

# Check status
git droidStatus

# If changes to common were made
# Push changes to common then run droidUpdateCommon
git droidUpdateCommon

# Push changes made to other repos

# On Github create PRs to integrate the feature branches

```
> NOTE: Open to adding support for droidPush and for opening PRs from the command line.

### droidUpdateCommon

This build places a shared common repo at the root of the global project.  In order to ensure that your checkin builds for ADAL, MSAL and broker are updated with the correct sub-module pointer the following command is provided to update the sub-modules to the matching revision.

```bat
git droidUpdateCommon
```

>NOTE: Your changes to common need to be committed and pushed to github in order for the sub-module update to succeed.

### droidStatus

Outputs the git status for each of the repos under the project

```bat
git droidStatus
```

### droidNewFeature

Creates a new feature with the specified name in each of the repositories

```bat
git droidNewFeature <nameofnewfeaturebranch>
```

### droidCheckout

Attempts to check out the specified branch in each repo

```bat
git droidCheckout <nameofbranchtocheckout>
```

### droidPull

Pulls changes from origin to local for each repository

```bat
git droidPull
```

### droidStash

Runs stash on each of the repositories...

```bat
git droidStash
git droidStash apply
git droidStash clear
```

# Contributing

This project welcomes contributions and suggestions.  Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit https://cla.microsoft.com.

When you submit a pull request, a CLA-bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., label, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.
