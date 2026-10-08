function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function fieldControl(field) {
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
      data[field.name] = field.type === "checkbox" ? control.checked : control.value;
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
    for (const field of definition.fields) {
      const wrapper = element("div", `field${field.wide ? " wide" : ""}`);
      const control = fieldControl(field);
      control.id = `${definition.id}-${field.name}`;
      control.name = field.name;

      if (field.type === "checkbox") {
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
