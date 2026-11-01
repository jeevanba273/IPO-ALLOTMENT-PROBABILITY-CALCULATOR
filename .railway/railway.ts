import { defineRailway, project, service } from "railway/iac";
// This repository manages only its own resources in the environment. Other
// repositories export their own partial name.
// See https://docs.railway.com/infrastructure-as-code#multi-repo-projects
export const partial = "ipo-dashboard";
export default defineRailway(() => {
  const ipoDashboard = service("ipo-dashboard", {
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    healthcheck: "/healthz",
    healthcheckTimeout: 120,
    replicas: { "asia-southeast1-eqsg3a": 1 },
  });

  return project("IPO INTELLIGENCE DASHBOARD", {
    variables: { managed: false },
    resources: [ipoDashboard],
  });
});
