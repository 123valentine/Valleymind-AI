/**
 * ValleyMind-AI Contextual Activity Indicator Controller (v2)
 * ============================================================
 * - Compact inline 3-dot animation in ValleyMind cyan-blue (#00d4ff)
 * - Moving faint-gray skeleton shimmer status text while processing
 * - Collapses cleanly after response completion with tap-to-open step drawer
 */
(function (global) {
  "use strict";

  const TERMINAL_STATUSES = new Set(["completed", "failed", "skipped"]);

  function injectStyles() {
    if (typeof document === "undefined") return;
    if (document.getElementById("vm-activity-v2-styles")) return;
    const style = document.createElement("style");
    style.id = "vm-activity-v2-styles";
    style.textContent = `
      .vm-activity-inline {
        display: inline-flex;
        align-items: center;
        gap: 9px;
        padding: 4px 2px;
        margin: 2px 0 6px 0;
        background: transparent;
        border: none;
        font-size: 13.5px;
        line-height: 1.35;
        max-width: 100%;
        user-select: none;
      }
      .vm-activity-inline.vm-activity-hidden {
        display: none;
      }
      .vm-activity-inline.is-clickable {
        cursor: pointer;
      }
      .vm-activity-dots {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        flex-shrink: 0;
      }
      .vm-activity-dots.is-hidden {
        display: none;
      }
      .vm-activity-dot {
        width: 5.5px;
        height: 5.5px;
        border-radius: 50%;
        background: #00d4ff;
        box-shadow: 0 0 6px rgba(0, 212, 255, 0.55);
        display: inline-block;
        animation: vmPulseWave 1.2s ease-in-out infinite;
      }
      .vm-activity-dot:nth-child(2) {
        animation-delay: 0.18s;
      }
      .vm-activity-dot:nth-child(3) {
        animation-delay: 0.36s;
      }
      @keyframes vmPulseWave {
        0%, 100% {
          transform: scale(0.72) translateY(0);
          opacity: 0.35;
        }
        40% {
          transform: scale(1.15) translateY(-2.5px);
          opacity: 1;
        }
        75% {
          transform: scale(0.9) translateY(1px);
          opacity: 0.65;
        }
      }
      .vm-activity-shimmer-text {
        font-weight: 500;
        color: rgba(203, 213, 225, 0.55);
        background: linear-gradient(
          90deg,
          rgba(148, 163, 184, 0.45) 0%,
          rgba(226, 232, 240, 0.92) 50%,
          rgba(148, 163, 184, 0.45) 100%
        );
        background-size: 220% 100%;
        -webkit-background-clip: text;
        background-clip: text;
        -webkit-text-fill-color: transparent;
        animation: vmSkeletonShimmer 1.8s linear infinite;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .vm-activity-settled-text {
        font-weight: 500;
        color: rgba(148, 163, 184, 0.78);
        display: inline-flex;
        align-items: center;
        gap: 5px;
      }
      .vm-activity-settled-text.is-error {
        color: #f87171;
      }
      .vm-activity-chevron {
        font-size: 12px;
        opacity: 0.75;
        transition: transform 0.16s ease;
      }
      @keyframes vmSkeletonShimmer {
        0% { background-position: 120% 0; }
        100% { background-position: -120% 0; }
      }

      /* Bottom Sheet Drawer for Completed/Active Steps */
      .vm-activity-sheet-backdrop {
        position: fixed;
        inset: 0;
        background: rgba(2, 10, 14, 0.65);
        backdrop-filter: blur(2px);
        z-index: 9998;
        display: flex;
        align-items: flex-end;
        justify-content: center;
      }
      .vm-activity-sheet {
        width: 100%;
        max-width: 540px;
        max-height: 70vh;
        overflow-y: auto;
        background: #0a1b1f;
        border-top: 1px solid rgba(0, 212, 255, 0.22);
        border-radius: 18px 18px 0 0;
        padding: 14px 18px 24px 18px;
        color: #e2e8f0;
        box-shadow: 0 -8px 30px rgba(0, 0, 0, 0.55);
      }
      .vm-activity-sheet-handle {
        width: 36px;
        height: 4px;
        border-radius: 999px;
        background: rgba(148, 163, 184, 0.35);
        margin: 0 auto 12px auto;
      }
      .vm-activity-sheet-title {
        display: flex;
        align-items: center;
        justify-content: space-between;
        font-size: 14px;
        font-weight: 600;
        color: #94a3b8;
        margin-bottom: 12px;
      }
      .vm-activity-sheet-close {
        background: rgba(255, 255, 255, 0.06);
        border: none;
        color: #cbd5e1;
        border-radius: 999px;
        width: 26px;
        height: 26px;
        cursor: pointer;
        font-size: 14px;
      }
      .vm-activity-timeline {
        display: flex;
        flex-direction: column;
        gap: 12px;
        border-left: 1.5px solid rgba(0, 212, 255, 0.25);
        margin-left: 7px;
        padding-left: 14px;
      }
      .vm-activity-timeline-item {
        position: relative;
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .vm-activity-timeline-dot {
        position: absolute;
        left: -19px;
        top: 5px;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #00d4ff;
      }
      .vm-activity-timeline-dot.is-failed {
        background: #f87171;
      }
      .vm-activity-timeline-dot.is-skipped {
        background: #64748b;
      }
      .vm-activity-timeline-label {
        font-size: 13.5px;
        font-weight: 500;
        color: #e2e8f0;
      }
      .vm-activity-timeline-detail {
        font-size: 12px;
        color: #94a3b8;
        word-break: break-word;
      }
    `;
    document.head.appendChild(style);
  }

  class ActivityTrackerUI {
    constructor(options = {}) {
      this.seenKeys = new Set();
      this.operations = new Map();
      this.order = [];
      this.activeRequestId = null;
      this.isCompleted = false;
      this.onChange = typeof options.onChange === "function" ? options.onChange : null;
      this.rootEl = null;
      this.sheetBackdropEl = null;
    }

    reset(requestId = null) {
      this.seenKeys.clear();
      this.operations.clear();
      this.order = [];
      this.activeRequestId = requestId;
      this.isCompleted = false;
      this.closeDrawer();
      this.render();
    }

    /**
     * Ingests either a raw ActivityEvent object or an `{type:"activity", activity:{...}}` envelope.
     */
    ingest(payload) {
      if (!payload || typeof payload !== "object") return false;
      const event =
        payload.type === "activity" && payload.activity && typeof payload.activity === "object"
          ? payload.activity
          : payload;

      if (!event.operation && !event.label) return false;

      if (event.request_id) {
        if (this.activeRequestId && this.activeRequestId !== event.request_id) {
          this.reset(event.request_id);
        } else if (!this.activeRequestId) {
          this.activeRequestId = event.request_id;
        }
      }

      const dedupKey = `${event.request_id || "default"}:${event.seq ?? ""}:${event.event_id || ""}:${event.status || ""}:${event.label || ""}`;
      if (this.seenKeys.has(dedupKey)) {
        return false;
      }
      this.seenKeys.add(dedupKey);

      const id = event.event_id || `op_${this.order.length + 1}`;
      if (!this.operations.has(id)) {
        this.order.push(id);
      }
      this.operations.set(id, event);
      this.render();

      if (this.onChange) {
        this.onChange(this.getSnapshot());
      }
      return true;
    }

    ingestBatch(events) {
      if (!Array.isArray(events)) return;
      for (const ev of events) {
        this.ingest(ev);
      }
    }

    abort(reason = "Request interrupted") {
      this.isCompleted = true;
      for (const id of this.order) {
        const ev = this.operations.get(id);
        if (ev && !TERMINAL_STATUSES.has(ev.status)) {
          this.operations.set(id, {
            ...ev,
            status: "failed",
            label: reason,
          });
        }
      }
      this.render();
    }

    /**
     * Collapses the active shimmer + dots into a clean clickable gray summary line
     * above the finished response (matching the Thought process / Searched the web pattern).
     */
    finish(keepCollapsedSummary = true) {
      this.isCompleted = true;
      for (const id of this.order) {
        const ev = this.operations.get(id);
        if (ev && !TERMINAL_STATUSES.has(ev.status)) {
          this.operations.set(id, {
            ...ev,
            status: "completed",
          });
        }
      }
      if (!keepCollapsedSummary || this.order.length === 0) {
        if (this.rootEl) {
          this.rootEl.classList.add("vm-activity-hidden");
        }
        return;
      }
      this.render();
    }

    getCurrentOperation() {
      for (let i = this.order.length - 1; i >= 0; i--) {
        const ev = this.operations.get(this.order[i]);
        if (ev && !TERMINAL_STATUSES.has(ev.status)) {
          return ev;
        }
      }
      if (this.order.length > 0) {
        return this.operations.get(this.order[this.order.length - 1]);
      }
      return null;
    }

    getSummaryLabel() {
      if (this.order.length === 0) return "Thought process";
      const steps = this.order.map((id) => this.operations.get(id)).filter(Boolean);
      const meaningful = steps.filter(
        (s) => s.operation !== "request_understanding" && s.operation !== "response_preparation"
      );
      if (meaningful.length === 1) {
        return meaningful[0].label;
      }
      if (meaningful.length > 1) {
        const first = meaningful[0];
        return `${first.label} (+${meaningful.length - 1} steps)`;
      }
      return steps[steps.length - 1].label || "Thought process";
    }

    getSnapshot() {
      return {
        requestId: this.activeRequestId,
        isCompleted: this.isCompleted,
        current: this.getCurrentOperation(),
        steps: this.order.map((id) => this.operations.get(id)),
      };
    }

    mount(containerEl) {
      if (typeof document === "undefined" || !containerEl) return null;
      injectStyles();
      if (!this.rootEl) {
        this.rootEl = document.createElement("div");
        this.rootEl.className = "vm-activity-inline";
        this.rootEl.setAttribute("role", "status");
        this.rootEl.setAttribute("aria-live", "polite");
      }
      if (this.rootEl.parentNode !== containerEl) {
        containerEl.appendChild(this.rootEl);
      }
      this.render();
      return this.rootEl;
    }

    openDrawer() {
      if (typeof document === "undefined" || this.order.length === 0) return;
      this.closeDrawer();

      const backdrop = document.createElement("div");
      backdrop.className = "vm-activity-sheet-backdrop";
      backdrop.onclick = (e) => {
        if (e.target === backdrop) this.closeDrawer();
      };

      const sheet = document.createElement("div");
      sheet.className = "vm-activity-sheet";

      const handle = document.createElement("div");
      handle.className = "vm-activity-sheet-handle";
      sheet.appendChild(handle);

      const titleRow = document.createElement("div");
      titleRow.className = "vm-activity-sheet-title";
      const titleText = document.createElement("span");
      titleText.textContent = "Activity steps";
      titleRow.appendChild(titleText);

      const closeBtn = document.createElement("button");
      closeBtn.type = "button";
      closeBtn.className = "vm-activity-sheet-close";
      closeBtn.textContent = "✕";
      closeBtn.onclick = () => this.closeDrawer();
      titleRow.appendChild(closeBtn);
      sheet.appendChild(titleRow);

      const timeline = document.createElement("div");
      timeline.className = "vm-activity-timeline";

      for (const id of this.order) {
        const step = this.operations.get(id);
        if (!step) continue;
        const item = document.createElement("div");
        item.className = "vm-activity-timeline-item";

        const dot = document.createElement("span");
        dot.className = "vm-activity-timeline-dot";
        if (step.status === "failed") dot.classList.add("is-failed");
        if (step.status === "skipped") dot.classList.add("is-skipped");
        item.appendChild(dot);

        const lbl = document.createElement("div");
        lbl.className = "vm-activity-timeline-label";
        lbl.textContent = step.label;
        item.appendChild(lbl);

        if (step.detail) {
          const det = document.createElement("div");
          det.className = "vm-activity-timeline-detail";
          det.textContent = step.detail;
          item.appendChild(det);
        }

        timeline.appendChild(item);
      }

      sheet.appendChild(timeline);
      backdrop.appendChild(sheet);
      document.body.appendChild(backdrop);
      this.sheetBackdropEl = backdrop;
    }

    closeDrawer() {
      if (this.sheetBackdropEl && this.sheetBackdropEl.parentNode) {
        this.sheetBackdropEl.parentNode.removeChild(this.sheetBackdropEl);
      }
      this.sheetBackdropEl = null;
    }

    render() {
      if (!this.rootEl || typeof document === "undefined") return;

      this.rootEl.classList.remove("vm-activity-hidden");
      this.rootEl.innerHTML = "";

      const current = this.getCurrentOperation();

      // Active processing state: 3 compact ValleyMind cyan-blue dots + moving gray skeleton shimmer text
      if (!this.isCompleted) {
        this.rootEl.classList.remove("is-clickable");
        this.rootEl.onclick = null;

        const dots = document.createElement("span");
        dots.className = "vm-activity-dots";
        dots.innerHTML =
          '<span class="vm-activity-dot"></span>' +
          '<span class="vm-activity-dot"></span>' +
          '<span class="vm-activity-dot"></span>';
        this.rootEl.appendChild(dots);

        const textEl = document.createElement("span");
        textEl.className = "vm-activity-shimmer-text";
        let labelText = current ? current.label : "Thinking...";
        if (
          current &&
          current.progress &&
          typeof current.progress.current === "number" &&
          typeof current.progress.total === "number"
        ) {
          labelText += ` (${current.progress.current}/${current.progress.total})`;
        }
        textEl.textContent = labelText;
        this.rootEl.appendChild(textEl);
        return;
      }

      // Completed / settled state: compact gray clickable summary header (no bouncing dots)
      if (this.order.length === 0) {
        this.rootEl.classList.add("vm-activity-hidden");
        return;
      }

      this.rootEl.classList.add("is-clickable");
      this.rootEl.onclick = () => this.openDrawer();

      const settled = document.createElement("span");
      settled.className = "vm-activity-settled-text";
      if (current && current.status === "failed") {
        settled.classList.add("is-error");
      }
      settled.textContent = this.getSummaryLabel();

      const chevron = document.createElement("span");
      chevron.className = "vm-activity-chevron";
      chevron.textContent = "›";
      settled.appendChild(chevron);

      this.rootEl.appendChild(settled);
    }
  }

  /**
   * Parses either a JSON object (`{type:"activity", activity:{...}}`) or raw SSE lines.
   */
  function handleSseEvent(tracker, evtOrBlock) {
    if (!tracker || !evtOrBlock) return false;
    if (typeof evtOrBlock === "object") {
      if (evtOrBlock.type === "activity" && evtOrBlock.activity) {
        return tracker.ingest(evtOrBlock.activity);
      }
      if (evtOrBlock.operation && evtOrBlock.status && evtOrBlock.event_id) {
        return tracker.ingest(evtOrBlock);
      }
      return false;
    }
    if (typeof evtOrBlock === "string") {
      const lines = evtOrBlock.split(/\r?\n/);
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        try {
          const parsed = JSON.parse(trimmed.slice(5).trim());
          if (handleSseEvent(tracker, parsed)) return true;
        } catch (_) {}
      }
    }
    return false;
  }

  const exported = {
    ActivityTrackerUI,
    handleSseEvent,
    handleSseBlock: handleSseEvent,
    TERMINAL_STATUSES,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = exported;
  }
  global.ValleyMindActivity = exported;
})(typeof window !== "undefined" ? window : globalThis);
