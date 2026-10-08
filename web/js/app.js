import "./components/tool-panel.js";
import { tools } from "./tools/index.js";

const grid = document.querySelector("#tool-grid");
for (const definition of tools) {
  const panel = document.createElement("pdf-tool-panel");
  panel.definition = definition;
  grid.append(panel);
}
