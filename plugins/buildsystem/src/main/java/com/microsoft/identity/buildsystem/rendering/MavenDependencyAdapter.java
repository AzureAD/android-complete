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

import org.gradle.api.artifacts.Dependency;
import org.gradle.api.artifacts.ModuleVersionIdentifier;
import org.gradle.api.artifacts.result.DependencyResult;
import org.gradle.api.artifacts.result.ResolvedDependencyResult;

import lombok.NonNull;

public class MavenDependencyAdapter implements IMavenDependencyAdapter {
    @Override
    public IMavenDependency adapt(@NonNull final Dependency dependency) {
        final String group = dependency.getGroup();
        final String name = dependency.getName();
        final String version = dependency.getVersion();

        return new MavenDependency(group, name, version);
    }

    @Override
    public IMavenDependency adapt(DependencyResult dependencyResult) {
        if (dependencyResult instanceof ResolvedDependencyResult) {
            return adapt((ResolvedDependencyResult) dependencyResult);
        } else {
            return null;
        }
    }

    private IMavenDependency adapt(ResolvedDependencyResult resolvedDependencyResult) {
        final ModuleVersionIdentifier selectedModuleVersion = resolvedDependencyResult.getSelected().getModuleVersion();

        if (selectedModuleVersion == null) {
            return null;
        }

        final String group = selectedModuleVersion.getGroup();
        final String name = selectedModuleVersion.getName();
        final String version = selectedModuleVersion.getVersion();

        return new MavenDependency(group, name, version);
    }
}
