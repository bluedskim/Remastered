(function () {
  "use strict";

  var FREQ_TICKS = [
    { hz: 50, label: "50" },
    { hz: 100, label: "100" },
    { hz: 200, label: "200" },
    { hz: 500, label: "500" },
    { hz: 1000, label: "1k" },
    { hz: 2000, label: "2k" },
    { hz: 5000, label: "5k" },
    { hz: 10000, label: "10k" },
    { hz: 20000, label: "20k" },
  ];

  var BANDS = [
    { name: "Bass", lo: 20, hi: 250 },
    { name: "Mid", lo: 250, hi: 2000 },
    { name: "Treble", lo: 2000, hi: 6000 },
    { name: "Air", lo: 6000, hi: 20000 },
  ];

  var COLORS = {
    original: "#9ca3af",
    reference: "#f97316",
    remastered: "#ec4899",
  };

  /**
   * Y-axis rule (keep in sync with tests/test_job_spectrum_chart_axis.py):
   * - Tick labels are 0-anchored multiples of tickStep (…, -20, -10, 0, 10, …).
   * - Prefer tickStep 10 dB; try 20 dB or 6 dB, or label every 2nd gridline, for 5–7 labels.
   * - Display floor yMin: −40 dB when tickStep is 10 or 20, else −42 dB (6 dB grid).
   * - Curves clip to yMin at the bottom edge; deep data below −42 is not shown.
   * - yMax is ALWAYS ceil((dataMax + 3 dB) / tickStep) * tickStep (never reduced).
   */
  var CLIP_TARGET_DB = -42;

  function ceilToStep(value, step) {
    return Math.ceil(value / step) * step;
  }

  function displayFloorForStep(step) {
    return step === 6 ? -42 : -40;
  }

  function tickCount(yMin, yMax, tickStep) {
    return (yMax - yMin) / tickStep + 1;
  }

  /** Keep in sync with tests/test_job_spectrum_chart_axis.py */
  function computeYAxisRange(allDb) {
    var dataMax = Math.max.apply(null, allDb);
    var headroom = 3;
    var requiredTop = dataMax + headroom;
    var minTicks = 5;
    var maxTicks = 7;
    var stepCandidates = [10, 20, 6];
    for (var si = 0; si < stepCandidates.length; si++) {
      var tickStep = stepCandidates[si];
      var yMin = displayFloorForStep(tickStep);
      var yMax = ceilToStep(requiredTop, tickStep);
      var count = tickCount(yMin, yMax, tickStep);
      if (count > maxTicks) {
        var labelEvery = 2;
        var labelCount = Math.ceil(count / labelEvery);
        if (labelCount >= minTicks && labelCount <= maxTicks) {
          return { yMin: yMin, yMax: yMax, tickStep: tickStep, labelEvery: labelEvery };
        }
        continue;
      }
      while (count < minTicks) {
        yMax += tickStep;
        count++;
      }
      return { yMin: yMin, yMax: yMax, tickStep: tickStep, labelEvery: 1 };
    }
    var tickStep = 20;
    var yMin = displayFloorForStep(tickStep);
    var yMax = ceilToStep(requiredTop, tickStep);
    return { yMin: yMin, yMax: yMax, tickStep: tickStep, labelEvery: 1 };
  }

  function yAxisTickValues(yMin, yMax, tickStep) {
    var k0 = yMin / tickStep;
    var k1 = yMax / tickStep;
    var ticks = [];
    for (var k = k0; k <= k1 + 1e-6; k++) {
      ticks.push(k * tickStep);
    }
    return ticks;
  }

  function logX(freq, w, fMin, fMax) {
    var minL = Math.log10(fMin);
    var maxL = Math.log10(fMax);
    return ((Math.log10(freq) - minL) / (maxL - minL)) * w;
  }

  function yMap(db, h, yMin, yMax) {
    return h - ((db - yMin) / (yMax - yMin)) * h;
  }

  function parsePayload(el) {
    var panel = el.closest("[data-spectrum-panel]");
    var script = panel && panel.querySelector("script.spectrum-data");
    if (!script) return null;
    try {
      return JSON.parse(script.textContent || "");
    } catch (_e) {
      return null;
    }
  }

  function polylinePoints(freqs, dbs, plotW, plotH, padL, padT, yMin, yMax, fMin, fMax) {
    var parts = [];
    for (var i = 0; i < freqs.length; i++) {
      var x = padL + logX(freqs[i], plotW, fMin, fMax);
      var db = dbs[i];
      if (db < yMin || db < CLIP_TARGET_DB) {
        db = yMin;
      }
      var y = padT + yMap(db, plotH, yMin, yMax);
      parts.push(x.toFixed(2) + "," + y.toFixed(2));
    }
    return parts.join(" ");
  }

  function renderChart(container, data) {
    var freqs = data.f;
    var fMin = freqs[0];
    var fMax = freqs[freqs.length - 1];
    var all = data.o.concat(data.r, data.m);
    var yAxis = computeYAxisRange(all);
    var yMin = yAxis.yMin;
    var yMax = yAxis.yMax;
    var yTickStep = yAxis.tickStep;
    var labelEvery = yAxis.labelEvery || 1;
    var yTicks = yAxisTickValues(yMin, yMax, yTickStep);

    var width = 720;
    var height = 280;
    var padL = 44;
    var padR = 12;
    var padT = 16;
    var padB = 36;
    var plotW = width - padL - padR;
    var plotH = height - padT - padB;

    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("class", "spectrum-chart__svg");
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");

    var defs = document.createElementNS(ns, "defs");
    var grad = document.createElementNS(ns, "linearGradient");
    grad.setAttribute("id", "spectrum-band-grad");
    grad.setAttribute("x1", "0");
    grad.setAttribute("y1", "0");
    grad.setAttribute("x2", "1");
    grad.setAttribute("y2", "0");
    grad.innerHTML =
      '<stop offset="0%" stop-color="rgba(139,92,246,0.08)"/>' +
      '<stop offset="100%" stop-color="rgba(236,72,153,0.05)"/>';
    defs.appendChild(grad);
    svg.appendChild(defs);

    BANDS.forEach(function (band) {
      var x1 = padL + logX(band.lo, plotW, fMin, fMax);
      var x2 = padL + logX(band.hi, plotW, fMin, fMax);
      var rect = document.createElementNS(ns, "rect");
      rect.setAttribute("x", x1.toFixed(2));
      rect.setAttribute("y", padT);
      rect.setAttribute("width", Math.max(0, x2 - x1).toFixed(2));
      rect.setAttribute("height", plotH);
      rect.setAttribute("fill", "url(#spectrum-band-grad)");
      rect.setAttribute("opacity", "0.55");
      svg.appendChild(rect);

      var label = document.createElementNS(ns, "text");
      label.setAttribute("x", ((x1 + x2) / 2).toFixed(2));
      label.setAttribute("y", (padT + 14).toFixed(2));
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("class", "spectrum-band-label");
      label.textContent = band.name;
      svg.appendChild(label);
    });

    yTicks.forEach(function (g, idx) {
      var gy = padT + yMap(g, plotH, yMin, yMax);
      var line = document.createElementNS(ns, "line");
      line.setAttribute("x1", padL);
      line.setAttribute("x2", padL + plotW);
      line.setAttribute("y1", gy.toFixed(2));
      line.setAttribute("y2", gy.toFixed(2));
      line.setAttribute("class", "spectrum-grid-line");
      svg.appendChild(line);
      if (idx % labelEvery === 0) {
        var ylab = document.createElementNS(ns, "text");
        ylab.setAttribute("x", (padL - 6).toFixed(2));
        ylab.setAttribute("y", (gy + 4).toFixed(2));
        ylab.setAttribute("text-anchor", "end");
        ylab.setAttribute("class", "spectrum-axis-label");
        ylab.textContent = g + " dB";
        svg.appendChild(ylab);
      }
    });

    FREQ_TICKS.forEach(function (tick) {
      if (tick.hz < fMin || tick.hz > fMax) return;
      var x = padL + logX(tick.hz, plotW, fMin, fMax);
      var vline = document.createElementNS(ns, "line");
      vline.setAttribute("x1", x.toFixed(2));
      vline.setAttribute("x2", x.toFixed(2));
      vline.setAttribute("y1", padT);
      vline.setAttribute("y2", padT + plotH);
      vline.setAttribute("class", "spectrum-grid-line spectrum-grid-line--minor");
      svg.appendChild(vline);
      var xlab = document.createElementNS(ns, "text");
      xlab.setAttribute("x", x.toFixed(2));
      xlab.setAttribute("y", (height - 10).toFixed(2));
      xlab.setAttribute("text-anchor", "middle");
      xlab.setAttribute("class", "spectrum-axis-label");
      xlab.textContent = tick.label;
      svg.appendChild(xlab);
    });

    var series = [
      { key: "o", color: COLORS.original, dash: "6 4", width: "1.5" },
      { key: "r", color: COLORS.reference, dash: "", width: "1.75" },
      { key: "m", color: COLORS.remastered, dash: "", width: "2.5" },
    ];

    series.forEach(function (s) {
      var pl = document.createElementNS(ns, "polyline");
      pl.setAttribute(
        "points",
        polylinePoints(freqs, data[s.key], plotW, plotH, padL, padT, yMin, yMax, fMin, fMax)
      );
      pl.setAttribute("fill", "none");
      pl.setAttribute("stroke", s.color);
      pl.setAttribute("stroke-width", s.width);
      if (s.dash) pl.setAttribute("stroke-dasharray", s.dash);
      if (s.key === "m") pl.setAttribute("class", "spectrum-line--remastered");
      svg.appendChild(pl);
    });

    var overlay = document.createElementNS(ns, "rect");
    overlay.setAttribute("x", padL);
    overlay.setAttribute("y", padT);
    overlay.setAttribute("width", plotW);
    overlay.setAttribute("height", plotH);
    overlay.setAttribute("fill", "transparent");
    overlay.setAttribute("class", "spectrum-hit-target");
    svg.appendChild(overlay);

    container.textContent = "";
    container.appendChild(svg);

    var panel = container.closest("[data-spectrum-panel]");
    var tooltipEl = panel ? panel.querySelector("[data-spectrum-tooltip]") : null;
    if (tooltipEl) {
      function showTip(evt) {
        var pt = svg.createSVGPoint();
        pt.x = evt.clientX;
        pt.y = evt.clientY;
        var ctm = svg.getScreenCTM();
        if (!ctm) return;
        var loc = pt.matrixTransform(ctm.inverse());
        var relX = loc.x - padL;
        if (relX < 0 || relX > plotW) {
          tooltipEl.hidden = true;
          return;
        }
        var minL = Math.log10(fMin);
        var maxL = Math.log10(fMax);
        var targetLog = minL + (relX / plotW) * (maxL - minL);
        var targetHz = Math.pow(10, targetLog);
        var idx = 0;
        var best = Infinity;
        for (var i = 0; i < freqs.length; i++) {
          var d = Math.abs(Math.log(freqs[i]) - Math.log(targetHz));
          if (d < best) {
            best = d;
            idx = i;
          }
        }
        var hz = freqs[idx];
        var labelHz = hz >= 1000 ? (hz / 1000).toFixed(1) + " kHz" : Math.round(hz) + " Hz";
        tooltipEl.textContent =
          labelHz +
          " · Original " +
          data.o[idx].toFixed(1) +
          " dB · Reference " +
          data.r[idx].toFixed(1) +
          " dB · Remastered " +
          data.m[idx].toFixed(1) +
          " dB";
        tooltipEl.hidden = false;
      }
      overlay.addEventListener("mousemove", showTip);
      overlay.addEventListener("touchmove", showTip, { passive: true });
      overlay.addEventListener("mouseleave", function () {
        tooltipEl.hidden = true;
      });
    }
  }

  document.querySelectorAll("[data-spectrum-chart]").forEach(function (el) {
    var data = parsePayload(el);
    if (data && data.f && data.o && data.r && data.m) {
      renderChart(el, data);
    }
  });
})();