(function (global) {
  "use strict";

  var TARGET_SAMPLE_RATE = 48000;
  var BLOCK_SAMPLES = Math.round(0.4 * TARGET_SAMPLE_RATE);

  /** ITU-R BS.1770-4 K-weighting biquads @ 48 kHz (same strategy as common ebur128 libs). */
  var STAGE1 = { b0: 1.53512485958697, b1: -2.69169618940638, b2: 1.19839281085285, a1: -1.69065929318241, a2: 0.73248077421585 };
  var STAGE2 = { b0: 1.0, b1: -2.0, b2: 1.0, a1: -1.99004745483398, a2: 0.99007225036621, g: 1.58489319246111 };

  function attenuationGainsDb(lufsOriginal, lufsRemastered) {
    if (!isFinite(lufsOriginal) || !isFinite(lufsRemastered)) {
      return { original: 0, remastered: 0 };
    }
    var target = Math.min(lufsOriginal, lufsRemastered);
    var origDb = Math.min(target - lufsOriginal, 0);
    var remDb = Math.min(target - lufsRemastered, 0);
    return { original: origDb, remastered: remDb };
  }

  function attenuationDbToLinear(db) {
    if (!isFinite(db) || db >= 0) return 1;
    return Math.pow(10, db / 20);
  }

  function attenuationGainsLinear(lufsOriginal, lufsRemastered) {
    var db = attenuationGainsDb(lufsOriginal, lufsRemastered);
    return {
      original: attenuationDbToLinear(db.original),
      remastered: attenuationDbToLinear(db.remastered),
    };
  }

  function filterBiquad(samples, coeff, z) {
    var b0 = coeff.b0;
    var b1 = coeff.b1;
    var b2 = coeff.b2;
    var a1 = coeff.a1;
    var a2 = coeff.a2;
    var out = new Float32Array(samples.length);
    var z1 = z[0];
    var z2 = z[1];
    for (var i = 0; i < samples.length; i++) {
      var x = samples[i];
      var y = b0 * x + z1;
      z1 = b1 * x - a1 * y + z2;
      z2 = b2 * x - a2 * y;
      out[i] = y;
    }
    z[0] = z1;
    z[1] = z2;
    return out;
  }

  function kWeightChannel(channel) {
    var z1 = [0, 0];
    var z2 = [0, 0];
    var s1 = filterBiquad(channel, STAGE1, z1);
    var s2 = filterBiquad(s1, STAGE2, z2);
    if (STAGE2.g) {
      for (var i = 0; i < s2.length; i++) s2[i] *= STAGE2.g;
    }
    return s2;
  }

  function blockMeanSquare(channel, start, end) {
    var sum = 0;
    var n = end - start;
    if (n <= 0) return 0;
    for (var i = start; i < end; i++) {
      var v = channel[i];
      sum += v * v;
    }
    return sum / n;
  }

  function loudnessFromMeanSquare(ms) {
    if (ms <= 0) return -Infinity;
    return -0.691 + 10 * Math.log10(ms);
  }

  function integratedLoudnessLufs(buffer) {
    if (!buffer || buffer.length < 1) return NaN;
    var channels = buffer.numberOfChannels;
    var len = buffer.length;
    if (len < BLOCK_SAMPLES) return NaN;

    var weighted = [];
    for (var c = 0; c < channels; c++) {
      weighted.push(kWeightChannel(buffer.getChannelData(c)));
    }

    var blockLoudness = [];
    for (var start = 0; start + BLOCK_SAMPLES <= len; start += BLOCK_SAMPLES) {
      var end = start + BLOCK_SAMPLES;
      var acc = 0;
      for (c = 0; c < channels; c++) {
        acc += blockMeanSquare(weighted[c], start, end);
      }
      var ms = acc / channels;
      blockLoudness.push(loudnessFromMeanSquare(ms));
    }

    if (!blockLoudness.length) return NaN;

    var gated = blockLoudness.filter(function (l) {
      return isFinite(l) && l > -70;
    });
    if (!gated.length) return NaN;

  var ungated = gated.reduce(function (a, b) {
      return a + Math.pow(10, (b + 0.691) / 10);
    }, 0) / gated.length;
    ungated = loudnessFromMeanSquare(ungated);

    var relThreshold = ungated - 10;
    var gated2 = gated.filter(function (l) {
      return l >= relThreshold;
    });
    if (!gated2.length) return ungated;

    var integrated = gated2.reduce(function (a, b) {
      return a + Math.pow(10, (b + 0.691) / 10);
    }, 0) / gated2.length;
    return loudnessFromMeanSquare(integrated);
  }

  function resampleBufferTo48k(buffer, AudioContextCtor) {
    if (buffer.sampleRate === TARGET_SAMPLE_RATE) return Promise.resolve(buffer);
    var Ctor = AudioContextCtor || (typeof AudioContext !== "undefined" ? AudioContext : null);
    if (!Ctor) return Promise.resolve(buffer);
    var channels = buffer.numberOfChannels;
    var length = Math.max(1, Math.ceil(buffer.duration * TARGET_SAMPLE_RATE));
    var offline = new OfflineAudioContext(channels, length, TARGET_SAMPLE_RATE);
    var src = offline.createBufferSource();
    src.buffer = buffer;
    src.connect(offline.destination);
    src.start(0);
    return offline.startRendering();
  }

  function decodeAudioArrayBuffer(arrayBuffer, AudioContextCtor) {
    var Ctor = AudioContextCtor || (typeof AudioContext !== "undefined" ? AudioContext : null);
    if (!Ctor) return Promise.reject(new Error("no-audio"));
    var ctx = new Ctor();
    return ctx
      .decodeAudioData(arrayBuffer.slice(0))
      .then(function (buf) {
        try {
          ctx.close();
        } catch (err) {
          /* ignore */
        }
        return resampleBufferTo48k(buf, Ctor);
      });
  }

  function measureIntegratedLufsFromUrl(url, fetchFn) {
    var fetcher = fetchFn || global.fetch;
    if (!fetcher) return Promise.reject(new Error("no-fetch"));
    return fetcher(url, { credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("fetch-failed");
        return res.arrayBuffer();
      })
      .then(decodeAudioArrayBuffer)
      .then(integratedLoudnessLufs);
  }

  global.RemasteredAbLoudnessMatch = {
    TARGET_SAMPLE_RATE: TARGET_SAMPLE_RATE,
    BLOCK_SAMPLES: BLOCK_SAMPLES,
    attenuationGainsDb: attenuationGainsDb,
    attenuationGainsLinear: attenuationGainsLinear,
    attenuationDbToLinear: attenuationDbToLinear,
    integratedLoudnessLufs: integratedLoudnessLufs,
    decodeAudioArrayBuffer: decodeAudioArrayBuffer,
    measureIntegratedLufsFromUrl: measureIntegratedLufsFromUrl,
  };
})(typeof window !== "undefined" ? window : globalThis);
