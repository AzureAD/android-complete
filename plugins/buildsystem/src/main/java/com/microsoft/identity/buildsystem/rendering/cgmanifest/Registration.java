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
package com.microsoft.identity.buildsystem.rendering.cgmanifest;

import com.google.gson.annotations.SerializedName;

import java.util.Set;

import lombok.AllArgsConstructor;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.experimental.Accessors;

/**
 * Represents the Registration of a dependency in the {@link CgManifest}.
 * <p>
 * For more information, read the docs here: https://docs.opensource.microsoft.com/tools/cg/features/cgmanifest/
 */
@EqualsAndHashCode()
@AllArgsConstructor
@Getter
@Accessors(prefix = "m")
public class Registration {

    @SerializedName(SerializedNames.COMPONENT)
    private final Component mComponent;

    @SerializedName(SerializedNames.DEVELOPMENT_DEPENDENCY)
    private final boolean mDevelopmentDependency;

    @SerializedName(SerializedNames.DEPENDENCY_ROOTS)
    private final Set<Component> mDependencyRoots;

    private static class SerializedNames {
        private static final String COMPONENT = "Component";
        private static final String DEVELOPMENT_DEPENDENCY = "DevelopmentDependency";
        private static final String DEPENDENCY_ROOTS = "DependencyRoots";
    }
}
