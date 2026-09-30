import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import "./index.css";
import Landing from "./pages/Landing.jsx";
import OperatorHUD from "./pages/OperatorHUD.jsx";
import Supervisor from "./pages/Supervisor.jsx";

createRoot(document.getElementById("root")).render(
  <BrowserRouter>
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/hud/:machineId" element={<OperatorHUD />} />
      <Route path="/supervisor" element={<Supervisor />} />
    </Routes>
  </BrowserRouter>
);
