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

import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NonNull;
import lombok.experimental.Accessors;

/**
 * Represents a Maven Component i.e. a {@link Component} of the Maven type in a {@link Registration}
 * in the CG Manifest.
 * <p>
 * For more information, read the docs here: https://docs.opensource.microsoft.com/tools/cg/features/cgmanifest/
 */
@EqualsAndHashCode(callSuper = true)
@Getter
@Accessors(prefix = "m")
public class MavenComponent extends Component {

    private static final String MAVEN_COMPONENT_TYPE_NAME = "Maven";

    @SerializedName(MAVEN_COMPONENT_TYPE_NAME)
    private final MavenComponentInfo mMavenComponentInfo;

    public MavenComponent(@NonNull final MavenComponentInfo mavenComponentInfo) {
        super(MAVEN_COMPONENT_TYPE_NAME);
        this.mMavenComponentInfo = mavenComponentInfo;
    }
}
