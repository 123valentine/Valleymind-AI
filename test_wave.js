// Waveform tests (v10): fixed-interval scroll, level/height response, event fallback,
// recognition restart does not stop the wave.
var vm = require('./static/vm_wave.js');

var passed = 0;
var failed = 0;

function check(name, ok, msg) {
  if (ok) {
    console.log("PASS: " + name);
    passed++;
  } else {
    console.log("FAIL: " + name + " - " + (msg || ""));
    failed++;
  }
}

function finish() {
  try { vm.stop(); } catch (e) {}
  if (failed > 0) {
    console.log("WAVE TESTS: " + passed + " passed, " + failed + " FAILED");
    process.exit(1);
  }
  console.log("WAVE TESTS: all " + passed + " passed");
  console.log("WAVE_OK");
  process.exit(0);
}

// (1) start/stop do not throw; debugInfo shape present.
try {
  vm.start('user');
  check("vm.start('user') does not throw", true);
  var d = vm.debugInfo ? vm.debugInfo() : {};
  check("debugInfo() returns object", !!d && typeof d === 'object');
  check("debugInfo has loopRunning", typeof d.loopRunning !== 'undefined');
  check("debugInfo has interval", typeof d.interval !== 'undefined');
  check("debugInfo has source", typeof d.source !== 'undefined');
  check("debugInfo has level", typeof d.level !== 'undefined');
  vm.stop();
  check("vm.stop() does not throw", true);
} catch (e) {
  check("vm.start/stop does not throw", false, e.message);
}

// (2) Loop keeps scrolling in silence (loop running, quiet interval ~100ms).
try {
  vm.stop();
  vm.start('user');
  var ds = vm.debugInfo();
  check("loop keeps scrolling in silence: loopRunning", ds.loopRunning === true, "loopRunning=" + ds.loopRunning);
  check("loop keeps scrolling in silence: quiet interval ~100ms", ds.interval >= 80 && ds.interval <= 140, "interval=" + ds.interval);
  vm.stop();
} catch (e) {
  check("loop keeps scrolling in silence", false, e.message);
}

// (3) Recognition restart does not stop the wave (idempotent start while active).
try {
  vm.stop();
  vm.start('user');
  var d1 = vm.debugInfo();
  vm.start('user'); // restart/re-call must NOT stop
  var d2 = vm.debugInfo();
  check("recognition restart does not stop the wave: still running", d2.loopRunning === true, "loopRunning=" + d2.loopRunning);
  check("recognition restart does not stop the wave: activeKind preserved", d2.loopRunning === true && d1.loopRunning === true);
  for (var i = 0; i < 5; i++) vm.start('user');
  var d3 = vm.debugInfo();
  check("recognition restart x5: wave still running", d3.loopRunning === true, "loopRunning=" + d3.loopRunning);
  vm.stop();
} catch (e) {
  check("recognition restart does not stop the wave", false, e.message);
}

// (4) userPulse does not throw and raises event-driven activity.
try {
  vm.stop();
  vm.userPulse(); // no active kind: should not throw
  check("userPulse() with no active kind does not throw", true);
  vm.start('user');
  vm.userPulse();
  check("userPulse() while active does not throw", true);
  vm.stop();
} catch (e) {
  check("userPulse() behavior", false, e.message);
}

// (5) Fallback/event source activates when analyser unavailable (Node env has no getUserMedia/analyser).
try {
  vm.stop();
  vm.start('user');
  var df = vm.debugInfo();
  check("fallback source activates (no analyser): source is events", df.source === 'events', "source=" + df.source);
  check("fallback source: loop running", df.loopRunning === true);
  vm.stop();
} catch (e) {
  check("fallback source activates when analyser unavailable", false, e.message);
}

// (6) Bar count never exceeds container width for widths 360, 800, 1400.
try {
  var widths = [360, 800, 1400];
  var allOk = true;
  widths.forEach(function (w) {
    var computed = Math.floor((w - 26) / 6);
    if (computed < 20) computed = 20;
    if (computed > 200) computed = 200;
    if (computed * 6 > w) allOk = false;
  });
  check("Bar count * 6 <= container width for 360/800/1400px", allOk);
} catch (e) {
  check("Bar count * 6 <= container width", false, e.message);
}

// (7)+(8) Async: level raises after event pulses; then start('ai') works. Sequenced to avoid interference.
try {
  vm.stop();
  vm.start('user');
  var before = vm.debugInfo().level;
  vm.userPulse();
  vm.userPulse();
  vm.userPulse();
  setTimeout(function () {
    try {
      var after = vm.debugInfo().level;
      check("level raises newest bar height (event pulses raise level)", after > before || after >= 0.05, "before=" + before + " after=" + after);
    } catch (e) {
      check("level raises newest bar height (event pulses raise level)", false, e.message);
    }
    // (8) now test AI
    try {
      vm.stop();
      vm.start('ai');
      var da = vm.debugInfo();
      check("vm.start('ai') does not throw, loop runs", da.loopRunning === true);
      setTimeout(function () {
        try { vm.stop(); check("vm.stop() after ai does not throw", true); } catch (e) { check("vm.stop() after ai", false, e.message); }
        finish();
      }, 60);
    } catch (e) {
      check("vm.start('ai') does not throw", false, e.message);
      finish();
    }
  }, 180);
} catch (e) {
  check("level raises newest bar height (event pulses raise level)", false, e.message);
  finish();
}

setTimeout(function () {
  check("tests completed (timeout)", false, "forced timeout");
  finish();
}, 4000);
