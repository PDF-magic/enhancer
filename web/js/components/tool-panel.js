function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function fieldControl(field) {
  if (field.type === "file") {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = field.accept;
    return input;
  }

  if (field.type === "select") {
    const select = document.createElement("select");
    for (const option of field.options) {
      const node = document.createElement("option");
      node.value = option.value;
      node.textContent = option.label;
      select.append(node);
    }
    select.value = field.value ?? "";
    return select;
  }

  const input = document.createElement("input");
  input.type = field.type ?? "text";
  if (field.placeholder) input.placeholder = field.placeholder;
  if (field.min != null) input.min = String(field.min);
  if (field.required) input.required = true;
  return input;
}

export class ToolPanel extends HTMLElement {
  set definition(value) {
    this._definition = value;
    if (this.isConnected) this.render();
  }

  connectedCallback() {
    if (this._definition) this.render();
  }

  values(form) {
    const data = {};
    for (const field of this._definition.fields) {
      const control = form.elements.namedItem(field.name);
      data[field.name] = field.type === "file"
        ? (control.dataset.uploadedPath || "")
        : field.type === "checkbox" ? control.checked : control.value;
    }
    return data;
  }

  render() {
    const definition = this._definition;
    this.id = definition.id;
    this.replaceChildren();

    const article = element("article", "tool-card");
    article.dataset.accent = definition.accent;
    const head = element("header", "card-head");
    head.append(
      element("span", "step", definition.step),
      element("p", "eyebrow", definition.eyebrow),
      element("h2", "", definition.title),
      element("p", "summary", definition.summary),
    );

    const form = document.createElement("form");
    const fields = element("div", "fields");
    let pendingUploads = 0;
    for (const field of definition.fields) {
      const wrapper = element("div", `field${field.wide ? " wide" : ""}`);
      const control = fieldControl(field);
      control.id = `${definition.id}-${field.name}`;
      control.name = field.name;

      if (field.type === "file") {
        const label = element("label", "", field.label);
        label.htmlFor = control.id;
        if (field.required) label.append(element("span", "required", " *"));

        const zone = element("div", "file-drop-zone");
        const symbol = element("span", "file-drop-symbol", "⇧");
        symbol.setAttribute("aria-hidden", "true");
        const instruction = element("span", "file-drop-instruction",
          `Drop ${field.accept === ".pdf" ? "a PDF" : field.accept.toUpperCase() + " file"} here or click to choose`);
        const hint = element("span", "file-drop-hint", "A copy stays on this Mac.");
        const feedback = element("span", "file-drop-status", "No file selected");
        feedback.setAttribute("role", "status");
        feedback.setAttribute("aria-live", "polite");
        feedback.id = `${control.id}-feedback`;
        control.setAttribute("aria-describedby", feedback.id);
        zone.append(symbol, instruction, hint, feedback, control);
        wrapper.append(label, zone);
        if (field.help) wrapper.append(element("span", "help", field.help));

        let generation = 0;
        async function importFile(file) {
          if (!file) return;
          const current = ++generation;
          delete control.dataset.uploadedPath;
          zone.classList.remove("file-ready", "file-error");
          if (!file.name.toLowerCase().endsWith(field.accept)) {
            feedback.textContent = `Select a ${field.accept} file.`;
            zone.classList.add("file-error");
            return;
          }

          pendingUploads += 1;
          build.disabled = true;
          zone.classList.add("file-uploading");
          feedback.textContent = `Importing ${file.name}…`;
          try {
            const response = await fetch(
              `/api/upload?name=${encodeURIComponent(file.name)}`,
              { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file },
            );
            if (!response.ok) {
              throw new Error([404, 405, 501].includes(response.status)
                ? "Use the local server (npm run serve or npm run open) to import files."
                : `Local file import failed (HTTP ${response.status}).`);
            }
            const result = await response.json();
            if (!result.path || typeof result.path !== "string") {
              throw new Error("The local server did not return a file path.");
            }
            if (current !== generation) return;
            control.dataset.uploadedPath = result.path;
            zone.classList.add("file-ready");
            feedback.textContent = `Ready: ${file.name}`;
          } catch (error) {
            if (current !== generation) return;
            zone.classList.add("file-error");
            feedback.textContent = error.message || "The file could not be imported.";
          } finally {
            pendingUploads -= 1;
            zone.classList.remove("file-uploading");
            build.disabled = pendingUploads > 0;
          }
        }

        control.addEventListener("change", () => importFile(control.files?.[0]));
        zone.addEventListener("dragenter", (event) => {
          event.preventDefault();
          zone.classList.add("file-drag-over");
        });
        zone.addEventListener("dragover", (event) => {
          event.preventDefault();
          if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
          zone.classList.add("file-drag-over");
        });
        zone.addEventListener("dragleave", (event) => {
          if (!zone.contains(event.relatedTarget)) zone.classList.remove("file-drag-over");
        });
        zone.addEventListener("drop", (event) => {
          event.preventDefault();
          zone.classList.remove("file-drag-over");
          const files = event.dataTransfer?.files;
          if (!files?.length) return;
          if (files.length !== 1) {
            feedback.textContent = "Select one file at a time.";
            zone.classList.add("file-error");
            return;
          }
          importFile(files[0]);
        });
      } else if (field.type === "checkbox") {
        const check = element("label", "check-field");
        check.htmlFor = control.id;
        check.append(control, document.createTextNode(field.label));
        wrapper.append(check);
      } else {
        const label = element("label", "", field.label);
        label.htmlFor = control.id;
        if (field.required) label.append(element("span", "required", " *"));
        wrapper.append(label, control);
        if (field.help) wrapper.append(element("span", "help", field.help));
      }
      fields.append(wrapper);
    }

    const actions = element("div", "actions");
    const build = element("button", "primary", "Build command");
    build.type = "submit";
    const copy = element("button", "secondary", "Copy");
    copy.type = "button";
    copy.disabled = true;
    actions.append(build, copy);

    const result = element("div", "result");
    result.hidden = true;
    const resultLabel = element("p", "result-label", "Terminal command");
    const output = element("code", "command");
    const status = element("p", "status");
    status.setAttribute("aria-live", "polite");
    result.append(resultLabel, output, status);

    form.append(fields, actions, result);
    article.append(head, form);
    this.append(article);

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (pendingUploads > 0) return;
      status.classList.remove("error");
      try {
        output.textContent = definition.build(this.values(form));
        status.textContent = "Command ready to run from the repository root.";
        result.hidden = false;
        copy.disabled = false;
      } catch (error) {
        output.textContent = "";
        status.textContent = error.message;
        status.classList.add("error");
        result.hidden = false;
        copy.disabled = true;
      }
    });

    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(output.textContent);
        status.textContent = "Copied to clipboard.";
      } catch {
        status.textContent = "Clipboard access is unavailable; select the command manually.";
      }
    });
  }
}

customElements.define("pdf-tool-panel", ToolPanel);
