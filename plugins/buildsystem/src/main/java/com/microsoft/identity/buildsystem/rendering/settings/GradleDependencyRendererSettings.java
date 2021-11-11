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

import java.io.File;

import lombok.Builder;
import lombok.Getter;
import lombok.NonNull;
import lombok.experimental.Accessors;

/**
 * Represents the settings that control some of the rendering of the dependencies in the
 * {@link com.microsoft.identity.buildsystem.rendering.AbstractGradleDependencyRenderer}.
 */
@Builder
@Getter
@Accessors(prefix = "m")
public class GradleDependencyRendererSettings {

    /**
     * Indicates whether project dependencies should be rendered.
     * <p>
     * These are basically when we do things like "implementation project(":projectName")"
     */
    @Builder.Default
    private final boolean mRenderProjectDependency = false;

    /**
     * Indicates whether transitive dependencies of a dependency should be rendered.
     */
    @Builder.Default
    private final boolean mRenderTransitiveDependencies = true;

    /**
     * Indicates the directory on the machine where the CG Manifest should be created.
     */
    @NonNull
    private final File mCgManifestReportDirectory;
}
