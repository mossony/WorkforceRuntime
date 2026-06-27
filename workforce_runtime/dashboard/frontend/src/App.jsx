import { useEffect } from "react";
import dashboardShell from "./dashboardShell.html?raw";
import { initializeDashboard } from "./dashboardController.js";

export default function App() {
  useEffect(() => {
    initializeDashboard();
  }, []);

  return <div className="dashboard-root" dangerouslySetInnerHTML={{ __html: dashboardShell }} />;
}
