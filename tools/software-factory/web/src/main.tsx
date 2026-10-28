// Copyright (c) Microsoft Corporation. All rights reserved.

import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./style.css";
import "./factory.css";

const root = document.getElementById("root");
if (!root) throw new Error("Software Factory root element is missing.");
createRoot(root).render(<React.StrictMode><App /></React.StrictMode>);
