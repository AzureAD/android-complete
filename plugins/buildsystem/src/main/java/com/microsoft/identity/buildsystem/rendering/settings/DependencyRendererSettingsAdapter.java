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
package com.microsoft.identity.buildsystem.rendering.settings;

import org.gradle.api.Project;
import org.gradle.api.provider.Property;

import java.io.File;

import javax.annotation.Nullable;

import lombok.AllArgsConstructor;

/**
 * An implementation of the {@link IDependencyRendererSettingsAdapter}.
 */
@AllArgsConstructor
public class DependencyRendererSettingsAdapter implements IDependencyRendererSettingsAdapter {

    private final Project mProject;

    @Override
    public GradleDependencyRendererSettings adapt(@Nullable final DependencyRendererSettingsExtension extension) {
        final GradleDependencyRendererSettings.GradleDependencyRendererSettingsBuilder builder =
                GradleDependencyRendererSettings.builder();

        if (extension == null) {
            return builder.build();
        }

        final Property<Boolean> renderProjectDependency = extension.getRenderProjectDependency();
        if (renderProjectDependency != null && renderProjectDependency.isPresent()) {
            builder.renderProjectDependency(renderProjectDependency.get());
        }

        final Property<Boolean> renderTransitiveDependencies = extension.getRenderTransitiveDependencies();
        if (renderTransitiveDependencies != null && renderTransitiveDependencies.isPresent()) {
            builder.renderTransitiveDependencies(renderTransitiveDependencies.get());
        }

        final Property<File> cgManifestReportDirectory = extension.getCgManifestReportDirectory();
        if (cgManifestReportDirectory != null && cgManifestReportDirectory.isPresent()) {
            builder.cgManifestReportDirectory(cgManifestReportDirectory.get());
        } else {
            // project.getBuildDir is the default if a File location is not project
            builder.cgManifestReportDirectory(mProject.getBuildDir());
        }

        return builder.build();
    }
}
