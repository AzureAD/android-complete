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

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonParser;
import com.microsoft.identity.buildsystem.rendering.AbstractGradleDependencyRenderer;
import com.microsoft.identity.buildsystem.rendering.DependencyType;
import com.microsoft.identity.buildsystem.rendering.GradleDependency;
import com.microsoft.identity.buildsystem.rendering.IMavenDependency;
import com.microsoft.identity.buildsystem.rendering.settings.GradleDependencyRendererSettings;

import org.gradle.api.Project;

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.io.PrintWriter;
import java.util.Collection;
import java.util.HashSet;
import java.util.Set;

import lombok.NonNull;

/**
 * An implementation of {@link AbstractGradleDependencyRenderer} to render dependencies in a
 * {@link CgManifest} format and also write them to the cgmanifest.json file.
 * <p>
 * The renderer will create the CG Manifest file in the location provided to the
 * {@link GradleDependencyRendererSettings} object.
 */
public class CGManifestGradleDependencyRenderer extends AbstractGradleDependencyRenderer {

    private static final String CG_MANIFEST_FILE_NAME = "cgmanifest.json";

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();
    private static final JsonParser JSON_PARSER = new JsonParser();

    private final IComponentAdapter mDependencyComponentAdapter = new ComponentAdapter();

    public CGManifestGradleDependencyRenderer(GradleDependencyRendererSettings gradleDependencyRendererSettings) {
        super(gradleDependencyRendererSettings);
    }

    @Override
    public void render(@NonNull GradleDependency gradleDependency) {
        // we could do something here...but let's just take the final result from the complete
        // method
    }

    @Override
    public void complete(@NonNull final Project project,
                         @NonNull Collection<GradleDependency> gradleDependencies) {
        // create CG Manifest object from these dependencies
        final CgManifest cgManifest = createCgManifest(gradleDependencies);

        // convert it to JSON representation
        final String cgManifestJson = GSON.toJson(cgManifest);

        // let's get a pretty representation of it
        final JsonElement cgManifestJsonElement = JSON_PARSER.parse(cgManifestJson);

        // and now convert to JSON again so that the string is in the pretty format
        final String cgManifestPrettyJson = GSON.toJson(cgManifestJsonElement);

        // log the JSON to console
        System.out.println(cgManifestPrettyJson);

        // and also dump it into the cgmanifest file i.e. cgmanifest.json
        dumpToCgManifestJsonFile(
                mGradleDependencyRendererSettings.getCgManifestReportDirectory(), cgManifestPrettyJson
        );
    }

    @Override
    public void completeProject(Project project) {
        super.completeProject(project);
    }

    private CgManifest createCgManifest(@NonNull final Collection<GradleDependency> gradleDependencies) {
        final CgManifest cgManifest = new CgManifest();

        // iterate over all the gradle dependencies that we have received
        for (final GradleDependency gradleDependency : gradleDependencies) {
            final IMavenDependency mavenDependency = gradleDependency.getMavenDependency();
            final DependencyType dependencyType = gradleDependency.getDependencyType();
            final Set<IMavenDependency> rootDeps = gradleDependency.getDependencyRoots();

            // take the maven dependency that we got and convert that into a MavenComponent
            // MavenComponent is type that we need for the CG Manifest
            final MavenComponent mavenComponent = mDependencyComponentAdapter.adapt(
                    mavenDependency
            );

            final Set<Component> rootComponents = new HashSet<>();

            // for each root dependency of the dependency we received..we would also convert these
            // to a CG Manifest Maven Component and add it as the root components
            rootDeps.iterator().forEachRemaining(mavenDep -> rootComponents.add(mDependencyComponentAdapter.adapt(mavenDep)));

            // Create a Registration object out of the data we have for this dependency and add it
            // to the CG Manifest. Each Dependency object essentially translates to a Registration
            // object in the CG Manifest.
            //
            // Just look at the Registration.java class to understand what all is in there
            // or just read the docs here: https://docs.opensource.microsoft.com/tools/cg/features/cgmanifest/
            cgManifest.addRegistration(new Registration(
                    mavenComponent,
                    dependencyType == DependencyType.DEVELOPMENT,
                    rootComponents
            ));
        }

        return cgManifest;
    }

    private void dumpToCgManifestJsonFile(@NonNull final File rootDir, @NonNull final String text) {
        try {
            final File cgManifestFile = new File(rootDir, CG_MANIFEST_FILE_NAME);
            System.out.println("Writing cg manifest to file: " + cgManifestFile.getAbsolutePath());
            final FileWriter fileWriter = new FileWriter(cgManifestFile);
            PrintWriter printWriter = new PrintWriter(fileWriter);
            printWriter.print(text);
            printWriter.close();
        } catch (final IOException e) {
            throw new RuntimeException(e);
        }
    }
}
