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
package com.microsoft.identity.buildsystem;

import com.android.build.gradle.LibraryExtension;
import com.microsoft.identity.buildsystem.constants.Constants;
import com.microsoft.identity.buildsystem.extensions.AndroidBuildExtension;
import com.microsoft.identity.buildsystem.extensions.BuildPluginExtension;
import com.microsoft.identity.buildsystem.extensions.JavaBuildExtension;
import com.microsoft.identity.buildsystem.extensions.appliers.AndroidBuildExtensionApplier;
import com.microsoft.identity.buildsystem.extensions.appliers.JavaBuildExtensionApplier;
import com.microsoft.identity.buildsystem.spotbugs.SpotBugs;

import org.gradle.api.JavaVersion;
import org.gradle.api.Plugin;
import org.gradle.api.Project;
import org.gradle.api.provider.Property;

import static com.microsoft.identity.buildsystem.constants.Constants.PluginIdentifiers.ANDROID_LIBRARY_PLUGIN_ID;
import static com.microsoft.identity.buildsystem.constants.Constants.PluginIdentifiers.JAVA_LIBRARY_PLUGIN_ID;
import static com.microsoft.identity.buildsystem.constants.Constants.ProjectProperties.JAVA_SOURCE_COMPATIBILITY_PROPERTY;
import static com.microsoft.identity.buildsystem.constants.Constants.ProjectProperties.JAVA_TARGET_COMPATIBILITY_PROPERTY;

public class BuildPlugin implements Plugin<Project> {

    @Override
    public void apply(final Project project) {
        project.getPluginManager().withPlugin(ANDROID_LIBRARY_PLUGIN_ID, appliedPlugin -> {
            final AndroidBuildExtension androidConfig = project.getExtensions()
                    .create(Constants.ExtensionNames.ANDROID_BUILD_EXTENSION, AndroidBuildExtension.class);

            new AndroidBuildExtensionApplier().applyBuildExtensionProperties(project, androidConfig);
        });

        project.getPluginManager().withPlugin(JAVA_LIBRARY_PLUGIN_ID, appliedPlugin -> {
            final JavaBuildExtension javaConfig = project.getExtensions()
                    .create(Constants.ExtensionNames.JAVA_BUILD_EXTENSION, JavaBuildExtension.class);

            new JavaBuildExtensionApplier().applyBuildExtensionProperties(project, javaConfig);
        });

        applyBuildSystemConfig(project);

        SpotBugs.applySpotBugsPlugin(project);
    }

    @Deprecated
    private void applyBuildSystemConfig(final Project project) {
        final BuildPluginExtension config = project.getExtensions()
                .create("buildSystem", BuildPluginExtension.class);

        project.afterEvaluate(project1 -> {
            final Property<Boolean> desugarProperty = config.getDesugar();

            if (desugarProperty != null && desugarProperty.isPresent()) {
                final boolean desugar = desugarProperty.get();
                if (desugar) {
                    project1.getLogger().warn("DESUGARING ENABLED");
                    applyDesugaringToAndroidProject(project1);
                    applyJava8ToJavaProject(project1);
                } else {
                    project1.getLogger().warn("DESUGARING DISABLED");
                }
            }
        });
    }

    @Deprecated
    private void applyDesugaringToAndroidProject(final Project project) {
        project.getPluginManager().withPlugin(ANDROID_LIBRARY_PLUGIN_ID, appliedPlugin -> {
            LibraryExtension libraryExtension = project.getExtensions().findByType(LibraryExtension.class);
            libraryExtension.getCompileOptions().setSourceCompatibility(JavaVersion.VERSION_1_8);
            libraryExtension.getCompileOptions().setTargetCompatibility(JavaVersion.VERSION_1_8);
            libraryExtension.getCompileOptions().setCoreLibraryDesugaringEnabled(true);
        });

    }

    @Deprecated
    private void applyJava8ToJavaProject(final Project project) {
        project.getPluginManager().withPlugin(JAVA_LIBRARY_PLUGIN_ID, appliedPlugin -> {
            project.setProperty(JAVA_SOURCE_COMPATIBILITY_PROPERTY, JavaVersion.VERSION_1_8);
            project.setProperty(JAVA_TARGET_COMPATIBILITY_PROPERTY, JavaVersion.VERSION_1_8);
        });
    }
}
