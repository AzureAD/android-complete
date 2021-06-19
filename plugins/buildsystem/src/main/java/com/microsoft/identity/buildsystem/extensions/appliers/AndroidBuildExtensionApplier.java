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
package com.microsoft.identity.buildsystem.extensions.appliers;

import com.android.build.gradle.LibraryExtension;
import com.microsoft.identity.buildsystem.constants.ProjectType;
import com.microsoft.identity.buildsystem.extensions.AndroidBuildExtension;
import com.microsoft.identity.buildsystem.extensions.JavaBuildExtension;

import org.gradle.api.Project;
import org.gradle.api.provider.Property;

import static com.microsoft.identity.buildsystem.constants.Constants.PluginIdentifiers.ANDROID_LIBRARY_PLUGIN_ID;

public class AndroidBuildExtensionApplier extends AbstractJavaBuildExtensionApplier<AndroidBuildExtension> {

    public AndroidBuildExtensionApplier() {
        super(ProjectType.ANDROID);
    }

    @Override
    public void applyBuildExtensionProperties(final Project project, final AndroidBuildExtension buildExtension) {
        super.applyBuildExtensionProperties(project, (JavaBuildExtension) buildExtension);
        project.afterEvaluate(evaluatedProject -> {
            final Property<Boolean> desugarProperty = buildExtension.getDesugar();

            if (desugarProperty != null && desugarProperty.isPresent()) {
                final boolean desugar = desugarProperty.get();

                applyDesugaringToAndroidProject(project, desugar);
            }
        });
    }

    private void applyDesugaringToAndroidProject(final Project project, final boolean desugar) {
        project.getPluginManager().withPlugin(ANDROID_LIBRARY_PLUGIN_ID, appliedPlugin -> {
            LibraryExtension libraryExtension = project.getExtensions().findByType(LibraryExtension.class);
            libraryExtension.getCompileOptions().setCoreLibraryDesugaringEnabled(desugar);
        });
    }
}
