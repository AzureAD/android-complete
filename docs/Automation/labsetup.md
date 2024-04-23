# Lab Setup for running Automation

## Introduction

Lab API is an API that allows fetching test user accounts and their passwords that can be used in the context of either manual testing or automated testing. To learn more about the LAB API, please visit: https://aka.ms/idlabs

Android MSAL / Broker End-to-End tests that hit the network use the Lab API provided by the lab team to obtain test accounts. We have generated a swagger client for the lab api by using the json file that they provided. Link to lab api swagger: https://msidlab.com/swagger/index.html

The code for this swagger client is located here: https://github.com/AzureAD/microsoft-authentication-library-common-for-android/tree/dev/labapi

## How to get access to LAB API

Please visit this URL to learn the process of getting access to the LAB API: https://docs.msidlab.com/labinfo/labaccess.html

## Authentication

The Lab Api is a protected Api and we need an access token to be able to make request against the Lab Api. Since we use the Lab Api as part of our automated tests, we need to programmatically access the Lab Api as an application (without any context of a user) and thus we need to make a client credentials grant request to the ESTS to obtain an access token for our application that has been given permission to the use the Lab Api. You can find the details about programmatic access here: https://docs.msidlab.com/labapi/intro.html?q=client

**NOTE:** Please note that you must be a Microsoft employee and part of the relevant groups to be able to acquire credentials that are necessary for authenticating against the LAB API. In other words, the LAB API and the tests that utilize it are only going to run successfully if supplied with required lab credentials.

### Making client credential grant request with secret

**How to get access to the KeyVault where Client Secret is stored (You MAY skip this if already have access):**

1. Go to https://coreidentity.microsoft.com/
2. Request membership from one of the following groups:
    - If a member of the Identity org, request read-write permissions for entitlement **TM-MSIDLabs-Int**
    - If outside the Identity org, request read access to entitlement **TM-MSIDLABS-DevKV**
3. You can also reach out to *msidlabint@microsoft.com* to ask them for a rushed approval
4. After access has been approved, wait for 2-24 hours for changes to be effective.

**How to get the secret for accessing Lab Api:**

1. Go to Azure Portal: https://portal.azure.com/ and login with MS credentials
2. Switch to the Microsoft directory (if not already there)
3. Search for the KeyVault named "**MSIDLABS**" (be sure to select "all" for the subcription, location etc filters) 
4. Click into the **MSIDLABS** keyvault
5. Under settings, click on secrets
6. The secret we are looking for is called **LabVaultAppSecret**
7. Get the value for this secret and copy to clipboard

**How to run the tests with this secret:**

To run tests with client secret, you would have to pass the following command line parameters when building/running tests.
`-PlabSecret="<secret-value>"`

**Example test execution**

`./gradlew app:test -PlabSecret="<secret-value>"`

**From within Android Studio**

This can also be achieved from within Android Studio as follows:

![Android Studio Command Line Parameters](images/android_studio_cmd_params.png "Android Studio Command Line Parameters")
