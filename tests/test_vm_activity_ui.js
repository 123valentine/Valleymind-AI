/**
 * Unit tests for static/vm_activity.js (state transitions, deduplication,
 * Layer-1 JSON SSE envelopes, completion summary collapse, and abort handling).
 */
const assert = require("assert");
const { ActivityTrackerUI, handleSseEvent } = require("../static/vm_activity.js");

(function runTests() {
  const ui = new ActivityTrackerUI();
  assert.strictEqual(ui.getCurrentOperation(), null);

  // 1. Ingest envelope format emitted by core/activity_v2.py
  const ingested1 = ui.ingest({
    type: "activity",
    activity: {
      request_id: "req_1",
      event_id: "act_1",
      seq: 1,
      operation: "web_search",
      status: "started",
      label: "Searching the web...",
    },
  });
  assert.strictEqual(ingested1, true);
  assert.strictEqual(ui.getCurrentOperation().label, "Searching the web...");

  // 2. Duplicate suppression
  const dup = ui.ingest({
    type: "activity",
    activity: {
      request_id: "req_1",
      event_id: "act_1",
      seq: 1,
      operation: "web_search",
      status: "started",
      label: "Searching the web...",
    },
  });
  assert.strictEqual(dup, false);
  assert.strictEqual(ui.getSnapshot().steps.length, 1);

  // 3. Single-line SSE data frame parsing
  const sseLine =
    'data: {"type":"activity","activity":{"request_id":"req_1","event_id":"act_2","seq":2,"operation":"memory_retrieval","status":"started","label":"Recalling relevant memories..."}}';
  assert.strictEqual(handleSseEvent(ui, sseLine), true);
  assert.strictEqual(
    ui.getCurrentOperation().label,
    "Recalling relevant memories..."
  );

  // 4. Non-activity SSE frames ignored cleanly
  const tokenLine = 'data: {"token":"Hello"}';
  assert.strictEqual(handleSseEvent(ui, tokenLine), false);

  // 5. Finish collapses into summary label
  ui.finish(true);
  assert.strictEqual(ui.getSnapshot().isCompleted, true);
  assert.ok(ui.getSummaryLabel().includes("Searching the web..."));

  // 6. New request_id resets state cleanly
  ui.ingest({
    type: "activity",
    activity: {
      request_id: "req_2",
      event_id: "act_9",
      seq: 1,
      operation: "image_analysis",
      status: "started",
      label: "Analyzing image...",
    },
  });
  assert.strictEqual(ui.getSnapshot().isCompleted, false);
  assert.strictEqual(ui.getSnapshot().steps.length, 1);
  assert.strictEqual(ui.getCurrentOperation().operation, "image_analysis");

  // 7. Abort marks active steps as failed
  ui.abort("Connection lost");
  assert.strictEqual(ui.getSnapshot().steps[0].status, "failed");

  console.log("All vm_activity.js tests passed.");
})();
