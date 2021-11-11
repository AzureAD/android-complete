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

/**
 * An adapter to convert different types of internal Gradle dependency objects into a customized
 * {@link IMavenDependency} type.
 */
public interface IMavenDependencyAdapter {

    /**
     * Convert a {@link Dependency} into a {@link IMavenDependency}.
     *
     * @param dependency the {@link Dependency} to convert
     * @return an {@link IMavenDependency} representation of the provided dep
     */
    IMavenDependency adapt(Dependency dependency);

    /**
     * Convert a {@link DependencyResult} into a {@link IMavenDependency}.
     *
     * @param dependencyResult the {@link DependencyResult} to convert
     * @return an {@link IMavenDependency} representation of the provided dep
     */
    IMavenDependency adapt(DependencyResult dependencyResult);

    /**
     * Convert a {@link ModuleVersionIdentifier} into a {@link IMavenDependency}.
     *
     * @param moduleVersionIdentifier the {@link ModuleVersionIdentifier} to convert
     * @return an {@link IMavenDependency} representation of the provided dep
     */
    IMavenDependency adapt(ModuleVersionIdentifier moduleVersionIdentifier);
}
