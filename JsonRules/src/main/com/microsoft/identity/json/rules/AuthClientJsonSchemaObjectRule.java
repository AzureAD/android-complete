// Copyright (c) Microsoft Corporation.
// All rights reserved.
//
// This code is licensed under the MIT License.
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files(the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and / or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions :
//
// The above copyright notice and this permission notice shall be included in
// all copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
// THE SOFTWARE.
package com.microsoft.identity.json.rules;

import com.fasterxml.jackson.databind.JsonNode;
import com.sun.codemodel.JDefinedClass;
import com.sun.codemodel.JPackage;
import com.sun.codemodel.JType;

import org.jsonschema2pojo.Schema;
import org.jsonschema2pojo.rules.ObjectRule;
import org.jsonschema2pojo.util.ParcelableHelper;
import org.jsonschema2pojo.util.ReflectionHelper;

/**
 * A custom {@link ObjectRule} that has the capability to generate private constructor if the
 * config specified to NOT generate constructor AND also NOT generate any setters.
 * <p>
 * We do this because if no constructor is generated, then JAVA will by default have a public
 * NO-arg constructor for the class. This is a problem because this would allow someone to
 * instantiate the object without actually populating any of its fields and also not having the
 * ability to populate any fields post object construction because setters were not generated. To
 * overcome this problem, we would simply generate a PRIVATE no-arg constructor so that no one can
 * actually instantiate the object without any properties on it.
 */
public class AuthClientJsonSchemaObjectRule extends ObjectRule {

    private AuthClientJsonSchemaRuleFactory ruleFactory;

    protected AuthClientJsonSchemaObjectRule(AuthClientJsonSchemaRuleFactory ruleFactory, ParcelableHelper parcelableHelper, ReflectionHelper reflectionHelper) {
        super(ruleFactory, parcelableHelper, reflectionHelper);
        this.ruleFactory = ruleFactory;
    }

    @Override
    public JType apply(final String nodeName, final JsonNode node, final JsonNode parent,
                       final JPackage jPackage, final Schema schema) {
        final JType jType = super.apply(nodeName, node, parent, jPackage, schema);
        if (jType instanceof JDefinedClass
                && !ruleFactory.getGenerationConfig().isIncludeConstructors()
                && !ruleFactory.getGenerationConfig().isIncludeSetters()) {
            new PrivateConstructorRule().apply(nodeName, node, parent, (JDefinedClass) jType, schema);
        }
        return jType;
    }
}
