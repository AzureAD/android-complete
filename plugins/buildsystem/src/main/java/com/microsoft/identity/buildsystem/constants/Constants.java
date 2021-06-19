//  Copyright (c) Microsoft Corporation.
//  All rights reserved.
//
//  This code is licensed under the MIT License.
//
//  Permission is hereby granted, free of charge, to any person obtaining a copy
//  of this software and associated documentation files(the "Software"), to deal
//  in the Software without restriction, including without limitation the rights
//  to use, copy, modify, merge, publish, distribute, sublicense, and / or sell
//  copies of the Software, and to permit persons to whom the Software is
//  furnished to do so, subject to the following conditions :
//
//  The above copyright notice and this permission notice shall be included in
//  all copies or substantial portions of the Software.
//
//  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
//  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
//  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
//  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
//  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
//  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
//  THE SOFTWARE.
package com.microsoft.identity.buildsystem.constants;

public class Constants {

    public static class PluginIdentifiers {
        public final static String ANDROID_LIBRARY_PLUGIN_ID = "com.android.library";
        public final static String JAVA_LIBRARY_PLUGIN_ID = "java-library";
        public final static String BUILD_SYSTEM_PLUGIN_ID = "com.microsoft.identity.buildsystem";
    }

    public static class ProjectProperties {
        public final static String JAVA_SOURCE_COMPATIBILITY_PROPERTY = "sourceCompatibility";
        public final static String JAVA_TARGET_COMPATIBILITY_PROPERTY = "targetCompatibility";
    }

    public static class ExtensionNames {
        public final static String JAVA_BUILD_EXTENSION = "javaBuild";
        public final static String ANDROID_BUILD_EXTENSION = "androidBuild";
    }
}
