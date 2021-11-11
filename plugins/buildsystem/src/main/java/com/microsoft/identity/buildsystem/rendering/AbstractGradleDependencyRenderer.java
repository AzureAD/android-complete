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
import org.gradle.api.artifacts.Configuration;
import org.gradle.api.artifacts.Dependency;
import org.gradle.api.artifacts.DependencySet;
import org.gradle.api.artifacts.ProjectDependency;
import org.gradle.api.artifacts.component.ProjectComponentIdentifier;
import org.gradle.api.artifacts.result.DependencyResult;
import org.gradle.api.artifacts.result.ResolutionResult;
import org.gradle.api.artifacts.result.ResolvedComponentResult;
import org.gradle.api.tasks.diagnostics.internal.DependencyReportRenderer;
import org.gradle.api.tasks.diagnostics.internal.TextReportRenderer;
import org.gradle.internal.deprecation.DeprecatableConfiguration;
import org.jetbrains.annotations.Nullable;

import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

import lombok.AllArgsConstructor;
import lombok.NonNull;
import lombok.experimental.Accessors;

/**
 * An abstract implementation of {@link DependencyReportRenderer} that uses the settings provided by
 * the {@link GradleDependencyRendererSettings} and renders the dependencies as specified by the
 * implementation of the {@link AbstractGradleDependencyRenderer#render(GradleDependency)} and
 * {@link AbstractGradleDependencyRenderer#complete(Project, Collection)} methods.
 */
@AllArgsConstructor
@Accessors(prefix = "m")
public abstract class AbstractGradleDependencyRenderer extends TextReportRenderer implements DependencyReportRenderer {

    /**
     * Settings that govern some of the behavior while rendering the dependencies.
     * <p>
     * We are making this field available to children of this class so they can also take action on
     * these settings as applicable.
     */
    protected final GradleDependencyRendererSettings mGradleDependencyRendererSettings;

    private static final IMavenDependencyAdapter sMavenDependencyAdapter = new MavenDependencyAdapter();

    private static final IDependencyTypeAdapter sConfigurationAdapter = new DependencyTypeAdapter();

    /**
     * Renders a {@link GradleDependency} somewhere as indicated by the implementation of this
     * method.
     *
     * @param gradleDependency the {@link GradleDependency} to render
     */
    public abstract void render(@NonNull final GradleDependency gradleDependency);

    /**
     * Complete the rendering of all the {@link GradleDependency} dependencies in a {@link Project}.
     *
     * @param project            the {@link Project} for which to complete the rendering
     * @param gradleDependencies the list of {@link GradleDependency} in this project
     */
    public abstract void complete(@NonNull final Project project,
                                  @NonNull final Collection<GradleDependency> gradleDependencies);

    /**
     * Keeps track of which dependencies have already been rendered.
     */
    private final Map<String, GradleDependency> mRenderedDepMap = new HashMap<>();

    @Override
    public void startConfiguration(Configuration configuration) {
        // We don't need to do anything here by default
        // but let's just print configuration name
        System.out.println("Starting configuration: " + configuration.getName());
    }

    @Override
    public void completeProject(Project project) {
        // let's just call our own complete method here and delegate the actual work to that
        // method's implementation
        complete(project, mRenderedDepMap.values());
    }


    @Override
    public void render(Configuration configuration) {
        // If we want to render transitive dependencies and we can resolve them then let's render
        // them
        if (mGradleDependencyRendererSettings.isRenderTransitiveDependencies() && canBeResolved(configuration)) {
            // First resolve the configuration
            final ResolutionResult result = configuration.getIncoming().getResolutionResult();
            // now render all dependencies in this configuration
            result.allDependencies(dependencyResult -> renderDependencyResult(configuration, dependencyResult));
        } else {
            // The configuration couldn't be resolved..let's just render the direct dependencies
            System.out.println("Unable to resolve configuration: " + configuration.getName());
            final DependencySet dependencies = configuration.getDependencies();
            renderDependencySet(configuration, dependencies);
        }
    }

    @Override
    public void completeConfiguration(Configuration configuration) {
        // We don't need to do anything here by default
    }

    /**
     * Render each dependency in a Dependency Set.
     *
     * @param configuration the {@link Configuration} for which we are rendering dependencies
     * @param dependencies  the {@link DependencySet} that contains a set of dependencies
     */
    private void renderDependencySet(@NonNull final Configuration configuration,
                                     @NonNull final DependencySet dependencies) {
        dependencies.iterator().forEachRemaining(
                dependency -> renderDependency(configuration, dependency)
        );
    }

    /**
     * Render the dependency represented by a {@link DependencyResult} object.
     *
     * @param configuration    the {@link Configuration} for which we are rendering the dependency
     * @param dependencyResult the {@link DependencyResult} that needs to be rendered
     */
    private void renderDependencyResult(@NonNull final Configuration configuration,
                                        @NonNull final DependencyResult dependencyResult) {
        // first convert the internal dependency result into our custom IMavenDependency object
        final IMavenDependency depToRender = sMavenDependencyAdapter.adapt(dependencyResult);

        // Get the root dependency that brought in this dependency
        final ResolvedComponentResult rootResult = dependencyResult.getFrom();

        // If the dependency is not null then we can render it
        if (depToRender != null) {
            IMavenDependency depRoot;

            // if the root is a Project and we've decided to NOT to render Projects, then let's skip
            if (rootResult.getId() instanceof ProjectComponentIdentifier && !mGradleDependencyRendererSettings.isRenderProjectDependency()) {
                depRoot = null;
            } else {
                // the root was NOT a project so we should proceed to render it anyway
                // take the root and convert that into our own custom IMavenDependency object
                depRoot = sMavenDependencyAdapter.adapt(
                        rootResult.getModuleVersion()
                );
            }

            // Now render the dependency that we received
            renderInternal(configuration, depToRender, depRoot);
        } else {
            System.out.println("Weird. The dependency was null for " + dependencyResult.toString());
        }
    }

    /**
     * Render the dependency represented by a {@link Dependency} object.
     *
     * @param configuration the {@link Configuration} for which we are rendering the dependency
     * @param dependency    the {@link Dependency} that needs to be rendered
     */
    private void renderDependency(@NonNull final Configuration configuration,
                                  @NonNull final Dependency dependency) {
        if (shouldRender(dependency)) {
            renderInternal(configuration, sMavenDependencyAdapter.adapt(dependency), null);
        }
    }

    /**
     * Determines if we should render a dependency represented by the {@link Dependency} object.
     *
     * @param dependency the {@link Dependency} for which we need to decide if we want to render it
     * @return a boolean that indicates whether the dependency should be rendered
     */
    private boolean shouldRender(@NonNull final Dependency dependency) {
        return !(dependency instanceof ProjectDependency) ||
                mGradleDependencyRendererSettings.isRenderProjectDependency();
    }

    /**
     * Determines whether a gradle dependency configuration can be resolved.
     *
     * @param configuration the {@link Configuration} for which we need to decide if it can be
     *                      resolved
     * @return a boolean that indicates whether the configuration can be resolved
     */
    private boolean canBeResolved(Configuration configuration) {
        boolean isDeprecatedForResolving = ((DeprecatableConfiguration) configuration).getResolutionAlternatives() != null;
        return configuration.isCanBeResolved() && !isDeprecatedForResolving;
    }

    /**
     * An internal method that will take the dependency information supplied to it, convert it into
     * a {@link GradleDependency} representation and delegate the actual rendering to the
     * implementation of {@link AbstractGradleDependencyRenderer#render(GradleDependency)} method.
     *
     * @param configuration   the {@link Configuration} for which we are rendering the dependency
     * @param mavenDependency the {@link IMavenDependency} that needs to be rendered
     * @param depRoot         the {@link IMavenDependency} root of the dependency that we need to render
     */
    private void renderInternal(@NonNull final Configuration configuration,
                                @NonNull final IMavenDependency mavenDependency,
                                @Nullable final IMavenDependency depRoot) {
        // Take the configuration and adapt that into an internal dependency type
        final DependencyType incomingDependencyType = sConfigurationAdapter.adapt(configuration);

        // check if we already received (rendered) this dependency
        GradleDependency gradleDependency = mRenderedDepMap.get(mavenDependency.toString());

        // we have not seen this dependency yet
        if (gradleDependency == null) {
            final Set<IMavenDependency> depRoots = new HashSet<>();

            // we also have root dependency so let's add that to roots
            if (depRoot != null) {
                depRoots.add(depRoot);
            }

            // Create a GradleDependency object from the information we have
            gradleDependency = new GradleDependency(
                    incomingDependencyType,
                    mavenDependency,
                    depRoots
            );
        } else {
            // we have already seen this dependency
            // so now we just need to update the roots because the root we received this time
            // might not have been recorded yet
            if (depRoot != null) {
                gradleDependency.addRootDependency(depRoot);
            }

            // We would also update dependency type.
            // It is possible that when we saw this dep earlier we got in a compile only classpath
            // so if we got runtime now then we would overwrite the dependency type
            if (incomingDependencyType == DependencyType.RUNTIME) {
                gradleDependency.setDependencyType(incomingDependencyType);
            }
        }

        // Now actually render this dependency
        // or do whatever the render method does ;)
        render(gradleDependency);

        // we rendered it...save it into the Map
        mRenderedDepMap.put(mavenDependency.toString(), gradleDependency);
    }
}
