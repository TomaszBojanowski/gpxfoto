// The list of photos. Only the rows in view exist, and they are reused
// while scrolling, so thousands of photos cost no more than a screenful.

const ROW_HEIGHT = 60;
const EXTRA_ROWS = 6;

export class PhotoList {
  // render(element, id, selected) fills a row; onSelect(id) on a click
  constructor(element, { render, onSelect }) {
    this.element = element;
    this.spacer = element.querySelector(".spacer");
    this.render = render;
    this.onSelect = onSelect;
    this.ids = [];
    this.rows = new Map();          // index -> row element
    this.free = [];
    this.selected = null;
    this.frame = null;
    element.addEventListener("scroll", () => this.schedule());
    new ResizeObserver(() => this.schedule()).observe(element);
    element.addEventListener("click", (event) => {
      const row = event.target.closest(".row");
      if (row) {
        this.onSelect(Number(row.dataset.id));
      }
    });
    element.addEventListener("keydown", (event) => this.key(event));
  }

  setIds(ids) {
    this.ids = ids;
    this.spacer.style.height = ids.length * ROW_HEIGHT + "px";
    this.refresh();
  }

  // Draw the rows in view again, e.g. after the match changed
  refresh() {
    for (const row of this.rows.values()) {
      this.free.push(row);
      row.remove();
    }
    this.rows.clear();
    this.draw();
  }

  schedule() {
    if (this.frame === null) {
      this.frame = requestAnimationFrame(() => {
        this.frame = null;
        this.draw();
      });
    }
  }

  draw() {
    const top = this.element.scrollTop;
    const first = Math.max(0, Math.floor(top / ROW_HEIGHT) - EXTRA_ROWS);
    const last = Math.min(this.ids.length,
      Math.ceil((top + this.element.clientHeight) / ROW_HEIGHT) + EXTRA_ROWS);
    for (const [index, row] of this.rows) {
      if (index < first || index >= last) {
        this.rows.delete(index);
        this.free.push(row);
        row.remove();
      }
    }
    for (let index = first; index < last; index++) {
      if (this.rows.has(index)) {
        continue;
      }
      const row = this.free.pop() || document.createElement("div");
      const id = this.ids[index];
      row.className = "row";
      row.setAttribute("role", "option");
      row.dataset.id = id;
      row.style.top = index * ROW_HEIGHT + "px";
      row.setAttribute("aria-selected", String(id === this.selected));
      this.render(row, id, id === this.selected);
      this.element.appendChild(row);
      this.rows.set(index, row);
    }
  }

  select(id, scroll = true) {
    this.selected = id;
    const index = this.ids.indexOf(id);
    if (scroll && index >= 0) {
      const top = index * ROW_HEIGHT;
      const view = this.element;
      if (top < view.scrollTop || top + ROW_HEIGHT > view.scrollTop + view.clientHeight) {
        view.scrollTop = Math.max(0, top - view.clientHeight / 2 + ROW_HEIGHT / 2);
      }
    }
    this.refresh();
  }

  key(event) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") {
      return;
    }
    event.preventDefault();
    if (!this.ids.length) {
      return;
    }
    const index = this.ids.indexOf(this.selected);
    const next = event.key === "ArrowDown"
      ? Math.min(this.ids.length - 1, index + 1)
      : Math.max(0, index < 0 ? 0 : index - 1);
    this.onSelect(this.ids[next]);
  }
}
