/* Optional page-side helper for the tagged boards story2egn.py writes as <stem>.board.svg.
 *
 * A board wraps every activity, actor, work object and note in a <g> carrying one of
 * data-s, data-a, data-o, data-n (comma separated step numbers) and data-name. A page that
 * includes this file can light one or more steps on an inline board:
 *
 *   StoryBoard.light(svg, { steps: [2, 3], dim: true, ring: true, pulse: true, crop: true })
 *   StoryBoard.clear(svg)
 *
 * Nothing is applied by default. A page opts in per call. Plain script, no dependencies,
 * and it also loads as a CommonJS module so the string and geometry helpers run under node.
 */
(function (root) {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var TAGS = "[data-s],[data-a],[data-o],[data-n]";
  var KINDS = ["s", "a", "o", "n"];
  var DIM_OPACITY = "0.25";
  var CROP_PAD = 40;

  // "1,3" -> ["1","3"]. Step labels stay strings so a spec numbering by label still matches.
  function parseSteps(text) {
    if (text == null || text === "") return [];
    return String(text).split(",").map(function (s) { return s.trim(); }).filter(function (s) { return s !== ""; });
  }

  function tagOf(el) {
    for (var i = 0; i < KINDS.length; i++) {
      var raw = el.getAttribute("data-" + KINDS[i]);
      if (raw !== null && raw !== undefined) {
        return { el: el, kind: KINDS[i], steps: parseSteps(raw), name: el.getAttribute("data-name") || "" };
      }
    }
    return null;
  }

  function tags(svg) {
    var out = [];
    var list = svg.querySelectorAll(TAGS);
    for (var i = 0; i < list.length; i++) {
      var t = tagOf(list[i]);
      if (t) out.push(t);
    }
    return out;
  }

  // A tag is lit when any of its steps is in the focus. Untied tags (empty list) never are.
  function isLit(tag, focus) {
    return tag.steps.some(function (s) { return focus.indexOf(s) !== -1; });
  }

  // The smallest box holding every input box, grown by pad and clamped to the full picture.
  function unionBox(boxes, pad, limit) {
    if (!boxes.length) return null;
    var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    boxes.forEach(function (b) {
      x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y);
      x1 = Math.max(x1, b.x + b.width); y1 = Math.max(y1, b.y + b.height);
    });
    x0 -= pad; y0 -= pad; x1 += pad; y1 += pad;
    if (limit) {
      x0 = Math.max(x0, limit.x); y0 = Math.max(y0, limit.y);
      x1 = Math.min(x1, limit.x + limit.width); y1 = Math.min(y1, limit.y + limit.height);
    }
    return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
  }

  function parseViewBox(text) {
    var n = String(text || "").trim().split(/[\s,]+/).map(Number);
    if (n.length !== 4 || n.some(isNaN)) return null;
    return { x: n[0], y: n[1], width: n[2], height: n[3] };
  }

  function viewBoxText(b) {
    return [b.x, b.y, b.width, b.height].map(function (v) { return Math.round(v * 100) / 100; }).join(" ");
  }

  // The ring takes its colour from the board's own style block, keyed by the root group's first
  // class. A page may add classes of its own after it, so only the first one is the prefix.
  function prefixOf(svg) {
    var group = svg.querySelector("g[data-name]");
    var classes = ((group && group.getAttribute("class")) || "").trim().split(/\s+/);
    return classes[0] || "";
  }

  function makeRing(svg, box, prefix, pulse) {
    var doc = svg.ownerDocument || root.document;
    var ring = doc.createElementNS(SVG_NS, "rect");
    var pad = 6;
    ring.setAttribute("x", String(box.x - pad));
    ring.setAttribute("y", String(box.y - pad));
    ring.setAttribute("width", String(box.width + 2 * pad));
    ring.setAttribute("height", String(box.height + 2 * pad));
    ring.setAttribute("rx", "10");
    ring.setAttribute("class", prefix ? prefix + "-ring" : "");
    ring.setAttribute("data-sb", "ring");
    if (pulse) {
      var anim = doc.createElementNS(SVG_NS, "animate");
      anim.setAttribute("attributeName", "stroke-opacity");
      anim.setAttribute("values", "1;0.3;1");
      anim.setAttribute("dur", "1.4s");
      anim.setAttribute("repeatCount", "indefinite");
      ring.appendChild(anim);
    }
    return ring;
  }

  function boxOf(el) {
    if (typeof el.getBBox !== "function") return null;
    try { return el.getBBox(); } catch (e) { return null; }
  }

  // Undo whatever light() applied. Safe to call on a board that was never lit.
  function clear(svg) {
    var rings = svg.querySelectorAll('[data-sb="ring"]');
    for (var i = 0; i < rings.length; i++) rings[i].parentNode.removeChild(rings[i]);
    var dimmed = svg.querySelectorAll('[data-sb="dim"]');
    for (var j = 0; j < dimmed.length; j++) {
      dimmed[j].removeAttribute("opacity");
      dimmed[j].removeAttribute("data-sb");
    }
    var original = svg.getAttribute("data-sb-viewbox");
    if (original !== null && original !== undefined) {
      svg.setAttribute("viewBox", original);
      svg.removeAttribute("data-sb-viewbox");
    }
  }

  // opts: steps (array of step numbers or labels), dim, ring, pulse, crop. Returns the lit tags.
  function light(svg, opts) {
    opts = opts || {};
    clear(svg);
    var focus = (opts.steps || []).map(String);
    if (!focus.length) return [];
    var all = tags(svg);
    var lit = all.filter(function (t) { return isLit(t, focus); });

    if (opts.dim) {
      all.forEach(function (t) {
        if (isLit(t, focus)) return;
        t.el.setAttribute("opacity", DIM_OPACITY);
        t.el.setAttribute("data-sb", "dim");
      });
    }

    var prefix = prefixOf(svg);
    if (opts.ring || opts.pulse) {
      var host = svg.querySelector("g[data-name]") || svg;
      lit.forEach(function (t) {
        if (t.kind === "s") return; // an arrow's box is a diagonal, so it is lit but never ringed
        var box = boxOf(t.el);
        if (box) host.appendChild(makeRing(svg, box, prefix, !!opts.pulse));
      });
    }

    if (opts.crop) {
      var boxes = lit.map(function (t) { return boxOf(t.el); }).filter(Boolean);
      var full = parseViewBox(svg.getAttribute("viewBox"));
      var crop = unionBox(boxes, CROP_PAD, full);
      if (crop && full) {
        svg.setAttribute("data-sb-viewbox", svg.getAttribute("viewBox"));
        svg.setAttribute("viewBox", viewBoxText(crop));
      }
    }
    return lit;
  }

  var api = {
    parseSteps: parseSteps, tags: tags, isLit: isLit, unionBox: unionBox,
    parseViewBox: parseViewBox, viewBoxText: viewBoxText, light: light, clear: clear
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.StoryBoard = api;
})(typeof window !== "undefined" ? window : globalThis);
