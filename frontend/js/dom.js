// DOM helpers. There is no framework auto-escaping here, so:
//   - put API/user data into the DOM with el()/textContent, not innerHTML
//   - if HTML strings are unavoidable, pass every interpolated value through escapeHtml()

const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch]);
}

// el("p", { class: "muted", dataset: { id: 3 } }, "text", childNode)
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, val] of Object.entries(attrs)) {
    if (val == null || val === false) continue;
    if (key === "class") node.className = val;
    else if (key === "dataset") Object.assign(node.dataset, val);
    else if (key.startsWith("on") && typeof val === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), val);
    } else node.setAttribute(key, val === true ? "" : String(val));
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
