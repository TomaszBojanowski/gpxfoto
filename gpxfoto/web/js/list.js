// The list of photos. Only the rows in view exist, and they are reused
// while scrolling, so thousands of photos cost no more than a screenful.
// The selected row grows to show all about its photo.

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
    this.selectedIndex = -1;
    this.extra = 0;                 // px the selected row is taller than the others
    this.frame = null;
    element.addEventListener("scroll", () => this.schedule());
    new ResizeObserver(() => this.schedule()).observe(element);
    element.addEventListener("click", (event) => {
      const row = event.target.closest(".row");
      if (row && !event.target.closest("button, a, input")) {
        this.onSelect(Number(row.dataset.id));
      }
    });
    element.addEventListener("keydown", (event) => this.key(event));
  }

  setIds(ids) {
    this.ids = ids;
    this.selectedIndex = ids.indexOf(this.selected);
    this.refresh();
  }

  // Draw the rows in view again, e.g. after the match changed
  refresh() {
    for (const row of this.rows.values()) {
      this.free.push(row);
      row.remove();
    }
    this.rows.clear();
    if (this.selectedIndex < 0) {
      this.extra = 0;
    }
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

  top(index) {
    const below = this.selectedIndex >= 0 && index > this.selectedIndex;
    return index * ROW_HEIGHT + (below ? this.extra : 0);
  }

  height(index) {
    return ROW_HEIGHT + (index === this.selectedIndex ? this.extra : 0);
  }

  // The index of the row at y px from the top of the list
  indexAt(y) {
    const selected = this.selectedIndex;
    if (selected >= 0 && y >= (selected + 1) * ROW_HEIGHT) {
      y = Math.max((selected + 1) * ROW_HEIGHT - 1, y - this.extra);
    }
    return Math.floor(y / ROW_HEIGHT);
  }

  draw() {
    this.spacer.style.height = this.ids.length * ROW_HEIGHT + this.extra + "px";
    const top = this.element.scrollTop;
    const first = Math.max(0, this.indexAt(top) - EXTRA_ROWS);
    const last = Math.min(this.ids.length,
      this.indexAt(top + this.element.clientHeight) + 1 + EXTRA_ROWS);
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
      const selected = index === this.selectedIndex;
      row.className = "row";
      row.setAttribute("role", "option");
      row.dataset.id = id;
      row.style.top = this.top(index) + "px";
      row.setAttribute("aria-selected", String(selected));
      this.render(row, id, selected);
      this.element.appendChild(row);
      this.rows.set(index, row);
    }
    // Also when the width of the list changed, the selected row's height did
    const selectedRow = this.rows.get(this.selectedIndex);
    if (selectedRow) {
      this.measure(selectedRow);
    }
  }

  // The selected row is as tall as what it shows; the rows below move
  measure(row) {
    const extra = Math.max(0, row.offsetHeight - ROW_HEIGHT);
    if (extra !== this.extra) {
      this.extra = extra;
      this.spacer.style.height = this.ids.length * ROW_HEIGHT + this.extra + "px";
      for (const [index, other] of this.rows) {
        other.style.top = this.top(index) + "px";
      }
    }
  }

  select(id, scroll = true) {
    this.selected = id;
    this.selectedIndex = this.ids.indexOf(id);
    this.extra = 0;
    this.refresh();
    const index = this.selectedIndex;
    if (scroll && index >= 0) {
      const top = this.top(index);
      const height = this.height(index);
      const view = this.element;
      if (top < view.scrollTop || top + height > view.scrollTop + view.clientHeight) {
        view.scrollTop = Math.max(0, top - view.clientHeight / 2 + height / 2);
      }
    }
  }

  key(event) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") {
      return;
    }
    event.preventDefault();
    if (!this.ids.length) {
      return;
    }
    const index = this.selectedIndex;
    const next = event.key === "ArrowDown"
      ? Math.min(this.ids.length - 1, index + 1)
      : Math.max(0, index < 0 ? 0 : index - 1);
    this.onSelect(this.ids[next]);
  }
}
