import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Self-hosted Inter (no Google CDN dependency).
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";

import App from "./App.tsx";
import { ColorModeProvider } from "./theme/ColorModeContext";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ColorModeProvider>
      <App />
    </ColorModeProvider>
  </StrictMode>,
);
