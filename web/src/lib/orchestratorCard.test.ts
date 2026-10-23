import assert from "node:assert/strict";
import { test } from "node:test";

import {
  activeTaskCount,
  blockReasonText,
  cardPrice,
  detailsWorthShowing,
  moneyCents,
  nodeKeyRu,
  roundMoneyText,
  splitCardSteps,
  tasksTabLabel,
} from "./orchestratorCard.ts";

test("money in chat answers is rounded to cents", () => {
  assert.equal(roundMoneyText("Траты: $0.4415, по оценке."), "Траты: $0.44, по оценке.");
  assert.equal(roundMoneyText("Траты по оценке: $5.8786"), "Траты по оценке: $5.88");
  assert.equal(roundMoneyText("лимит $1 и $2.50"), "лимит $1 и $2.50");
  assert.equal(moneyCents(0.001), "меньше $0.01");
  assert.equal(moneyCents(0.4), "$0.40");
  assert.equal(moneyCents(1.005), "$1.01");
  assert.equal(roundMoneyText("итого $1.005"), "итого $1.01");
});

test("a card that spends money never says about $0", () => {
  const queue = cardPrice({ costEstimate: 0, costCredits: null, costText: "", paid: true });
  assert.equal(queue.unknown, true);
  assert.doesNotMatch(queue.text, /\$0/);
  const zeroText = cardPrice({ costEstimate: null, costCredits: null, costText: "около $0", paid: true });
  assert.equal(zeroText.unknown, true);
  const later = cardPrice({
    costEstimate: null,
    costCredits: null,
    costText: "неизвестна: сам шаг бесплатный, но дальше воркер запустит платные шаги",
    paid: false,
  });
  assert.equal(later.unknown, true);
  assert.match(later.text, /^Цена неизвестна: сам шаг бесплатный/);
});

test("free step that makes the worker spend later is not called free", () => {
  const queue = cardPrice({
    costEstimate: 0,
    costCredits: null,
    costText: "",
    paid: false,
    effects: ["14 проектов встанут в очередь генерации: воркер сам пройдёт конвейер в каждом и потратит деньги."],
  });
  assert.deepEqual(queue, { text: "Цена неизвестна, но траты будут", unknown: true });
});

test("known and free prices stay plain", () => {
  assert.deepEqual(cardPrice({ costEstimate: 0.4415, costCredits: null, costText: "", paid: true }), {
    text: "около $0.44",
    unknown: false,
  });
  assert.deepEqual(
    cardPrice({ costEstimate: 0, costCredits: null, costText: "бесплатно: платных шагов нет", paid: false }),
    { text: "бесплатно: платных шагов нет", unknown: false },
  );
  assert.deepEqual(cardPrice({ costEstimate: 0, costCredits: null, costText: "", paid: false }), {
    text: "бесплатно",
    unknown: false,
  });
});

test("block reasons drop the plan step number even after the project", () => {
  assert.equal(blockReasonText("проект #5: шаг 1: стоимость неизвестна"), "проект #5: стоимость неизвестна");
  assert.equal(blockReasonText("шаг 2: нет кадров"), "нет кадров");
});

test("read steps fold into one line", () => {
  const read = { title: "Статус проекта", paid: false, risk: "none" };
  const queue = { title: "Поставить проекты в очередь", paid: false, risk: "high" };
  const { main, reads } = splitCardSteps([...Array.from({ length: 14 }, () => read), queue]);
  assert.deepEqual(main, [queue]);
  assert.equal(reads, "статус проекта (14 раз)");
  const onlyReads = splitCardSteps([read]);
  assert.deepEqual(onlyReads, { main: [read], reads: "" });
});

test("task counter shows only tasks still in work", () => {
  const tasks = [{ status: "done" }, { status: "failed" }, { status: "running" }, { status: "waiting_approval" }];
  assert.equal(activeTaskCount(tasks), 2);
  assert.equal(tasksTabLabel(tasks), "Задачи (2 в работе)");
  assert.equal(tasksTabLabel([{ status: "done" }]), "Задачи");
});

test("details link only for tasks that changed, spent or failed", () => {
  const read = { mutating: false, paid: false };
  assert.equal(detailsWorthShowing([read, read], [{ paid: false, status: "done" }]), false);
  assert.equal(detailsWorthShowing([read, { mutating: true, paid: false }], []), true);
  assert.equal(detailsWorthShowing([read], [{ paid: false, status: "failed" }]), true);
  // Планировщик не отметил mutating, но риск шага «low»: задача что-то меняла.
  assert.equal(detailsWorthShowing([{ ...read, risk: "low" }], [{ paid: false, status: "done" }]), true);
  assert.equal(detailsWorthShowing([{ ...read, risk: "none" }], [{ paid: false, status: "done" }]), false);
});

test("node keys become node names, unknown keys disappear", () => {
  assert.equal(nodeKeyRu("n_images"), "Картинки");
  assert.equal(nodeKeyRu("n_excel_gpt_2"), "Работа с GPT 2");
  assert.equal(nodeKeyRu("n_weird_thing"), "");
  assert.equal(nodeKeyRu("n_plan"), "План");
  assert.equal(nodeKeyRu("n_sd_agent"), "Агент сцен");
});
