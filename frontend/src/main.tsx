import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ConsoleProvider } from "./ActivityConsole";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ConsoleProvider>
      <App />
    </ConsoleProvider>
  </StrictMode>,
);
