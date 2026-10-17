"use client";
/** Глобальный фильтр: бренды сторонних провайдеров никогда не показываются в UI.
 *  Имена хранятся кодами символов, чтобы сами бренды не попадали в JS-бандл (его видно в devtools). */
import { useEffect } from "react";

const NAMES: string[] = ([[111,117,116,115,101,101], [1086,1091,1090,1089,1080], [1072,1091,1090,1089,1080], [107,105,101], [53,50,112,97,121], [112,97,121,53,50], [118,105,98,101,99,111,100,101], [119,97,118,101,115,112,101,101,100], [101,108,101,118,101,110,108,97,98,115], [97,110,116,104,114,111,112,105,99], [111,112,101,110,97,105]] as number[][]).map((c) => String.fromCharCode(...c));
const SRC = "(?<![\\p{L}\\d])(" + NAMES.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")(\\.io|\\.ai)?(?![\\p{L}\\d])";
const RX = new RegExp(SRC, "giu");
const TEST = new RegExp(SRC, "iu");
const ATTRS = ["title", "placeholder", "aria-label", "alt"];

const fix = (s: string) => s.replace(RX, "VIA");

function scrub(root: Node) {
  if (root.nodeType === 3) {
    const v = root.nodeValue || "";
    if (TEST.test(v)) root.nodeValue = fix(v);
    return;
  }
  if (root.nodeType !== 1) return;
  const el = root as Element;
  const tag = el.tagName;
  if (tag === "SCRIPT" || tag === "STYLE") return;
  for (const a of ATTRS) {
    const v = el.getAttribute(a);
    if (v && TEST.test(v)) el.setAttribute(a, fix(v));
  }
  if (tag === "INPUT" || tag === "TEXTAREA") return;
  const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let n: Node | null;
  while ((n = w.nextNode())) {
    const p = n.parentElement;
    if (p && (p.tagName === "SCRIPT" || p.tagName === "STYLE" || p.tagName === "TEXTAREA")) continue;
    const v = n.nodeValue || "";
    if (TEST.test(v)) n.nodeValue = fix(v);
  }
  el.querySelectorAll("[title],[placeholder],[aria-label],[alt]").forEach((c) => {
    for (const a of ATTRS) {
      const v = c.getAttribute(a);
      if (v && TEST.test(v)) c.setAttribute(a, fix(v));
    }
  });
}

export function BrandScrub() {
  useEffect(() => {
    scrub(document.body);
    if (TEST.test(document.title)) document.title = fix(document.title);
    const mo = new MutationObserver((ms) => {
      for (const m of ms) {
        if (m.type === "characterData" || m.type === "attributes") scrub(m.target);
        else m.addedNodes.forEach((n) => scrub(n));
      }
    });
    mo.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    return () => mo.disconnect();
  }, []);
  return null;
}
