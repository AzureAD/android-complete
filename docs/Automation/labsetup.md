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

### Making client credential grant request With Client Assertion (Certificate)

**How to get access to the KeyVault where Client Certificate is stored (You MAY skip this if already have access):**

1. Go to https://coreidentity.microsoft.com/
2. Request membership from one of the following groups:
   - If a member of the Identity org, request read-write permissions for entitlement **TM-MSIDLabs-Int**
   - If outside the Identity org, request read access to entitlement **TM-MSIDLABS-DevKV**
3. You can also reach out to *msidlabint@microsoft.com* to ask them for a rushed approval
4. After access has been approved, wait for 2-24 hours for changes to be effective.

Client assertion uses a certificate, therefore we need to have a certificate installed on our local machine for us to be able to successfully create a client_assertion and be able to obtain the access token for the Lab Api. 
This certificate is stored securely in a KeyVault that all Microsoft employees can get access to, and from there they can download it onto their local machines. 
Read through the steps below to obtain the certificate required for the lab automation.

**How to get the certificate for accessing Lab Api:**

1. Go to Azure Portal: https://portal.azure.com/ and login with MS credentials
2. Switch to the Microsoft directory (if not already there)
3. Search for the KeyVault named "**MSIDLABS**" (be sure to select "all" for the subcription, location etc filters)
4. Click into the **MSIDLABS** keyvault
5. Under Objects, click on certificates
6. Click on the **LabAuth** to open the cert in detail view
7. Click on the current version to view details about the current version
8. Download it on your local machine in the PFX format
9. After download, double click on the cert file to install on your machine
10. When prompted for location of installation, select CurrentUser and for certificate store select Personal
11. If prompted for password, you can optionally enter one but it is not required.
12. Proceed through the steps to finish the installation.

**How to run the tests with this certificate:**

To run tests with certificate, you would have to pass the following command line parameters when building/running tests.
`-PlabSecret="<path-to-cert-pfx-file>"`

**Example test execution**

If running UI Automation on the Android device push the certificate pfx file to the device before running the tests:
`./gradlew msalautomationapp:connectedLocalBrokerHostDebugAndroidTest -PlabSecret="<path-to-cert-pfx-file-on-device>"`

If running JVM tests, you can install the certificate on the machine and then run the tests without passing specific build flag:
`./gradlew :common:testLocalDebugUnitTest

**From within Android Studio**

This can also be achieved from within Android Studio as follows:

![Android Studio Command Line Parameters](images/android_studio_cmd_params.png "Android Studio Command Line Parameters")

