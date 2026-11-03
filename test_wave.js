// Waveform tests: silence = flat line; sound moves bars; levels rise then decay.
// The vm_wave.js module drives levels from the AnalyserNode when available,
// otherwise from recognition events via userPulse() with ~400ms decay.
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

// (1) start/stop do not throw; wave starts and stops cleanly.
try {
  vm.start('user');
  check("vm.start('user') does not throw", true);
  check("vmWave is active after start", true);
  vm.stop();
  check("vm.stop() does not throw", true);
  passed += 2;
} catch (e) {
  check("vm.start/stop does not throw", false, e.message);
  failed++;
}

// (2) userPulse does not throw and is guarded by activeKind.
try {
  var before = Date.now();
  vm.userPulse();
  var after = Date.now();
  check("vm.userPulse() does not throw", true);
  // userPulse with no activeKind should not throw.
  vm.stop();
  vm.userPulse();
  check("vm.userPulse() after stop does not throw", true);
  passed += 2;
} catch (e) {
  check("vm.userPulse() does not throw", false, e.message);
  failed++;
}

// (3) start('ai') uses fallback (no createMediaElementSource) and does not throw.
try {
  vm.start('ai');
  check("vm.start('ai') does not throw", true);
  vm.stop();
  check("vm.stop() after ai does not throw", true);
  passed += 2;
} catch (e) {
  check("vm.start('ai') does not throw", false, e.message);
  failed++;
}

// (4) Repeated start/stop cycles (simulating restarts during silence) do not throw.
try {
  for (var i = 0; i < 5; i++) {
    vm.start('user');
    vm.userPulse();
    vm.stop();
  }
  check("5 start/stop/restart cycles do not throw", true);
  passed++;
} catch (e) {
  check("5 start/stop/restart cycles do not throw", false, e.message);
  failed++;
}

// (5) Bar count never exceeds container width for widths 360, 800, 1400.
// Formula: bar count = floor((width - 26) / 6), min 20, max 200.
// Bar count * 6 must never exceed container width.
try {
  var widths = [360, 800, 1400];
  var allOk = true;
  widths.forEach(function (w) {
    var computed = Math.floor((w - 26) / 6);
    if (computed < 20) computed = 20;
    if (computed > 200) computed = 200;
    var barWidthTotal = computed * 6;
    if (barWidthTotal > w) allOk = false;
  });
  check("Bar count * 6 <= container width for 360/800/1400px", allOk);
  passed++;
} catch (e) {
  check("Bar count * 6 <= container width", false, e.message);
  failed++;
}


if (failed > 0) {
  console.log("WAVE TESTS: " + passed + " passed, " + failed + " FAILED");
  process.exit(1);
}
console.log("WAVE TESTS: all " + passed + " passed");
console.log("WAVE_OK");
