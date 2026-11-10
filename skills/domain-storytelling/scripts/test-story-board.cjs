#!/usr/bin/env node
"use strict";

// Regression harness for story-board.js, the page-side helper for the tagged boards that
// story2egn.py writes. No dependencies. A tiny DOM stand-in implements only what the module
// touches and throws on any selector it does not know, so a new query shows up as a failure.
//   node shared/domain-storytelling/scripts/test-story-board.cjs
// scripts/run-tests.sh runs it as part of the whole suite.

const fs = require("fs");
const os = require("os");
const path = require("path");
const assert = require("assert");
const { execFileSync } = require("child_process");

const SB = require("./story-board.js");
const SVG_NS = "http://www.w3.org/2000/svg";
const PASS = [];
const FAIL = [];

function test(label, fn) {
  try {
    fn();
    PASS.push(label);
    console.log("PASS  " + label);
  } catch (err) {
    FAIL.push(label);
    console.log("FAIL  " + label + "\n      " + String(err.message).split("\n").join("\n      "));
  }
}

// ---------------------------------------------------------------- DOM stand-in

const SIMPLE = /^([a-zA-Z]*)((?:\[[\w-]+(?:="[^"]*")?\])*)$/;

function compile(selector) {
  return selector.split(",").map(part => {
    const m = SIMPLE.exec(part.trim());
    if (!m) throw new Error("stand-in does not support selector: " + selector);
    const attrs = [...m[2].matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)].map(a => [a[1], a[2]]);
    return { tag: m[1], attrs };
  });
}

class Doc {
  createElementNS(ns, name) {
    const el = new El(name, this);
    el.namespaceURI = ns;
    return el;
  }
}

class El {
  constructor(name, doc, attrs = {}, box = undefined) {
    this.nodeName = name;
    this.ownerDocument = doc;
    this.attrs = new Map(Object.entries(attrs));
    this.childNodes = [];
    this.parentNode = null;
    // undefined: no layout at all, null: getBBox throws as it does for an element that is not rendered
    if (box === null) this.getBBox = () => { throw new Error("element is not rendered"); };
    else if (box) this.getBBox = () => ({ x: box.x, y: box.y, width: box.width, height: box.height });
  }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  setAttribute(name, value) { this.attrs.set(name, String(value)); }
  removeAttribute(name) { this.attrs.delete(name); }
  appendChild(child) { child.parentNode = this; this.childNodes.push(child); return child; }
  removeChild(child) {
    const at = this.childNodes.indexOf(child);
    if (at === -1) throw new Error("not a child");
    this.childNodes.splice(at, 1);
    child.parentNode = null;
    return child;
  }
  matches(rules) {
    return rules.some(r => (!r.tag || r.tag === this.nodeName) &&
      r.attrs.every(([k, v]) => this.attrs.has(k) && (v === undefined || this.attrs.get(k) === v)));
  }
  querySelectorAll(selector) {
    const rules = compile(selector);
    const out = [];
    const walk = el => el.childNodes.forEach(c => { if (c.matches(rules)) out.push(c); walk(c); });
    walk(this);
    return out;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function snapshot(el) {
  const attrs = [...el.attrs.entries()].sort().map(([k, v]) => `${k}=${v}`).join(" ");
  return `<${el.nodeName} ${attrs}>${el.childNodes.map(snapshot).join("")}</${el.nodeName}>`;
}

// ---------------------------------------------------------------- a small board

function makeBoard() {
  const doc = new Doc();
  const svg = new El("svg", doc, { viewBox: "0 0 1000 800" });
  const root = new El("g", doc, { class: "sbTEST", "data-name": "Story" });
  svg.appendChild(root);
  root.appendChild(new El("g", doc, { class: "decor" }, { x: 0, y: 0, width: 5, height: 5 }));
  const add = (attrs, box) => root.appendChild(new El("g", doc, attrs, box));
  const t = {
    arrow1: add({ "data-s": "1", "data-name": "files" }, { x: 100, y: 100, width: 200, height: 50 }),
    arrow2: add({ "data-s": "2", "data-name": "routes" }, { x: 300, y: 200, width: 100, height: 60 }),
    arrow3: add({ "data-s": "3", "data-name": "approves" }, { x: 500, y: 300, width: 80, height: 40 }),
    relation: add({ "data-s": "", "data-name": "is owed to" }, { x: 600, y: 600, width: 100, height: 50 }),
    actorA: add({ "data-a": "1,2", "data-name": "Driver" }, { x: 20, y: 30, width: 76, height: 76 }),
    actorB: add({ "data-a": "3", "data-name": "Agent" }, { x: 900, y: 700, width: 60, height: 60 }),
    object: add({ "data-o": "2", "data-name": "Refund" }, { x: 350, y: 180, width: 60, height: 60 }),
    hidden: add({ "data-o": "2", "data-name": "Hidden" }, null),
    noLayout: add({ "data-a": "2", "data-name": "NoLayout" }),
    note: add({ "data-n": "2", "data-name": "note on Refund" }, { x: 420, y: 220, width: 210, height: 70 }),
    ownNote: add({ "data-n": "", "data-name": "note on Driver" }, { x: 10, y: 10, width: 210, height: 50 }),
  };
  return { doc, svg, root, t };
}

const names = list => list.map(tag => tag.name);
const rings = svg => svg.querySelectorAll('[data-sb="ring"]');
const dimmed = svg => svg.querySelectorAll('[data-sb="dim"]');

// ---------------------------------------------------------------- string and geometry helpers

test("parseSteps reads a comma list into trimmed strings", () => {
  assert.deepStrictEqual(SB.parseSteps("1"), ["1"]);
  assert.deepStrictEqual(SB.parseSteps("1,3,4"), ["1", "3", "4"]);
  assert.deepStrictEqual(SB.parseSteps(" 2 , 3 ,"), ["2", "3"]);
  assert.deepStrictEqual(SB.parseSteps("a,b"), ["a", "b"]);
});

test("parseSteps reads nothing as an empty list", () => {
  for (const nothing of ["", null, undefined]) assert.deepStrictEqual(SB.parseSteps(nothing), []);
});

test("parseSteps keeps step zero as a step and reads an empty value as none", () => {
  assert.deepStrictEqual(SB.parseSteps("0"), ["0"]);
  assert.deepStrictEqual(SB.parseSteps("0,2"), ["0", "2"]);
  assert.deepStrictEqual(SB.parseSteps(""), []);
  assert.deepStrictEqual(SB.parseSteps(null), []);
});

test("light lights a group tagged with step zero", () => {
  const { svg, root, doc } = makeBoard();
  const zero = root.appendChild(new El("g", doc, { "data-s": "0", "data-name": "starts" }));
  assert.deepStrictEqual(SB.light(svg, { steps: [0] }).map(t => t.el), [zero]);
});

test("isLit lights a tag when any of its steps is in the focus and never an untied one", () => {
  const tag = steps => ({ steps });
  assert.strictEqual(SB.isLit(tag(["1", "2"]), ["2"]), true);
  assert.strictEqual(SB.isLit(tag(["1", "2"]), ["3"]), false);
  assert.strictEqual(SB.isLit(tag([]), ["1", "2"]), false);
  assert.strictEqual(SB.isLit(tag(["1"]), []), false);
});

test("unionBox holds every box, grows by the pad and clamps to the limit", () => {
  const boxes = [{ x: 10, y: 20, width: 30, height: 40 }, { x: 100, y: 50, width: 10, height: 10 }];
  assert.deepStrictEqual(SB.unionBox(boxes, 0), { x: 10, y: 20, width: 100, height: 40 });
  assert.deepStrictEqual(SB.unionBox(boxes, 5), { x: 5, y: 15, width: 110, height: 50 });
  assert.deepStrictEqual(SB.unionBox(boxes, 50, { x: 0, y: 0, width: 130, height: 90 }),
    { x: 0, y: 0, width: 130, height: 90 });
  assert.strictEqual(SB.unionBox([], 10), null);
});

test("parseViewBox and viewBoxText round-trip and refuse what is not four numbers", () => {
  assert.deepStrictEqual(SB.parseViewBox("-715 -498 1343 1161"), { x: -715, y: -498, width: 1343, height: 1161 });
  assert.deepStrictEqual(SB.parseViewBox("0,0,10,20"), { x: 0, y: 0, width: 10, height: 20 });
  for (const bad of ["", null, undefined, "1 2 3", "1 2 3 x"]) assert.strictEqual(SB.parseViewBox(bad), null);
  assert.strictEqual(SB.viewBoxText({ x: 1.006, y: -2, width: 3.14159, height: 4 }), "1.01 -2 3.14 4");
});

// ---------------------------------------------------------------- reading a board

test("tags lists every tagged group in document order with its kind, steps and name", () => {
  const { svg } = makeBoard();
  const found = SB.tags(svg).map(t => [t.kind, t.steps, t.name]);
  assert.deepStrictEqual(found, [
    ["s", ["1"], "files"], ["s", ["2"], "routes"], ["s", ["3"], "approves"], ["s", [], "is owed to"],
    ["a", ["1", "2"], "Driver"], ["a", ["3"], "Agent"], ["o", ["2"], "Refund"], ["o", ["2"], "Hidden"],
    ["a", ["2"], "NoLayout"], ["n", ["2"], "note on Refund"], ["n", [], "note on Driver"],
  ]);
});

test("reading a board and never calling light leaves it exactly as it was", () => {
  const { svg } = makeBoard();
  const before = snapshot(svg);
  SB.tags(svg).forEach(t => SB.isLit(t, ["2"]));
  SB.clear(svg);
  assert.strictEqual(snapshot(svg), before);
});

// ---------------------------------------------------------------- light

test("light with dim marks exactly the unlit tags and returns the lit ones", () => {
  const { svg, t } = makeBoard();
  const lit = SB.light(svg, { steps: [2], dim: true });
  assert.deepStrictEqual(names(lit), ["routes", "Driver", "Refund", "Hidden", "NoLayout", "note on Refund"]);
  const unlit = [t.arrow1, t.arrow3, t.relation, t.actorB, t.ownNote];
  assert.deepStrictEqual(dimmed(svg), unlit);
  unlit.forEach(el => {
    assert.strictEqual(el.getAttribute("opacity"), "0.25");
    assert.strictEqual(el.getAttribute("data-sb"), "dim");
  });
  [t.arrow2, t.actorA, t.object, t.hidden, t.noLayout, t.note].forEach(el => {
    assert.strictEqual(el.getAttribute("opacity"), null);
    assert.strictEqual(el.getAttribute("data-sb"), null);
  });
});

test("light with a step and no other option changes nothing but the return value", () => {
  const { svg } = makeBoard();
  const before = snapshot(svg);
  const lit = SB.light(svg, { steps: ["2"] });
  assert.strictEqual(lit.length, 6);
  assert.strictEqual(snapshot(svg), before);
});

test("light matches steps given as numbers or as strings", () => {
  const a = makeBoard(), b = makeBoard();
  assert.deepStrictEqual(names(SB.light(a.svg, { steps: [3] })), names(SB.light(b.svg, { steps: ["3"] })));
  assert.deepStrictEqual(names(SB.light(a.svg, { steps: [3] })), ["approves", "Agent"]);
});

test("light lights every step of a list and an untied tag stays unlit", () => {
  const { svg } = makeBoard();
  const lit = names(SB.light(svg, { steps: [1, 3], dim: true }));
  assert.deepStrictEqual(lit, ["files", "approves", "Driver", "Agent"]);
  assert.ok(!lit.includes("is owed to") && !lit.includes("note on Driver"));
});

test("light with no steps applies nothing, whatever else is asked for", () => {
  const all = { dim: true, ring: true, pulse: true, crop: true };
  for (const opts of [undefined, {}, { steps: [] }, { ...all }, { ...all, steps: [] }, { ...all, steps: undefined }]) {
    const { svg } = makeBoard();
    const before = snapshot(svg);
    assert.deepStrictEqual(SB.light(svg, opts), []);
    assert.strictEqual(snapshot(svg), before);
  }
});

// ---------------------------------------------------------------- ring and pulse

test("ring appends one prefixed rect per lit tag that is not an arrow", () => {
  const { svg, root, t } = makeBoard();
  SB.light(svg, { steps: [2], ring: true });
  const made = rings(svg);
  assert.strictEqual(made.length, 3, "Driver, Refund and the note, but not the arrow, the hidden or the unlaid-out");
  made.forEach(r => {
    assert.strictEqual(r.nodeName, "rect");
    assert.strictEqual(r.namespaceURI, SVG_NS);
    assert.strictEqual(r.getAttribute("class"), "sbTEST-ring");
    assert.strictEqual(r.parentNode, root);
  });
  const grown = el => { const b = el.getBBox(); return [b.x - 6, b.y - 6, b.width + 12, b.height + 12].map(String); };
  assert.deepStrictEqual(made.map(r => ["x", "y", "width", "height"].map(k => r.getAttribute(k))),
    [grown(t.actorA), grown(t.object), grown(t.note)]);
  made.forEach(r => assert.strictEqual(r.getAttribute("rx"), "10"));
});

test("an arrow is lit but never ringed, even when it is the only thing lit", () => {
  const { svg } = makeBoard();
  const lit = SB.light(svg, { steps: [3], ring: true });
  assert.deepStrictEqual(names(lit), ["approves", "Agent"]);
  assert.strictEqual(rings(svg).length, 1);
  const { svg: lone } = makeBoard();
  lone.querySelector('[data-a="3"]').removeAttribute("data-a");
  assert.strictEqual(SB.light(lone, { steps: [3], ring: true }).length, 1);
  assert.strictEqual(rings(lone).length, 0);
});

test("ring without pulse draws no animation", () => {
  const { svg } = makeBoard();
  SB.light(svg, { steps: [2], ring: true });
  rings(svg).forEach(r => assert.strictEqual(r.childNodes.length, 0));
});

test("pulse gives each ring one animate child", () => {
  const { svg } = makeBoard();
  SB.light(svg, { steps: [2], ring: true, pulse: true });
  const made = rings(svg);
  assert.strictEqual(made.length, 3);
  made.forEach(r => {
    assert.strictEqual(r.childNodes.length, 1);
    const anim = r.childNodes[0];
    assert.strictEqual(anim.nodeName, "animate");
    assert.strictEqual(anim.namespaceURI, SVG_NS);
    assert.strictEqual(anim.getAttribute("attributeName"), "stroke-opacity");
    assert.strictEqual(anim.getAttribute("repeatCount"), "indefinite");
  });
});

test("the ring takes its class from the root group, so two boards keep their own", () => {
  const { svg, root } = makeBoard();
  root.setAttribute("class", "sbOTHER");
  SB.light(svg, { steps: [2], ring: true });
  rings(svg).forEach(r => assert.strictEqual(r.getAttribute("class"), "sbOTHER-ring"));
});

// ---------------------------------------------------------------- crop

test("crop rewrites the viewBox to the padded union of the lit boxes and keeps the original", () => {
  const { svg } = makeBoard();
  SB.light(svg, { steps: [2], crop: true });
  // lit boxes span x 20..630 and y 30..290, grown by 40 and clamped at the top left corner
  assert.strictEqual(svg.getAttribute("viewBox"), "0 0 670 330");
  assert.strictEqual(svg.getAttribute("data-sb-viewbox"), "0 0 1000 800");
});

test("crop never grows past the original picture", () => {
  const { svg } = makeBoard();
  SB.light(svg, { steps: [3], crop: true });
  assert.strictEqual(svg.getAttribute("viewBox"), "460 260 540 540");
  assert.strictEqual(svg.getAttribute("data-sb-viewbox"), "0 0 1000 800");
});

test("crop leaves the viewBox alone when no lit tag has a measurable box", () => {
  const { svg, t } = makeBoard();
  [t.arrow2, t.actorA, t.object, t.note].forEach(el => { el.getBBox = undefined; });
  SB.light(svg, { steps: [2], crop: true });
  assert.strictEqual(svg.getAttribute("viewBox"), "0 0 1000 800");
  assert.strictEqual(svg.getAttribute("data-sb-viewbox"), null);
});

test("crop is skipped, without failing, when the svg has no viewBox", () => {
  const { svg } = makeBoard();
  svg.removeAttribute("viewBox");
  SB.light(svg, { steps: [2], crop: true });
  assert.strictEqual(svg.getAttribute("viewBox"), null);
  assert.strictEqual(svg.getAttribute("data-sb-viewbox"), null);
});

// ---------------------------------------------------------------- clear

test("clear undoes dim, ring, pulse and crop and leaves the board as it was", () => {
  const { svg } = makeBoard();
  const before = snapshot(svg);
  SB.light(svg, { steps: [2], dim: true, ring: true, pulse: true, crop: true });
  assert.notStrictEqual(snapshot(svg), before);
  SB.clear(svg);
  assert.strictEqual(snapshot(svg), before);
  assert.strictEqual(rings(svg).length + dimmed(svg).length, 0);
});

test("clear on a board that was never lit changes nothing", () => {
  const { svg } = makeBoard();
  const before = snapshot(svg);
  SB.clear(svg);
  SB.clear(svg);
  assert.strictEqual(snapshot(svg), before);
});

test("lighting twice replaces the first lighting and keeps the original viewBox", () => {
  const { svg, t } = makeBoard();
  const all = { dim: true, ring: true, crop: true };
  SB.light(svg, { steps: [2], ...all });
  SB.light(svg, { steps: [3], ...all });
  assert.deepStrictEqual(dimmed(svg).map(el => el.getAttribute("data-name")).sort(),
    ["Driver", "Hidden", "NoLayout", "Refund", "files", "is owed to", "note on Driver", "note on Refund", "routes"]);
  assert.strictEqual(t.arrow3.getAttribute("opacity"), null);
  assert.strictEqual(rings(svg).length, 1);
  assert.strictEqual(svg.getAttribute("data-sb-viewbox"), "0 0 1000 800");
  assert.strictEqual(svg.getAttribute("viewBox"), "460 260 540 540");
});

// ---------------------------------------------------------------- a real board

const PYTHON = process.env.TEST_PYTHON || "python3";
const SCRIPT = path.join(__dirname, "story2egn.py");
const STORY = path.join(__dirname, "fixtures", "review-a-claim.story.json");

function decode(text) {
  return text.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&amp;/g, "&");
}

// A regex-level reader for the generator's output, which is flat enough to need no real parser
function parseSvg(text, doc) {
  const token = /<!--[\s\S]*?-->|<(\/?)([A-Za-z][\w:-]*)((?:\s+[\w:-]+="[^"]*")*)\s*(\/?)>|([^<]+)/g;
  const stack = [];
  let first = null;
  let m;
  while ((m = token.exec(text))) {
    if (m[0].startsWith("<!--") || m[5] !== undefined) continue;
    if (m[1]) { stack.pop(); continue; }
    const attrs = {};
    for (const a of m[3].matchAll(/([\w:-]+)="([^"]*)"/g)) attrs[a[1]] = decode(a[2]);
    const el = new El(m[2], doc, attrs);
    if (stack.length) stack[stack.length - 1].appendChild(el);
    first = first || el;
    if (!m[4]) stack.push(el);
  }
  return first;
}

// A stand-in measure for a real element: icons are 58 wide at their translate, the rest by coordinates
function measure(el) {
  const xs = [], ys = [];
  const walk = node => {
    const a = k => node.getAttribute(k);
    const move = a("transform") && /translate\(([-\d.]+),([-\d.]+)\)/.exec(a("transform"));
    if (move) { xs.push(+move[1], +move[1] + 58); ys.push(+move[2], +move[2] + 58); return; }
    if (a("d")) {
      let x = 0, y = 0;
      for (const seg of a("d").matchAll(/([MLHV])([^MLHVZ]*)/g)) {
        const n = seg[2].trim().split(/[\s,]+/).filter(Boolean).map(Number);
        if (seg[1] === "H") x = n[0];
        else if (seg[1] === "V") y = n[0];
        else { x = n[0]; y = n[1]; }
        xs.push(x); ys.push(y);
      }
    }
    if (a("x") !== null && a("y") !== null) {
      xs.push(+a("x"), +a("x") + +(a("width") || 0));
      ys.push(+a("y"), +a("y") + +(a("height") || 0));
    }
    if (a("cx") !== null) { xs.push(+a("cx") - +a("r"), +a("cx") + +a("r")); ys.push(+a("cy") - +a("r"), +a("cy") + +a("r")); }
    node.childNodes.forEach(walk);
  };
  walk(el);
  return { x: Math.min(...xs), y: Math.min(...ys), width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) };
}

function measured(root) {
  const all = root.querySelectorAll("[data-s],[data-a],[data-o],[data-n]");
  all.forEach(el => { el.getBBox = () => measure(el); });
}

function renderBoard() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "story-board-node-"));
  try {
    execFileSync(PYTHON, [SCRIPT, STORY, "--format", "board.svg", "--out-dir", dir], { stdio: "pipe" });
    const file = fs.readdirSync(dir).find(f => f.endsWith(".board.svg"));
    assert.ok(file, "the generator wrote no board");
    return fs.readFileSync(path.join(dir, file), "utf8");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

// What a step should light, read from the spec and not from the generator
function expectedLit(spec, step) {
  const out = new Set();
  spec.sentences.forEach((s, i) => {
    if (String(s.n === undefined ? i + 1 : s.n) !== String(step)) return;
    out.add(`s:${s.verb || ""}`);
    out.add(`a:${s.from}`);
    if (s.to) out.add(`a:${s.to}`);
    if (s.object) {
      out.add(`o:${s.object.name}`);
      if (s.to) out.add(`s:${s.prep || ""}`);
      if (s.note) out.add(`n:note on ${s.object.name}`);
    }
  });
  return [...out].sort();
}

const REAL_TEXT = (() => { try { return renderBoard(); } catch (err) { return err; } })();
const spec = JSON.parse(fs.readFileSync(STORY, "utf8"));

function realBoard() {
  if (REAL_TEXT instanceof Error) throw REAL_TEXT;
  const doc = new Doc();
  const svg = parseSvg(REAL_TEXT, doc);
  measured(svg);
  return svg;
}

test("on a real board tags reads what the generator wrote, tag for tag", () => {
  const svg = realBoard();
  const scraped = [...REAL_TEXT.matchAll(/<g data-([saon])="([^"]*)" data-name="([^"]*)">/g)]
    .map(m => [m[1], SB.parseSteps(m[2]), decode(m[3])]);
  const read = SB.tags(svg).map(t => [t.kind, t.steps, t.name]);
  assert.ok(scraped.length > 10);
  assert.deepStrictEqual(read, scraped);
  const count = kind => read.filter(r => r[0] === kind).length;
  const sentences = spec.sentences;
  assert.strictEqual(count("a"), spec.actors.length);
  assert.strictEqual(count("o"), sentences.filter(s => s.object).length);
  assert.strictEqual(count("s"), sentences.reduce((n, s) => n + 1 + (s.object && s.to ? 1 : 0), 0) + spec.relations.length);
  assert.strictEqual(count("n"), sentences.filter(s => s.note && s.object).length + spec.actors.filter(a => a.note).length);
});

test("on a real board a parallel step lights each of its sentences once and the shared actor once", () => {
  const svg = realBoard();
  const lit = SB.light(svg, { steps: [3] });
  const found = lit.map(t => `${t.kind}:${t.name}`).sort();
  assert.deepStrictEqual(found, expectedLit(spec, 3));
  assert.strictEqual(lit.filter(t => t.kind === "a" && t.name === "Assessor").length, 1);
});

test("on a real board dim, ring and crop hold together and clear restores the file", () => {
  const svg = realBoard();
  const before = snapshot(svg);
  const original = svg.getAttribute("viewBox");
  const lit = SB.light(svg, { steps: ["3"], dim: true, ring: true, pulse: true, crop: true });
  const all = SB.tags(svg);
  const litEls = new Set(lit.map(t => t.el));
  assert.strictEqual(dimmed(svg).length, all.length - lit.length);
  all.forEach(t => assert.strictEqual(t.el.getAttribute("data-sb") === "dim", !litEls.has(t.el), t.name));

  const prefix = svg.querySelector("g[data-name]").getAttribute("class");
  assert.match(prefix, /^sb[0-9a-f]{8}$/);
  assert.ok(REAL_TEXT.includes(`.${prefix}-ring{`), "the ring class has no rule in the board's style block");
  const made = rings(svg);
  assert.strictEqual(made.length, lit.filter(t => t.kind !== "s").length);
  made.forEach(r => {
    assert.strictEqual(r.getAttribute("class"), `${prefix}-ring`);
    assert.strictEqual(r.childNodes.length, 1);
  });

  const full = SB.parseViewBox(original);
  const crop = SB.parseViewBox(svg.getAttribute("viewBox"));
  assert.strictEqual(svg.getAttribute("data-sb-viewbox"), original);
  assert.ok(crop.x >= full.x && crop.y >= full.y);
  assert.ok(crop.x + crop.width <= full.x + full.width && crop.y + crop.height <= full.y + full.height);
  assert.ok(crop.width * crop.height < full.width * full.height, "the crop is no smaller than the picture");
  lit.forEach(t => {
    const b = t.el.getBBox();
    assert.ok(crop.x <= Math.max(b.x, full.x) && crop.y <= Math.max(b.y, full.y), t.name);
    assert.ok(crop.x + crop.width >= Math.min(b.x + b.width, full.x + full.width), t.name);
    assert.ok(crop.y + crop.height >= Math.min(b.y + b.height, full.y + full.height), t.name);
  });

  SB.clear(svg);
  assert.strictEqual(snapshot(svg), before);
});

test("rings keep the board prefix when the page adds a class after it", () => {
  const svg = realBoard();
  const prefix = svg.querySelector("g[data-name]").getAttribute("class");
  svg.querySelector("g[data-name]").setAttribute("class", prefix + " focus");
  SB.light(svg, { steps: [1], ring: true });
  const made = rings(svg);
  assert.ok(made.length > 0, "step 1 lights at least one actor or object to ring");
  made.forEach(r => assert.strictEqual(r.getAttribute("class"), prefix + "-ring"));
});

test("on a real board light without steps leaves the file as written", () => {
  const svg = realBoard();
  const before = snapshot(svg);
  SB.light(svg, { dim: true, ring: true, pulse: true, crop: true });
  assert.strictEqual(snapshot(svg), before);
});

// ---------------------------------------------------------------- result

console.log("");
console.log(`${PASS.length} passed, ${FAIL.length} failed`);
if (FAIL.length) {
  console.log("failed: " + FAIL.join("; "));
  process.exit(1);
}
