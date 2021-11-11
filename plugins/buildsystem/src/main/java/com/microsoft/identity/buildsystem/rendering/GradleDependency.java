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

import java.util.Set;

import lombok.AllArgsConstructor;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NonNull;
import lombok.experimental.Accessors;

/**
 * Represents a Gradle Dependency.
 */
@AllArgsConstructor
@Getter
@Accessors(prefix = "m")
@EqualsAndHashCode
public class GradleDependency {
    private DependencyType mDependencyType;
    private final IMavenDependency mMavenDependency;
    private final Set<IMavenDependency> mDependencyRoots;

    /**
     * Add a root dependency to this gradle dependency.
     *
     * @param mavenDependency the {@link IMavenDependency} that needs to be added as a root this
     *                        gradle dependency
     */
    public void addRootDependency(@NonNull final IMavenDependency mavenDependency) {
        mDependencyRoots.add(mavenDependency);
    }

    /**
     * Set the dependency type of this gradle dependency.
     *
     * @param dependencyType the {@link DependencyType}
     */
    public void setDependencyType(@NonNull final DependencyType dependencyType) {
        mDependencyType = dependencyType;
    }

    @Override
    public String toString() {
        return "dep = " + mMavenDependency.toString() + " scope = " + mDependencyType.name()
                + " roots = " + mDependencyRoots.toString();

    }
}
