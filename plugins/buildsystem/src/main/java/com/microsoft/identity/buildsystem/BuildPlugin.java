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
import com.microsoft.identity.buildsystem.rendering.ConsoleGradleDependencyRenderer;
import com.microsoft.identity.buildsystem.rendering.IMavenDependencyFormatter;
import com.microsoft.identity.buildsystem.rendering.SimpleMavenDependencyFormatter;
import com.microsoft.identity.buildsystem.rendering.cgmanifest.CGManifestGradleDependencyRenderer;
import com.microsoft.identity.buildsystem.rendering.settings.DependencyRendererSettingsAdapter;
import com.microsoft.identity.buildsystem.rendering.settings.DependencyRendererSettingsExtension;
import com.microsoft.identity.buildsystem.rendering.settings.GradleDependencyRendererSettings;
import com.microsoft.identity.buildsystem.rendering.settings.IDependencyRendererSettingsAdapter;

import com.microsoft.identity.buildsystem.codecov.CodeCoverage;
import org.gradle.api.JavaVersion;
import org.gradle.api.Plugin;
import org.gradle.api.Project;
import org.gradle.api.tasks.diagnostics.DependencyReportTask;

public class BuildPlugin implements Plugin<Project> {

    private final static String ANDROID_LIBRARY_PLUGIN_ID = "com.android.library";
    private final static String JAVA_LIBRARY_PLUGIN_ID = "java-library";

    private final static String JAVA_SOURCE_COMPATIBILITY_PROPERTY = "sourceCompatibility";
    private final static String JAVA_TARGET_COMPATIBILITY_PROPERTY = "targetCompatibility";

    @Override
    public void apply(final Project project) {

        final BuildPluginExtension config = project.getExtensions()
                .create("buildSystem", BuildPluginExtension.class);

        final DependencyRendererSettingsExtension dependencyRendererSettingsExtension =
                project.getExtensions().create("dependencyRendering", DependencyRendererSettingsExtension.class);

        project.afterEvaluate(evaluatedProject -> {
            if (config.getDesugar().get()) {
                evaluatedProject.getLogger().warn("DESUGARING ENABLED");
                applyDesugaringToAndroidProject(evaluatedProject);
                applyJava8ToJavaProject(evaluatedProject);
            } else {
                evaluatedProject.getLogger().warn("DESUGARING DISABLED");
            }

            final IDependencyRendererSettingsAdapter mDependencyRendererSettingsAdapter =
                    new DependencyRendererSettingsAdapter(evaluatedProject);

            final GradleDependencyRendererSettings gradleDependencyRendererSettings =
                    mDependencyRendererSettingsAdapter.adapt(dependencyRendererSettingsExtension);

            // generate gradle task to print dependencies to console
            final DependencyReportTask consoleTask = project.getTasks().create("printDependenciesToConsole", DependencyReportTask.class);
            final IMavenDependencyFormatter dependencyFormatter = new SimpleMavenDependencyFormatter();
            consoleTask.setRenderer(new ConsoleGradleDependencyRenderer(
                    gradleDependencyRendererSettings, dependencyFormatter
            ));

            // generate gradle task to create CG Manifest
            final DependencyReportTask cgManifestTask = project.getTasks().create("createDependenciesCgManifest", DependencyReportTask.class);
            cgManifestTask.setRenderer(new CGManifestGradleDependencyRenderer(gradleDependencyRendererSettings));
        });

        SpotBugs.applySpotBugsPlugin(project);

        CodeCoverage.applyCodeCoveragePlugin(project);
    }

    private void applyDesugaringToAndroidProject(final Project project) {

        project.getPluginManager().withPlugin(ANDROID_LIBRARY_PLUGIN_ID, appliedPlugin -> {
            LibraryExtension libraryExtension = project.getExtensions().findByType(LibraryExtension.class);
            libraryExtension.getCompileOptions().setSourceCompatibility(JavaVersion.VERSION_1_8);
            libraryExtension.getCompileOptions().setTargetCompatibility(JavaVersion.VERSION_1_8);
            libraryExtension.getCompileOptions().setCoreLibraryDesugaringEnabled(true);
        });

    }

    private void applyJava8ToJavaProject(final Project project) {
        project.getPluginManager().withPlugin(JAVA_LIBRARY_PLUGIN_ID, appliedPlugin -> {
            project.setProperty(JAVA_SOURCE_COMPATIBILITY_PROPERTY, JavaVersion.VERSION_1_8);
            project.setProperty(JAVA_TARGET_COMPATIBILITY_PROPERTY, JavaVersion.VERSION_1_8);
        });
    }
}
