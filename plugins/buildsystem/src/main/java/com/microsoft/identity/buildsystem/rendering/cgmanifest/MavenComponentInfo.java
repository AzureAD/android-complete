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

import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.experimental.Accessors;

/**
 * Provides information about a {@link MavenComponent}.
 * <p>
 * For more information, read the docs here: https://docs.opensource.microsoft.com/tools/cg/features/cgmanifest/
 */
@Getter
@AllArgsConstructor
@Accessors(prefix = "m")
public class MavenComponentInfo {

    @SerializedName(SerializedNames.GROUP_ID)
    private final String mGroupId;

    @SerializedName(SerializedNames.ARTIFACT_ID)
    private final String mArtifactId;

    @SerializedName(SerializedNames.VERSION)
    private final String mVersion;

    private static class SerializedNames {
        private static final String GROUP_ID = "GroupId";
        private static final String ARTIFACT_ID = "ArtifactId";
        private static final String VERSION = "Version";
    }
}
