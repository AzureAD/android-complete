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
package com.microsoft.identity.buildsystem.java.version.setters;

import org.gradle.api.JavaVersion;
import org.gradle.api.Project;

import static com.microsoft.identity.buildsystem.constants.Constants.PluginIdentifiers.JAVA_LIBRARY_PLUGIN_ID;
import static com.microsoft.identity.buildsystem.constants.Constants.ProjectProperties.JAVA_SOURCE_COMPATIBILITY_PROPERTY;
import static com.microsoft.identity.buildsystem.constants.Constants.ProjectProperties.JAVA_TARGET_COMPATIBILITY_PROPERTY;

public class JavaProjectJavaVersionSetter implements IProjectJavaVersionSetter {

    @Override
    public void setJavaVersionOnProject(final Project project, final JavaVersion javaVersion) {
        project.getPluginManager().withPlugin(JAVA_LIBRARY_PLUGIN_ID, appliedPlugin -> {
            project.setProperty(JAVA_SOURCE_COMPATIBILITY_PROPERTY, javaVersion);
            project.setProperty(JAVA_TARGET_COMPATIBILITY_PROPERTY, javaVersion);
        });
    }
}
