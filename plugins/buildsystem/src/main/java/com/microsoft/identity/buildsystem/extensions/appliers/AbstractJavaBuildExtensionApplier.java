package com.microsoft.identity.buildsystem.extensions.appliers;

import com.microsoft.identity.buildsystem.constants.ProjectType;
import com.microsoft.identity.buildsystem.extensions.JavaBuildExtension;
import com.microsoft.identity.buildsystem.java.version.setters.JavaVersionSetterFactory;

import org.gradle.api.JavaVersion;
import org.gradle.api.Project;
import org.gradle.api.provider.Property;

public abstract class AbstractJavaBuildExtensionApplier<T extends JavaBuildExtension> implements IBuildExtensionApplier<T> {

    private final ProjectType mProjectType;

    public AbstractJavaBuildExtensionApplier(final ProjectType projectType) {
        this.mProjectType = projectType;
    }

    @Override
    public void applyBuildExtensionProperties(final Project project, final JavaBuildExtension buildExtension) {
        project.afterEvaluate(evaluatedProject -> {
            final Property<JavaVersion> javaVersionProperty = buildExtension.getJavaVersion();

            if (javaVersionProperty != null && javaVersionProperty.isPresent()) {
                final JavaVersion javaVersion = javaVersionProperty.get();

                JavaVersionSetterFactory.INSTANCE.getJavaVersionSetter(mProjectType)
                        .setJavaVersionOnProject(project, javaVersion);
            }
        });
    }
}
