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
package com.microsoft.identity.buildsystem.rendering;

import com.microsoft.identity.buildsystem.rendering.settings.GradleDependencyRendererSettings;

import org.gradle.api.Project;

import java.util.Collection;

import lombok.NonNull;

/**
 * An implementation of {@link AbstractGradleDependencyRenderer} that will use the provided
 * {@link IMavenDependencyFormatter} to format dependency into a String and render them to console.
 */
public class ConsoleGradleDependencyRenderer extends AbstractGradleDependencyRenderer {

    private final IMavenDependencyFormatter mDependencyFormatter;

    public ConsoleGradleDependencyRenderer(@NonNull final GradleDependencyRendererSettings gradleDependencyRendererSettings,
                                           @NonNull final IMavenDependencyFormatter dependencyFormatter) {
        super(gradleDependencyRendererSettings);
        mDependencyFormatter = dependencyFormatter;
    }

    @Override
    public void render(@NonNull final GradleDependency gradleDependency) {
        render(mDependencyFormatter.formatDependency(gradleDependency.getMavenDependency()));
    }

    @Override
    public void complete(@NonNull final Project project,
                         @NonNull final Collection<GradleDependency> gradleDependencies) {
        // don't do anything
        System.out.println("Rendering all now..");
        gradleDependencies.iterator().forEachRemaining(gradleDependency ->
                System.out.println(gradleDependency.toString())
        );
    }

    private void render(@NonNull final String formattedDependency) {
        System.out.println(formattedDependency);
    }
}
