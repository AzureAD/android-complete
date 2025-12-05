# Introduction 

This document contains the test strategy for Android: MSAL, ADAL and Broker Libraries.  This strategy focuses first and foremost on functionality of the primary code paths.  Later sections of the document speak to other dimensions of testing including:

- Device compatibility
- Android Version (API Level) Test Coverage
- Performance
- User experience



## Test Types

* Unit tests
* Instrumented Tests
* UI Automation / End to End Scenario Tests



## Tested Code Path Selection Factors

The code path that is exercised is based on on the configuration of the device before the test is executed.  The following are the factors impacting which code paths are executed:

- Broker Installation Status - The Android Broker Library (Ad-Accounts) ships as part of the Intune Company Portal and the Microsoft Authenticator App.  If the broker is not installed then all operations will happen within either ADAL or MSAL within the application under test.

> NOTE: Assumption here is we always have the latest broker on a device.  This is generally true for most of our devices (eventually; however IP Phones may be a different case)

- Account Issuer/Account Type (MSAL Only) - There are 2 account types, issuers, home tenants:
  - Microsoft account (consumer identity) - AKA live id, AKA xbox live id, etc...
  - Organizations (AAD or Active Directory)
  - The Android Broker Library only supports adding "work accounts" to the broker library cache.  This means accounts used in an organization context.  If the authority specified by the client application is: https://login.microsoftonline.com/consumers then broker installation status does not matter and the request will not leave the local application.  If the client applications uses the tenant alias "common" or "organizations" then the broker will determine if the account is a Microsoft account based on the home tenant id of the account (Microsoft account tenant - identified by well known tenant id).  If a Microsoft account the authentication result from the broker will be returned to the client application.  If the client application uses a specific tenant id in their authority then if the account is a member (guest/member) of that tenant will determine if a Microsoft account is stored in the cache or not.
- Account Type (ADAL Only) - ADAL only supports organizational accounts.  Microsoft accounts can be used to get tokens with ADAL; however not for consumer resources... only for organizational resources.  (Using a tenant id in the authority)  
  - NOTE: I don't recall the exact semantics for "pass-through" which is able to use the common authority.  
- Account Status (Broker only - accounts used in organization context only) - The account status is related to the how the account was "added" to the broker and what type of "credential" is associated with the account.  The following are current account statuses (NOTE: This naming is historical and potentially not helpful):
  - Joined - Account has a Primary Refresh Token which is derived from device registration (work place joined - hence "joined account").  A primary refresh token supports device wide SSO.  All clients applications that have been provided with consent to use the account may be able to sign in with it without requiring user interaction.  (Depends on policy)
  - Non-joined - Account has a regular regular refresh token and is not "joined" to a specific device.
- Future Account Statuses (Broker only - all accounts):
  - Deviceless PRT - Non-Joined (Includes organizational accounts and Microsoft accounts) - A deviceless PRT is not associated with a device credential and does not include device claims.  All applications provided with consent, may use the PRT to get single sign on.
  - Device derived PRT - Joined (Organizational account or Microsoft account members of an organization) - PRT is associated with a device credential and includes device claims.  All applications provided with consent, may use the PRT to get single sign on.
- Authorization method (user agent) (MSAL only for now) include:
  - Embedded Web view
  - Custom Tabs
  - Browser
  - NOTE: Authorization method used is based on MSAL configuration and device state.  MSAL will attempt to use the configured option first and then fall back based on algorithm attempting to provide the best opportunity for SSO.
- Shared Device Mode (MSAL Only) - Devices can be configured as shared devices or not.  Shared devices are typically owned and enrolled in device management by organizations.  They are placed in shared mode to allow multiple users to share 1 physical device and to allow users to switch between accounts the device easily.
- Library Version - Different library versions use different IPC methods for communicating with the broker.  Bound Services (2: 1 for MSAL, 1 for ADAL), Content Provider, Account Manager
- Single vs Multi-Account Mode (MSAL Only) - Client applications can be setup to use a single account or multiple accounts.  This overlaps a bit with shared device mode which is single account by default. 
-  Authority Validation (ADAL), Known Authorities(MSAL) - Known authorities is configured by the developer for developer known authorities.  Known authorities listed in the configuration are combined with authorities known to Microsoft as discovered from the Cloud Alias Instance Discovery metadata document from ESTS. Both of these features are designed to keep an app from sending a use to an authorization server that is not known to Microsoft or is not known to the developer.  

> NOTE: Authority validation and known authorities are designed to prevent a malicious resource server from sending a dumb client to a malicious authorization server where they can collect a account username/password and potentially trick them into completing authentication/authorization for the purposes of impersonation/elevation of privilege.

## Features & Possible Outcomes

### Construct Feature

#### Applicable Factors:

- Broker Installation
- Shared Device Mode (MSAL Only)
- Single Account vs. Multi-account mode (MSAL Only)
- Library version

#### Internal Operation

- Validate redirect URI matches package signature
- If authorization agent is "Default" validate that manifest is configured correctly to receive authorization result

#### Operations

> NOTE: ADAL and MSAL have very different object models for developers to interact with.  ADAL models the issuer and you create an AuthenticationContext which you can think of as the issuer/Authority.   MSAL models the public client application itself (client of the issuer).  The primary difference in terms of flows is that you generally have 1 PCA instance per piece of client code, where as with ADAL you have an instance of AuthenticationContext for every authority that you request a token from.  In the case of both MSAL and ADAL there is a single cache with data cached based on the universal cache schema.

Create/New - create a new instance of AuthenticationContext or PublicClientApplication.  In the case of MSAL a static factory methods can be used to construct the PCA.  If the user has configured single account public client application then only that application type can be constructed.  If they've selected multi account PCA, but are also configured to support shared device mode then they can use a static constructor that will return single account PCA when shared device mode is enable and multiple account PCA when not.

> NOTE: Broker must be installed in order for the client app to make a request to broker to determine if in shared mode.

### Account Feature

#### Applicable Factors:

- Broker Installation Status
- Account Issuer/Type
- Library Version
- Single Account vs Multi-account mode (MSAL Only)

#### Internal Operations

- Is broker installed
- Select broker IPC method
- Invoke Broker - Is device in shared mode

#### Operations

> NOTE: ADAL does not have account enumeration restriction.  MSAL restricts accounts enumeration/usage to apps that have been granted consent to use that account.  For 3P apps consent is implied via account selection UI.  For 1P apps all are considered consented based on Microsoft terms of use.

> NOTE: Single account public client application has a special method to get the current account, rather than enumerate all accounts.

- GetAccounts (MSAL) / GetUsers (ADAL) - Enumerate all accounts currently available to the client app.  If the broker is not installed then the request is to the client local cache.  If the broker is installed then account returned from the broker are unioned with accounts in the local cache.  
- GetAccount (MSAL) / GetUser (ADAL) - Get a specific account from from the library OR broker if installed.  Will check the local cache for the specified account by ID (upn/object id, etc...).  If not in the local cache will also check the broker.  
- Tenant Profiles (MSAL Only) for MSAL accounts we allow the user to access tenant profiles.  These are tenant specific claims from the id_tokens associated with requests to different tenants.  
- GetCurrentAccount (MSAL Single Account PCA Only) - Gets the current account from the local cache when the broker is not installed.  Get the current account from the broker when installed.  The account returned may be different (different account or null) for the current account.  Null meaning that no account should be signed in currently.  A different account meaning that

### Acquire Token Interactive

#### Applicable Factors:

* Broker installation status
* Account Issuer/Type
* Library version
* Known authorities
* Authorization Method



#### Internal Operations

* Select authorization strategy - Return the correct authorization strategy based on configured preferred authorization method.  Fallback to supported authorization strategy with the best chance of experiencing SSO.  
* ValidateAuthority - Verify that authority is a known authority
* Select broker IPC method
* Invoke Broker - getIntentForAcquireToken - The method used to invoked the broker varies based on the library version.

